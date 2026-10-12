"""V0.9.3.1 — car identity (cut model names, safe alias learning, what to check by hand) and
the theoretical best (one car model, track conditions never mixed silently)."""
from __future__ import annotations

import numpy as np
import pytest

from server import car_identity as ci
from server import lap_insights as li
from tests.helpers.v093 import clean_lap, headers, lap_row, register, upload_df

ADMIN = {"X-Admin-Token": "test-admin-token"}


# ---------------------------------------------------------------- cut model names

def test_lmu_cuts_model_names_at_29_characters_and_known_ones_are_repaired():
    for cut, full in ci.MODEL_NAME_FIXES.items():
        assert len(cut) == ci.MODEL_NAME_MAX_CHARS, "every table entry is a name of the cut length"
        assert ci.model_identity(cut) == full
        assert ci.model_identity(full) == full, "idempotent"
    assert ci.model_identity("Ferrari 499P") == "Ferrari 499P"


def test_an_unknown_cut_name_is_marked_never_passed_off_as_complete():
    cut = "Someb Brand Long Model GT3 Ev"            # 29 characters, not in the table
    assert len(cut) == 29 and ci.is_cut(cut)
    assert ci.model_identity(cut) == cut + "…"
    assert ci.model_identity(cut + "…") == cut + "…"


def test_the_client_marks_a_cut_name_even_when_lmu_cut_right_after_a_space():
    from client.telemetry.reader import CUT_MARK, MODEL_NAME_MAX_CHARS, model_text

    assert (MODEL_NAME_MAX_CHARS, CUT_MARK) == (ci.MODEL_NAME_MAX_CHARS, ci.TRUNCATED_MARK)
    assert model_text(b"Ferrari 499P\x00\x00\x00") == "Ferrari 499P"
    assert model_text(b"Lamborghini Huracan LMGT3 Evo\x00") == "Lamborghini Huracan LMGT3 Evo…"
    assert ci.model_identity("Lamborghini Huracan LMGT3 Evo…") == "Lamborghini Huracan LMGT3 Evo2"
    spaced = model_text(b"Brandname Racing Model LMGT3 \x00")      # 29 bytes, the last one a space
    assert spaced == "Brandname Racing Model LMGT3…", "stripped, the cut would be invisible"
    assert ci.model_identity(spaced) == "Brandname Racing Model LMGT3…"


def test_two_variants_cut_to_the_same_name_share_an_identity_and_are_listed_for_checking():
    from client.telemetry.reader import model_text

    # what the 30-byte limit does to two names that differ only after character 29
    a = model_text("Brandname Racing Model LMGT3 Evo".encode()[:29] + b"\x00")
    b = model_text("Brandname Racing Model LMGT3 Evo2".encode()[:29] + b"\x00")
    assert a == b and ci.model_identity(a) == ci.model_identity(b)
    suspects = ci.identity_suspects([
        {"identity": ci.model_identity(a), "veh_file": "1_25_Model_Evo", "car_class": "GT3", "laps": 3},
        {"identity": ci.model_identity(b), "veh_file": "2_25_Model_Evo2", "car_class": "GT3", "laps": 2},
    ])
    assert suspects == [{"identity": ci.model_identity(a), "reasons": ["cut_name", "several_models"], "laps": 5,
                         "veh_models": ["Model Evo", "Model Evo2"], "classes": ["GT3"]}]


def test_identical_model_names_are_one_car_unless_a_spec_rule_splits_them():
    # LMU naming two specifications alike is NOT guessed away: the same name is one identity …
    assert ci.model_identity("Peugeot 9X8", "93_23_9X8") == ci.model_identity("Peugeot 9X8", "93_24_9X8")
    # … until a rule with evidence splits them (SPEC_RULES stays empty without real data)
    rule = ci.SpecRule(model="Peugeot 9X8", seasons=(2024, 2026), suffix="(2024 spec)", note="test only")
    assert ci.model_identity("Peugeot 9X8", "93_24_9X8", [rule]) == "Peugeot 9X8 (2024 spec)"
    assert ci.model_identity("Peugeot 9X8", "93_23_9X8", [rule]) == "Peugeot 9X8"
    assert ci.SPEC_RULES == ()


def test_identity_suspects_flag_classes_and_ignore_clean_groups():
    rows = [{"identity": "Car A", "veh_file": "1_25_A", "car_class": "GT3", "laps": 1},
            {"identity": "Car A", "veh_file": "2_25_A", "car_class": "GTE", "laps": 1},
            {"identity": "Car B", "veh_file": "3_25_B", "car_class": "GT3", "laps": 4}]
    assert [(s["identity"], s["reasons"]) for s in ci.identity_suspects(rows)] == [("Car A", ["several_classes"])]


# ---------------------------------------------------------------- learning aliases safely

def test_a_pair_seen_only_in_grid_lists_is_used_from_the_second_upload(client):
    old, a, b = register(client, "OldClient"), register(client, "GridA"), register(client, "GridB")
    old_lap = upload_df(client, old, clean_lap(seed=61), car_model="7_25_GRIDCAR")["lap_id"]
    grid = [{"veh_file": "8_25_GRIDCAR", "model": "Grid Car", "class": "GT3"}]
    upload_df(client, a, clean_lap(seed=62), car_model="1_25_OTHER", vehicle_model="Other Car", grid_vehicles=grid)
    assert lap_row(old_lap).car_model == "GRIDCAR (2025)", "one grid sighting is not enough"
    alias = {x["key"]: x for x in client.get("/admin/car-identity", headers=ADMIN).json()["aliases"]}["GRIDCAR (2025)"]
    assert alias["used"] is False and alias["seen"] == 1
    upload_df(client, b, clean_lap(seed=63), car_model="2_25_OTHER", vehicle_model="Other Car", grid_vehicles=grid)
    assert (lap_row(old_lap).car_model, lap_row(old_lap).car_identity_source) == ("Grid Car", "alias")


