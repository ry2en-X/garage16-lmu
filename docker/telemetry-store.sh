# docker/telemetry-store.sh — the incremental telemetry backup, POSIX sh + busybox.
#
# Sourced by scripts/ops/backup.sh and scripts/ops/restore.sh (inside an Alpine container) and by
# docker/backup-loop.sh (the `backup` container).
#
# Telemetry files never change after the upload — a lap's file is written once and only ever
# deleted. So instead of packing the whole archive every night (V0.9.8–V1.0.1: telemetry.tar.gz,
# 1000+ files, growing), every backup folder holds
#
#     db.sql.gz              the whole database, as before
#     telemetry-files.txt    "<bytes> <path>" for every file the volume held at that moment
#
# and the files themselves live ONCE in <backup dir>/telemetry-store/<path>, next to the backup
# folders: a backup copies only the files that are new since the last one. Restoring a backup
# takes exactly the files its list names from the store (sizes checked). Cleaning up removes a file
# from the store when no remaining backup lists it any more.
#
# A file that is in the store under the same path with ANOTHER size (should never happen) is
# replaced; an older backup listing the old size is then refused by the restore instead of being
# restored with the wrong file.

# _human <bytes> — "1.1 GB" / "340.5 MB" / "12 KB" (shell arithmetic only: busybox, no awk needed)
_human() {
    if [ "$1" -ge 1073741824 ]; then
        echo "$(($1 / 1073741824)).$(($1 % 1073741824 * 10 / 1073741824)) GB"
    elif [ "$1" -ge 1048576 ]; then
        echo "$(($1 / 1048576)).$(($1 % 1048576 * 10 / 1048576)) MB"
    else
        echo "$(($1 / 1024)) KB"
    fi
}

# _progress <prefix> <what> — one line on STDERR. stdout of store_add is its result line, which the
# callers capture ($(...), > file), so the operator would never see anything on stdout while it
# runs; stderr goes straight to the terminal / the container log.
_progress() { echo "$1 $2" >&2; }

# store_add <data dir> <store dir> <list file> — copies new files, writes the list; prints
# "<files> <new files> <bytes>" on stdout. Progress ("[backup] ...") goes to stderr: first how much
# is new, then a line about every 100 files or every 10 s while copying.
store_add() {
    _data="$1"; _store="$2"; _list="$3"
    mkdir -p "$_store" || return 1
    : > "$_list.tmp" || return 1
    : > "$_list.todo" || return 1
    _files=0; _new=0; _bytes=0; _newbytes=0
    # the volume's own bookkeeping (lock, maintenance marker) and a restore's rollback copy are not data
    ( cd "$_data" && find . \( -path './.garage16-*' -o -path './.pre_restore_*' \) -prune -o -type f -print ) \
        | sed 's|^\./||' | sort > "$_list.paths" || return 1
    # pass 1: the list, and which files the store does not have yet
    while IFS= read -r _path; do
        _size=$(stat -c %s "$_data/$_path") || return 1
        if [ ! -f "$_store/$_path" ] || [ "$(stat -c %s "$_store/$_path")" != "$_size" ]; then
            echo "$_path" >> "$_list.todo" || return 1
            _new=$((_new + 1)); _newbytes=$((_newbytes + _size))
        fi
        echo "$_size $_path" >> "$_list.tmp" || return 1
        _files=$((_files + 1)); _bytes=$((_bytes + _size))
    done < "$_list.paths"
    _progress "[backup]" "$_files files in the volume ($(_human "$_bytes")), $_new of them new for the store ($(_human "$_newbytes"))."
    if [ "$_new" -gt 100 ] || [ "$_newbytes" -gt 536870912 ]; then
        _progress "[backup]" "The first backup into the store copies everything once - this can take a while, do not interrupt (Ctrl+C)."
    fi
    # pass 2: copy the new ones
    _done=0; _donebytes=0; _last=$(date +%s)
    while IFS= read -r _path; do
        _size=$(stat -c %s "$_data/$_path") || return 1
        mkdir -p "$_store/$(dirname "$_path")" || return 1
        cp -p "$_data/$_path" "$_store/$_path.part" && mv -f "$_store/$_path.part" "$_store/$_path" || return 1
        _done=$((_done + 1)); _donebytes=$((_donebytes + _size))
        _now=$(date +%s)
        if [ $((_done % 100)) -eq 0 ] || [ $((_now - _last)) -ge 10 ]; then
            if [ "$_done" -lt "$_new" ]; then
                _progress "[backup]" "$_done / $_new files ($(_human "$_donebytes") of $(_human "$_newbytes")) copied ..."
            fi
            _last=$_now
        fi
    done < "$_list.todo"
    [ "$_new" -gt 0 ] && _progress "[backup]" "$_new / $_new files ($(_human "$_newbytes")) copied."
    rm -f "$_list.paths" "$_list.todo"
    mv -f "$_list.tmp" "$_list" || return 1
    echo "$_files $_new $_bytes"
}

