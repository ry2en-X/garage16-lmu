"""Client-side server migration & update-required behavior (V0.8.6 §3–§8),
tested against REAL local HTTP/HTTPS servers (tests/local_servers.py), not
mock objects.

This file REPLACES the three mock-based follow_migration() tests from
V0.8.1 (which patched `client.main.requests.get` — that logic now lives in
client/migration.py). The same three scenarios are covered again below
against real servers, alongside the spec's cases A–F.

Case map (spec §7):
  A  no migration                      -> test_case_a_*
  B  migration, valid new URL          -> test_case_b_*
  C  migration, bad/unreachable URL    -> test_case_c_*
  D  UPDATE_REQUIRED                   -> test_case_d_*
  E  restart after migration           -> test_case_e_*
  F  auth intact, no creds to wrong URL-> test_case_f_*
"""

from __future__ import annotations

import json

import pytest

import client.config as client_config
import client.migration as migration_module
from client.config import ClientConfig, load_config
from client.migration import apply_migration_if_safe, check_and_apply_migration
from client.server_check import run_server_check
from tests.local_servers import FakeGarageServer, RealAppServer, free_port, make_self_signed_cert

OK_HEALTH = {"status": "ok", "version": "0.8.6", "min_client_version": "0.5.3", "db_ok": True}


# --------------------------------------------------------------- fixtures