def test_the_lap_s_own_car_is_trusted_at_once_and_stored_names_are_repaired(client):
    old, new = register(client, "OldLambo"), register(client, "NewLambo")
    old_lap = upload_df(client, old, clean_lap(seed=64), car_model="19_25_HURACAN")["lap_id"]
    new_lap = upload_df(client, new, clean_lap(seed=65), car_model="63_25_HURACAN",
                        vehicle_model="Lamborghini Huracan LMGT3 Evo")["lap_id"]
    assert lap_row(new_lap).car_model == "Lamborghini Huracan LMGT3 Evo2"
    assert lap_row(new_lap).car_vehicle_model == "Lamborghini Huracan LMGT3 Evo", "the raw value is kept"
    assert lap_row(old_lap).car_model == "Lamborghini Huracan LMGT3 Evo2"
    overview = client.get("/admin/car-identity", headers=ADMIN).json()
    assert overview["rules_version"] == ci.IDENTITY_RULES_VERSION
    assert overview["suspects"] == []


# ---------------------------------------------------------------- track conditions per lap

def _surfaces(df, wet_from=None, wet_to=None):
    for w in ("fl", "fr", "rl", "rr"):
        col = np.zeros(len(df), dtype=np.float32)
        if wet_from is not None:
            col[wet_from:wet_to] = 1.0
        df[f"surface_type_{w}"] = col
    return df


def test_the_wet_share_of_a_lap_is_stored_and_classified():
    n = 1000
    t = np.linspace(0, 100, n)
    cols = {"t": t, "lap_dist": np.linspace(0, 4000, n)}
    for w in ("fl", "fr", "rl", "rr"):
        cols[f"surface_type_{w}"] = np.where(np.arange(n) >= 700, 1.0, 0.0)
    stats = li.compute(cols, lap_time=100.0)
    assert stats["conditions"]["wet_share"] == pytest.approx(0.3, abs=0.01)
    assert li.condition_class(stats) == "mixed"
    assert li.condition_class({"conditions": {"wet_share": 0.0}}) == "dry"
    assert li.condition_class({"conditions": {"wet_share": 0.9}}) == "wet"
    assert li.condition_class({}) == "unknown" and li.condition_class(None) == "unknown"


def test_theoretical_best_needs_a_car_model(client):
    me = register(client, "NoCar")
    assert client.get("/telemetry/analysis/theoretical-best", params={"track_name": "Le Mans"},
                      headers=headers(me)).status_code == 422


def test_theoretical_best_never_mixes_dry_and_wet_without_saying_so(client):
    me = register(client, "Weather")
    dry = [upload_df(client, me, _surfaces(clean_lap(seed=70 + i, lap_time=lt)), lap_time=lt, lap_number=i + 1,
                     recorded_at=1_700_000_000 + 100 * i)["lap_id"] for i, lt in enumerate([90.0, 90.5])]
    wet = upload_df(client, me, _surfaces(clean_lap(seed=79, lap_time=99.0), 0, None), lap_time=99.0, lap_number=5,
                    recorded_at=1_700_000_900)["lap_id"]
    params = {"track_name": "Le Mans", "car_model": "499P (2025)"}
    res = client.get("/telemetry/analysis/theoretical-best", params=params, headers=headers(me)).json()
    assert res["calculated"] is True
    c = res["conditions"]
    assert c["selected"] == "dry" and c["excluded_laps"] == 1
    assert c["groups"] == {"dry": {"laps": 2, "best": 90.0}, "wet": {"laps": 1, "best": 99.0}}
    assert res["result"]["laps_used"] == 2 and str(wet) not in res["result"]["from_laps"]

    res = client.get("/telemetry/analysis/theoretical-best", params={**params, "conditions": "wet"}, headers=headers(me)).json()
    assert res["result"]["laps_used"] == 1 and res["result"]["reference_lap_id"] == wet

    res = client.get("/telemetry/analysis/theoretical-best", params={**params, "conditions": "all"}, headers=headers(me)).json()
    assert res["conditions"]["mixed_on_request"] is True and res["result"]["laps_used"] == 3
    assert set(map(int, res["result"]["from_laps"])) <= set(dry) | {wet}


def test_laps_without_condition_data_are_counted_not_hidden(client):
    me = register(client, "Unknown")
    df = clean_lap(seed=80)
    for w in ("fl", "fr", "rl", "rr"):
        if f"surface_type_{w}" in df:
            del df[f"surface_type_{w}"]
    upload_df(client, me, df, lap_time=90.0)
    upload_df(client, me, _surfaces(clean_lap(seed=81, lap_time=91.0)), lap_time=91.0, lap_number=2, recorded_at=1_700_000_100)
    res = client.get("/telemetry/analysis/theoretical-best", params={"track_name": "Le Mans", "car_model": "499P (2025)"},
                     headers=headers(me)).json()
    c = res["conditions"]
    assert c["selected"] == "unknown", "the fastest lap has no condition data: its group is used"
    assert c["laps_without_conditions"] == 1 and c["excluded_laps"] == 1 and c["groups"]["dry"]["laps"] == 1


def test_conditions_summary_reports_varying_grip_and_temperature():
    laps = [{"lap_time": 90.0, "conditions": "dry", "grip": 2, "track_temp": 25.0},
            {"lap_time": 91.0, "conditions": "dry", "grip": 3, "track_temp": 38.0}]
    summary = li.conditions_summary(laps)
    assert summary["grip_levels"] == [2, 3] and summary["track_temp_c"] == [25.0, 38.0] and summary["varying"] is True
    assert li.conditions_summary(laps[:1])["varying"] is False
