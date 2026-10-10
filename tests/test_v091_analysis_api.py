"""V0.9.1 phase 3 — analysis cache, HTTP validators, downsampling / zoom ranges,
and the reference-lap endpoints (Garage16 Best, Team Best, My Best, quick pairs).

Real objects throughout: laps are uploaded through the real client uploader into
the real app (tests/test_analysis.py's helpers), then read back through the real
endpoints. The cache is cleared between tests (conftest.py).
"""

from __future__ import annotations

import json

import pytest

from server.analysis_cache import ANALYSIS_CACHE_VERSION, AnalysisCache, analysis_cache
from tests.test_analysis import _headers, _lap_row, _register, _upload

COMPARE = "/telemetry/analysis/compare"
REFERENCE = "/telemetry/analysis/reference"
QUICK = "/telemetry/analysis/quick"


# ---------------------------------------------------------------------------
# The cache itself
# ---------------------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_cache_hit_miss_and_stats():
    cache = AnalysisCache()
    key = cache.make_key("compare", "h1", "h2")
    assert cache.get(key) is None
    assert cache.put(key, b"payload")
    assert cache.get(key) == b"payload"
    stats = cache.stats()
    assert (stats.hits, stats.misses, stats.entries, stats.bytes) == (1, 1, 1, len(b"payload"))


def test_cache_key_order_matters_because_delta_flips_sign():
    assert AnalysisCache.make_key("compare", "A", "B") != AnalysisCache.make_key("compare", "B", "A")


def test_cache_evicts_least_recently_used_by_entry_count():
    cache = AnalysisCache(max_entries=3)
    keys = [cache.make_key(i) for i in range(4)]
    for key in keys[:3]:
        cache.put(key, b"x")
    cache.get(keys[0])               # 0 is now the freshest, 1 the oldest
    cache.put(keys[3], b"x")
    assert cache.get(keys[1]) is None
    assert all(cache.get(k) == b"x" for k in (keys[0], keys[2], keys[3]))
    assert cache.stats().evictions == 1


def test_cache_is_bounded_in_bytes_and_refuses_one_huge_entry():
    cache = AnalysisCache(max_entries=100, max_bytes=1000)
    for i in range(10):
        cache.put(cache.make_key(i), b"y" * 200)   # 200 <= 1000 // 4
    stats = cache.stats()
    assert stats.bytes <= 1000 and stats.entries <= 5
    assert cache.put(cache.make_key("huge"), b"z" * 300) is False   # > a quarter of the budget
    assert cache.stats().rejected_too_large == 1
    assert cache.stats().entries >= 1   # the huge request did not flush the rest


def test_cache_entries_expire():
    clock = _Clock()
    cache = AnalysisCache(ttl_seconds=60, clock=clock)
    key = cache.make_key("k")
    cache.put(key, b"v")
    clock.now += 59
    assert cache.get(key) == b"v"
    clock.now += 2
    assert cache.get(key) is None
    assert cache.stats().expirations == 1 and cache.stats().entries == 0


def test_cache_version_is_part_of_every_key(monkeypatch):
    cache = AnalysisCache()
    old_key = cache.make_key("compare", "h")
    cache.put(old_key, b"computed by version 1")
    monkeypatch.setattr("server.analysis_cache.ANALYSIS_CACHE_VERSION", ANALYSIS_CACHE_VERSION + 1)
    new_key = cache.make_key("compare", "h")
    assert new_key != old_key
    assert cache.get(new_key) is None, "a bumped version must never be served the old computation"
    assert AnalysisCache.etag_for(new_key) != AnalysisCache.etag_for(old_key)


def test_cache_rejects_nonsense_limits():
    for bad in ({"max_entries": 0}, {"max_bytes": 0}, {"ttl_seconds": 0}):
        with pytest.raises(ValueError):
            AnalysisCache(**bad)


# ---------------------------------------------------------------------------
# /compare: cache, ETag, authorisation before cache
# ---------------------------------------------------------------------------

def _two_laps(client):
    me, rival = _register(client, "Me"), _register(client, "Rival")
    a = _upload(client, me, lap_time=92.0, seed=31)["lap_id"]
    b = _upload(client, rival, lap_time=90.0, seed=32)["lap_id"]
    return me, rival, a, b


