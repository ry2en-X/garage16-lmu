"""Less load on the NAS: what the API lets browsers and Cloudflare keep.

* /api/v1/public/*: the same for every visitor — `Cache-Control: public` for 30–60 s and an ETag; a
  repeat with If-None-Match is a 304 without data (server/api/caching.py);
* an endpoint that sets its own policy keeps it (legal data, channel catalog);
* the analysis reads of a signed-in driver: ETag + `private, no-cache` + `Vary: Authorization,
  Cookie` — never `public`;
* nothing else is touched (accounts, uploads).
"""

from __future__ import annotations

from tests.helpers.v093 import headers, register, upload_df
from tests.helpers.synthetic_lap import make_lap


def test_public_answers_are_cacheable_for_a_short_time_and_revalidate(client):
    for path, seconds in (("/api/v1/public/activity", 30), ("/api/v1/public/catalog/venues", 60),
                          ("/api/v1/public/leaderboards/class/Le%20Mans/Hypercar", 30)):
        first = client.get(path)
        assert first.status_code == 200
        assert first.headers["cache-control"] == f"public, max-age={seconds}", path
        etag = first.headers["etag"]
        again = client.get(path, headers={"If-None-Match": etag})
        assert again.status_code == 304 and again.content == b"", path
        assert client.get(path, headers={"If-None-Match": '"other"'}).status_code == 200


def test_an_endpoint_with_its_own_policy_keeps_it(client):
    assert client.get("/api/v1/public/legal").headers["cache-control"] == "public, max-age=3600"
    assert client.get("/api/v1/public/analysis/channels").headers["cache-control"] == "public, max-age=300"


def test_the_old_paths_get_the_same_caching(client):
    resp = client.get("/leaderboard/recent")
    assert resp.status_code == 200 and resp.headers["cache-control"] == "public, max-age=30" and resp.headers.get("etag")


def test_a_public_answer_changes_its_etag_when_the_data_changes(client):
    me = register(client, "Cache Driver")
    before = client.get("/api/v1/public/activity").headers["etag"]
    upload_df(client, me, make_lap(seed=7))
    after = client.get("/api/v1/public/activity")
    assert after.headers["etag"] != before and after.json()


def test_analysis_reads_are_private_and_revalidated(client):
    me = register(client, "Private Reader")
    lap_id = upload_df(client, me, make_lap(seed=8))["lap_id"]
    for path in (f"/api/v1/laps/{lap_id}/analysis", f"/api/v1/drivers/{me['driver_id']}/laps"):
        first = client.get(path, headers=headers(me))
        assert first.status_code == 200, (path, first.text[:200])
        assert first.headers["cache-control"] == "private, no-cache", path
        assert "Authorization" in first.headers["vary"] and "Cookie" in first.headers["vary"]
        again = client.get(path, headers={**headers(me), "If-None-Match": first.headers["etag"]})
        assert again.status_code == 304, path
        # without a session the permission check comes first — no answer from any cache
        assert client.get(path, headers={"If-None-Match": first.headers["etag"]}).status_code == 401


def test_accounts_and_uploads_are_not_touched(client):
    me = register(client, "Untouched")
    resp = client.get("/api/v1/accounts/me", headers=headers(me))
    assert resp.status_code == 200 and "etag" not in resp.headers
    assert "public" not in resp.headers.get("cache-control", "")
