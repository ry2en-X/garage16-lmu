"""V0.9.6 — the record feeds carry the lap and the car model, so the web shows one row per lap
with a link into the analysis (additive fields; nothing else in the responses changed)."""
from __future__ import annotations

from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import headers, register, upload_df


def test_activity_entries_name_their_lap_and_car_model(client):
    me = register(client, "Feed")
    lap_id = upload_df(client, me, make_lap(seed=4), lap_number=1, recorded_at=1_700_000_000)["lap_id"]
    profile = client.get(f"/drivers/{me['driver_id']}/public").json()
    assert profile["recent_activity"], "a first valid lap sets records"
    assert {a["lap_id"] for a in profile["recent_activity"]} == {lap_id}
    assert all(a["car_model"] for a in profile["recent_activity"])
    recent = client.get("/leaderboard/recent").json()
    mine = [r for r in recent if r["lap_id"] == lap_id]
    assert mine and mine[0]["car_model"]

    team = client.post("/teams", json={"name": "Feed Team"}, headers=headers(me)).json()
    feed = client.get(f"/teams/{team['id']}/activity", headers=headers(me)).json()
    assert feed and all(a["lap_id"] == lap_id and a["car_model"] for a in feed)
