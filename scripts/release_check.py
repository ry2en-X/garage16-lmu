"""
scripts/release_check.py — the release gate (V0.9.7).

One command that decides whether a state of the code may be released, and leaves a report of why:

    python -m scripts.release_check                       check + report (release/TEST_REPORT.md)
    python -m scripts.release_check --zip OUT.zip         ... and only when everything passed: the
                                                          release archive, with the report inside

What it does, in this order — and why the order matters:

  1. Environment (infrastructure). Every Python package, Node + jsdom, tkinter, bash/gzip,
     xvfb-run and caddy the suite drives. Anything missing stops here with exit code 6 and a
     list of what to install. A missing pyarrow or jsdom is an INCOMPLETE ENVIRONMENT, not a test
     result: before V0.9.7 such tests skipped themselves and a run could look green while half the
     checks never happened.
  2. Versions. The installed versions are compared with constraints.txt (what the release is
     tested and shipped with — the Docker images install exactly these). A difference is reported
     and stops the gate unless --allow-unpinned is given (then the report says so).
  3. The whole suite with GARAGE16_STRICT_TESTS=1 (tests/conftest.py): a skipped test counts as a
     failure — except documented opt-in checks and what --allow-skip names explicitly (listed in
     the report).
  4. V0.9.8 — the quality steps: the linter (ruff.toml), static security analysis (bandit, medium
     and high), known vulnerabilities in the pinned versions (pip-audit — needs network; not
     reachable = infrastructure, exit 6), line AND branch coverage against the floors in
     tests/coverage_floors.json (the suite runs under coverage).
  5. The report: release/TEST_REPORT.md (readable), release/test-results.xml (JUnit, for CI),
     release/environment.json (versions, platform, a hash over the released files),
     release/coverage.json.

V0.9.8 — with --zip, everything above runs in a STAGED copy of exactly the files that go into the
archive (release/stage/<name>/; only node_modules and the test downloads are linked in), and the
archive is built from that same copy: the tested tree IS the published tree (its hash is in the
report). Nothing is rebuilt after the run.

V0.9.9 — `--verify-archive OUT.zip` checks the archive against its .sha256, extracts it into an
empty folder (refusing any path that would leave it), confirms the extracted tree has the code hash
its own TEST_REPORT.md records, and runs the whole gate again there with the archive's own scripts:

    python -m scripts.release_check --verify-archive OUT.zip --report-dir release-verify

Exit codes: 0 everything passed · 1 a test or a quality step failed · 6 environment incomplete or
versions differ.

Set up an environment that passes step 1 with scripts/setup_test_env.sh.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.test_requirements import missing_test_dependencies  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_INFRASTRUCTURE = 0, 1, 6

# Never in a release archive (keep in sync with .gitignore / package_release.bat).
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git", ".venv", ".venv-build", "venv", "env", "node_modules",
                "build", "dist", "dist-installer", "server_telemetry_storage", "lmu_garage_data", ".lmu_garage",
                "test-backups", ".vscode", ".idea", "__MACOSX", "secrets", "release", "release-archive", "release-verify", ".cache", ".hypothesis"}
EXCLUDE_FILES = ("*.db", "*.db-journal", "*.sqlite", "*.sqlite3", "*.pyc", "*.pyo", ".env", "*.pem", "*.key",
                 "client_config.json", "*.log", "test_*.csv", "test_*_log.txt", "Tests_Garage16.txt",
                 "lmu_dump_*.csv", "smoketest_*.py", "*.sql.gz", "*.tar.gz", "*.zip", ".DS_Store", "Thumbs.db")


def server_version() -> str:
    match = re.search(r'^VERSION = "([^"]+)"', (ROOT / "server" / "main.py").read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else "?"


def read_constraints(path: Path = ROOT / "constraints.txt") -> dict:
    pins = {}
    if not path.exists():
        return pins
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[_norm(name)] = version.strip()
    return pins


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_versions() -> dict:
    from importlib import metadata

    out = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            out[_norm(name)] = dist.version
    return out


def version_differences(pins: dict, installed: dict) -> list:
    """[(package, pinned, installed or None)] for every pin this environment does not match.
    Packages pinned for another platform or Python (not installed here at all) are not counted
    unless they are direct requirements of the suite."""
    from scripts.test_requirements import PYTHON_PACKAGES

    direct = {_norm(p) for _, p in PYTHON_PACKAGES}
    diffs = []
    for name, version in sorted(pins.items()):
        have = installed.get(name)
        if have is None and name not in direct:
            continue
        if have != version:
            diffs.append((name, version, have))
    return diffs


def released_files(root: Path = ROOT):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.is_dir() or any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDE_FILES):
            continue
        yield rel


def tree_hash(root: Path = ROOT) -> str:
    """sha256 over (path, content) of every file that goes into a release — two archives with the
    same hash contain the same code."""
    digest = hashlib.sha256()
    for rel in released_files(root):
        digest.update(rel.as_posix().encode() + b"\0")
        digest.update(hashlib.sha256((root / rel).read_bytes()).digest())
    return digest.hexdigest()


def parse_junit(path: Path) -> dict:
    tree = ET.parse(path)  # nosec B314 — the JUnit file pytest just wrote in this run
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    failed, skipped = [], []
    for case in tree.iter("testcase"):
        counts["tests"] += 1
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        for child in case:
            if child.tag in ("failure", "error"):
                counts["failures" if child.tag == "failure" else "errors"] += 1
                failed.append((name, (child.get("message") or "").splitlines()[0][:300] if child.get("message") else ""))
            elif child.tag == "skipped":
                counts["skipped"] += 1
                skipped.append((name, (child.get("message") or "")[:300]))
    for suite in tree.iter("testsuite"):
        counts["time"] += float(suite.get("time") or 0)
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    return {"counts": counts, "failed": failed, "skipped": skipped}


def node_version() -> str:
    node = shutil.which("node")
    if not node:
        return "missing"
    return subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()


def jsdom_version() -> str:
    pkg = ROOT / "tests" / "js" / "node_modules" / "jsdom" / "package.json"
    return json.loads(pkg.read_text(encoding="utf-8")).get("version", "?") if pkg.exists() else "missing"


def write_report(report_dir: Path, *, verdict: str, reason: str, env: dict, result: dict | None,
                 missing: list, diffs: list, allowed: list, duration: float,
                 quality: list | None = None, coverage: tuple | None = None) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "environment.json").write_text(json.dumps(env, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        f"# Garage16 {env['version']} — release test report",
        "",
        f"**Verdict: {verdict}** — {reason}",
        "",
        f"- Date (UTC): {env['date']}",
        f"- Code hash (sha256 over all released files): `{env['tree_hash']}`",
        f"- Python {env['python']} on {env['platform']} · Node {env['node']} · jsdom {env['jsdom']}",
        f"- Strict mode: a skipped test counts as a failure (allowed: {', '.join(allowed) or 'only opt-in checks'})",
        f"- Duration: {duration:.0f} s",
        "",
    ]
    if missing:
        lines += ["## Environment incomplete (nothing was tested)", ""] + [f"- {m}" for m in missing] + [
            "", "Set it up with `scripts/setup_test_env.sh`.", ""]
    if diffs:
        lines += ["## Versions differing from constraints.txt", "", "| Package | Pinned | Installed |", "|---|---|---|"]
        lines += [f"| {n} | {p} | {h or 'not installed'} |" for n, p, h in diffs] + [""]
    if result:
        c = result["counts"]
        lines += ["## Tests", "", "| Passed | Failed | Errors | Skipped (allowed) | Total |", "|---|---|---|---|---|",
                  f"| {c['passed']} | {c['failures']} | {c['errors']} | {c['skipped']} | {c['tests']} |", ""]
        if result["failed"]:
            lines += ["### Failed", ""] + [f"- `{n}` — {m}" for n, m in result["failed"][:200]] + [""]
        if result["skipped"]:
            lines += ["### Skipped (allowed)", ""] + [f"- `{n}` — {m}" for n, m in result["skipped"]] + [""]
    if quality:
        lines += ["## Quality steps", "", "| Step | Result |", "|---|---|"]
        lines += [f"| {q['name']} | {'passed' if q['ok'] else ('NOT RUN (infrastructure)' if q['infra'] else 'FAILED')} |" for q in quality] + [""]
        for q in quality:
            if not q["ok"]:
                lines += [f"### {q['name']}", "", "```", q["detail"], "```", ""]
    if coverage:
        rows, short = coverage
        lines += ["## Coverage (lines / branches, floor in brackets)", "", "| Module | Lines | Branches |", "|---|---|---|"]
        lines += [f"| {n} | {l} ({fl}) | {b} ({fb}) |" for n, l, b, fl, fb in rows] + [""]
        if short:
            lines += ["### Below the floor", ""] + [f"- {x}" for x in short] + [""]
    lines += ["## Installed versions (direct dependencies)", "", "| Package | Version |", "|---|---|"]
    lines += [f"| {n} | {v} |" for n, v in sorted(env["packages"].items())] + [""]
    path = report_dir / "TEST_REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def stage_tree(dst: Path) -> Path:
    """V0.9.8: a copy of exactly the released files — the tree that is tested and then zipped."""
    if dst.exists():
        shutil.rmtree(dst)
    for rel in released_files():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    # what the tests need but the archive never contains: installed packages, downloads
    for rel in ("tests/js/node_modules", "tests/.cache"):
        if (ROOT / rel).exists():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            (dst / rel).symlink_to(ROOT / rel, target_is_directory=True)
    return dst


def _run(command, cwd: Path, env=None, timeout=None) -> subprocess.CompletedProcess:
    print("$", " ".join(str(c) for c in command), flush=True)
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def quality_steps(workdir: Path, report_dir: Path) -> list:
    """V0.9.8: lint, static security analysis, known vulnerabilities. -> [{name, ok, infra, detail}]"""
    steps = []
    r = _run([sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format", "concise", "."], workdir)
    steps.append({"name": "Linter (ruff, ruff.toml)", "ok": r.returncode == 0, "infra": False,
                  "detail": "no findings" if r.returncode == 0 else (r.stdout + r.stderr).strip()[-3000:]})
    r = _run([sys.executable, "-m", "bandit", "-q", "-r", "server", "discord_bot", "client", "scripts", "-ll", "-f", "txt"], workdir)
    steps.append({"name": "Static security analysis (bandit, medium + high)", "ok": r.returncode == 0, "infra": False,
                  "detail": "no findings" if r.returncode == 0 else (r.stdout + r.stderr).strip()[-3000:]})
    pins = report_dir / "pins.txt"
    pins.write_text("\n".join(f"{n}=={v}" for n, v in sorted(read_constraints(workdir / "constraints.txt").items())) + "\n", encoding="utf-8")
    r = _run([sys.executable, "-m", "pip_audit", "-r", str(pins), "--no-deps", "--disable-pip", "--progress-spinner", "off"], workdir, timeout=600)
    out = (r.stdout + r.stderr)
    if "No known vulnerabilities found" in out:
        steps.append({"name": "Known vulnerabilities (pip-audit, every pinned package)", "ok": True, "infra": False, "detail": "none"})
    elif "Found" in out and "vulnerabilit" in out:
        steps.append({"name": "Known vulnerabilities (pip-audit, every pinned package)", "ok": False, "infra": False, "detail": out.strip()[-3000:]})
    else:
        steps.append({"name": "Known vulnerabilities (pip-audit, every pinned package)", "ok": False, "infra": True,
                      "detail": "the vulnerability database could not be reached: " + out.strip()[-800:]})
    return steps


def coverage_shortfalls(cov_json: Path, floors_file: Path) -> tuple:
    """-> (summary rows, shortfalls) — line and branch coverage per critical module against its floor."""
    data = json.loads(cov_json.read_text(encoding="utf-8"))
    floors = json.loads(floors_file.read_text(encoding="utf-8"))

    def pct(summary):
        lines = 100.0 * summary["covered_lines"] / max(summary["num_statements"], 1)
        branches = 100.0 * summary["covered_branches"] / summary["num_branches"] if summary.get("num_branches") else 100.0
        return round(lines, 1), round(branches, 1)

    rows, short = [], []
    total = pct(data["totals"])
    rows.append(("TOTAL", *total, floors["total"]["lines"], floors["total"]["branches"]))
    if total[0] < floors["total"]["lines"] or total[1] < floors["total"]["branches"]:
        short.append(f"TOTAL {total} < {floors['total']}")
    for name, floor in sorted(floors["files"].items()):
        summary = data["files"].get(name, {}).get("summary")
        if summary is None:
            short.append(f"{name}: not measured")
            continue
        got = pct(summary)
        rows.append((name, *got, floor["lines"], floor["branches"]))
        if got[0] < floor["lines"] or got[1] < floor["branches"]:
            short.append(f"{name}: lines {got[0]} (floor {floor['lines']}), branches {got[1]} (floor {floor['branches']})")
    return rows, short


def build_zip(out: Path, report_dir: Path, prefix: str, tree: Path = ROOT) -> int:
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in released_files(tree):
            archive.write(tree / rel, f"{prefix}/{rel.as_posix()}")
            count += 1
        for name in ("TEST_REPORT.md", "environment.json"):
            if (report_dir / name).exists():
                archive.write(report_dir / name, f"{prefix}/release/{name}")
                count += 1
    return count


def verify_archive(archive: Path, report_dir: Path, extra_args) -> int:
    """V0.9.9: the gate once more, on a clean copy extracted from the release archive itself.

    1. the archive matches its published sha256 (`<zip>.sha256`, when present);
    2. every member stays inside the extraction folder (no absolute paths, no "..");
    3. the extracted tree has exactly the code hash the archive's own TEST_REPORT.md records;
    4. the full gate runs in that copy, with ITS scripts (`python -m scripts.release_check`).
    Exit code: the gate's; 1 when 1–3 fail."""
    import re
    import tempfile

    archive = archive.resolve()
    sidecar = archive.with_name(archive.name + ".sha256")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if sidecar.exists() and sidecar.read_text(encoding="utf-8").split()[0] != digest:
        print(f"FAILED: {archive.name} does not match {sidecar.name}", file=sys.stderr)
        return EXIT_FAILED
    with tempfile.TemporaryDirectory(prefix="garage16-archive-") as tmp:
        dest = Path(tmp) / "x"
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                target = (dest / member).resolve()
                if member.startswith("/") or ".." in Path(member).parts or dest.resolve() not in target.parents:
                    print(f"FAILED: unsafe path in the archive: {member}", file=sys.stderr)
                    return EXIT_FAILED
            zf.extractall(dest)
        roots = [d for d in dest.iterdir() if d.is_dir()]
        if len(roots) != 1:
            print("FAILED: the archive must hold exactly one top folder", file=sys.stderr)
            return EXIT_FAILED
        tree = roots[0]
        report = (tree / "release" / "TEST_REPORT.md").read_text(encoding="utf-8") if (tree / "release" / "TEST_REPORT.md").exists() else ""
        match = re.search(r"Code hash \(sha256 over all released files\): `([0-9a-f]{64})`", report)
        actual = tree_hash(tree)
        if not match or match.group(1) != actual:
            print(f"FAILED: the extracted tree hash {actual} is not the tested one ({match.group(1) if match else 'none in the report'})",
                  file=sys.stderr)
            return EXIT_FAILED
        print(f"Archive {archive.name}: sha256 {digest}, extracted tree hash {actual} = the tested tree", flush=True)
        for rel in ("tests/js/node_modules", "tests/.cache"):
            if (ROOT / rel).exists() and not (tree / rel).exists():
                (tree / rel).parent.mkdir(parents=True, exist_ok=True)
                (tree / rel).symlink_to(ROOT / rel, target_is_directory=True)
        report_dir.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "scripts.release_check", "--report-dir", str(report_dir.resolve()), *extra_args]
        print("$ (in the extracted archive)", " ".join(command), flush=True)
        return subprocess.run(command, cwd=tree, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1")).returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report-dir", default=str(ROOT / "release"))
    parser.add_argument("--allow-skip", action="append", default=[], metavar="TEXT",
                        help="a skip whose reason contains TEXT does not fail the gate (repeatable; listed in the report)")
    parser.add_argument("--allow-unpinned", action="store_true", help="versions differing from constraints.txt only warn")
    parser.add_argument("--zip", metavar="OUT.zip", help="build the release archive when everything passed")
    parser.add_argument("--zip-prefix", default=None, help="folder name inside the archive (default: the zip's name)")
    parser.add_argument("--verify-archive", metavar="RELEASE.zip",
                        help="V0.9.9: run the gate again on a clean copy extracted from this archive")
    parser.add_argument("pytest_args", nargs="*", help="extra pytest arguments (e.g. -x)")
    args = parser.parse_args(argv)
    if args.verify_archive:
        return verify_archive(Path(args.verify_archive), Path(args.report_dir), args.pytest_args)

    started = time.time()
    report_dir = Path(args.report_dir)
    pins = read_constraints()
    installed = installed_versions()
    from scripts.test_requirements import PYTHON_PACKAGES

    env = {
        "version": server_version(),
        "date": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "node": node_version(),
        "jsdom": jsdom_version(),
        "tree_hash": tree_hash(),
        "packages": {p: installed.get(_norm(p), "missing") for _, p in PYTHON_PACKAGES},
    }
    allowed = list(args.allow_skip)

    missing = missing_test_dependencies(include_tools=True)
    if missing:
        path = write_report(report_dir, verdict="NOT TESTED", reason="the test environment is incomplete",
                            env=env, result=None, missing=missing, diffs=[], allowed=allowed,
                            duration=time.time() - started)
        print("INFRASTRUCTURE: the test environment is incomplete — nothing was tested:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        print(f"Set it up with scripts/setup_test_env.sh. Report: {path}", file=sys.stderr)
        return EXIT_INFRASTRUCTURE

    diffs = version_differences(pins, installed)
    if diffs and not args.allow_unpinned:
        path = write_report(report_dir, verdict="NOT TESTED", reason="installed versions differ from constraints.txt",
                            env=env, result=None, missing=[], diffs=diffs, allowed=allowed,
                            duration=time.time() - started)
        print(f"INFRASTRUCTURE: {len(diffs)} installed version(s) differ from constraints.txt "
              f"(reinstall with `-c constraints.txt`, or pass --allow-unpinned). Report: {path}", file=sys.stderr)
        return EXIT_INFRASTRUCTURE

    report_dir.mkdir(parents=True, exist_ok=True)
    report_dir = report_dir.resolve()
    workdir = ROOT
    prefix = None
    if args.zip:
        prefix = args.zip_prefix or Path(args.zip).stem
        workdir = stage_tree(report_dir / "stage" / prefix)
        env["tree_hash"] = tree_hash(workdir)
        env["staged"] = True
        print(f"Staged the release tree: {workdir}", flush=True)

    quality = quality_steps(workdir, report_dir)
    if any(q["infra"] for q in quality):
        write_report(report_dir, verdict="NOT TESTED", reason="a quality step could not run (infrastructure)", env=env,
                     result=None, missing=[], diffs=diffs, allowed=allowed, duration=time.time() - started, quality=quality)
        print("INFRASTRUCTURE: " + "; ".join(q["detail"][:200] for q in quality if q["infra"]), file=sys.stderr)
        return EXIT_INFRASTRUCTURE

    junit = report_dir / "test-results.xml"
    run_env = dict(os.environ, GARAGE16_STRICT_TESTS="1",
                   GARAGE16_ALLOWED_SKIPS=",".join(filter(None, [os.environ.get("GARAGE16_ALLOWED_SKIPS", "")] + allowed)))
    cov_data = report_dir / ".coverage"
    cov_data.unlink(missing_ok=True)
    run_env["COVERAGE_FILE"] = str(cov_data)
    command = [sys.executable, "-m", "coverage", "run", "--rcfile", str(workdir / ".coveragerc"), "-m", "pytest", "-q",
               "-rfE", "-p", "no:cacheprovider", f"--junitxml={junit}", *args.pytest_args]
    print("$", " ".join(command), flush=True)
    code = subprocess.run(command, cwd=workdir, env=run_env).returncode
    if code == EXIT_INFRASTRUCTURE or not junit.exists():
        write_report(report_dir, verdict="NOT TESTED", reason="the test session did not start (see the output above)",
                     env=env, result=None, missing=[], diffs=diffs, allowed=allowed, duration=time.time() - started)
        return EXIT_INFRASTRUCTURE
    result = parse_junit(junit)
    c = result["counts"]
    coverage = None
    cov_json = report_dir / "coverage.json"
    if subprocess.run([sys.executable, "-m", "coverage", "json", "-q", "--rcfile", str(workdir / ".coveragerc"), "-o", str(cov_json)],
                      cwd=workdir, env=run_env).returncode == 0:
        coverage = coverage_shortfalls(cov_json, workdir / "tests" / "coverage_floors.json")
    else:
        coverage = ([], ["coverage data could not be read"])
    tests_green = code == 0 and c["failures"] == 0 and c["errors"] == 0 and c["tests"] > 0
    quality_green = all(q["ok"] for q in quality)
    coverage_green = not coverage[1]
    green = tests_green and quality_green and coverage_green
    verdict = "PASSED" if green else "FAILED"
    if green:
        reason = f"{c['passed']} tests passed, none failed; linter, security analysis, vulnerability check and coverage floors passed"
    else:
        parts = []
        if not tests_green:
            parts.append(f"{c['failures'] + c['errors']} of {c['tests']} tests failed")
        parts += [f"{q['name']} failed" for q in quality if not q["ok"]]
        if not coverage_green:
            parts.append(f"{len(coverage[1])} coverage floor(s) missed")
        reason = "; ".join(parts)
    if diffs:
        reason += f" (WARNING: {len(diffs)} version(s) differ from constraints.txt)"
    path = write_report(report_dir, verdict=verdict, reason=reason, env=env, result=result, missing=[], diffs=diffs,
                        allowed=allowed, duration=time.time() - started, quality=quality, coverage=coverage)
    print(f"{verdict}: {reason}. Report: {path}")
    if not green:
        return EXIT_FAILED
    if args.zip:
        out = Path(args.zip).resolve()
        if tree_hash(workdir) != env["tree_hash"]:
            print("FAILED: the staged tree changed during the run — not publishing it", file=sys.stderr)
            return EXIT_FAILED
        n = build_zip(out, report_dir, prefix, tree=workdir)
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        (out.parent / (out.name + ".sha256")).write_text(f"{digest}  {out.name}\n", encoding="utf-8")
        print(f"Release archive: {out} ({n} files, report included; sha256 {digest})")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
