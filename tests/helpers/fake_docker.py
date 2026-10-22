#!/usr/bin/env python3
"""
A `docker` command for the backup / restore tests (V0.9.8): runs what the scripts ask for for
real, on this machine, instead of in containers.

  * `alpine` containers run in a real Alpine root file system (chroot) with the requested volumes
    bind-mounted in a private mount namespace (unshare -m) — busybox sh, flock, tar, cp exactly as
    on the NAS.
  * the `db` service is a real PostgreSQL (FAKE_PG_*): `docker compose exec -T db psql|pg_dump|
    pg_isready ...` run the local client tools against it.
  * the `server` service is the real app: `compose up -d server` starts uvicorn (after `alembic
    upgrade head`, like the entrypoint) with the telemetry volume bind-mounted at /data/telemetry;
    `compose exec/run ... server` run the given command with the project's Python in the same kind
    of namespace; `compose stop server` stops it.
  * named volumes are folders under FAKE_DOCKER_STATE/volumes.

Fault injection: FAKE_FAIL=<regex> makes the first FAKE_FAIL_COUNT (default 1) calls whose command
line matches fail with exit code 1 before doing anything; FAKE_SLOW=<regex>=<seconds> delays them.
Every call is appended to FAKE_DOCKER_STATE/calls.log.

Needs root (or passwordless sudo) for the namespaces and chroot.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

STATE = Path(os.environ["FAKE_DOCKER_STATE"])
ALPINE = os.environ.get("FAKE_ALPINE_ROOT", "")
REPO = os.environ["FAKE_REPO"]
PY = os.environ.get("FAKE_PYTHON", sys.executable)
PG_HOST = os.environ["FAKE_PG_HOST"]
PG_PORT = os.environ["FAKE_PG_PORT"]
PG_PASSWORD = os.environ.get("FAKE_PG_PASSWORD", "")
SERVER_PORT = os.environ.get("FAKE_SERVER_PORT", "18000")
LIVE_VOLUME = os.environ.get("FAKE_LIVE_VOLUME", "garage16_telemetry_data")
SUDO = [] if os.geteuid() == 0 else ["sudo", "-n", "-E"]

VOLUMES = STATE / "volumes"
CONTAINERS = STATE / "containers"


def log(text: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "calls.log", "a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def inject(argv) -> None:
    line = " ".join(argv)
    pattern = os.environ.get("FAKE_FAIL")
    if pattern and re.search(pattern, line):
        counter = STATE / "fail.count"
        n = int(counter.read_text()) if counter.exists() else 0
        if n < int(os.environ.get("FAKE_FAIL_COUNT", "1")):
            counter.write_text(str(n + 1))
            log(f"INJECTED FAILURE: {line}")
            sys.exit(1)
    slow = os.environ.get("FAKE_SLOW")
    if slow:
        rx, _, seconds = slow.rpartition("=")
        if re.search(rx, line):
            time.sleep(float(seconds))


def volume_dir(name: str) -> Path:
    path = VOLUMES / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_mounts(args):
    """-v SRC:DST[:ro] / -e K=V / --name N / -d / --rm / -T / --no-deps / --entrypoint E; returns
    (mounts, env, opts, rest)."""
    mounts, env, opts, rest = [], {}, {}, []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-v":
            spec = args[i + 1].split(":")
            src, dst, ro = spec[0], spec[1], len(spec) > 2 and spec[2] == "ro"
            mounts.append((src if "/" in src else str(volume_dir(src)), dst, ro))
            i += 2
        elif a == "-e":
            k, _, v = args[i + 1].partition("=")
            env[k] = v
            i += 2
        elif a in ("--name", "--entrypoint", "-f", "--format"):
            opts[a] = args[i + 1]
            i += 2
        elif a in ("-d", "--rm", "-T", "--no-deps", "-i", "-t"):
            opts[a] = True
            i += 1
        else:
            rest = args[i:]
            break
    return mounts, env, opts, rest


def ns_script(mounts, root: str, command, chroot: bool) -> str:
    lines = ["set -e"]
    for src, dst, ro in mounts:
        target = (root.rstrip("/") + dst) if chroot else dst
        lines.append(f"mkdir -p {shlex.quote(target)}")
        lines.append(f"mount --bind {shlex.quote(src)} {shlex.quote(target)}")
        if ro:
            lines.append(f"mount -o remount,ro,bind {shlex.quote(target)}")
    if chroot:
        lines.append("cd /")
        lines.append("exec chroot " + shlex.quote(root) + " " + " ".join(shlex.quote(c) for c in command))
    else:
        lines.append(f"cd {shlex.quote(REPO)}")
        lines.append("exec " + " ".join(shlex.quote(c) for c in command))
    return "\n".join(lines)


def shim_dir() -> Path:
    """`python` inside the commands the scripts pass to the server container = the project's Python."""
    d = STATE / "bin"
    shim = d / "python"
    if not shim.exists():
        d.mkdir(parents=True, exist_ok=True)
        shim.write_text(f"#!/bin/sh\nexec {shlex.quote(PY)} \"$@\"\n")
        shim.chmod(0o755)
    return d