def test_second_identical_request_is_served_from_the_cache(client, monkeypatch):
    import server.routers.analysis as analysis_router

    me, _rival, a, b = _two_laps(client)
    loads = []
    real = analysis_router.load_lap
    monkeypatch.setattr(analysis_router, "load_lap", lambda *args, **kw: (loads.append(args[1]), real(*args, **kw))[1])

    params = {"lap_a": a, "lap_b": b, "channels": "speed,throttle,brake"}
    first = client.get(COMPARE, params=params, headers=_headers(me))
    assert first.status_code == 200 and len(loads) == 2        # two laps read on the miss
    second = client.get(COMPARE, params=params, headers=_headers(me))
    assert second.status_code == 200 and len(loads) == 2       # nothing read on the hit
    assert second.content == first.content
    assert analysis_cache.stats().hits >= 1 and analysis_cache.stats().entries == 1


def test_cache_misses_when_channels_step_or_lap_order_change(client):
    me, _rival, a, b = _two_laps(client)
    h = _headers(me)
    base = {"lap_a": a, "lap_b": b, "channels": "speed"}
    first = client.get(COMPARE, params=base, headers=h).json()
    client.get(COMPARE, params={**base, "channels": "speed,brake"}, headers=h)
    client.get(COMPARE, params={**base, "step": 5}, headers=h)
    swapped = client.get(COMPARE, params={**base, "lap_a": b, "lap_b": a}, headers=h).json()
    assert analysis_cache.stats().entries == 4      # four different questions, four entries
    # the swap is a different answer, not the cached one with the wrong sign
    assert swapped["delta"][-1] == pytest.approx(-first["delta"][-1], abs=0.05)


def test_response_reports_the_analysis_version(client):
    me, _r, a, b = _two_laps(client)
    body = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed"}, headers=_headers(me)).json()
    assert body["analysis_version"] == ANALYSIS_CACHE_VERSION


def test_cached_numbers_are_identical_to_a_fresh_computation(client):
    me, _r, a, b = _two_laps(client)
    params = {"lap_a": a, "lap_b": b, "channels": "speed,throttle,gear,steering"}
    cold = client.get(COMPARE, params=params, headers=_headers(me)).json()
    analysis_cache.clear()
    fresh = client.get(COMPARE, params=params, headers=_headers(me)).json()
    warm = client.get(COMPARE, params=params, headers=_headers(me)).json()
    assert cold == fresh == warm


def test_lap_headers_are_never_served_stale_from_the_cache(client):
    from server.database import SessionLocal
    from server.models import Driver

    me, rival, a, b = _two_laps(client)
    h = _headers(me)
    params = {"lap_a": a, "lap_b": b, "channels": "speed"}
    first = client.get(COMPARE, params=params, headers=h)
    with SessionLocal() as db:
        driver = db.query(Driver).filter(Driver.display_name == "Rival").one()
        driver.display_name = "Renamed Rival"
        db.commit()
    second = client.get(COMPARE, params=params, headers=h)
    assert first.json()["laps"]["b"]["driver_name"] == "Rival"
    assert second.json()["laps"]["b"]["driver_name"] == "Renamed Rival"
    assert second.headers["etag"] != first.headers["etag"], "a changed header must change the validator"
    assert first.json()["channels"] == second.json()["channels"]   # the numbers did come from the cache
    assert analysis_cache.stats().hits >= 1


