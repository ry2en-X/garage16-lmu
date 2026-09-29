"""Readable class names (V0.8.9): LMU's raw "Hyper" becomes "Hypercar" (and the
other classes get consistent names), every class exists before its first lap,
unknown classes are still learned automatically, old links keep working, and
migration 0012 rewrites existing data (and undoes it on downgrade)."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from server import catalog
from tests.test_moderation import _upload_lap

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------ the mapping

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hyper", "Hypercar"), ("hyper", "Hypercar"), ("HYPERCAR", "Hypercar"), ("  Hyper ", "Hypercar"),
        ("LMP2_ELMS", "LMP2 ELMS"), ("LMP2ELMS", "LMP2 ELMS"), ("lmp2 elms", "LMP2 ELMS"),
        ("LMP2_WEC", "LMP2 WEC"), ("LMP2WEC", "LMP2 WEC"),
        ("LMP3", "LMP3"), ("GTE", "GTE"), ("LMGTE", "GTE"),
        ("GT3", "GT3"), ("LMGT3", "GT3"), ("lmgt3", "GT3"),
    ],
)
def test_lmu_class_strings_map_to_readable_names(raw, expected):
    assert catalog.canonical_class(raw) == expected


@pytest.mark.parametrize("raw", ["SomeNewClass", "LMP2", "Cup 911", "GT4"])
def test_unknown_classes_are_kept_exactly_as_sent_so_they_are_learned_not_lost(raw):
    assert catalog.canonical_class(raw) == raw


def test_blank_and_none_classes_stay_empty():
    assert catalog.canonical_class(None) is None
    assert catalog.canonical_class("   ") is None


def test_the_six_known_classes_and_their_order():
    assert catalog.known_class_names() == ["Hypercar", "LMP2 WEC", "LMP2 ELMS", "LMP3", "GTE", "GT3"]


# ----------------------------------------------------------- through the API

def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _upload(client, account, track, car_class, car_model="397_25_499P", lap_time=90.0, car_name="499P #1"):
    from tests.test_catalog import _upload as upload

    return upload(client, account, track, car_name, car_class, car_model, lap_time=lap_time)


def test_a_hyper_upload_is_stored_as_hypercar_and_keeps_the_raw_value(client):
    from server.database import SessionLocal
    from server.models import Lap

    account = _register(client)
    _upload(client, account, "Le Mans", "Hyper")
    db = SessionLocal()
    try:
        lap = db.query(Lap).one()
        assert lap.car_class == "Hypercar"
        assert lap.car_class_raw == "Hyper"
    finally:
        db.close()


def test_old_links_and_the_bot_can_still_say_hyper(client):
    """/leaderboard/class/<track>/Hyper (bookmarks, Discord, older frontends)
    returns the same board as /Hypercar."""
    account = _register(client)
    _upload(client, account, "Le Mans", "Hyper")
    hyper = client.get("/leaderboard/class/Le%20Mans/Hyper").json()
    hypercar = client.get("/leaderboard/class/Le%20Mans/Hypercar").json()
    assert hyper == hypercar and len(hyper) == 1
    assert client.get("/leaderboard/catalog/cars", params={"track_name": "Le Mans", "car_class": "Hyper"}).json() == ["397_25_499P"]


def test_the_team_class_board_also_accepts_the_old_name(client):
    account = _register(client)
    team = client.post("/teams", json={"name": "Aliases"}, headers={"Authorization": f"Bearer {account['auth_token']}"}).json()
    _upload(client, account, "Le Mans", "Hyper")
    headers = {"Authorization": f"Bearer {account['auth_token']}"}
    old = client.get(f"/teams/{team['id']}/leaderboard/class/Le%20Mans/Hyper", headers=headers).json()
    new = client.get(f"/teams/{team['id']}/leaderboard/class/Le%20Mans/Hypercar", headers=headers).json()
    assert old == new and len(new) == 1


def test_class_options_lists_all_six_classes_before_any_lap_exists(client):
    options = client.get("/leaderboard/catalog/class-options").json()
    assert [o["name"] for o in options] == ["Hypercar", "LMP2 WEC", "LMP2 ELMS", "LMP3", "GTE", "GT3"]
    assert all(o["lap_count"] == 0 for o in options)


def test_class_options_count_valid_laps_per_track(client):
    account = _register(client)
    _upload(client, account, "Fuji Speedway", "GT3", car_model="gt3_car", lap_time=100.0)
    _upload(client, account, "Fuji Speedway", "Hyper", car_model="hyper_car", lap_time=95.0)
    _upload(client, account, "Monza", "GT3", car_model="gt3_car", lap_time=101.0)
    at_fuji = {o["name"]: o["lap_count"] for o in client.get("/leaderboard/catalog/class-options", params={"track_name": "Fuji Speedway"}).json()}
    assert at_fuji["GT3"] == 1 and at_fuji["Hypercar"] == 1 and at_fuji["GTE"] == 0
    overall = {o["name"]: o["lap_count"] for o in client.get("/leaderboard/catalog/class-options").json()}
    assert overall["GT3"] == 2


def test_a_class_we_have_never_heard_of_appears_by_itself(client):
    account = _register(client)
    _upload(client, account, "Monza", "BrandNewClass", car_model="new_car")
    names = [o["name"] for o in client.get("/leaderboard/catalog/class-options").json()]
    assert names[:6] == catalog.known_class_names() and "BrandNewClass" in names


def test_invalid_laps_do_not_count_toward_class_options(client):
    from server.database import SessionLocal
    from server.models import Lap

    account = _register(client)
    _upload(client, account, "Monza", "GT3", car_model="gt3_car")
    db = SessionLocal()
    try:
        db.query(Lap).update({"is_valid": False})
        db.commit()
    finally:
        db.close()
    assert {o["name"]: o["lap_count"] for o in client.get("/leaderboard/catalog/class-options").json()}["GT3"] == 0


# ------------------------------------------------------------ migration 0012

def _load_migration():
    spec = importlib.util.spec_from_file_location("m0012", ROOT / "alembic" / "versions" / "0012_readable_car_classes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migrations_alias_copy_matches_server_catalog():
    """The migration keeps its own copy of the alias table (it must keep
    working even if application code changes) — this pins it to the app's."""
    migration = _load_migration()
    app_aliases = {alias: display for display, aliases in catalog.CLASSES for alias in aliases}
    assert migration._ALIASES == app_aliases
    for raw in ("Hyper", "lmp2_elms", "LMGT3", "GTE", "Unknown", "  gt3 "):
        assert migration._canonical(raw) == catalog.canonical_class(raw)


