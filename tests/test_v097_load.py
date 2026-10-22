"""V0.9.7 — the read-only load test (scripts/load_test.py), run against the app in-process: several
users at once comparing laps, reading the leaderboard and the health report — no errors, every
request answered, and the analysis answers stay identical under load (the caches are shared)."""

from __future__ import annotations

from scripts.load_test import run, scenario
from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import headers, register, upload_df


def test_parallel_users_get_correct_answers_without_errors(client):
    me = register(client, "Load Driver")
    a = upload_df(client, me, make_lap(seed=31), lap_number=1)["lap_id"]
    b = upload_df(client, me, make_lap(seed=32), lap_number=2, recorded_at=1_700_000_200)["lap_id"]
    auth = headers(me)
    reference = client.get(f"/telemetry/analysis/compare?lap_a={a}&lap_b={b}", headers=auth).json()
    answers = []

    def fetch(path):
        resp = client.get(path, headers=auth)
        if path.startswith("/telemetry/analysis/compare"):
            answers.append(resp.json()["corners"] == reference["corners"])
        return resp.status_code

    result = run(fetch, scenario(a, b), users=4, seconds=1.5)
    for name, r in result.items():
        assert r["requests"] > 0 and not r["errors"], (name, r)
    assert answers and all(answers), "the same comparison, every time"