def test_etag_gives_304_and_never_a_body(client):
    me, _r, a, b = _two_laps(client)
    h = _headers(me)
    params = {"lap_a": a, "lap_b": b, "channels": "speed"}
    first = client.get(COMPARE, params=params, headers=h)
    etag = first.headers["etag"]
    assert first.headers["cache-control"] == "private, no-cache"
    assert "Authorization" in first.headers["vary"]

    again = client.get(COMPARE, params=params, headers={**h, "If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    assert client.get(COMPARE, params=params, headers={**h, "If-None-Match": f"W/{etag}"}).status_code == 304
    assert client.get(COMPARE, params=params, headers={**h, "If-None-Match": '"other", ' + etag}).status_code == 304
    assert client.get(COMPARE, params=params, headers={**h, "If-None-Match": '"stale"'}).status_code == 200
    # a different question has a different validator
    other = client.get(COMPARE, params={**params, "channels": "brake"}, headers=h)
    assert other.headers["etag"] != etag


def test_a_valid_etag_cannot_unlock_a_lap_the_viewer_may_not_see(client):
    """Authorisation runs before the cache and before the validator check."""
    me, rival = _register(client, "Me"), _register(client, "Rival")
    mine = _upload(client, me, seed=41)["lap_id"]
    invalid = _upload(client, rival, lap_time=90.0, claim_lap_time=60.0, seed=42)["lap_id"]
    assert _lap_row(invalid).is_valid is False

    rival_view = client.get(COMPARE, params={"lap_a": invalid, "channels": "speed"}, headers=_headers(rival))
    assert rival_view.status_code == 200                      # the owner may; it is now cached
    etag = rival_view.headers["etag"]

    params = {"lap_a": mine, "lap_b": invalid, "channels": "speed"}
    for extra in ({}, {"If-None-Match": etag}, {"If-None-Match": "*"}):
        resp = client.get(COMPARE, params=params, headers={**_headers(me), **extra})
        assert resp.status_code == 404, extra
    # ... nor does the cached entry of the owner's own request leak to a stranger
    stranger = client.get(COMPARE, params={"lap_a": invalid, "channels": "speed"},
                          headers={**_headers(me), "If-None-Match": etag})
    assert stranger.status_code == 404


def test_cache_hits_still_count_against_the_rate_limit(client, monkeypatch):
    from server.config import settings

    me, _r, a, b = _two_laps(client)
    h = _headers(me)
    params = {"lap_a": a, "lap_b": b, "channels": "speed"}
    etag = client.get(COMPARE, params=params, headers=h).headers["etag"]       # 1: computes
    monkeypatch.setitem(settings.rate_limits, "analysis", (3, 3600.0))
    from server.rate_limit import reset_all
    reset_all()
    assert client.get(COMPARE, params=params, headers=h).status_code == 200                          # cache hit
    assert client.get(COMPARE, params=params, headers={**h, "If-None-Match": etag}).status_code == 304  # 304
    assert client.get(COMPARE, params=params, headers=h).status_code == 200                          # cache hit
    assert client.get(COMPARE, params=params, headers=h).status_code == 429


def test_storage_path_is_rechecked_even_when_the_result_is_cached(client, tmp_path):
    from server.database import SessionLocal
    from server.models import Lap

    me, _r, a, b = _two_laps(client)
    params = {"lap_a": a, "lap_b": b, "channels": "speed"}
    assert client.get(COMPARE, params=params, headers=_headers(me)).status_code == 200
    outside = tmp_path / "elsewhere.parquet"
    outside.write_bytes(b"not for you")
    with SessionLocal() as db:
        db.get(Lap, b).telemetry_path = str(outside)
        db.commit()
    assert client.get(COMPARE, params=params, headers=_headers(me)).status_code == 404


def test_a_lap_whose_file_vanished_is_404_even_with_a_cached_result(client):
    import os

    me, _r, a, b = _two_laps(client)
    params = {"lap_a": a, "lap_b": b, "channels": "speed"}
    assert client.get(COMPARE, params=params, headers=_headers(me)).status_code == 200
    os.remove(_lap_row(b).telemetry_path)
    resp = client.get(COMPARE, params=params, headers=_headers(me))
    assert resp.status_code == 404 and "no longer stored" in resp.json()["detail"]


def test_cache_stays_within_its_bounds_under_many_distinct_requests(client, monkeypatch):
    import server.routers.analysis as analysis_router

    small = AnalysisCache(max_entries=3, max_bytes=10_000_000)
    monkeypatch.setattr(analysis_router, "analysis_cache", small)
    me, _r, a, b = _two_laps(client)
    for step in (2, 3, 4, 5, 6, 7, 8):
        assert client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed", "step": step},
                          headers=_headers(me)).status_code == 200
    stats = small.stats()
    assert stats.entries == 3 and stats.evictions == 4


# ---------------------------------------------------------------------------
# Less data: column pruning, downsampling, zoom ranges
# ---------------------------------------------------------------------------

def test_only_the_needed_parquet_columns_are_read(client, monkeypatch):
    import pyarrow.parquet as pq

    me, _r, a, b = _two_laps(client)
    from server.telemetry_analysis import clear_lap_cache

    clear_lap_cache()  # prepared laps are cached in-process; this test must see the file being read
    read = []
    # every read goes through ParquetFile.read (one open per lap, memory-mapped) — also spy on
    # read_table so a regression to a full-file read_table() would be caught too
    real_pf_read = pq.ParquetFile.read
    real_read_table = pq.read_table
    monkeypatch.setattr(pq.ParquetFile, "read", lambda self, columns=None, *a, **kw: (read.append(columns), real_pf_read(self, columns=columns, *a, **kw))[1])
    monkeypatch.setattr(pq, "read_table", lambda path, columns=None, **kw: (read.append(columns), real_read_table(path, columns=columns, **kw))[1])
    assert client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed,throttle,brake"}, headers=_headers(me)).status_code == 200
    assert read, "the parquet file was never read"
    from tests.helpers.synthetic_lap import make_lap
    total_columns = len(make_lap(n=50).columns)
    for columns in read:
        assert columns is not None, "a full-file read slipped in"
        assert len(columns) < total_columns / 4, f"read {len(columns)} of {total_columns} columns"
        assert not any(c.startswith(("susp_", "tc_", "abs_")) for c in columns)


def test_max_points_bounds_every_series_and_widens_the_step(client):
    me, _r, a, b = _two_laps(client)
    h = _headers(me)
    full = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed", "step": 0.5}, headers=h).json()
    small = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed", "step": 0.5, "max_points": 400}, headers=h).json()
    assert small["grid"]["count"] <= 400 < full["grid"]["count"]
    assert small["grid"]["step"] > full["grid"]["step"]
    count = small["grid"]["count"]
    assert len(small["delta"]) == len(small["time"]["a"]) == len(small["channels"]["speed"]["a"]) == count
    assert len(json.dumps(small)) < len(json.dumps(full)) / 2
    # the delta at the end of the lap does not depend on how coarsely it is sampled
    assert small["delta"][-1] == pytest.approx(full["delta"][-1], abs=0.05)


def test_start_and_end_return_just_that_stretch_at_a_finer_step(client):
    me, _r, a, b = _two_laps(client)
    h = _headers(me)
    whole = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed"}, headers=h).json()
    zoom = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed", "start": 1000, "end": 1200, "step": 0.5}, headers=h).json()
    assert zoom["grid"]["start"] == pytest.approx(1000, abs=1.0)
    last = zoom["grid"]["start"] + zoom["grid"]["step"] * (zoom["grid"]["count"] - 1)
    assert 1195 <= last <= 1200
    assert zoom["grid"]["step"] == pytest.approx(0.5)
    assert zoom["grid"]["count"] < whole["grid"]["count"]
    assert len(zoom["channels"]["speed"]["a"]) == zoom["grid"]["count"]
    # the zoomed values agree with the same metres of the whole-lap response
    index_whole = round((1100 - whole["grid"]["start"]) / whole["grid"]["step"])
    index_zoom = round((1100 - zoom["grid"]["start"]) / zoom["grid"]["step"])
    assert zoom["channels"]["speed"]["a"][index_zoom] == pytest.approx(whole["channels"]["speed"]["a"][index_whole], abs=1.0)


def test_a_range_outside_the_lap_is_a_clear_error_not_a_crash(client):
    me, _r, a, b = _two_laps(client)
    resp = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed", "start": 900000, "end": 900100}, headers=_headers(me))
    assert resp.status_code == 422 and "does not overlap" in resp.json()["detail"]


def test_bad_range_and_point_parameters_are_rejected(client):
    me, _r, a, _b = _two_laps(client)
    for params in ({"max_points": 10}, {"max_points": 99999}, {"start": -5}, {"step": 0}):
        resp = client.get(COMPARE, params={"lap_a": a, "channels": "speed", **params}, headers=_headers(me))
        assert resp.status_code == 422, params


def test_the_response_without_new_parameters_keeps_the_v090_shape(client):
    me, _r, a, b = _two_laps(client)
    body = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed"}, headers=_headers(me)).json()
    for key in ("grid", "laps", "delta", "time", "channels", "recording", "unavailable", "map", "sectors", "orientation"):
        assert key in body, key
    assert set(body["grid"]) == {"start", "step", "count"}
    assert body["laps"]["a"]["id"] and body["laps"]["b"]["id"]


def test_old_client_laps_stay_comparable_through_the_cache(client):
    me, rival = _register(client, "Me"), _register(client, "Old")
    full = _upload(client, me, seed=51)["lap_id"]
    basic = _upload(client, rival, seed=52, client_version="0.8.6", with_extended=False)["lap_id"]
    params = {"lap_a": full, "lap_b": basic, "channels": "speed,susp_travel_fl,tc_level"}
    first = client.get(COMPARE, params=params, headers=_headers(me))
    second = client.get(COMPARE, params=params, headers=_headers(me))
    assert first.status_code == second.status_code == 200 and first.content == second.content
    body = second.json()
    assert body["recording"]["b"]["level"] == "basic"
    assert body["channels"]["speed"]["b"] is not None
    assert body["unavailable"], "channels the basic lap lacks must be listed, not silently zero"


# ---------------------------------------------------------------------------
# Static responses
# ---------------------------------------------------------------------------

def test_channel_catalog_is_publicly_cacheable_with_a_content_etag(client):
    first = client.get("/telemetry/analysis/channels")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "public, max-age=300"
    etag = first.headers["etag"]
    assert client.get("/telemetry/analysis/channels", headers={"If-None-Match": etag}).status_code == 304
    assert client.get("/telemetry/analysis/channels", headers={"If-None-Match": '"nope"'}).status_code == 200
    assert "channels" in first.json() and "default" in first.json()


def test_user_specific_endpoints_are_never_publicly_cacheable(client):
    me, _r, a, _b = _two_laps(client)
    resp = client.get(COMPARE, params={"lap_a": a, "channels": "speed"}, headers=_headers(me))
    assert "public" not in resp.headers["cache-control"]
    ref = client.get(REFERENCE, params={"kind": "wr", "lap_id": a}, headers=_headers(me))
    assert "public" not in ref.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# Reference laps
# ---------------------------------------------------------------------------

def _ref(client, viewer, kind, lap_id, **extra):
    resp = client.get(REFERENCE, params={"kind": kind, "lap_id": lap_id, **extra}, headers=_headers(viewer))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _field(client):
    """Three drivers on the same layout and car: fast, mid, slow (Me)."""
    me, mid, fast = _register(client, "Me"), _register(client, "Mid"), _register(client, "Fast")
    return {
        "me": me, "mid": mid, "fast": fast,
        "me_lap": _upload(client, me, lap_time=95.0, seed=61)["lap_id"],
        "mid_lap": _upload(client, mid, lap_time=92.0, seed=62)["lap_id"],
        "fast_lap": _upload(client, fast, lap_time=90.0, seed=63)["lap_id"],
    }


def test_garage16_best_is_the_fastest_valid_lap_on_this_layout_and_car(client):
    f = _field(client)
    out = _ref(client, f["me"], "wr", f["me_lap"])
    assert out["found"] and out["lap"]["id"] == f["fast_lap"] and out["lap"]["driver_name"] == "Fast"
    assert out["label"] == "Garage16 Best"          # never claims to be a world record
    assert out["reason"] is None and out["already_best"] is False


def test_garage16_best_ignores_other_cars_tracks_and_invalid_laps(client):
    f = _field(client)
    other = _register(client, "Other")
    _upload(client, other, lap_time=80.0, seed=64, car_model="Porsche 963")                    # faster, other car
    _upload(client, other, lap_time=81.0, seed=65, track="Monza")                              # faster, other track
    _upload(client, other, lap_time=82.0, claim_lap_time=60.0, seed=66)                        # faster but invalid
    out = _ref(client, f["me"], "wr", f["me_lap"])
    assert out["lap"]["id"] == f["fast_lap"]


def test_scope_class_widens_to_the_whole_car_class(client):
    f = _field(client)
    other = _register(client, "Other")
    porsche = _upload(client, other, lap_time=85.0, seed=67, car_model="Porsche 963", car_class="Hypercar")["lap_id"]
    assert _ref(client, f["me"], "wr", f["me_lap"], scope="model")["lap"]["id"] == f["fast_lap"]
    assert _ref(client, f["me"], "wr", f["me_lap"], scope="class")["lap"]["id"] == porsche


def test_the_lap_being_viewed_is_never_offered_as_its_own_reference(client):
    f = _field(client)
    out = _ref(client, f["fast"], "wr", f["fast_lap"])
    assert out["already_best"] is True
    assert out["found"] and out["lap"]["id"] == f["mid_lap"] != f["fast_lap"]      # the next-fastest instead


def test_best_lap_alone_on_the_layout_has_nothing_to_compare_with(client):
    me = _register(client, "Solo")
    lap = _upload(client, me, seed=68)["lap_id"]
    out = _ref(client, me, "wr", lap)
    assert out["found"] is False and out["lap"] is None
    assert out["reason"] == "already_best" and out["already_best"] is True


def test_my_best_is_my_fastest_valid_lap_and_not_the_one_i_am_viewing(client):
    f = _field(client)
    faster_own = _upload(client, f["me"], lap_time=93.0, seed=69)["lap_id"]
    out = _ref(client, f["me"], "my_best", f["me_lap"])
    assert out["lap"]["id"] == faster_own and out["lap"]["driver_name"] == "Me"
    best = _ref(client, f["me"], "my_best", faster_own)
    assert best["already_best"] is True and best["lap"]["id"] == f["me_lap"]


def test_my_best_never_picks_an_invalid_lap_of_mine(client):
    f = _field(client)
    _upload(client, f["me"], lap_time=88.0, claim_lap_time=60.0, seed=70)                # my fastest, but invalid
    out = _ref(client, f["me"], "my_best", f["mid_lap"])
    assert out["lap"]["id"] == f["me_lap"]


def test_my_best_for_a_lap_on_a_layout_where_i_have_none(client):
    f = _field(client)
    out = _ref(client, f["me"], "my_best", f["mid_lap"], scope="model")
    assert out["lap"]["id"] == f["me_lap"]                       # I do have one here ...
    other = _register(client, "Other")
    foreign = _upload(client, other, lap_time=90.0, seed=71, track="Monza")["lap_id"]
    out = _ref(client, f["me"], "my_best", foreign)
    assert out["found"] is False and out["reason"] == "no_own_lap" and out["lap"] is None


def _make_team(client, owner, name="Team Red"):
    resp = client.post("/teams", json={"name": name}, headers=_headers(owner))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _join(client, member, team):
    resp = client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=_headers(member))
    assert resp.status_code == 200, resp.text


