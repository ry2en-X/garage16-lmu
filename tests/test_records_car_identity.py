"""Tests for server/records.py's car-identity fix (V0.6.9).

Before this fix, PR/WR/TEAM_BEST were computed by comparing Lap.car_name
(which includes team/livery/car number, e.g. "GT3 Custom #12" vs. "GT3
Custom #7"), while the leaderboard (routers/leaderboard.py) and the team
dashboard (routers/teams.py) group by Lap.car_model (the exact vehicle
model, ignoring livery/number). That mismatch meant a driver switching
livery/number on the same physical car started a brand-new PR/WR chain,
and a TEAM_BEST announcement could fire (or fail to fire) out of step
with what the team dashboard's own car_model-grouped "current record"
query would show for the exact same lap.
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _upload_lap(
    client,
    account,
    lap_time=90.0,
    car_name="GT3 Custom #12",
    car_class="GT3",
    car_model="Porsche 911 GT3 R",
    track_name="Monza",
):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        n = 50
        lap_dist_m = 4500.0
        speed = lap_dist_m / lap_time  # must match lap_dist/lap_time or validation rejects the lap
        df = pd.DataFrame({
            "t": np.linspace(0, lap_time, n), "lap_dist": np.linspace(0, lap_dist_m, n),
            "speed": np.full(n, speed), "throttle": np.full(n, 0.8), "brake": np.zeros(n),
            "clutch": np.zeros(n), "steering": np.zeros(n), "gear": np.full(n, 4),
            "rpm": np.full(n, 6000.0), "fuel": np.linspace(50, 48, n),
        })
        parquet_path = tmp_dir / "lap.parquet"
        df.to_parquet(parquet_path, index=False)
        meta = {
            "track_name": track_name, "car_name": car_name, "car_class": car_class,
            "session_type": 10, "lap_number": 1, "lap_time": lap_time,
            "sector_times": [lap_time / 3, lap_time / 3, lap_time / 3],
            "is_valid": True, "started_at": 0.0, "recorded_at": 1700000000,
            "sample_count": n, "telemetry_file": "lap.parquet", "uploaded": False,
        }
        if car_model is not None:
            meta["car_model"] = car_model
        meta_path = tmp_dir / "lap.json"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token=account["auth_token"], client_secret=account["client_secret"],
        )
        payload = uploader._build_payload(meta_path)
        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers={"Authorization": f"Bearer {account['auth_token']}"},
        )
    assert resp.status_code == 200
    return resp.json()


def _record_types(upload_response):
    """UploadResponse doesn't expose record events to the client — they're
    an internal side effect (DB rows + Discord announcements), not part of
    the upload contract. Read them straight from the DB instead."""
    from server.database import SessionLocal
    from server.models import RecordEvent

    db = SessionLocal()
    try:
        rows = (
            db.query(RecordEvent.record_type)
            .filter(RecordEvent.lap_id == upload_response["lap_id"])
            .all()
        )
        return sorted(r[0] for r in rows)
    finally:
        db.close()


def test_pb_continues_across_livery_change_on_same_car_model(client):
    """A driver's PR chain must survive switching livery/car number on the
    same underlying car_model — that's the whole point of car_model
    existing separately from car_name."""
    account = _register(client)

    first = _upload_lap(client, account, lap_time=95.0, car_name="GT3 Custom #12")
    assert "PR" in _record_types(first)

    # Same car_model, different livery/number, same driver — must be
    # compared against the 95.0s lap above, not treated as a fresh car.
    second = _upload_lap(client, account, lap_time=92.0, car_name="GT3 Custom #7")
    assert "PR" in _record_types(second), (
        "faster lap in the same car_model under a different livery must "
        "still count as a new personal best"
    )

    # A slower lap in yet another livery of the same car_model must NOT
    # set a new PR — it's still worse than the 92.0s best.
    third = _upload_lap(client, account, lap_time=93.0, car_name="GT3 Custom #99")
    assert "PR" not in _record_types(third)


def test_team_best_agrees_with_team_dashboards_own_record_query(client):
    """The TEAM_BEST event fired at upload time must agree with what
    GET /teams/{id}/records (car_model-grouped) reports as the team's
    current record for that exact lap — they must never disagree."""
    owner = _register(client, "Owner")
    resp = client.post("/teams", json={"name": "Test Team"}, headers=_auth(owner))
    assert resp.status_code == 200
    team = resp.json()

    mate = _register(client, "Mate")
    join = client.post(
        "/teams/join", json={"invite_code": team["invite_code"]}, headers=_auth(mate)
    )
    assert join.status_code == 200

    # Owner sets an initial time in one livery.
    _upload_lap(client, owner, lap_time=100.0, car_name="Team Car #1")

    # Teammate beats it in a DIFFERENT livery of the same car_model.
    second = _upload_lap(client, mate, lap_time=98.0, car_name="Team Car #2")
    assert "TEAM_BEST" in _record_types(second), (
        "a faster lap in a different livery of the same car_model must "
        "still set a new TEAM_BEST"
    )

    records = client.get(f"/teams/{team['id']}/records", headers=_auth(owner))
    assert records.status_code == 200
    rows = records.json()
    assert len(rows) == 1
    assert rows[0]["lap_time"] == 98.0, (
        "the team dashboard's own record query must show the same lap "
        "that just triggered the TEAM_BEST event, not disagree with it"
    )


def test_pre_v0_6_3_laps_without_car_model_still_group_by_car_name(client):
    """Backward compatibility: laps from clients older than V0.6.3 have no
    car_model at all. Their PR/WR history must keep working exactly as it
    did before this fix — grouped by car_name, since that's all they have.
    """
    account = _register(client)

    first = _upload_lap(client, account, lap_time=95.0, car_name="Old Client Car", car_model=None, car_class=None)
    assert "PR" in _record_types(first)

    second = _upload_lap(client, account, lap_time=93.0, car_name="Old Client Car", car_model=None, car_class=None)
    assert "PR" in _record_types(second)

    # A different car_name with no car_model is a genuinely different car
    # under the old (pre-V0.6.3) rules — must not share a PR chain.
    different_car = _upload_lap(client, account, lap_time=200.0, car_name="Different Old Car", car_model=None, car_class=None)
    assert "PR" in _record_types(different_car), (
        "a differently-named car with no car_model info is still a fresh "
        "PR chain under the old car_name-only comparison"
    )


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}