def _alembic(*args, db_url):
    # Start from the real environment (not a hardcoded Linux PATH) so this
    # also works on Windows — see tests/test_migrations.py's _run_alembic
    # docstring for why replacing PATH entirely breaks the subprocess there.
    env = {**os.environ, "LMU_GARAGE_DB_URL": db_url, "LMU_GARAGE_ENV": "development"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_migration_rewrites_existing_laps_keeps_the_raw_value_and_downgrades_cleanly(tmp_path):
    db_file = tmp_path / "m.db"
    url = f"sqlite:///{db_file}"
    assert _alembic("upgrade", "0011", db_url=url).returncode == 0

    con = sqlite3.connect(db_file)
    columns = con.execute("PRAGMA table_info(laps)").fetchall()  # cid, name, type, notnull, default, pk
    required = []
    for _, name, ctype, notnull, default, pk in columns:
        if notnull and default is None and not pk:
            required.append((name, (ctype or "").upper()))

    def insert(i, raw):
        """One lap row with unique values (laps has unique constraints on
        (driver_id, telemetry_hash) etc.) and the given class."""
        values = {}
        for name, t in required:
            values[name] = (
                i if "INT" in t or "BOOL" in t
                else float(i) if "FLOAT" in t or "REAL" in t or "NUM" in t
                else "2026-01-01 00:00:00" if "DATE" in t or "TIME" in t
                else f"v{i}"
            )
        values.update(track_name="T", car_name="C", car_class=raw)
        con.execute(f"INSERT INTO laps ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", list(values.values()))

    for i, raw in enumerate(["Hyper", "GT3", "LMP2_ELMS", "NewThing", None]):  # None = a lap from an old client
        insert(i + 1, raw)
    con.commit()
    con.close()

    assert _alembic("upgrade", "head", db_url=url).returncode == 0
    con = sqlite3.connect(db_file)
    rows = sorted(con.execute("SELECT car_class_raw, car_class FROM laps WHERE car_class IS NOT NULL").fetchall())
    assert rows == sorted([("Hyper", "Hypercar"), ("GT3", "GT3"), ("LMP2_ELMS", "LMP2 ELMS"), ("NewThing", "NewThing")])
    assert con.execute("SELECT count(*) FROM laps WHERE car_class IS NULL AND car_class_raw IS NULL").fetchone()[0] == 1  # old-client lap untouched
    con.close()

    assert _alembic("downgrade", "0011", db_url=url).returncode == 0
    con = sqlite3.connect(db_file)
    assert sorted(r[0] for r in con.execute("SELECT car_class FROM laps WHERE car_class IS NOT NULL").fetchall()) == sorted(["Hyper", "GT3", "LMP2_ELMS", "NewThing"])
    assert "car_class_raw" not in [c[1] for c in con.execute("PRAGMA table_info(laps)").fetchall()]
    con.close()