def test_team_best_is_the_fastest_valid_lap_among_my_teammates(client):
    f = _field(client)
    team = _make_team(client, f["me"])
    _join(client, f["mid"], team)                                  # Fast is NOT in the team
    out = _ref(client, f["me"], "team_best", f["me_lap"])
    assert out["found"] and out["lap"]["id"] == f["mid_lap"]       # not the faster, foreign Fast lap
    assert out["label"] == "Team Best"


def test_team_best_without_a_team_says_so_and_picks_no_foreign_lap(client):
    f = _field(client)
    out = _ref(client, f["me"], "team_best", f["me_lap"])
    assert out["found"] is False and out["lap"] is None and out["reason"] == "no_team"


def test_team_best_when_the_team_has_no_lap_on_this_layout(client):
    f = _field(client)
    loner = _register(client, "Loner")
    _make_team(client, loner, "Loners")
    out = _ref(client, loner, "team_best", f["fast_lap"])           # viewing someone else's lap
    assert out["found"] is False and out["lap"] is None and out["reason"] == "no_team_lap"


def test_team_best_when_i_already_am_the_team_best(client):
    f = _field(client)
    team = _make_team(client, f["fast"])
    _join(client, f["me"], team)                                   # Fast (90 s) and Me (95 s) are teammates
    out = _ref(client, f["fast"], "team_best", f["fast_lap"])
    assert out["already_best"] is True
    assert out["lap"]["id"] == f["me_lap"] != f["fast_lap"]        # the next-fastest team lap, never the same one twice


