"""What must (not) end up in a published copy of the repository, and that
the operational docs match the scripts (V0.8.8). Checks the SHIPPED tree —
not anyone's working machine."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = (ROOT / ".gitignore").read_text(encoding="utf-8")
PACKAGER = (ROOT / "package_release.bat").read_text(encoding="utf-8")

TEXT_SUFFIXES = {".py", ".md", ".txt", ".yml", ".yaml", ".js", ".html", ".css", ".sh", ".ps1", ".bat", ".iss", ".spec", ".ini", ".example", ".json", ".mjs"}
# Not part of the shipped source: dependency/virtualenv folders (a developer's own
# `.venv` inside the project — exactly what the docs recommend — holds thousands of
# third-party files with LAN-address examples), build output, caches, test data.
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "server_telemetry_storage", "tests",
    ".venv", "venv", "env", ".venv-build", "build", "dist", "dist-installer",
    ".pytest_cache", "site-packages",
}


def _shipped_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES and path.name != ".env.example":
            continue
        if set(path.relative_to(ROOT).parts) & SKIP_DIRS:
            continue
        yield path


def test_gitignore_keeps_secrets_data_and_build_output_out_of_git():
    for entry in (".env", "*.db", "lmu_garage_data/", "client_config.json", "*.sql.gz", "*.tar.gz",
                  ".venv-build/", "build/", "dist/", "dist-installer/", "node_modules/", "*.log"):
        assert entry in GITIGNORE.splitlines(), f".gitignore is missing {entry!r}"
    assert ".env.example" not in GITIGNORE.splitlines()  # the template must stay committable


def test_the_release_packager_excludes_the_same_build_output():
    for name in (".venv-build", "build", "dist", "dist-installer", "node_modules", "server_telemetry_storage", "lmu_garage_data"):
        assert name in PACKAGER, f"package_release.bat does not exclude {name}"
    assert ".env" in PACKAGER and "*.log" in PACKAGER


def test_no_private_lan_addresses_or_real_tunnel_hostnames_in_shipped_files():
    """Things that identify one person's home network don't belong in a
    published repository. (example/placeholder hosts are fine.)"""
    lan = re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")
    quick_tunnel = re.compile(r"[a-z0-9-]+\.trycloudflare\.com")
    offenders = []
    for path in _shipped_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in (lan, quick_tunnel):
            for match in pattern.findall(text):
                offenders.append(f"{path.relative_to(ROOT)}: {match}")
    assert offenders == []


def test_no_real_secrets_or_env_file_in_the_tree():
    assert not (ROOT / ".env").exists()
    for path in _shipped_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"LMU_GARAGE_(?:SECRET_KEY|ADMIN_TOKEN|REGISTRATION_SECRET)\s*=\s*(\S{20,})", text):
            assert "your" in match.group(1).lower() or "<" in match.group(1) or "$" in match.group(1), f"real-looking secret in {path}"


def test_operational_docs_point_at_the_restore_script_and_the_correct_procedure():
    rollback = (ROOT / "docs" / "ROLLBACK.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "scripts/restore.sh" in rollback and "scripts/restore.sh" in readme
    assert "DROP DATABASE IF EXISTS garage16 WITH (FORCE)" in rollback   # the manual fallback drops first
    assert (ROOT / "scripts" / "restore.sh").exists() and (ROOT / "scripts" / "backup.sh").exists()
    # the old, wrong sequence: stop everything, THEN exec into the stopped db container
    assert "docker compose down\n\n   **Wiederherstellen" not in rollback


def test_readme_no_longer_tells_operators_to_reconfigure_every_client_after_a_move():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "update the desktop client's configured\n   server URL for every driver" not in readme
    assert "SAME `LMU_GARAGE_SECRET_KEY`" in readme   # the trap when moving servers


# ------------------------------------------------ line endings (CRLF)

def test_gitattributes_forces_lf_for_everything_that_runs_on_linux():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for rule in ("*.sh             text eol=lf", "Caddyfile        text eol=lf", "Dockerfile*      text eol=lf", "*.yml            text eol=lf", "*.bat            text eol=crlf"):
        assert rule in attrs, rule


def test_no_shipped_linux_file_contains_carriage_returns():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or set(path.relative_to(ROOT).parts) & SKIP_DIRS - {"tests"}:
            continue
        if path.suffix in {".sh", ".yml"} or path.name in {"Caddyfile"} or path.name.startswith("Dockerfile"):
            if b"\r" in path.read_bytes():
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_both_dockerfiles_strip_crlf_from_their_entrypoint():
    for name, script in (("Dockerfile.server", "entrypoint.sh"), ("Dockerfile.discord_bot", "entrypoint-discord-bot.sh")):
        text = (ROOT / "docker" / name).read_text(encoding="utf-8")
        assert f"RUN sed -i 's/\\r$//' ./docker/{script}" in text, name
        assert text.index("RUN sed -i") < text.index("RUN chmod +x"), name  # normalized before it's made executable


def test_a_crlf_script_really_breaks_and_the_dockerfile_fix_really_repairs_it(tmp_path):
    """Demonstrates the actual failure the fix is for (Linux only)."""
    import subprocess
    import sys

    if not sys.platform.startswith("linux"):
        return
    script = tmp_path / "entrypoint.sh"
    script.write_bytes(b"#!/bin/sh\r\necho container-started\r\n")
    script.chmod(0o755)
    # The kernel looks for an interpreter literally named "sh\r" -> ENOENT, i.e.
    # "exec ./docker/entrypoint.sh: no such file or directory" in the container.
    try:
        broken = subprocess.run([str(script)], capture_output=True, text=True)
        assert broken.returncode != 0 and "container-started" not in broken.stdout
    except FileNotFoundError:
        pass

    subprocess.run(["sed", "-i", "s/\\r$//", str(script)], check=True)  # the exact command from the Dockerfiles
    fixed = subprocess.run([str(script)], capture_output=True, text=True)
    assert fixed.returncode == 0 and "container-started" in fixed.stdout