def server_env(extra=None) -> dict:
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("FAKE_FAIL") or k == "FAKE_SLOW":
            env.pop(k)
    env.update({
        "POSTGRES_HOST": PG_HOST if not PG_HOST.startswith("/") else "127.0.0.1",
        "POSTGRES_PORT": PG_PORT, "POSTGRES_USER": os.environ.get("POSTGRES_USER", "garage16"),
        "POSTGRES_PASSWORD": PG_PASSWORD or "x", "POSTGRES_DB": os.environ.get("POSTGRES_DB", "garage16"),
        "LMU_GARAGE_STORAGE_DIR": "/data/telemetry", "LMU_GARAGE_ENV": "development",
        "PATH": f"{shim_dir()}:{Path(PY).parent}:{env.get('PATH', '/usr/bin:/bin')}",
    })
    env["LMU_GARAGE_DB_URL"] = (f"postgresql+psycopg://{env['POSTGRES_USER']}:{env['POSTGRES_PASSWORD']}"
                                f"@{env['POSTGRES_HOST']}:{env['POSTGRES_PORT']}/{env['POSTGRES_DB']}")
    env.update(extra or {})
    return env


def run_ns(mounts, command, *, chroot: bool, detach_name=None, env=None, stdin=None) -> int:
    root = ALPINE if chroot else "/"
    script = ns_script(mounts, root, command, chroot)
    argv = SUDO + ["unshare", "-m", "--propagation", "private", "sh", "-c", script]
    if detach_name:
        CONTAINERS.mkdir(parents=True, exist_ok=True)
        out = open(CONTAINERS / f"{detach_name}.log", "ab")
        rc_file = CONTAINERS / f"{detach_name}.rc"
        rc_file.unlink(missing_ok=True)
        wrapped = SUDO + ["sh", "-c", 'unshare -m --propagation private sh -c "$1"; echo $? > "$2"', "sh", script, str(rc_file)]
        proc = subprocess.Popen(wrapped, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
        (CONTAINERS / f"{detach_name}.pid").write_text(str(proc.pid))
        print(detach_name)
        return 0
    return subprocess.run(argv, env=env, stdin=stdin).returncode


def ns_pid(name: str):
    """The pid of the process inside the container's mount namespace."""
    pid_file = CONTAINERS / f"{name}.pid"
    if not pid_file.exists():
        return None
    outer = int(pid_file.read_text())
    # the wrapper sh -> unshare (exec'd into sh in the new namespace)
    children = subprocess.run(["pgrep", "-P", str(outer)], capture_output=True, text=True).stdout.split()
    return int(children[0]) if children else None


def alive(name: str) -> bool:
    pid_file = CONTAINERS / f"{name}.pid"
    if not pid_file.exists():
        return False
    try:
        state = Path(f"/proc/{int(pid_file.read_text())}/stat").read_text().split()[2]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def kill(name: str) -> None:
    pid_file = CONTAINERS / f"{name}.pid"
    if pid_file.exists():
        pid = int(pid_file.read_text())
        try:
            pgid = os.getpgid(pid)
            if SUDO:
                subprocess.run(SUDO + ["kill", "-9", "--", f"-{pgid}"], capture_output=True)
            else:
                os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for _ in range(50):
            if not alive(name):
                break
            time.sleep(0.05)
        pid_file.unlink(missing_ok=True)


def pg(tool: str, args) -> int:
    out = [tool]
    if tool in ("psql", "pg_dump", "pg_isready"):
        out += ["-h", PG_HOST, "-p", PG_PORT]
    out += args
    env = dict(os.environ, PGPASSWORD=PG_PASSWORD) if PG_PASSWORD else None
    return subprocess.run(out, env=env).returncode


# ---------------------------------------------------------------- the server service

def server_up() -> int:
    if alive("server"):
        return 0
    if subprocess.run(SUDO + ["unshare", "-m", "--propagation", "private", "sh", "-c",
                              ns_script([(str(volume_dir(LIVE_VOLUME)), "/data/telemetry", False)], "/", [PY, "-m", "alembic", "upgrade", "head"], False)],
                      env=server_env(), capture_output=True).returncode != 0:
        return 1
    run_ns([(str(volume_dir(LIVE_VOLUME)), "/data/telemetry", False)],
           [PY, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", SERVER_PORT, "--log-level", "warning"],
           chroot=False, detach_name="server", env=server_env())
    import urllib.request
    for _ in range(200):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{SERVER_PORT}/health", timeout=1)
            return 0
        except Exception:
            time.sleep(0.05)
    return 1


def compose(args) -> int:
    i = 0
    while i < len(args) and args[i].startswith("-"):     # compose-level flags (--profile x)
        i += 2 if args[i] in ("--profile", "-f", "-p") else 1
    sub, rest = args[i], args[i + 1:]
    services_file = STATE / "services"
    running = set(services_file.read_text().split()) if services_file.exists() else set()

    if sub in ("stop", "down"):
        for name in [r for r in rest if not r.startswith("-")] or ["server", "discord_bot"]:
            if name == "server":
                kill("server")
            running.discard(name)
        services_file.write_text(" ".join(sorted(running)))
        return 0
    if sub in ("up", "start"):
        names = [r for r in rest if not r.startswith("-")]
        for name in names:
            if name == "server" and server_up() != 0:
                return 1
            running.add(name)
        services_file.write_text(" ".join(sorted(running)))
        return 0
    if sub == "exec":
        mounts, env, opts, rest2 = parse_mounts(rest)
        service, command = rest2[0], rest2[1:]
        if service == "db":
            return pg(command[0], command[1:])
        if service == "server":
            if not alive("server"):
                print("service \"server\" is not running", file=sys.stderr)
                return 1
            command = [c.replace("localhost:8000", f"127.0.0.1:{SERVER_PORT}") for c in command]
            command = [PY if c == "python" else c for c in command]
            return run_ns([(str(volume_dir(LIVE_VOLUME)), "/data/telemetry", False)], command, chroot=False, env=server_env())
        return 0
    if sub == "run":
        mounts, env, opts, rest2 = parse_mounts(rest)
        service, command = rest2[0], rest2[1:]
        if opts.get("--entrypoint"):
            command = [opts["--entrypoint"]] + command
        command = [PY if c == "python" else c for c in command]
        all_mounts = [(str(volume_dir(LIVE_VOLUME)), "/data/telemetry", False)] + mounts
        return run_ns(all_mounts, command, chroot=False, env=server_env(env))
    if sub == "ps":
        print("\n".join(sorted(running)))
        return 0
    return 0


def main(argv) -> int:
    log("docker " + " ".join(argv))
    inject(argv)
    cmd, rest = argv[0], argv[1:]
    if cmd == "volume":
        name = rest[1] if len(rest) > 1 else ""
        if rest[0] == "inspect":
            return 0 if (VOLUMES / name).is_dir() else 1
        if rest[0] == "create":
            volume_dir(name)
            print(name)
            return 0
        if rest[0] == "rm":
            if not (VOLUMES / name).is_dir():
                return 1
            subprocess.run(SUDO + ["rm", "-rf", str(VOLUMES / name)])
            return 0
        if rest[0] == "ls":
            print("\n".join(sorted(p.name for p in VOLUMES.iterdir())) if VOLUMES.exists() else "")
            return 0
    if cmd == "compose":
        return compose(rest)
    if cmd == "run":
        mounts, env, opts, rest2 = parse_mounts(rest)
        image, command = rest2[0], rest2[1:]
        assert image == "alpine", image
        return run_ns(mounts, command, chroot=True, detach_name=opts.get("--name") if opts.get("-d") else None)
    if cmd == "exec":
        name, command = rest[0], rest[1:]
        pid = ns_pid(name)
        if pid is None or not alive(name):
            return 1
        return subprocess.run(SUDO + ["nsenter", "-t", str(pid), "-m", "chroot", ALPINE] + command).returncode
    if cmd == "inspect":
        name = rest[-1]
        print("true" if alive(name) else "false")
        return 0 if (CONTAINERS / f"{name}.pid").exists() or alive(name) else 1
    if cmd == "wait":
        name = rest[0]
        while alive(name):
            time.sleep(0.05)
        rc = CONTAINERS / f"{name}.rc"
        print(rc.read_text().strip() if rc.exists() else "137")
        return 0
    if cmd == "rm":
        for name in [r for r in rest if not r.startswith("-")]:
            kill(name)
        return 0
    print(f"fake docker: unsupported: {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