def test_a_reference_whose_file_is_gone_is_skipped(client):
    import os

    f = _field(client)
    os.remove(_lap_row(f["fast_lap"]).telemetry_path)
    out = _ref(client, f["me"], "wr", f["me_lap"])
    assert out["lap"]["id"] == f["mid_lap"]                         # a reference can always be opened


def test_cars_without_a_model_fall_back_to_the_car_name(client):
    """Laps from clients older than V0.6.3 have no car_model; records.py then
    matches by car_name, and so does the reference."""
    from server.database import SessionLocal
    from server.models import Lap

    f = _field(client)
    with SessionLocal() as db:
        for lap_id, name in ((f["me_lap"], "Old Car"), (f["mid_lap"], "Old Car"), (f["fast_lap"], "Other Old Car")):
            lap = db.get(Lap, lap_id)
            lap.car_model, lap.car_name = None, name
        db.commit()
    out = _ref(client, f["me"], "wr", f["me_lap"])
    assert out["lap"]["id"] == f["mid_lap"]                         # same car_name, not the faster 'Other Old Car'


def test_reference_requires_a_lap_the_viewer_may_open(client):
    me, rival = _register(client, "Me"), _register(client, "Rival")
    invalid = _upload(client, rival, lap_time=90.0, claim_lap_time=60.0, seed=72)["lap_id"]
    resp = client.get(REFERENCE, params={"kind": "wr", "lap_id": invalid}, headers=_headers(me))
    assert resp.status_code == 404
    assert client.get(REFERENCE, params={"kind": "wr", "lap_id": 99999}, headers=_headers(me)).status_code == 404


