"""Backup and restore, run for real: the shipped scripts/ops/backup.sh,
docker/backup-loop.sh and scripts/ops/restore.sh against a real PostgreSQL, the real app, real Alpine
containers (busybox flock / tar / cp) — only `docker` itself is replaced by tests/helpers/
fake_docker.py, which runs those containers as chroots in private mount namespaces.

Backup: uploads running DURING a backup can never produce a database dump and a file archive of
different moments; an interrupted or killed backup publishes nothing and blocks uploads no longer
than its lock allows; a failing step publishes nothing.

Restore: a fault injected into EACH step leaves either the validated backup live or the state
before the restore — never a mix; a failing rollback says so (exit 3) instead of staying silent;
--rollback and --cleanup do what they say.

Needs: PostgreSQL (GARAGE16_TEST_PG_ADMIN_URL, a superuser), root or passwordless sudo (mount
namespaces, chroot) and the Alpine mini root file system (scripts/dev/setup_test_env.sh fetches it to
tests/.cache/). In strict mode (release gate, CI) a missing one fails the run.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

from tests.helpers.synthetic_lap import make_lap
from tests.local_servers import free_port

ROOT = Path(__file__).resolve().parents[2]
ALPINE_TGZ = Path(os.environ.get("GARAGE16_ALPINE_ROOTFS", ROOT / "tests" / ".cache" / "alpine-minirootfs-3.21.8-x86_64.tar.gz"))
PG_URL = os.environ.get("GARAGE16_TEST_PG_ADMIN_URL", "")


def _can_root() -> bool:
    if os.geteuid() == 0:
        return True
    return shutil.which("sudo") is not None and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


_missing = [m for m, ok in (
    ("PostgreSQL (set GARAGE16_TEST_PG_ADMIN_URL to a superuser URL)", bool(PG_URL)),
    ("root or passwordless sudo (mount namespaces, chroot)", _can_root()),
    (f"the Alpine root file system ({ALPINE_TGZ}; scripts/dev/setup_test_env.sh fetches it)", ALPINE_TGZ.exists()),
    ("unshare / nsenter / chroot / flock / pg_dump", all(shutil.which(t) for t in ("unshare", "nsenter", "chroot", "flock", "pg_dump", "psql"))),
) if not ok]
pytestmark = pytest.mark.skipif(bool(_missing), reason="backup/restore drill needs: " + "; ".join(_missing))

SUDO = [] if os.geteuid() == 0 else ["sudo", "-n"]


@pytest.fixture(scope="session")
def alpine_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("alpine") / "root"
    root.mkdir()
    subprocess.run(SUDO + ["tar", "xzf", str(ALPINE_TGZ), "-C", str(root)], check=True)
    for d in ("data", "backup", "live", "stage", "restore"):
        (root / d).mkdir(exist_ok=True)
    return root


class Api:
    """requests with a TestClient-like interface (tests/helpers/v093.upload_df talks to it)."""

    def __init__(self, base):
        self.base = base

    def post(self, url, **kw):
        return requests.post(self.base + url, timeout=30, **kw)

    def get(self, url, **kw):
        return requests.get(self.base + url, timeout=30, **kw)


class Stack:
    def __init__(self, tmp: Path, alpine_root: Path):
        u = urlparse(PG_URL)
        self.pg = {"host": u.hostname or "localhost", "port": str(u.port or 5432), "user": u.username, "password": u.password or ""}
        self.db = f"g16drill_{uuid.uuid4().hex[:8]}"
        self.state = tmp / "docker-state"
        self.backups = tmp / "backups"
        self.backups.mkdir()
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "docker"
        shim.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'tests' / 'helpers' / 'fake_docker.py'))} \"$@\"\n")
        shim.chmod(0o755)
        self.port = free_port()
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_DOCKER_STATE": str(self.state), "FAKE_ALPINE_ROOT": str(alpine_root), "FAKE_REPO": str(ROOT),
            "FAKE_PYTHON": sys.executable, "FAKE_PG_HOST": self.pg["host"], "FAKE_PG_PORT": self.pg["port"],
            "FAKE_PG_PASSWORD": self.pg["password"], "FAKE_SERVER_PORT": str(self.port),
            "POSTGRES_USER": self.pg["user"], "POSTGRES_DB": self.db, "PGPASSWORD": self.pg["password"],
        }
        for k in ("GARAGE16_DB_URL", "GARAGE16_STORAGE_DIR"):
            self.env.pop(k, None)
        (self.state / "volumes" / "garage16_telemetry_data").mkdir(parents=True)
        subprocess.run(SUDO + ["mkdir", "-p", "/data/telemetry", "/restore"], check=True)   # mount points
        self.psql_admin(f'CREATE DATABASE "{self.db}"')
        self.docker("compose", "up", "-d", "db", "server", "discord_bot")
        self.api = Api(f"http://127.0.0.1:{self.port}")
        from tests.helpers.v093 import register

        self.account = register(self.api, f"Drill {uuid.uuid4().hex[:6]}")
        self.n = 0

    # -- plumbing
    def docker(self, *args, check=True, extra_env=None):
        r = subprocess.run(["docker", *args], env={**self.env, **(extra_env or {})}, capture_output=True, text=True, timeout=300)
        if check and r.returncode != 0:
            raise AssertionError(f"docker {' '.join(args)} -> {r.returncode}: {r.stdout}{r.stderr}")
        return r

    def psql_admin(self, sql, db="postgres"):
        r = subprocess.run(["psql", "-h", self.pg["host"], "-p", self.pg["port"], "-U", self.pg["user"], "-d", db,
                            "-v", "ON_ERROR_STOP=1", "-Atc", sql], env={**os.environ, "PGPASSWORD": self.pg["password"]},
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def script(self, *args, extra_env=None, timeout=300):
        return subprocess.run(["bash", *args], cwd=ROOT, env={**self.env, **(extra_env or {})}, capture_output=True, text=True, timeout=timeout)

    def upload(self, retries=50):
        """One lap; on 503 (backup running) wait and retry like the desktop client. -> (lap_id, number of 503s)"""
        from tests.helpers.v093 import upload_df

        self.n += 1
        seen = 0
        for _ in range(retries):
            try:
                return upload_df(self.api, self.account, make_lap(seed=1000 + self.n, n=600), lap_number=self.n,
                                 recorded_at=1_700_000_000 + self.n)["lap_id"], seen
            except AssertionError as exc:
                if "503" not in str(exc) and "MAINTENANCE" not in str(exc):
                    raise
                seen += 1
                time.sleep(0.3)
        raise AssertionError("upload never got through")

    # -- what is live
    @property
    def volume(self) -> Path:
        return self.state / "volumes" / "garage16_telemetry_data"

    def live_laps(self) -> dict:
        out = self.psql_admin("select id, telemetry_hash from laps order by id", db=self.db)
        return dict(line.split("|") for line in out.splitlines()) if out else {}

    def live_files(self) -> set:
        return {str(p.relative_to(self.volume)) for p in self.volume.rglob("*.parquet")
                if not p.relative_to(self.volume).parts[0].startswith(".")}

    def databases(self) -> set:
        return set(self.psql_admin(f"select datname from pg_database where datname like '{self.db}%'").split())

    def snapshot(self):
        return self.live_laps(), self.live_files()

    def server_running(self) -> bool:
        try:
            return requests.get(self.api.base + "/health", timeout=2).status_code == 200
        except requests.RequestException:
            return False

    def close(self):
        self.docker("compose", "stop", "server", check=False)
        for name in (self.state / "containers").glob("*.pid") if (self.state / "containers").exists() else []:
            self.docker("rm", "-f", name.stem, check=False)
        for db in self.databases():
            self.psql_admin(f"select pg_terminate_backend(pid) from pg_stat_activity where datname = '{db}'")
            self.psql_admin(f'DROP DATABASE IF EXISTS "{db}"')
        subprocess.run(SUDO + ["rm", "-rf", str(self.state)])


@pytest.fixture
def stack(tmp_path, alpine_root):
    s = Stack(tmp_path, alpine_root)
    try:
        yield s
    finally:
        s.close()


def _published(stack) -> list:
    return sorted(p for p in stack.backups.iterdir() if p.is_dir() and p.name.startswith("garage16_"))


def _copy_backup(folder: Path, tmp_path: Path) -> Path:
    """A backup folder AND the telemetry store next to it (manifest format 2)."""
    bad = tmp_path / "bad"
    shutil.copytree(folder, bad)
    if (folder.parent / "telemetry-store").is_dir():
        shutil.copytree(folder.parent / "telemetry-store", tmp_path / "telemetry-store", dirs_exist_ok=True)
    return bad


def _backup_files(folder: Path, manifest: dict) -> dict:
    """{relative path: sha256} of the telemetry files a backup holds — V1.0.2: the files of its
    list in the store (sizes checked), before: the members of telemetry.tar.gz."""
    if manifest.get("format", 1) >= 2:
        store = (folder / manifest["telemetry"]["store"]).resolve()
        files = {}
        for line in (folder / "telemetry-files.txt").read_text().splitlines():
            size, rel = line.split(" ", 1)
            data = (store / rel).read_bytes()
            assert len(data) == int(size), rel
            if rel.endswith(".parquet"):
                files[rel] = hashlib.sha256(data).hexdigest()
        assert manifest["telemetry"]["files"] == len((folder / "telemetry-files.txt").read_text().splitlines())
        return files
    with tarfile.open(folder / "telemetry.tar.gz") as tar:
        return {m.name.lstrip("./"): hashlib.sha256(tar.extractfile(m).read()).hexdigest()
                for m in tar.getmembers() if m.isfile() and m.name.endswith(".parquet")}


def _check_backup(stack, folder: Path) -> dict:
    """Independent check of a backup: every lap row of the dump has its file in the archive with
    that sha256 — and the archive holds no lap file without a row."""
    manifest = json.loads((folder / "manifest.json").read_text())
    for part in ("database", "telemetry"):
        data = (folder / manifest[part]["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == manifest[part]["sha256"] and len(data) == manifest[part]["bytes"]
    scratch = f"{stack.db}_check"
    stack.psql_admin(f'DROP DATABASE IF EXISTS "{scratch}"')
    stack.psql_admin(f'CREATE DATABASE "{scratch}"')
    try:
        dump = gzip.decompress((folder / "db.sql.gz").read_bytes())
        r = subprocess.run(["psql", "-h", stack.pg["host"], "-p", stack.pg["port"], "-U", stack.pg["user"], "-d", scratch,
                            "-q", "-v", "ON_ERROR_STOP=1"], input=dump, capture_output=True, env={**os.environ, "PGPASSWORD": stack.pg["password"]})
        assert r.returncode == 0, r.stderr[-500:]
        rows = stack.psql_admin("select telemetry_path, telemetry_hash from laps", db=scratch)
        laps = dict(line.split("|") for line in rows.splitlines()) if rows else {}
        # The manifest carries the head of the audit chain of exactly this dump
        head = stack.psql_admin("select id || ' ' || mac from audit_events order by id desc limit 1", db=scratch).strip()
        assert manifest["audit_head"] == ({"id": int(head.split()[0]), "mac": head.split()[1]} if head else None)
    finally:
        stack.psql_admin(f'DROP DATABASE IF EXISTS "{scratch}"')
    files = _backup_files(folder, manifest)
    by_rel = {p.split("/data/telemetry/", 1)[1]: h for p, h in laps.items()}
    assert set(by_rel) == set(files), f"rows without file: {set(by_rel) - set(files)}; files without row: {set(files) - set(by_rel)}"
    assert all(files[rel] == h for rel, h in by_rel.items()), "a file in the archive is not the one the row describes"
    assert manifest["lap_count"] == len(laps) and manifest["consistent"] is True
    return manifest


# ================================================================ backup

def test_uploads_during_a_backup_never_split_database_and_files(stack):
    for _ in range(3):
        stack.upload()
    stop = threading.Event()
    results = []

    def uploader():
        while not stop.is_set():
            results.append(stack.upload())

    thread = threading.Thread(target=uploader)
    thread.start()
    time.sleep(0.5)
    # a slow dump keeps the window open long enough for uploads to hit it
    run = stack.script("scripts/ops/backup.sh", str(stack.backups), extra_env={"FAKE_SLOW": "pg_dump=2"})
    time.sleep(0.5)
    stop.set()
    thread.join()
    assert run.returncode == 0, run.stdout + run.stderr
    [folder] = _published(stack)
    assert not folder.name.endswith(".partial")
    _check_backup(stack, folder)
    assert sum(seen for _id, seen in results) >= 1, "no upload ran into the backup window — the test proved nothing"
    assert len(stack.live_laps()) == 3 + len(results), "every upload got through in the end"
    assert not (stack.volume / ".garage16-maintenance").exists()


def test_an_interrupted_backup_publishes_nothing_and_releases_uploads(stack):
    stack.upload()
    proc = subprocess.Popen(["bash", "scripts/ops/backup.sh", str(stack.backups)], cwd=ROOT,
                            env={**stack.env, "FAKE_SLOW": "pg_dump=6"}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        if (stack.volume / ".garage16-maintenance").exists():
            break
        time.sleep(0.1)
    time.sleep(1.0)
    proc.send_signal(signal.SIGTERM)
    out = proc.communicate(timeout=60)[0].decode()
    assert proc.returncode != 0, out
    assert _published(stack) == [], "nothing that looks like a backup"
    assert not (stack.volume / ".garage16-maintenance").exists()
    assert stack.upload()[1] == 0, "uploads go through again at once"


def test_a_killed_backup_blocks_uploads_no_longer_than_its_lock_allows(stack):
    stack.upload()
    proc = subprocess.Popen(["bash", "scripts/ops/backup.sh", str(stack.backups)], cwd=ROOT,
                            env={**stack.env, "FAKE_SLOW": "pg_dump=30", "GARAGE16_BACKUP_LOCK_MAX_S": "3"},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(100):
        if (stack.volume / ".garage16-maintenance").exists():
            break
        time.sleep(0.1)
    os.killpg(proc.pid, signal.SIGKILL)       # no trap runs — the helper container is left behind
    proc.wait()
    start = time.time()
    _lap, seen = stack.upload(retries=100)
    assert time.time() - start < 15, "the lock gave up on its own"
    assert [p for p in _published(stack) if not p.name.endswith(".partial")] == []


@pytest.mark.parametrize("fault", ["pg_dump", "store_add"])
def test_a_failing_step_publishes_nothing(stack, fault):
    stack.upload()
    run = stack.script("scripts/ops/backup.sh", str(stack.backups), extra_env={"FAKE_FAIL": fault})
    assert run.returncode != 0
    assert _published(stack) == [] and not (stack.volume / ".garage16-maintenance").exists()
    assert stack.upload()[1] == 0


def test_the_backup_loop_container_makes_the_same_consistent_backup(stack):
    stack.upload()
    stack.upload()
    script = ROOT / "docker" / "backup-loop.sh"
    # what docker-compose.yml mounts at /garage16: the program version and the store functions
    garage16 = stack.backups.parent / "garage16-mount"
    garage16.mkdir()
    shutil.copy(ROOT / "server" / "main.py", garage16 / "main.py")
    shutil.copy(ROOT / "docker" / "telemetry-store.sh", garage16 / "telemetry-store.sh")
    ns = "\n".join([
        "set -e",
        f"mount --bind {shlex.quote(str(stack.volume))} /data/telemetry",
        "mkdir -p /garage16 /backups",
        f"mount --bind {shlex.quote(str(stack.backups))} /backups",
        f"mount --bind {shlex.quote(str(garage16))} /garage16",
        f"exec sh {shlex.quote(str(script))}",
    ])
    r = subprocess.run(SUDO + ["unshare", "-m", "--propagation", "private", "sh", "-c", ns], capture_output=True, text=True, timeout=120,
                       env={**stack.env, "BACKUP_ONCE": "1", "PGHOST": stack.pg["host"], "PGPORT": stack.pg["port"],
                            "PGUSER": stack.pg["user"], "PGDATABASE": stack.db})
    assert r.returncode == 0, r.stdout + r.stderr
    [folder] = _published(stack)
    manifest = _check_backup(stack, folder)
    assert manifest["program_version"] and manifest["lap_count"] == 2


def test_a_second_backup_copies_only_the_new_files_and_cleaning_up_keeps_what_is_listed(stack):
    """The telemetry store — every file once; a backup adds the new ones; cleaning up removes
    a file only when no remaining backup lists it."""
    for _ in range(2):
        stack.upload()
    assert stack.script("scripts/ops/backup.sh", str(stack.backups)).returncode == 0
    time.sleep(1.1)                          # a new stamp for the second folder
    stack.upload()
    assert stack.script("scripts/ops/backup.sh", str(stack.backups)).returncode == 0
    first, second = _published(stack)
    m1, m2 = (json.loads((f / "manifest.json").read_text()) for f in (first, second))
    assert m1["format"] == m2["format"] == 2
    assert m1["telemetry"]["new_files"] == m1["telemetry"]["files"] >= 2
    assert m2["telemetry"]["new_files"] == 1 and m2["telemetry"]["files"] == m1["telemetry"]["files"] + 1
    assert not (first / "telemetry.tar.gz").exists() and not (second / "telemetry.tar.gz").exists()
    _check_backup(stack, first)
    _check_backup(stack, second)
    # the first backup ages out: its files stay, the second lists them all
    shutil.rmtree(first)
    run = subprocess.run(["sh", "-c", f". {ROOT / 'docker' / 'telemetry-store.sh'} && store_prune {stack.backups / 'telemetry-store'} {stack.backups}"],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    _check_backup(stack, second)
    # a file no backup lists any more is removed
    stray = stack.backups / "telemetry-store" / "not-listed.parquet"
    stray.write_bytes(b"x")
    subprocess.run(["sh", "-c", f". {ROOT / 'docker' / 'telemetry-store.sh'} && store_prune {stack.backups / 'telemetry-store'} {stack.backups}"], check=True)
    assert not stray.exists()


# ================================================================ restore

@pytest.fixture
def two_states(stack):
    """S1 = 3 laps (backed up), S2 = 5 laps (live)."""
    for _ in range(3):
        stack.upload()
    assert stack.script("scripts/ops/backup.sh", str(stack.backups)).returncode == 0
    s1 = stack.snapshot()
    [folder] = _published(stack)
    for _ in range(2):
        stack.upload()
    s2 = stack.snapshot()
    return folder, s1, s2


def _stamp(out: str) -> str:
    for line in out.splitlines():
        if "--rollback " in line:
            return line.rsplit("--rollback ", 1)[1].strip()
    raise AssertionError(out)


def test_restore_rollback_and_cleanup(stack, two_states):
    folder, s1, s2 = two_states
    run = stack.script("scripts/ops/restore.sh", "--yes", str(folder))
    assert run.returncode == 0, run.stdout + run.stderr
    assert stack.snapshot() == s1 and stack.server_running()
    stamp = _stamp(run.stdout)
    assert f"{stack.db}_pre_restore_{stamp}" in stack.databases()
    assert (stack.volume / f".pre_restore_{stamp}").is_dir()
    # the restored live system is consistent and keeps working
    assert stack.upload()[1] == 0
    s1_plus = stack.snapshot()

    back = stack.script("scripts/ops/restore.sh", "--rollback", stamp)
    assert back.returncode == 0, back.stdout + back.stderr
    assert stack.snapshot() == s2 and stack.server_running()

    again = stack.script("scripts/ops/restore.sh", "--yes", str(folder))
    stamp2 = _stamp(again.stdout)
    assert stack.snapshot() == s1
    clean = stack.script("scripts/ops/restore.sh", "--cleanup", stamp2)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert f"{stack.db}_pre_restore_{stamp2}" not in stack.databases()
    assert not (stack.volume / f".pre_restore_{stamp2}").exists()
    assert s1_plus != s1


# Every step, failing: the live system must end exactly as before (S2), running, without leftovers.
FAULTS = {
    "stage_db": "CREATE DATABASE",
    "stage_files": "store_stage",
    "validate": "consistency_check --deep",
    "stop": "compose stop server",
    "swap_db": "RENAME TO",
    "swap_files": "cp -a /stage",
    "start": "compose up -d server",
    "verify": "exec -T server sh -c .*consistency_check",
}


@pytest.mark.parametrize("step", list(FAULTS))
def test_a_fault_in_any_step_leaves_the_state_before_the_restore(stack, two_states, step):
    folder, s1, s2 = two_states
    run = stack.script("scripts/ops/restore.sh", "--yes", str(folder), extra_env={"FAKE_FAIL": FAULTS[step]})
    assert run.returncode == 1, (step, run.returncode, run.stdout + run.stderr)
    assert stack.snapshot() == s2, f"{step}: the live state changed"
    assert stack.server_running(), f"{step}: the server is not running again"
    leftovers = {d for d in stack.databases() if d != stack.db and "_pre_restore_" not in d}
    assert not leftovers, f"{step}: staged databases left: {leftovers}"
    assert not [p for p in stack.volume.iterdir() if p.name.startswith(".pre_restore_") and any(p.iterdir())], step
    assert "INJECTED FAILURE" in (stack.state / "calls.log").read_text()


def test_a_failing_rollback_is_never_silent(stack, two_states):
    folder, _s1, _s2 = two_states
    # the file switch fails AND moving the old files back fails
    run = stack.script("scripts/ops/restore.sh", "--yes", str(folder), extra_env={"FAKE_FAIL": "cp -a /stage|-name '.pre_restore_[0-9_]*' -exec rm", "FAKE_FAIL_COUNT": "2"})
    assert run.returncode == 3, run.stdout + run.stderr
    assert "MANUAL ATTENTION NEEDED" in run.stderr and "pre_restore" in run.stderr


@pytest.mark.parametrize("damage", ["sha", "truncated", "unknown_schema", "traversal", "no_manifest", "store_damaged"])
def test_a_damaged_or_foreign_backup_is_refused_before_anything_is_touched(stack, two_states, damage, tmp_path):
    folder, _s1, s2 = two_states
    bad = _copy_backup(folder, tmp_path)
    manifest = json.loads((bad / "manifest.json").read_text())
    if damage == "sha":
        data = bytearray((bad / "telemetry-files.txt").read_bytes())
        data[0] = ord("9") if data[0] != ord("9") else ord("8")
        (bad / "telemetry-files.txt").write_bytes(bytes(data))
    elif damage == "truncated":
        dump = gzip.decompress((bad / "db.sql.gz").read_bytes())
        (bad / "db.sql.gz").write_bytes(gzip.compress(dump[: len(dump) // 2]))
        manifest["database"]["sha256"] = hashlib.sha256((bad / "db.sql.gz").read_bytes()).hexdigest()
        manifest["database"]["bytes"] = (bad / "db.sql.gz").stat().st_size
    elif damage == "unknown_schema":
        manifest["schema_revision"] = "9999"
    elif damage == "traversal":
        listing = (bad / "telemetry-files.txt").read_bytes() + b"4 ../../etc/evil\n"
        (bad / "telemetry-files.txt").write_bytes(listing)
        manifest["telemetry"]["sha256"] = hashlib.sha256(listing).hexdigest()
        manifest["telemetry"]["bytes"] = len(listing)
    elif damage == "store_damaged":
        # The list and the manifest agree, but a file in the store is not the one listed
        size, rel = (bad / "telemetry-files.txt").read_text().splitlines()[0].split(" ", 1)
        target = tmp_path / "telemetry-store" / rel
        target.write_bytes(target.read_bytes()[:-1])
    if damage == "no_manifest":
        (bad / "manifest.json").unlink()
    else:
        (bad / "manifest.json").write_text(json.dumps(manifest))
    run = stack.script("scripts/ops/restore.sh", "--yes", str(bad))
    assert run.returncode == 1, run.stdout + run.stderr
    assert stack.snapshot() == s2 and stack.databases() == {stack.db}
    assert "compose stop" not in (stack.state / "calls.log").read_text(), "refused before the live system was touched"


# ================================================================ V0.9.9.1: the audit anchor
# The manifest's audit_head is compared with the staged, fully validated database in step 4 —
# BEFORE anything live is switched. The attacker here rewrites the dump AND fixes the manifest's
# sha256 and size (so step 1 passes), but cannot know to fix the recorded head.

def _rewrite_audit_tail(dump: bytes, how: str) -> bytes:
    """Deletes or rewrites (with a valid MAC — an attacker holding the key) the newest audit row."""
    from server.ops import audit
    from server.laps import integrity

    lines = dump.decode().split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("COPY public.audit_events ("))
    columns = lines[start].split("(", 1)[1].split(")", 1)[0].split(", ")
    end = lines.index("\\.", start)
    rows = [(i, lines[i].split("\t")) for i in range(start + 1, end)]
    assert rows, "the backup has audit rows"
    col = {name: columns.index(name) for name in ("id", "kind", "actor", "lap_id", "request_id", "detail", "prev_mac", "mac")}
    index, fields = max(rows, key=lambda r: int(r[1][col["id"]]))
    if how == "deleted":
        del lines[index]
    else:
        def value(name):
            v = fields[col[name]]
            return None if v == "\\N" else v
        detail = json.loads(fields[col["detail"]].replace("\\\\", "\\"))
        detail["forged"] = True
        lap_id = int(value("lap_id")) if value("lap_id") is not None else None
        content = audit._canonical(value("kind"), value("actor"), lap_id, value("request_id"), detail)
        fields[col["detail"]] = json.dumps(detail).replace("\\", "\\\\")
        fields[col["mac"]] = integrity.sign(audit._message(value("prev_mac"), content))
        lines[index] = "\t".join(fields)
    return "\n".join(lines).encode()


def _tampered_copy(folder: Path, tmp_path: Path, *, dump_change=None, manifest_change=None) -> Path:
    bad = _copy_backup(folder, tmp_path)
    manifest = json.loads((bad / "manifest.json").read_text())
    if dump_change:
        dump = _rewrite_audit_tail(gzip.decompress((bad / "db.sql.gz").read_bytes()), dump_change)
        (bad / "db.sql.gz").write_bytes(gzip.compress(dump))
        manifest["database"]["sha256"] = hashlib.sha256((bad / "db.sql.gz").read_bytes()).hexdigest()
        manifest["database"]["bytes"] = (bad / "db.sql.gz").stat().st_size
    if manifest_change:
        manifest_change(manifest)
    (bad / "manifest.json").write_text(json.dumps(manifest))
    return bad


@pytest.mark.parametrize("how", ["deleted", "rewritten"])
def test_a_backup_whose_last_audit_row_was_removed_or_rewritten_is_refused_before_the_switch(stack, two_states, how, tmp_path):
    folder, _s1, s2 = two_states
    bad = _tampered_copy(folder, tmp_path, dump_change=how)
    run = stack.script("scripts/ops/restore.sh", "--yes", str(bad))
    out = run.stdout + run.stderr
    assert run.returncode == 1, out
    assert "audit head: DIFFERS from the backup" in out, out
    assert ("rows at the end are missing" in out) if how == "deleted" else ("differs from the backup" in out)
    assert "did not pass validation" in out
    assert stack.snapshot() == s2 and stack.server_running()
    assert stack.databases() == {stack.db}, "the staged database was removed"
    assert "compose stop" not in (stack.state / "calls.log").read_text(), "refused before the live system was touched"


def test_the_chain_check_alone_would_have_passed_these_backups(stack, two_states, tmp_path):
    """Proof that the head comparison is what refuses them: the same tampered dumps restore when
    the manifest's head is fixed too (or absent, as in a V0.9.8 backup) — so the recorded head must
    be kept somewhere the attacker cannot reach (docs/RUNBOOKS.md)."""
    folder, s1, _s2 = two_states
    bad = _tampered_copy(folder, tmp_path, dump_change="deleted", manifest_change=lambda m: m.pop("audit_head"))
    run = stack.script("scripts/ops/restore.sh", "--yes", str(bad))
    assert run.returncode == 0, run.stdout + run.stderr
    assert "no audit_head (backup from before V0.9.9)" in run.stdout
    assert stack.snapshot() == s1


def test_a_backup_without_audit_head_restores_as_documented(stack, two_states, tmp_path):
    """Compatibility: V0.9.8 manifests have no audit_head — restored as before, head not compared."""
    folder, s1, _s2 = two_states
    bad = _tampered_copy(folder, tmp_path, manifest_change=lambda m: m.pop("audit_head"))
    run = stack.script("scripts/ops/restore.sh", "--yes", str(bad))
    assert run.returncode == 0, run.stdout + run.stderr
    assert "not compared" in run.stdout and stack.snapshot() == s1


@pytest.mark.parametrize("head, refused_at", [(None, "validation"), ("x", "manifest"), ({"id": 1, "mac": "$(touch /tmp/pwn)"}, "manifest")])
def test_a_null_or_unreadable_audit_head_is_never_read_as_no_anchor(stack, two_states, tmp_path, head, refused_at):
    folder, _s1, s2 = two_states
    bad = _tampered_copy(folder, tmp_path, manifest_change=lambda m: m.update(audit_head=head))
    run = stack.script("scripts/ops/restore.sh", "--yes", str(bad))
    out = run.stdout + run.stderr
    assert run.returncode == 1, out
    if refused_at == "manifest":
        assert "audit_head cannot be read" in out and stack.databases() == {stack.db}
        assert not Path("/tmp/pwn").exists()
    else:
        assert "the backup recorded no audit row, the database has some" in out
    assert stack.snapshot() == s2
    assert "compose stop" not in (stack.state / "calls.log").read_text()
