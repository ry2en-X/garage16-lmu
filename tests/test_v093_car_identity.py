"""V0.9.3 — a car is its MODEL (server/car_identity.py): every livery, car number and season
of one model counts as the same car; different specifications stay apart.

Covers the derivation rules, the learning of veh_filename -> model aliases from what the
client reports about the session's grid (and the conflict guard), the upload path end to
end, the migration on real rows (upgrade re-groups, downgrade restores byte for byte), the
report script, and the uploader's fallback for a server that predates these fields."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from server import car_identity as ci
from tests.helpers.v093 import clean_lap, headers, lap_row, register, upload_df

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- rules

@pytest.mark.parametrize("veh_file, expected", [
    ("397_25_499P", "499P (2025)"),          # the real recording: car #397, season 25, model 499P
    ("50_25_499P", "499P (2025)"),           # another number of the same model and season
    ("6_25_963", "963 (2025)"),
    ("3_25_963", "963 (2025)"),              # Penske #3 and #6: one car
    ("93_24_9X8_EVO", "9X8 EVO (2024)"),     # underscores in the model become spaces
    ("911_gt3_r", "911_gt3_r"),              # not the pattern: kept exactly (911 is a model, not a number)
    ("  TestCar ", "TestCar"),
    ("", None), (None, None),
])
def test_fallback_identity_drops_the_car_number_and_keeps_the_season(veh_file, expected):
    assert ci.fallback_identity(veh_file) == expected


def test_lmus_model_name_wins_over_the_file_name():
    assert ci.resolve("Ferrari 499P", "397_25_499P") == ci.Identity("Ferrari 499P", ci.SOURCE_MODEL)
    assert ci.resolve("  Ferrari   499P ", "50_25_499P").key == "Ferrari 499P", "whitespace does not split a model"
    assert ci.resolve(None, "397_25_499P") == ci.Identity("499P (2025)", ci.SOURCE_VEH_FILE)
    assert ci.resolve(None, "397_25_499P", lambda key: "Ferrari 499P") == ci.Identity("Ferrari 499P", ci.SOURCE_ALIAS)
    assert ci.resolve("", None) == ci.Identity(None, None)


def test_different_specifications_named_differently_by_lmu_stay_apart():
    """An Evo / a rebuilt aero version is a different car — automatically, as long as LMU
    names it differently (which is what its model name exists for)."""
    assert ci.resolve("McLaren 720S LMGT3", "59_25_720S").key != ci.resolve("McLaren 720S LMGT3 Evo", "59_25_720SEVO").key


def test_spec_rules_split_one_lmu_name_by_season_only_when_a_rule_says_so():
    rule = ci.SpecRule(model=r"Brand X", seasons=(2024, 2099), suffix="(2024 spec)", note="test only")
    assert ci.resolve("Brand X", "7_23_X", rules=[rule]).key == "Brand X"
    assert ci.resolve("Brand X", "7_24_X", rules=[rule]).key == "Brand X (2024 spec)"
    assert ci.SPEC_RULES == (), "no rule ships without real evidence (see car_identity.py)"


def test_the_migrations_copy_of_the_rule_matches_the_server():
    spec = importlib.util.spec_from_file_location("m0013", ROOT / "alembic/versions/0013_car_identity_and_lap_checks.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["m0013"] = module
    spec.loader.exec_module(module)
    for name in ["397_25_499P", "6_25_963", "911_gt3_r", "TestCar", "93_24_9X8_EVO", "", None, "1_2_x", "12345_25_X"]:
        assert module.fallback_identity(name) == ci.fallback_identity(name), name


def test_observations_take_the_lap_first_then_the_grid_once_per_group():
    pairs = ci.observations("397_25_499P", "Ferrari 499P", [
        {"veh_file": "50_25_499P", "model": "Ferrari 499P"},   # same group as the lap -> skipped
        {"veh_file": "6_25_963", "model": "Porsche 963"},
        {"veh_file": "7_25_GR010", "model": ""},              # no model name -> nothing to learn
        "garbage",
    ])
    assert pairs == [("499P (2025)", "Ferrari 499P", "lap"), ("963 (2025)", "Porsche 963", "grid")]


# ---------------------------------------------------------------- upload, end to end

def test_two_car_numbers_of_one_model_are_one_car_on_the_leaderboard(client):
    a, b = register(client, "Number 6"), register(client, "Number 3")
    upload_df(client, a, clean_lap(seed=1), lap_time=90.0, car_model="6_25_963", car_name="Porsche Penske 2025 #6")
    upload_df(client, b, clean_lap(seed=2), lap_time=89.0, car_model="3_25_963", car_name="Porsche Penske 2025 #3")
    assert client.get("/leaderboard/catalog/cars").json() == ["963 (2025)"]
    board = client.get("/leaderboard/car/Le Mans/963 (2025)").json()
    assert [row["driver_name"] for row in board] == ["Number 3", "Number 6"]


def test_a_093_upload_uses_the_model_name_and_regroups_older_laps(client):
    old, new = register(client, "Old Client"), register(client, "New Client")
    old_lap = upload_df(client, old, clean_lap(seed=3), car_model="397_25_499P")["lap_id"]
    assert lap_row(old_lap).car_model == "499P (2025)" and lap_row(old_lap).car_identity_source == "veh_file"

    new_lap = upload_df(
        client, new, clean_lap(seed=4), car_model="50_25_499P", vehicle_model="Ferrari 499P",
        grid_vehicles=[{"veh_file": "50_25_499P", "model": "Ferrari 499P", "class": "Hyper"},
                       {"veh_file": "6_25_963", "model": "Porsche 963", "class": "Hyper"}],
    )["lap_id"]
    row = lap_row(new_lap)
    assert (row.car_model, row.car_identity_source, row.car_model_raw, row.car_vehicle_model) == (
        "Ferrari 499P", "vehicle_model", "50_25_499P", "Ferrari 499P")
    # the old client's lap of the same model and season moved to the model name
    moved = lap_row(old_lap)
    assert (moved.car_model, moved.car_identity_source, moved.car_model_raw) == ("Ferrari 499P", "alias", "397_25_499P")
    assert client.get("/leaderboard/catalog/cars").json() == ["Ferrari 499P"]

    # a later old-client lap of that group gets the model name straight away
    later = upload_df(client, old, clean_lap(seed=5), car_model="51_25_499P", lap_number=2)["lap_id"]
    assert (lap_row(later).car_model, lap_row(later).car_identity_source) == ("Ferrari 499P", "alias")

    overview = client.get("/admin/car-identity", headers={"X-Admin-Token": "test-admin-token"}).json()
    keys = {a["key"]: a for a in overview["aliases"]}
    assert keys["499P (2025)"]["model"] == "Ferrari 499P" and keys["963 (2025)"]["source"] == "grid"
    assert overview["laps_by_source"] == {"alias": 2, "vehicle_model": 1}


def test_conflicting_reports_are_never_used_and_undo_the_regrouping(client):
    old, x, y = register(client, "Old"), register(client, "X"), register(client, "Y")
    old_lap = upload_df(client, old, clean_lap(seed=6), car_model="1_24_CAR")["lap_id"]
    upload_df(client, x, clean_lap(seed=7), car_model="2_24_CAR", vehicle_model="Brand Car")
    assert lap_row(old_lap).car_model == "Brand Car"
    # now the game reports a DIFFERENT model for the same veh_filename group
    upload_df(client, y, clean_lap(seed=8), car_model="3_24_CAR", vehicle_model="Brand Car Evo")
    assert (lap_row(old_lap).car_model, lap_row(old_lap).car_identity_source) == ("CAR (2024)", "veh_file")
    later = upload_df(client, old, clean_lap(seed=9), car_model="4_24_CAR", lap_number=2)["lap_id"]
    assert lap_row(later).car_model == "CAR (2024)", "an ambiguous group is not resolved by guessing"
    alias = [a for a in client.get("/admin/car-identity", headers={"X-Admin-Token": "test-admin-token"}).json()["aliases"]][0]
    assert alias["conflict"] is True and alias["conflict_with"] == "Brand Car Evo"


def test_grid_entries_are_validated(client):
    me = register(client, "Grid")
    resp = upload_df(client, me, clean_lap(seed=10), vehicle_model="Ferrari 499P",
                     grid_vehicles=[{"veh_file": "x" * 65, "model": "m"}], expect=400)
    assert "grid_vehicles" in resp["detail"]
    upload_df(client, me, clean_lap(seed=11), grid_vehicles=[{"veh_file": "1_25_A", "model": "A", "bogus": 1}], expect=400)


# ---------------------------------------------------------------- migration on real rows

def _alembic(*args, db_url):
    import os

    env = {**os.environ, "LMU_GARAGE_DB_URL": db_url, "LMU_GARAGE_ENV": "development"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_migration_0013_regroups_existing_laps_and_downgrade_restores_them(tmp_path):
    db = tmp_path / "m.db"
    url = f"sqlite:///{db}"
    assert _alembic("upgrade", "0012", db_url=url).returncode == 0
    con = sqlite3.connect(db)
    con.execute("INSERT INTO drivers (id, display_name, token_hash, client_secret, is_locked, email_verified) VALUES (1, 'D', 'h', 's', 0, 0)")
    rows = [("6_25_963", "Porsche Penske 2025 #6"), ("3_25_963", "Porsche Penske 2025 #3"), ("911_gt3_r", "911"), (None, "very old")]
    for i, (model, name) in enumerate(rows, start=1):
        con.execute(
            "INSERT INTO laps (id, driver_id, track_name, car_name, car_model, session_type, lap_number, lap_time, sector_times, "
            "is_valid, client_claimed_valid, started_at, recorded_at, telemetry_hash, telemetry_path) "
            "VALUES (?, 1, 'Le Mans', ?, ?, 10, 1, 200.0, '[1,1,1]', 1, 1, 0, 0, ?, '/x')",
            (i, name, model, f"h{i}"),
        )
    con.commit()
    con.close()

    up = _alembic("upgrade", "head", db_url=url)
    assert up.returncode == 0, up.stderr
    con = sqlite3.connect(db)
    got = con.execute("SELECT car_model, car_model_raw, car_identity_source FROM laps ORDER BY id").fetchall()
    assert got == [("963 (2025)", "6_25_963", "veh_file"), ("963 (2025)", "3_25_963", "veh_file"),
                   ("911_gt3_r", "911_gt3_r", "veh_file"), (None, None, None)]
    con.close()

    down = _alembic("downgrade", "0012", db_url=url)
    assert down.returncode == 0, down.stderr
    con = sqlite3.connect(db)
    assert [r[0] for r in con.execute("SELECT car_model FROM laps ORDER BY id")] == ["6_25_963", "3_25_963", "911_gt3_r", None]
    assert "car_model_raw" not in [c[1] for c in con.execute("PRAGMA table_info(laps)")]
    con.close()


# ---------------------------------------------------------------- report script

def test_the_report_is_a_dry_run_until_told_otherwise(client, capsys):
    from scripts import car_identity_report
    from server.database import SessionLocal
    from server.models import Lap

    me = register(client, "Report")
    lap_id = upload_df(client, me, clean_lap(seed=12), car_model="9_25_XYZ")["lap_id"]
    with SessionLocal() as db:   # simulate an identity derived by older rules
        db.query(Lap).filter(Lap.id == lap_id).update({Lap.car_model: "9_25_XYZ", Lap.car_identity_source: None})
        db.commit()
    assert car_identity_report.main([]) == 0
    out = capsys.readouterr().out
    assert "9_25_XYZ" in out and "XYZ (2025)" in out and "Dry run" in out
    assert lap_row(lap_id).car_model == "9_25_XYZ"
    assert car_identity_report.main(["--apply"]) == 0
    assert (lap_row(lap_id).car_model, lap_row(lap_id).car_identity_source) == ("XYZ (2025)", "veh_file")


# ---------------------------------------------------------------- uploader against an older server

def test_the_uploader_retries_once_without_the_new_fields_for_an_older_server(tmp_path, monkeypatch):
    import client.uploader.uploader as up
    from client.telemetry.recorder import LapRecorder

    clean_lap(seed=13).to_parquet(tmp_path / "lap.parquet", index=False)
    meta = {"track_name": "T", "car_name": "C", "session_type": 10, "lap_number": 1, "lap_time": 90.0,
            "sector_times": [30, 30, 30], "is_valid": True, "started_at": 0, "recorded_at": 1, "sample_count": 3000,
            "telemetry_file": "lap.parquet", "uploaded": False, "vehicle_model": "Ferrari 499P", "tail_samples": 3,
            "grid_vehicles": [{"veh_file": "1_25_A", "model": "A", "class": "H"}]}
    (tmp_path / "lap.json").write_text(json.dumps(meta), encoding="utf-8")

    sent = []

    class Resp:
        def __init__(self, status, text, body=None):
            self.status_code, self.text, self._body = status, text, body or {}

        def json(self):
            return self._body

    def fake_post(url, data=None, timeout=None, headers=None):
        body = b"".join(iter(data))
        envelope = json.loads(body.split(b'name="envelope"\r\n\r\n')[1].split(b"\r\n--")[0])
        sent.append(set(envelope["metadata"]))
        if "vehicle_model" in envelope["metadata"]:
            return Resp(400, '{"detail":"envelope.metadata is invalid: vehicle_model: Extra inputs are not permitted"}')
        return Resp(200, "{}", {"status": "ok", "lap_id": 5})

    monkeypatch.setattr(up.requests, "post", fake_post)
    monkeypatch.setattr(up.time, "sleep", lambda s: None)
    uploader = up.Uploader(recorder=LapRecorder(data_dir=str(tmp_path)), auth_token="t", client_secret="s")
    assert uploader.upload_one(tmp_path / "lap.json") is True
    assert "vehicle_model" in sent[0] and "vehicle_model" not in sent[1]
    assert uploader.legacy_metadata is True
    assert json.loads((tmp_path / "lap.json").read_text())["uploaded"] is True
    assert "rejected" not in json.loads((tmp_path / "lap.json").read_text())