def test_reference_rejects_unknown_kinds_and_needs_a_login(client):
    me = _register(client, "Me")
    lap = _upload(client, me, seed=73)["lap_id"]
    assert client.get(REFERENCE, params={"kind": "hacker", "lap_id": lap}, headers=_headers(me)).status_code == 422
    assert client.get(REFERENCE, params={"kind": "wr", "lap_id": lap, "scope": "galaxy"}, headers=_headers(me)).status_code == 422
    assert client.get(REFERENCE, params={"kind": "wr", "lap_id": lap}).status_code in (401, 403)


def test_reference_requests_are_rate_limited(client, monkeypatch):
    from server.config import settings

    me = _register(client, "Me")
    lap = _upload(client, me, seed=74)["lap_id"]
    monkeypatch.setitem(settings.rate_limits, "analysis", (2, 3600.0))
    h = _headers(me)
    assert client.get(REFERENCE, params={"kind": "wr", "lap_id": lap}, headers=h).status_code == 200
    assert client.get(REFERENCE, params={"kind": "wr", "lap_id": lap}, headers=h).status_code == 200
    assert client.get(REFERENCE, params={"kind": "wr", "lap_id": lap}, headers=h).status_code == 429


# ---------------------------------------------------------------------------
# Quick pairs: "You vs ..."
# ---------------------------------------------------------------------------

