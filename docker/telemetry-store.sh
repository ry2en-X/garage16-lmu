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

# store_add <data dir> <store dir> <list file> — copies new files, writes the list; prints
# "<files> <new files> <bytes>"
store_add() {
    _data="$1"; _store="$2"; _list="$3"
    mkdir -p "$_store" || return 1
    : > "$_list.tmp" || return 1
    _files=0; _new=0; _bytes=0
    # the volume's own bookkeeping (lock, maintenance marker) and a restore's rollback copy are not data
    ( cd "$_data" && find . \( -path './.garage16-*' -o -path './.pre_restore_*' \) -prune -o -type f -print ) \
        | sed 's|^\./||' | sort > "$_list.paths" || return 1
    while IFS= read -r _path; do
        _size=$(stat -c %s "$_data/$_path") || return 1
        if [ ! -f "$_store/$_path" ] || [ "$(stat -c %s "$_store/$_path")" != "$_size" ]; then
            mkdir -p "$_store/$(dirname "$_path")" || return 1
            cp -p "$_data/$_path" "$_store/$_path.part" && mv -f "$_store/$_path.part" "$_store/$_path" || return 1
            _new=$((_new + 1))
        fi
        echo "$_size $_path" >> "$_list.tmp" || return 1
        _files=$((_files + 1)); _bytes=$((_bytes + _size))
    done < "$_list.paths"
    rm -f "$_list.paths"
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

# store_stage <store dir> <list file> <target dir> — the files of one backup into an empty folder
store_stage() {
    _store="$1"; _list="$2"; _target="$3"
    store_check "$_store" "$_list" || return 1
    while read -r _size _path; do
        mkdir -p "$_target/$(dirname "$_path")" && cp -p "$_store/$_path" "$_target/$_path" || return 1
    done < "$_list"
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
