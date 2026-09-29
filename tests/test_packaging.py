"""Installer / package logic (V0.8.6 §2, §13). What's verified here, and
what deliberately is NOT:

  Verified in every run: the packaging files exist and are internally
  consistent — installer version can't drift from the client's, the spec
  builds a windowed one-dir app without server code, per-user install
  needs no admin rights, uninstall never deletes user data, the baked
  server-URL file sits where client/config.py looks for it, the CI
  workflow is valid YAML.

  Verified on demand (GARAGE16_TEST_FROZEN_BUILD=1): a REAL PyInstaller
  build of the client, then the frozen binary is executed — version
  output, baked server URL, and an HTTPS server migration through the
  bundled ssl stack. That's a Linux build here (PyInstaller can only
  build for the OS it runs on).

  NOT verifiable in this project's dev sandbox — BLOCKED, USER TEST
  REQUIRED: a Windows .exe, the Inno Setup installer, the GitHub Actions
  run, and running any of it on a real Windows PC.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"

sys.path.insert(0, str(PACKAGING))
import sync_version  # noqa: E402


# ---------------------------------------------------------- version sync

def test_client_version_is_readable_and_matches_the_module():
    from client.uploader.uploader import CLIENT_VERSION

    assert sync_version.read_client_version() == CLIENT_VERSION


def test_generated_version_include_is_in_sync_with_the_client():
    """version.iss is generated; if someone bumps CLIENT_VERSION and
    forgets to re-run sync_version.py (build_windows.ps1 always does),
    this fails instead of shipping an installer with the wrong version."""
    expected = sync_version.render_version_iss(sync_version.read_client_version())
    assert (PACKAGING / "version.iss").read_text(encoding="utf-8") == expected


def test_sync_version_writes_the_include(tmp_path, monkeypatch):
    fake_uploader = tmp_path / "uploader.py"
    fake_uploader.write_text('X = 1\nCLIENT_VERSION = "1.2.3"\n', encoding="utf-8")
    fake_iss = tmp_path / "version.iss"
    monkeypatch.setattr(sync_version, "UPLOADER", fake_uploader)
    monkeypatch.setattr(sync_version, "VERSION_ISS", fake_iss)
    assert sync_version.main() == 0
    assert '#define AppVersion "1.2.3"' in fake_iss.read_text(encoding="utf-8")


def test_sync_version_fails_loudly_without_a_version_line(tmp_path):
    empty = tmp_path / "uploader.py"
    empty.write_text("nothing = 1\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        sync_version.read_client_version(empty)


def test_versions_stated_in_the_repo_match_the_real_constants():
    """§11: no contradicting version numbers. Server and desktop client
    are versioned separately (a server-only release doesn't touch the
    client), so the rule is: README states BOTH exactly as the code
    defines them, and the installer carries the client's version."""
    from client.uploader.uploader import CLIENT_VERSION

    server_main = (ROOT / "server" / "main.py").read_text(encoding="utf-8")
    server_version = re.search(r'^VERSION = "([\d.]+)"', server_main, re.MULTILINE).group(1)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Current version: server {server_version} / desktop client {CLIENT_VERSION}." in readme
    assert (PACKAGING / "version.iss").read_text(encoding="utf-8").count(CLIENT_VERSION) == 1
    changelog_top = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("\n## ", 2)[1]
    assert changelog_top.startswith(f"V{server_version}"), "CHANGELOG's newest entry must describe the current server version"


# ------------------------------------------------------- installer script

def _iss_lines():
    return (PACKAGING / "garage16.iss").read_text(encoding="utf-8").splitlines()


def test_installer_is_per_user_and_needs_no_admin():
    text = "\n".join(_iss_lines())
    assert "PrivilegesRequired=lowest" in text
    assert "{localappdata}" in text


def test_installer_updates_in_place_via_a_fixed_app_id():
    text = "\n".join(_iss_lines())
    assert re.search(r"AppId=\{\{[0-9A-F-]{36}\}", text)
    assert "CloseApplications=yes" in text


def test_installer_never_deletes_user_data_on_uninstall():
    active = [l.strip() for l in _iss_lines() if not l.strip().startswith(";")]
    assert not any(l.startswith("[UninstallDelete]") for l in active)


def test_installer_pulls_its_version_from_the_generated_include_and_names_the_setup_exe():
    text = "\n".join(_iss_lines())
    assert '#include "version.iss"' in text
    assert "AppVersion={#AppVersion}" in text
    assert "OutputBaseFilename=Garage16-Client-Setup" in text
    assert 'Source: "..\\dist\\Garage16\\*"' in text


# ------------------------------------------------------------ PyInstaller

def test_spec_is_valid_python_and_builds_a_windowed_onedir_app():
    source = (PACKAGING / "garage16.spec").read_text(encoding="utf-8")
    ast.parse(source)
    assert "console=False" in source            # no terminal window (§2)
    assert "exclude_binaries=True" in source and "COLLECT(" in source  # one-dir
    assert 'name="Garage16"' in source