def _quick(client, viewer, mode, track="Le Mans", **extra):
    resp = client.get(QUICK, params={"mode": mode, "track_name": track, **extra}, headers=_headers(viewer))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_you_vs_garage16_best_picks_my_best_lap_and_the_fastest_lap(client):
    f = _field(client)
    better = _upload(client, f["me"], lap_time=93.0, seed=75)["lap_id"]
    out = _quick(client, f["me"], "wr")
    assert out["found"] and out["lap_a"]["id"] == better and out["lap_b"]["id"] == f["fast_lap"]
    assert out["lap_a"]["id"] != out["lap_b"]["id"]


def test_you_vs_team_best(client):
    f = _field(client)
    team = _make_team(client, f["me"])
    _join(client, f["mid"], team)
    out = _quick(client, f["me"], "team_best")
    assert out["found"] and out["lap_a"]["id"] == f["me_lap"] and out["lap_b"]["id"] == f["mid_lap"]


def test_you_vs_team_best_when_i_am_the_team_best_offers_the_next_lap_or_says_so(client):
    f = _field(client)
    team = _make_team(client, f["fast"])
    _join(client, f["mid"], team)
    out = _quick(client, f["fast"], "team_best")
    assert out["found"] and out["lap_a"]["id"] == f["fast_lap"] and out["lap_b"]["id"] == f["mid_lap"]
    assert out["already_best"] is True

    solo = _register(client, "Solo")
    _make_team(client, solo, "Solo Team")
    lone = _upload(client, solo, lap_time=70.0, seed=76, track="Spa")["lap_id"]
    out = _quick(client, solo, "team_best", track="Spa")
    assert out["found"] is False and out["lap_a"]["id"] == lone and out["lap_b"] is None
    assert out["reason"] == "already_best"


def test_you_vs_my_best_compares_my_latest_lap_with_my_best(client):
    f = _field(client)
    best = _upload(client, f["me"], lap_time=91.5, seed=77)["lap_id"]
    latest = _upload(client, f["me"], lap_time=99.0, seed=78)["lap_id"]
    out = _quick(client, f["me"], "my_best")
    assert out["found"] and out["lap_a"]["id"] == latest and out["lap_b"]["id"] == best


def test_quick_without_an_own_lap_on_the_layout(client):
    f = _field(client)
    out = _quick(client, f["me"], "wr", track="Spa")
    assert out["found"] is False and out["lap_a"] is None and out["lap_b"] is None
    assert out["reason"] == "no_own_lap" and out["message"]


def test_quick_can_be_restricted_to_one_car_model(client):
    f = _field(client)
    porsche = _upload(client, f["me"], lap_time=96.0, seed=79, car_model="Porsche 963")["lap_id"]
    out = _quick(client, f["me"], "wr", car_model="Porsche 963")
    assert out["lap_a"]["id"] == porsche
    assert out["found"] is False and out["reason"] == "already_best"   # nobody else drives the 963


def test_quick_ignores_my_invalid_laps(client):
    f = _field(client)
    _upload(client, f["me"], lap_time=85.0, claim_lap_time=60.0, seed=80)
    out = _quick(client, f["me"], "wr")
    assert out["lap_a"]["id"] == f["me_lap"]


def test_quick_needs_a_login_and_a_known_mode(client):
    me = _register(client, "Me")
    assert client.get(QUICK, params={"mode": "wr", "track_name": "Le Mans"}).status_code in (401, 403)
    assert client.get(QUICK, params={"mode": "nope", "track_name": "Le Mans"}, headers=_headers(me)).status_code == 422