# store_check <store dir> <list file> — every listed file is in the store with its size; no path
# leaves the store (absolute or ..)
store_check() {
    _store="$1"; _list="$2"
    while read -r _size _path; do
        case "$_path" in
            /*|..|../*|*/../*|*/..) echo "unsafe path in the list: $_path" >&2; return 1 ;;
        esac
        [ -f "$_store/$_path" ] || { echo "missing in the store: $_path" >&2; return 1; }
        [ "$(stat -c %s "$_store/$_path")" = "$_size" ] || { echo "another size in the store: $_path" >&2; return 1; }
    done < "$_list"
}

# store_stage <store dir> <list file> <target dir> — the files of one backup into an empty folder.
# Progress ("[restore] ...") on stderr: the total first, then about every 100 files or 10 s.
store_stage() {
    _store="$1"; _list="$2"; _target="$3"
    store_check "$_store" "$_list" || return 1
    _total=$(grep -c . "$_list"); _sum=0
    while read -r _size _path; do _sum=$((_sum + _size)); done < "$_list"
    _progress "[restore]" "Copying $_total files ($(_human "$_sum")) from the store."
    if [ "$_total" -gt 100 ] || [ "$_sum" -gt 536870912 ]; then
        _progress "[restore]" "This can take a while - do not interrupt (Ctrl+C)."
    fi
    _done=0; _donebytes=0; _last=$(date +%s)
    while read -r _size _path; do
        mkdir -p "$_target/$(dirname "$_path")" && cp -p "$_store/$_path" "$_target/$_path" || return 1
        _done=$((_done + 1)); _donebytes=$((_donebytes + _size))
        _now=$(date +%s)
        if [ $((_done % 100)) -eq 0 ] || [ $((_now - _last)) -ge 10 ]; then
            if [ "$_done" -lt "$_total" ]; then
                _progress "[restore]" "$_done / $_total files ($(_human "$_donebytes") of $(_human "$_sum")) copied ..."
            fi
            _last=$_now
        fi
    done < "$_list"
    _progress "[restore]" "$_total / $_total files copied."
}

# store_prune <store dir> <backups dir> — removes files no remaining backup lists (only when at
# least one backup with a list remains — never empties the store by accident)
store_prune() {
    _store="$1"; _backups="$2"
    [ -d "$_store" ] || return 0
    _keep="$_store/.keep.$$"
    for _l in "$_backups"/garage16_*/telemetry-files.txt; do
        case "$_l" in *.partial/*) continue ;; esac
        [ -f "$_l" ] || continue
        cut -d' ' -f2- "$_l"
    done | sort -u > "$_keep"
    if [ ! -s "$_keep" ]; then rm -f "$_keep"; return 0; fi
    ( cd "$_store" && find . -type f ! -name '.keep.*' | sed 's|^\./||' ) | sort | while IFS= read -r _path; do
        if ! grep -qxF "$_path" "$_keep"; then rm -f "$_store/$_path"; fi
    done
    find "$_store" -mindepth 1 -type d -empty -delete 2>/dev/null || true
    rm -f "$_keep"
}