def test_spec_keeps_server_and_tooling_out_of_the_client_package():
    source = (PACKAGING / "garage16.spec").read_text(encoding="utf-8")
    for excluded in ('"server"', '"discord_bot"', '"alembic"', '"tests"'):
        assert excluded in source


def test_spec_entry_point_exists_and_calls_main():
    entry = (PACKAGING / "garage16_entry.py").read_text(encoding="utf-8")
    assert "from client.main import main" in entry


def test_baked_server_file_location_matches_where_the_client_looks(monkeypatch, tmp_path):
    """The spec drops garage16_server.txt into the bundle root; in a
    PyInstaller one-dir build that root is sys._MEIPASS (and/or next to the
    executable). client/config.py must look in both."""
    import client.config as client_config

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Garage16.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    candidates = client_config._bundled_server_file_candidates()
    assert tmp_path.resolve() / client_config.BUNDLED_SERVER_FILENAME in candidates
    assert tmp_path / "_internal" / client_config.BUNDLED_SERVER_FILENAME in candidates
    assert 'garage16_server.txt' in (PACKAGING / "garage16.spec").read_text(encoding="utf-8")


# ------------------------------------------------------------- CI + build

def test_ci_workflow_is_valid_and_builds_on_windows():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "build-client.yml").read_text(encoding="utf-8"))
    assert workflow["jobs"]["windows"]["runs-on"] == "windows-latest"
    triggers = workflow.get(True) or workflow.get("on")  # PyYAML reads bare `on:` as True
    assert "workflow_dispatch" in triggers
    steps = json.dumps(workflow["jobs"]["windows"]["steps"])
    assert "build_windows.ps1" in steps and "upload-artifact" in steps


def test_build_script_smoke_tests_the_built_app_and_bakes_the_server_url():
    script = (PACKAGING / "build_windows.ps1").read_text(encoding="utf-8")
    assert "--version" in script
    assert "GARAGE16_SERVER_URL" in script
    assert "sync_version.py" in script
    assert "Garage16-Portable.zip" in script  # fallback when Inno Setup is missing


# --------------------------------------- opt-in: real build + real run

@pytest.mark.skipif(
    os.environ.get("GARAGE16_TEST_FROZEN_BUILD") != "1",
    reason="opt-in (slow, ~1 min): set GARAGE16_TEST_FROZEN_BUILD=1 to run a real PyInstaller build",
)
def test_real_frozen_build_runs_and_follows_a_migration_over_https(tmp_path):
    pytest.importorskip("PyInstaller")
    dist, work = tmp_path / "dist", tmp_path / "build"
    env = dict(os.environ, GARAGE16_SERVER_URL="https://baked.garage16.example.test")
    build = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(PACKAGING / "garage16.spec"), "--noconfirm",
         "--distpath", str(dist), "--workpath", str(work)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=900,
    )
    assert build.returncode == 0, build.stdout[-2000:] + build.stderr[-2000:]
    exe = dist / "Garage16" / ("Garage16.exe" if os.name == "nt" else "Garage16")

    home = tmp_path / "home"
    home.mkdir()
    run_env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), LMU_GARAGE_CONFIG_DIR=str(home / "cfg"))
    run_env.pop("LMU_GARAGE_API_URL", None)
    version = subprocess.run([str(exe), "--version"], env=run_env, capture_output=True, text=True, timeout=60)
    from client.uploader.uploader import CLIENT_VERSION

    assert version.returncode == 0
    assert f"Garage16 client {CLIENT_VERSION}" in version.stdout
    # the baked-in address is what an un-configured install points at
    assert "https://baked.garage16.example.test" in version.stdout

    # real HTTPS migration executed by the FROZEN binary
    from tests.local_servers import FakeGarageServer, make_self_signed_cert

    cert, key = make_self_signed_cert(tmp_path)
    new = FakeGarageServer({"status": "ok"}, tls=(cert, key)).start()
    old = FakeGarageServer({"status": "ok", "migrated_to": new.url}, tls=(cert, key)).start()
    try:
        cfg_dir = home / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / "client_config.json").write_text(json.dumps({"api_url": old.url, "auth_token": "t", "client_secret": "s"}), encoding="utf-8")
        migrate = subprocess.run(
            [str(exe), "--follow-migration"], env=dict(run_env, REQUESTS_CA_BUNDLE=str(cert)),
            capture_output=True, text=True, timeout=60,
        )
    finally:
        old.stop()
        new.stop()
    assert migrate.returncode == 0, migrate.stdout + migrate.stderr
    assert json.loads((cfg_dir / "client_config.json").read_text(encoding="utf-8"))["api_url"] == new.url


def test_attach_parent_console_is_a_noop_outside_frozen_windows(monkeypatch):
    import client.main as main_module

    assert main_module._attach_parent_console() is False  # source run / Linux
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert main_module._attach_parent_console() is False  # frozen but not Windows (os.name != "nt")


def test_build_script_waits_for_the_windowed_exe_in_its_smoke_test():
    script = (PACKAGING / "build_windows.ps1").read_text(encoding="utf-8")
    assert "Start-Process" in script and "-Wait" in script and "ExitCode" in script
