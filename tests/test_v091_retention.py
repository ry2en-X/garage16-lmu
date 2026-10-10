"""V0.9.1 §47 — local retention of uploaded telemetry files.

The promise being tested: the client NEVER loses a lap the server has not accepted,
deletes nothing by default for the first two weeks, can be switched off, and cannot be
tricked (by a hand-edited sidecar, a symlink or a typo in the settings) into deleting
anything but an old, uploaded lap's own .parquet file.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from client.config import DEFAULT_LOCAL_RETENTION_DAYS, MAX_LOCAL_RETENTION_DAYS, local_retention_days
from client.telemetry.recorder import LapRecorder

DAY = 86400.0


def _lap(directory: Path, name: str, **meta) -> Path:
    """A recorded lap on disk: <name>.parquet + <name>.json."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.parquet").write_bytes(b"PAR1-not-really-" + name.encode())
    sidecar = {
        "track_name": "Le Mans", "car_name": "#7", "lap_number": 1, "lap_time": 90.0, "sector_times": [30, 30, 30],
        "is_valid": True, "telemetry_file": f"{name}.parquet", "uploaded": False, **meta,
    }
    path = directory / f"{name}.json"
    path.write_text(json.dumps(sidecar), encoding="utf-8")
    return path


def _meta(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def rec(tmp_path):
    return LapRecorder(data_dir=str(tmp_path / "laps"))


NOW = 1_800_000_000.0


def test_mark_uploaded_starts_the_retention_clock(rec):
    meta = _lap(rec.data_dir / "t" / "c", "a")
    before = time.time()
    rec.mark_uploaded(meta)
    stamp = _meta(meta)["uploaded_at"]
    assert _meta(meta)["uploaded"] is True and before - 1 <= stamp <= time.time() + 1


def test_old_uploaded_lap_loses_its_file_but_keeps_its_sidecar(rec):
    d = rec.data_dir / "t" / "c"
    meta = _lap(d, "old", uploaded=True, uploaded_at=NOW - 20 * DAY)
    removed = rec.cleanup_uploaded(14, now=NOW)
    assert removed == [d / "old.parquet"]
    assert not (d / "old.parquet").exists()
    assert meta.exists()
    data = _meta(meta)
    assert data["telemetry_removed"] is True and data["uploaded"] is True
    assert list(rec.pending_uploads()) == [], "a cleaned lap is not offered for upload again"
    assert rec.cleanup_uploaded(14, now=NOW) == [], "running it again changes nothing"


def test_recent_uploaded_lap_is_kept(rec):
    d = rec.data_dir / "t" / "c"
    _lap(d, "fresh", uploaded=True, uploaded_at=NOW - 13.9 * DAY)
    assert rec.cleanup_uploaded(14, now=NOW) == []
    assert (d / "fresh.parquet").exists()


def test_a_lap_the_server_has_not_accepted_is_never_deleted(rec):
    d = rec.data_dir / "t" / "c"
    _lap(d, "pending", uploaded=False, uploaded_at=NOW - 400 * DAY)
    _lap(d, "no_flag")
    _lap(d, "rejected", uploaded=True, rejected=True, rejected_reason="HTTP 400", uploaded_at=NOW - 400 * DAY)
    _lap(d, "tricky", uploaded="yes", uploaded_at=NOW - 400 * DAY)       # not the boolean True
    os.utime(d / "no_flag.json", (NOW - 400 * DAY, NOW - 400 * DAY))
    assert rec.cleanup_uploaded(1, now=NOW) == []
    for name in ("pending", "no_flag", "rejected", "tricky"):
        assert (d / f"{name}.parquet").exists(), name


def test_retention_zero_or_negative_keeps_everything(rec):
    d = rec.data_dir / "t" / "c"
    _lap(d, "ancient", uploaded=True, uploaded_at=1.0)
    for days in (0, -5, None):
        assert rec.cleanup_uploaded(days, now=NOW) == []
    assert (d / "ancient.parquet").exists()


def test_a_lap_from_before_v091_uses_the_sidecars_modification_time(rec):
    d = rec.data_dir / "t" / "c"
    old = _lap(d, "legacy_old", uploaded=True)           # no uploaded_at
    new = _lap(d, "legacy_new", uploaded=True)
    os.utime(old, (NOW - 30 * DAY, NOW - 30 * DAY))
    os.utime(new, (NOW - 1 * DAY, NOW - 1 * DAY))
    removed = rec.cleanup_uploaded(14, now=NOW)
    assert removed == [d / "legacy_old.parquet"]
    assert (d / "legacy_new.parquet").exists()


def test_a_hand_edited_sidecar_cannot_delete_anything_else(rec, tmp_path):
    d = rec.data_dir / "t" / "c"
    victim = tmp_path / "precious.parquet"
    victim.write_bytes(b"do not delete")
    outside = tmp_path / "notes.txt"
    outside.write_text("keep")
    cases = {
        "traversal": "../../../precious.parquet",
        "absolute": str(victim),
        "not_parquet": "notes.json",
        "subdir": "sub/dir.parquet",
        "empty": "",
        "number": 5,
        "none": None,
    }
    for name, target in cases.items():
        _lap(d, name, uploaded=True, uploaded_at=NOW - 99 * DAY, telemetry_file=target)
    assert rec.cleanup_uploaded(14, now=NOW) == []
    assert victim.read_bytes() == b"do not delete" and outside.read_text() == "keep"
    for name in cases:   # the laps' own files are untouched too: their sidecar names something else
        assert (d / f"{name}.parquet").exists(), name


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks on this platform")
def test_a_symlink_is_never_followed(rec, tmp_path):
    d = rec.data_dir / "t" / "c"
    victim = tmp_path / "elsewhere.parquet"
    victim.write_bytes(b"keep me")
    d.mkdir(parents=True)
    link = d / "linked.parquet"
    try:
        link.symlink_to(victim)
    except OSError:
        pytest.skip("symlinks not permitted")
    (d / "linked.json").write_text(json.dumps({"uploaded": True, "uploaded_at": NOW - 99 * DAY, "telemetry_file": "linked.parquet"}))
    assert rec.cleanup_uploaded(14, now=NOW) == []
    assert victim.read_bytes() == b"keep me"


def test_damaged_and_missing_files_do_not_stop_the_cleanup(rec):
    d = rec.data_dir / "t" / "c"
    (d).mkdir(parents=True)
    (d / "broken.json").write_text("{not json")
    (d / "list.json").write_text("[1, 2]")
    _lap(d, "gone", uploaded=True, uploaded_at=NOW - 30 * DAY)
    (d / "gone.parquet").unlink()                       # file already missing
    _lap(d, "ok", uploaded=True, uploaded_at=NOW - 30 * DAY)
    removed = rec.cleanup_uploaded(14, now=NOW)
    assert removed == [d / "ok.parquet"]
    assert _meta(d / "gone.json")["telemetry_removed"] is True


def test_new_local_fields_never_reach_the_server(rec, tmp_path):
    """The upload allowlist drops local bookkeeping (uploaded_at, telemetry_removed, ...)."""
    from client.uploader.uploader import Uploader

    d = rec.data_dir / "t" / "c"
    meta = _lap(d, "x", uploaded_at=1, telemetry_removed=False, car_class="Hypercar", car_model="Ferrari 499P",
                started_at=0.0, recorded_at=1, ambient_temp=20.0, track_temp=30.0, sample_count=10, session_type=10)
    envelope = Uploader(recorder=rec)._build_payload(meta)["envelope"]["metadata"]
    assert "uploaded_at" not in envelope and "telemetry_removed" not in envelope and "uploaded" not in envelope


# ---------------------------------------------------------------- the setting

def test_default_is_two_weeks(monkeypatch, tmp_path):
    monkeypatch.delenv("LMU_GARAGE_LOCAL_RETENTION_DAYS", raising=False)
    monkeypatch.setattr("client.config.CONFIG_PATH", tmp_path / "missing.json")
    assert DEFAULT_LOCAL_RETENTION_DAYS == 14
    assert local_retention_days() == 14


@pytest.mark.parametrize("raw,expected", [
    ("7", 7.0), ("30", 30.0), ("0", 0.0), ("0.5", 0.5), (" 21 ", 21.0),
    ("-1", 14.0), ("abc", 14.0), ("", 14.0), ("nan", 14.0), ("inf", 14.0), ("1e999", 14.0),
    ("99999", float(MAX_LOCAL_RETENTION_DAYS)),
])
def test_setting_from_the_environment_is_validated(monkeypatch, tmp_path, raw, expected):
    monkeypatch.setattr("client.config.CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("LMU_GARAGE_LOCAL_RETENTION_DAYS", raw)
    assert local_retention_days() == expected


def test_setting_from_the_config_file(monkeypatch, tmp_path):
    cfg = tmp_path / "client_config.json"
    monkeypatch.setattr("client.config.CONFIG_PATH", cfg)
    monkeypatch.delenv("LMU_GARAGE_LOCAL_RETENTION_DAYS", raising=False)
    cfg.write_text(json.dumps({"local_retention_days": 7}))
    assert local_retention_days() == 7
    cfg.write_text(json.dumps({"local_retention_days": "soon"}))
    assert local_retention_days() == 14
    cfg.write_text(json.dumps({"local_retention_days": True}))
    assert local_retention_days() == 14
    cfg.write_text("{broken")
    assert local_retention_days() == 14
    cfg.write_text(json.dumps({"local_retention_days": 7}))
    monkeypatch.setenv("LMU_GARAGE_LOCAL_RETENTION_DAYS", "3")
    assert local_retention_days() == 3, "the environment wins over the file"


def test_saved_client_config_survives_without_the_new_key(tmp_path, monkeypatch):
    """client_config.json is parsed into a fixed dataclass; the extra key must not break loading."""
    from client import config as cfg_module

    path = tmp_path / "client_config.json"
    path.write_text(json.dumps({"api_url": "https://x.example", "auth_token": "t", "client_secret": "s", "local_retention_days": 5}))
    monkeypatch.setattr(cfg_module, "CONFIG_PATH", path)
    loaded = cfg_module._from_file()
    assert loaded is not None and loaded.api_url == "https://x.example"


def test_saving_the_config_keeps_the_retention_setting(tmp_path, monkeypatch):
    """Reconnecting an account / a server migration rewrites client_config.json. A user who set
    "never delete" (0) must not be reset to the 14-day default by that."""
    from client import config as cfg_module

    monkeypatch.setattr(cfg_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_module, "CONFIG_PATH", tmp_path / "client_config.json")
    monkeypatch.delenv("LMU_GARAGE_LOCAL_RETENTION_DAYS", raising=False)
    (tmp_path / "client_config.json").write_text(json.dumps(
        {"api_url": "https://old.example", "auth_token": "t1", "client_secret": "s1", "local_retention_days": 0}))
    cfg_module.save_config(cfg_module.ClientConfig(api_url="https://new.example", auth_token="t2", client_secret="s2"))
    data = json.loads((tmp_path / "client_config.json").read_text())
    assert data["api_url"] == "https://new.example" and data["auth_token"] == "t2", "the new values are written"
    assert data["local_retention_days"] == 0
    assert local_retention_days() == 0, "still 'never delete'"
    # a fresh install (no file yet) and a corrupt old file both just write the three fields
    (tmp_path / "client_config.json").unlink()
    cfg_module.save_config(cfg_module.ClientConfig(api_url="u", auth_token="a", client_secret="b"))
    assert set(json.loads((tmp_path / "client_config.json").read_text())) == {"api_url", "auth_token", "client_secret"}
    (tmp_path / "client_config.json").write_text("{corrupt")
    cfg_module.save_config(cfg_module.ClientConfig(api_url="u", auth_token="a", client_secret="b"))
    assert json.loads((tmp_path / "client_config.json").read_text())["auth_token"] == "a"