def test_quick_without_a_track_uses_my_most_recent_lap(client):
    f = _field(client)
    spa = _upload(client, f["me"], lap_time=77.0, seed=85, track="Spa")["lap_id"]          # most recent
    other = _upload(client, f["fast"], lap_time=75.0, seed=86, track="Spa")["lap_id"]
    resp = client.get(QUICK, params={"mode": "wr"}, headers=_headers(f["me"]))
    assert resp.status_code == 200
    out = resp.json()
    assert out["found"] and out["lap_a"]["id"] == spa and out["lap_b"]["id"] == other
    # a driver with no valid lap at all gets a reason, not an error
    newcomer = _register(client, "Newcomer")
    out = client.get(QUICK, params={"mode": "wr"}, headers=_headers(newcomer)).json()
    assert out["found"] is False and out["reason"] == "no_own_lap"


# ---------------------------------------------------------------------------
# "Compare Lap ID": the checks the frontend relies on (existing endpoint)
# ---------------------------------------------------------------------------

def test_lap_id_checks_cover_missing_foreign_invalid_gone_and_damaged_laps(client):
    import os

    me, rival = _register(client, "Me"), _register(client, "Rival")
    ok = _upload(client, rival, seed=81)["lap_id"]
    invalid = _upload(client, rival, lap_time=90.0, claim_lap_time=60.0, seed=82)["lap_id"]
    gone = _upload(client, rival, lap_time=91.0, seed=83)["lap_id"]
    damaged = _upload(client, rival, lap_time=93.0, seed=84)["lap_id"]
    h = _headers(me)

    info = client.get(f"/telemetry/analysis/laps/{ok}", headers=h)
    assert info.status_code == 200 and info.json()["lap"]["id"] == ok and info.json()["available_channels"]

    missing = client.get("/telemetry/analysis/laps/987654", headers=h)
    foreign_invalid = client.get(f"/telemetry/analysis/laps/{invalid}", headers=h)
    assert missing.status_code == foreign_invalid.status_code == 404
    assert foreign_invalid.json()["detail"].endswith("not found.") and "invalid" not in foreign_invalid.json()["detail"].lower()

    os.remove(_lap_row(gone).telemetry_path)
    assert client.get(f"/telemetry/analysis/laps/{gone}", headers=h).status_code == 404

    with open(_lap_row(damaged).telemetry_path, "wb") as handle:
        handle.write(b"this is no parquet file")
    resp = client.get(f"/telemetry/analysis/laps/{damaged}", headers=h)
    assert resp.status_code == 422 and "Traceback" not in resp.text

    # ids that cannot exist: 0 is simply "not found" (nothing is revealed), a non-number is rejected outright
    assert client.get("/telemetry/analysis/laps/0", headers=h).status_code == 404
    assert client.get("/telemetry/analysis/laps/abc", headers=h).status_code == 422


def test_the_default_analysis_rate_limit_leaves_room_for_zoom_refinement(monkeypatch):
    """V0.9.1 raised it from 300 to 900 per hour: every settled zoom/pan is one request now."""
    import importlib

    import server.config as config_module

    monkeypatch.delenv("LMU_GARAGE_RATELIMIT_ANALYSIS_MAX", raising=False)
    monkeypatch.delenv("LMU_GARAGE_RATELIMIT_ANALYSIS_WINDOW", raising=False)
    fresh = config_module.Settings() if hasattr(config_module, "Settings") else importlib.reload(config_module).settings
    limit, window = fresh.rate_limits["analysis"]
    assert (limit, window) == (900, 3600.0)
    monkeypatch.setenv("LMU_GARAGE_RATELIMIT_ANALYSIS_MAX", "40")
    assert config_module.Settings().rate_limits["analysis"][0] == 40, "the environment still overrides the default"


def test_refinement_requests_are_cached_and_answered_with_the_requested_grid(client):
    """What the browser's zoom refinement sends: a stretch at a fine step with every loaded channel."""
    me, _r, a, b = _two_laps(client)
    h = _headers(me)
    params = {"lap_a": a, "lap_b": b, "channels": "speed,throttle,brake", "start": 1000, "end": 1300, "step": 0.5, "max_points": 3000}
    first = client.get(COMPARE, params=params, headers=h)
    assert first.status_code == 200
    grid = first.json()["grid"]
    assert grid["step"] == 0.5 and 1000 <= grid["start"] <= 1001 and grid["count"] >= 590
    again = client.get(COMPARE, params=params, headers=h)
    assert again.content == first.content and analysis_cache.stats().hits >= 1
    assert client.get(COMPARE, params=params, headers={**h, "If-None-Match": first.headers["etag"]}).status_code == 304