@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Points client.config's module-level CONFIG_DIR/CONFIG_PATH at a
    throwaway directory (plain module attributes read at call time, so
    patching them is visible everywhere — no module reload; see
    tests/test_security_headers.py's docstring)."""
    config_dir = tmp_path / ".lmu_garage"
    monkeypatch.setattr(client_config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(client_config, "CONFIG_PATH", config_dir / "client_config.json")
    monkeypatch.delenv("LMU_GARAGE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("LMU_GARAGE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("LMU_GARAGE_API_URL", raising=False)
    return config_dir


@pytest.fixture
def servers(tmp_path, monkeypatch):
    """Factory for real local servers, https by default. Trusts the
    throwaway certificate via REQUESTS_CA_BUNDLE — certificate
    verification stays fully ON in the client, exactly like production."""
    cert, key = make_self_signed_cert(tmp_path)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(cert))
    started = []

    def make(health, tls=True):
        srv = FakeGarageServer(health, tls=(cert, key) if tls else None).start()
        started.append(srv)
        return srv

    make.tls_material = (cert, key)
    make.register = started.append
    yield make
    for srv in started:
        srv.stop()


def _cfg(url: str) -> ClientConfig:
    return ClientConfig(api_url=url, auth_token="tok-abc", client_secret="sec-xyz")


def _persist(isolated_config, cfg: ClientConfig) -> bytes:
    isolated_config.mkdir(parents=True, exist_ok=True)
    path = isolated_config / "client_config.json"
    client_config.save_config(cfg)
    return path.read_bytes()


def _file_bytes(isolated_config) -> bytes:
    return (isolated_config / "client_config.json").read_bytes()


# ------------------------------------------------------------------ Case A

def test_case_a_no_migration_keeps_config_and_file_untouched(isolated_config, servers):
    old = servers(OK_HEALTH)
    cfg = _cfg(old.url)
    before = _persist(isolated_config, cfg)

    result = check_and_apply_migration(cfg)

    assert result.config == cfg
    assert result.message is None
    assert result.migrated is False
    assert _file_bytes(isolated_config) == before


def test_case_a_server_check_reports_reachable_and_quiet(isolated_config, servers):
    old = servers(OK_HEALTH)
    result = run_server_check(_cfg(old.url), "0.8.6")
    assert result.reachable is True
    assert result.update_required is False
    assert result.notice == ""
    assert result.log_lines == []


def test_case_a_unreachable_server_is_reported_without_touching_config(isolated_config):
    dead = f"https://127.0.0.1:{free_port()}"
    cfg = _cfg(dead)
    before = _persist(isolated_config, cfg)

    result = run_server_check(cfg, "0.8.6", timeout=1)

    assert result.reachable is False
    assert "laps are saved" in result.notice
    assert result.config == cfg
    assert _file_bytes(isolated_config) == before


# ------------------------------------------------------------------ Case B

def test_case_b_valid_migration_switches_url_and_persists_it(isolated_config, servers):
    new = servers(OK_HEALTH)
    old = servers({**OK_HEALTH, "migrated_to": new.url})
    cfg = _cfg(old.url)
    _persist(isolated_config, cfg)

    result = check_and_apply_migration(cfg)

    assert result.migrated is True
    assert result.config.api_url == new.url
    saved = json.loads(_file_bytes(isolated_config))
    assert saved["api_url"] == new.url
    # credentials carried over unchanged
    assert saved["auth_token"] == "tok-abc"
    assert saved["client_secret"] == "sec-xyz"
    assert "switched" in result.message


def test_case_b_server_check_applies_migration_end_to_end(isolated_config, servers):
    new = servers(OK_HEALTH)
    old = servers({**OK_HEALTH, "migrated_to": new.url})
    _persist(isolated_config, _cfg(old.url))

    result = run_server_check(_cfg(old.url), "0.8.6")

    assert result.migrated is True
    assert result.config.api_url == new.url
    assert result.notice == ""  # a successful switch is good news, not a banner
    assert any("switched" in line for line in result.log_lines)


# ------------------------------------------------------------------ Case C

@pytest.mark.parametrize(
    "bad_target",
    ["javascript:alert(1)", "file:///etc/passwd", "ftp://example.com/x", "not a url at all", "https://", "//evil.example"],
)
def test_case_c_malformed_migration_target_is_ignored(isolated_config, servers, bad_target):
    old = servers({**OK_HEALTH, "migrated_to": bad_target})
    cfg = _cfg(old.url)
    before = _persist(isolated_config, cfg)

    result = check_and_apply_migration(cfg)

    assert result.migrated is False
    assert result.config == cfg
    assert "invalid" in result.message
    assert _file_bytes(isolated_config) == before


def test_case_c_unreachable_new_server_keeps_old_config(isolated_config, servers):
    dead_new = f"https://127.0.0.1:{free_port()}"
    old = servers({**OK_HEALTH, "migrated_to": dead_new})
    cfg = _cfg(old.url)
    before = _persist(isolated_config, cfg)

    result = check_and_apply_migration(cfg)

    assert result.migrated is False
    assert result.config == cfg
    assert "isn't responding" in result.message
    assert _file_bytes(isolated_config) == before


@pytest.mark.parametrize("broken_health", [500, 404, 200, {"foo": "bar"}])
def test_case_c_new_server_that_answers_garbage_is_not_adopted(isolated_config, servers, broken_health):
    """500/404 = server error; bare `200` = non-JSON body; {"foo":...} =
    valid JSON that isn't a Garage16 health payload."""
    new = servers(broken_health)
    old = servers({**OK_HEALTH, "migrated_to": new.url})
    cfg = _cfg(old.url)
    before = _persist(isolated_config, cfg)

    result = check_and_apply_migration(cfg)

    assert result.migrated is False
    assert result.config == cfg
    assert _file_bytes(isolated_config) == before


def test_case_c_failed_save_neither_crashes_nor_switches(isolated_config, servers, monkeypatch):
    new = servers(OK_HEALTH)
    old = servers({**OK_HEALTH, "migrated_to": new.url})
    cfg = _cfg(old.url)
    before = _persist(isolated_config, cfg)

    def boom(_config):
        raise OSError("disk full")

    monkeypatch.setattr(migration_module, "save_config", boom)
    result = check_and_apply_migration(cfg)

    assert result.migrated is False
    assert result.config == cfg  # not adopted in memory either
    assert "couldn't save" in result.message
    assert _file_bytes(isolated_config) == before


def test_case_c_atomic_save_leaves_old_file_intact_when_replace_fails(isolated_config, monkeypatch):
    cfg = _cfg("https://old.example")
    before = _persist(isolated_config, cfg)

    import os as _os

    def failing_replace(src, dst):
        raise OSError("simulated crash between write and rename")

    monkeypatch.setattr(_os, "replace", failing_replace)
    with pytest.raises(OSError):
        client_config.save_config(_cfg("https://new.example"))

    assert _file_bytes(isolated_config) == before  # old config fully intact
    assert json.loads(before)["api_url"] == "https://old.example"


def test_security_migration_signal_over_plain_http_is_not_auto_followed(isolated_config, servers):
    """The core §4 rule: a `migrated_to` seen over plain HTTP could be
    spoofed by anyone on the network — never auto-followed."""
    new = servers(OK_HEALTH)  # https
    old_http = servers({**OK_HEALTH, "migrated_to": new.url}, tls=False)
    cfg = _cfg(old_http.url)
    before = _persist(isolated_config, cfg)

    result = check_and_apply_migration(cfg)

    assert result.migrated is False
    assert result.config == cfg
    assert "isn't secure enough" in result.message
    assert _file_bytes(isolated_config) == before
    # ...and the new server was never even contacted
    assert new.requests_seen == []


def test_security_https_to_http_downgrade_is_not_followed(isolated_config, servers):
    new_http = servers(OK_HEALTH, tls=False)
    old = servers({**OK_HEALTH, "migrated_to": new_http.url})
    cfg = _cfg(old.url)
    before = _persist(isolated_config, cfg)

    result = check_and_apply_migration(cfg)

    assert result.migrated is False
    assert _file_bytes(isolated_config) == before
    assert new_http.requests_seen == []


# ------------------------------------------------------------------ Case D

def test_case_d_update_required_gives_a_plain_message_and_leaves_config_alone(isolated_config, servers):
    old = servers({**OK_HEALTH, "min_client_version": "9.0.0", "client_download_url": "https://example.com/get-garage16"})
    cfg = _cfg(old.url)
    before = _persist(isolated_config, cfg)

    result = run_server_check(cfg, "0.8.6")

    assert result.update_required is True
    assert "new version of Garage16" in result.notice
    assert "v9.0.0" in result.notice
    assert "https://example.com/get-garage16" in result.notice
    assert "laps are kept" in result.notice
    assert result.config == cfg
    assert _file_bytes(isolated_config) == before  # config not damaged


def test_case_d_update_required_without_download_link_points_at_the_admin(isolated_config, servers):
    old = servers({**OK_HEALTH, "min_client_version": "9.0.0"})
    result = run_server_check(_cfg(old.url), "0.8.6")
    assert result.update_required is True
    assert "Ask whoever runs your Garage16 server" in result.notice


def test_case_d_server_verdict_wins_over_local_comparison(isolated_config, servers):
    """Version negotiation: when the server states update_required, the
    client believes it (single source of truth, server/versioning.py)."""
    old = servers({**OK_HEALTH, "min_client_version": "0.1.0", "update_required": True})
    assert run_server_check(_cfg(old.url), "0.8.6").update_required is True


def test_case_d_client_sends_its_version_to_health(isolated_config, servers):
    old = servers(OK_HEALTH)
    run_server_check(_cfg(old.url), "0.8.6")
    assert old.requests_seen[0]["query"] == {"client_version": "0.8.6"}


def test_case_d_real_server_negotiates_update_required(client, monkeypatch, tmp_path):
    from server.config import settings

    monkeypatch.setattr(settings, "min_client_version", "99.0.0")
    monkeypatch.setattr(settings, "client_download_url", "https://example.com/dl")
    srv = RealAppServer().start()
    try:
        result = run_server_check(_cfg(srv.url), "0.8.6")
    finally:
        srv.stop()
    assert result.update_required is True
    assert "https://example.com/dl" in result.notice

    monkeypatch.setattr(settings, "min_client_version", "0.5.3")
    srv = RealAppServer().start()
    try:
        result = run_server_check(_cfg(srv.url), "0.8.6")
    finally:
        srv.stop()
    assert result.update_required is False


def test_case_d_update_required_upload_keeps_laps_pending_then_uploads_after_update(client, monkeypatch, tmp_path):
    """The real user-visible guarantee: a friend whose client is too old
    does NOT lose the laps they drove meanwhile. Real Uploader, real
    server, real HTTP — the lap stays pending on 426 (previously a plain
    400 marked it permanently rejected), and uploads normally once the
    minimum is satisfied."""
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader
    from server.config import settings
    from tests.test_integration_upload_flow import _register, _write_recorded_lap

    account = _register(client)
    lap_dir = tmp_path / "laps"
    lap_dir.mkdir()
    _write_recorded_lap(lap_dir)
    recorder = LapRecorder(data_dir=str(lap_dir))

    monkeypatch.setattr(settings, "min_client_version", "99.0.0")
    monkeypatch.setattr(settings, "client_download_url", "https://example.com/dl")
    srv = RealAppServer().start()
    try:
        uploader = Uploader(recorder=recorder, backend_url=srv.url, auth_token=account["auth_token"], client_secret=account["client_secret"])

        assert uploader.run_once() == 0
        assert uploader.update_required is not None
        assert uploader.update_required.min_version == "99.0.0"
        assert uploader.update_required.download_url == "https://example.com/dl"
        assert len(list(recorder.pending_uploads())) == 1  # still pending — NOT rejected

        # the friend updates (server minimum satisfied)
        monkeypatch.setattr(settings, "min_client_version", "0.5.3")
        assert uploader.run_once() == 1
        assert uploader.update_required is None
        assert list(recorder.pending_uploads()) == []
    finally:
        srv.stop()


# ------------------------------------------------------------------ Case E

def test_case_e_new_url_survives_a_restart(isolated_config, servers):
    new = servers(OK_HEALTH)
    old = servers({**OK_HEALTH, "migrated_to": new.url})
    _persist(isolated_config, _cfg(old.url))
    assert check_and_apply_migration(_cfg(old.url)).migrated is True

    # "restart": a brand-new process would resolve its config from disk
    reloaded = load_config()
    assert reloaded.api_url == new.url
    assert reloaded.auth_token == "tok-abc"

    # ...and the reloaded config runs cleanly against the new server
    after = run_server_check(reloaded, "0.8.6")
    assert after.reachable is True
    assert after.migrated is False
    assert after.notice == ""


def test_case_e_old_server_still_advertising_migration_is_harmless_after_switch(isolated_config, servers):
    new = servers(OK_HEALTH)
    cfg_new = _cfg(new.url)
    # a body that (wrongly, e.g. copied config) points at the URL we're already on
    result = apply_migration_if_safe(cfg_new, {**OK_HEALTH, "migrated_to": new.url})
    assert result.migrated is False
    assert result.message is None


# ------------------------------------------------------------------ Case F

def test_case_f_no_credentials_are_sent_while_verifying_the_new_server(isolated_config, servers):
    new = servers(OK_HEALTH)
    old = servers({**OK_HEALTH, "migrated_to": new.url})
    cfg = _cfg(old.url)
    _persist(isolated_config, cfg)

    assert check_and_apply_migration(cfg).migrated is True

    # the ONLY thing the new server ever saw: unauthenticated GET /health
    assert len(new.requests_seen) == 1
    seen = new.requests_seen[0]
    assert seen["method"] == "GET" and seen["path"] == "/health"
    assert seen["has_authorization"] is False and seen["body_len"] == 0
    # same for the old server
    assert all(not r["has_authorization"] and r["body_len"] == 0 for r in old.requests_seen)


def test_case_f_nothing_is_sent_to_a_rejected_target(isolated_config, servers):
    """Rejected migrations (insecure source) don't even probe the target."""
    new = servers(OK_HEALTH)
    old_http = servers({**OK_HEALTH, "migrated_to": new.url}, tls=False)
    check_and_apply_migration(_cfg(old_http.url))
    assert new.requests_seen == []


def test_case_f_auth_still_works_after_migration_against_the_real_upload_endpoint(client, isolated_config, servers, tmp_path):
    """End-to-end: old (fake, https) server announces a move to the REAL
    app over https; client migrates; the SAME auth_token/client_secret
    then uploads a lap successfully and it lands under the SAME driver —
    account, laps and (by extension, they're keyed on driver_id) teams
    survive a server move without re-registering."""
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader
    from server.database import SessionLocal
    from server.models import Lap
    from tests.test_integration_upload_flow import _register, _write_recorded_lap

    account = _register(client)
    cert, key = servers.tls_material
    new_real = RealAppServer(tls=(cert, key)).start()
    servers.register(new_real)  # torn down with the other servers
    old = servers({**OK_HEALTH, "migrated_to": new_real.url})

    cfg = ClientConfig(api_url=old.url, auth_token=account["auth_token"], client_secret=account["client_secret"])
    _persist(isolated_config, cfg)
    result = check_and_apply_migration(cfg)
    assert result.migrated is True and result.config.api_url == new_real.url

    lap_dir = tmp_path / "laps"
    lap_dir.mkdir()
    _write_recorded_lap(lap_dir)
    uploader = Uploader(
        recorder=LapRecorder(data_dir=str(lap_dir)),
        backend_url=result.config.api_url,
        auth_token=result.config.auth_token,
        client_secret=result.config.client_secret,
    )
    assert uploader.run_once() == 1
    assert uploader.auth_rejected is False

    db = SessionLocal()
    try:
        laps = db.query(Lap).filter(Lap.driver_id == account["driver_id"]).all()
        assert len(laps) == 1
    finally:
        db.close()


# ---------------------------------- manual `--follow-migration` fallback

def test_follow_migration_updates_config_for_https_servers(isolated_config, servers, capsys):
    from client.main import follow_migration

    new = servers(OK_HEALTH)
    old = servers({**OK_HEALTH, "migrated_to": new.url})
    _persist(isolated_config, _cfg(old.url))

    assert follow_migration() == 0
    saved = json.loads(_file_bytes(isolated_config))
    assert saved["api_url"] == new.url
    assert saved["auth_token"] == "tok-abc" and saved["client_secret"] == "sec-xyz"
    assert "switched" in capsys.readouterr().out


def test_follow_migration_manual_command_may_follow_a_plain_http_source(isolated_config, servers, capsys):
    """The manual flag is the documented path for old HTTP-only (LAN)
    servers: typing the command IS the consent the automatic path needs
    HTTPS for. The new server must still answer."""
    from client.main import follow_migration

    new = servers(OK_HEALTH)
    old_http = servers({**OK_HEALTH, "migrated_to": new.url}, tls=False)
    _persist(isolated_config, _cfg(old_http.url))

    assert follow_migration() == 0
    assert json.loads(_file_bytes(isolated_config))["api_url"] == new.url


def test_follow_migration_still_refuses_an_unreachable_target(isolated_config, servers, capsys):
    from client.main import follow_migration

    old_http = servers({**OK_HEALTH, "migrated_to": f"https://127.0.0.1:{free_port()}"}, tls=False)
    cfg = _cfg(old_http.url)
    before = _persist(isolated_config, cfg)

    assert follow_migration() == 0
    assert _file_bytes(isolated_config) == before
    assert "isn't responding" in capsys.readouterr().out


def test_follow_migration_is_a_no_op_when_no_migration_is_signaled(isolated_config, servers, capsys):
    from client.main import follow_migration

    old = servers(OK_HEALTH)
    before = _persist(isolated_config, _cfg(old.url))

    assert follow_migration() == 0
    assert _file_bytes(isolated_config) == before
    assert "No migration in progress" in capsys.readouterr().out


def test_follow_migration_reports_failure_when_server_unreachable(isolated_config, capsys):
    from client.main import follow_migration

    _persist(isolated_config, _cfg(f"http://127.0.0.1:{free_port()}"))
    assert follow_migration() == 1
    assert "Could not reach" in capsys.readouterr().out
