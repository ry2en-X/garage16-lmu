"""V1.0.3: GET /api/v1/public/drivers/{id}/progress — a driver's best lap per day, per track and car model."""

from __future__ import annotations

from datetime import datetime, timezone

from server.core.database import SessionLocal
from server.core.models import Lap

URL = "/api/v1/public/drivers/{}/progress"


def _ts(day: str, hour: int = 12) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=timezone.utc).timestamp())


def _register(client, name):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()["driver_id"]


def _add(driver_id, day, lap_time, track="Monza", car_model="499P", valid=True, review=None, hour=12):
    db = SessionLocal()
    try:
        db.add(Lap(
            driver_id=driver_id, track_name=track, car_name=f"{car_model} #1", car_class="Hypercar", car_model=car_model,
            session_type=10, lap_number=1, lap_time=lap_time, sector_times=[1, 1, 1], is_valid=valid,
            review_status=review, started_at=0.0, recorded_at=_ts(day, hour), sample_count=10,
            telemetry_hash=f"h{driver_id}-{day}-{lap_time}-{hour}-{track}-{car_model}", telemetry_path="x",
        ))
        db.commit()
    finally:
        db.close()


def test_days_are_aggregated_best_median_and_count(client):
    d = _register(client, "Prog1")
    _add(d, "2026-11-01", 100.0)
    _add(d, "2026-11-01", 98.0, hour=14)
    _add(d, "2026-11-01", 99.0, hour=15)
    _add(d, "2026-11-05", 97.5)
    body = client.get(URL.format(d), params={"track": "Monza", "car_model": "499P"}).json()
    assert body["days"] == [
        {"day": "2026-11-01", "best": 98.0, "median": 99.0, "laps": 3},
        {"day": "2026-11-05", "best": 97.5, "median": 97.5, "laps": 1},
    ]
    assert body["combos"][0]["laps"] == 4


def test_only_official_valid_laps_count(client):
    d = _register(client, "Prog2")
    _add(d, "2026-11-01", 100.0)
    _add(d, "2026-11-02", 90.0, valid=False)
    _add(d, "2026-11-03", 91.0, review="held")
    _add(d, "2026-11-04", 92.0, review="quarantined")
    _add(d, "2026-11-06", 99.0)
    body = client.get(URL.format(d), params={"track": "Monza", "car_model": "499P"}).json()
    assert [x["day"] for x in body["days"]] == ["2026-11-01", "2026-11-06"]
    assert min(x["best"] for x in body["days"]) == 99.0


def test_combos_need_two_valid_laps_and_default_is_most_recent(client):
    d = _register(client, "Prog3")
    _add(d, "2026-10-01", 100.0)
    _add(d, "2026-10-02", 99.0)
    _add(d, "2026-11-01", 80.0, track="Spa", car_model="296")
    _add(d, "2026-11-02", 79.0, track="Spa", car_model="296")
    _add(d, "2026-11-03", 120.0, track="Imola", car_model="296")  # one lap only
    _add(d, "2026-11-04", 95.0, track="Fuji", car_model="296", valid=False)
    _add(d, "2026-11-05", 94.0, track="Fuji", car_model="296")
    body = client.get(URL.format(d)).json()
    assert [(c["track_name"], c["car_model"]) for c in body["combos"]] == [("Spa", "296"), ("Monza", "499P")]
    assert body["selected"] == {"track_name": "Spa", "car_model": "296"}
    assert [x["day"] for x in body["days"]] == ["2026-11-01", "2026-11-02"]


def test_unknown_combination_is_empty_and_no_laps_at_all(client):
    d = _register(client, "Prog4")
    body = client.get(URL.format(d), params={"track": "Nowhere", "car_model": "X"}).json()
    assert body["days"] == [] and body["combos"] == []
    empty = client.get(URL.format(d)).json()
    assert empty == {"combos": [], "selected": None, "days": []}


def test_other_drivers_laps_are_not_mixed_in(client):
    a, b = _register(client, "ProgA"), _register(client, "ProgB")
    _add(a, "2026-11-01", 100.0)
    _add(a, "2026-11-02", 99.0)
    _add(b, "2026-11-01", 50.0)
    body = client.get(URL.format(a), params={"track": "Monza", "car_model": "499P"}).json()
    assert [x["best"] for x in body["days"]] == [100.0, 99.0]


def test_unknown_driver_is_404_like_the_public_profile(client):
    assert client.get(URL.format(999999)).status_code == 404
    assert client.get("/api/v1/public/drivers/999999").status_code == 404


def test_bad_parameters_are_422(client):
    d = _register(client, "Prog5")
    assert client.get(URL.format(d), params={"track": "Monza"}).status_code == 422
    assert client.get(URL.format(d), params={"car_model": "499P"}).status_code == 422
    assert client.get(URL.format(d), params={"track": "", "car_model": "499P"}).status_code == 422
    assert client.get(URL.format(d), params={"track": "x" * 500, "car_model": "499P"}).status_code == 422
    assert client.get("/api/v1/public/drivers/abc/progress").status_code == 422


def test_public_cache_headers(client):
    d = _register(client, "Prog6")
    r = client.get(URL.format(d))
    assert "public" in r.headers.get("cache-control", "") and r.headers.get("etag")
