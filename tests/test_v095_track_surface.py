"""V0.9.5 — the track surface behind the racing lines, rebuilt from LMU's path_lateral /
track_edge / surface type: both edges, the kerbs, the sign of path_lateral decided from the data."""
from __future__ import annotations

import numpy as np
import pytest

from server.telemetry_analysis import LapSeries
from server.telemetry_channels import calibrate_orientation
from server.track_surface import build_surface, decide_sign

HALF_WIDTH = 6.0   # the synthetic track is 12 m wide


def _centre(length=3000.0, straight=900.0):
    """A stadium (two straights, two half circles), anticlockwise, Y up — sampled every 0.5 m."""
    radius = (length - 2 * straight) / (2 * np.pi)
    s = np.arange(0.0, length, 0.5)
    x = np.empty_like(s)
    y = np.empty_like(s)
    for i, d in enumerate(s):
        if d < straight:
            x[i], y[i] = d, 0.0
        elif d < straight + np.pi * radius:
            a = (d - straight) / radius
            x[i], y[i] = straight + radius * np.sin(a), radius - radius * np.cos(a)
        elif d < 2 * straight + np.pi * radius:
            x[i], y[i] = straight - (d - straight - np.pi * radius), 2 * radius
        else:
            a = (d - 2 * straight - np.pi * radius) / radius
            x[i], y[i] = -radius * np.sin(a), radius + radius * np.cos(a)
    return s, x, y


def _lap(sign_left_positive: bool, phase: float = 0.0, kerbs: bool = True) -> LapSeries:
    s, cx, cy = _centre()
    tx, ty = np.gradient(cx), np.gradient(cy)
    norm = np.hypot(tx, ty)
    nx, ny = -ty / norm, tx / norm                              # left normal
    lat = 4.5 * np.sin(2 * np.pi * s / 750.0 + phase)           # the racing line swings across the track
    px, py = cx + lat * nx, cy + lat * ny
    speed = np.full_like(s, 50.0)
    t = s / 50.0
    raw = {
        "t": t, "lap_dist": s, "speed": speed,
        "pos_x": px, "pos_z": py,
        "path_lateral": lat if sign_left_positive else -lat,
        "track_edge": np.full_like(s, HALF_WIDTH) * np.where(lat >= 0, 1.0, -1.0),
    }
    for w, side in (("fl", 1), ("rl", 1), ("fr", -1), ("rr", -1)):
        wheel = lat + side * 0.8
        raw[f"surface_type_{w}"] = np.where(kerbs & (np.abs(wheel) > 5.0) & (np.abs(wheel) < 7.0), 5.0, 0.0)
    return LapSeries(dist=s.copy(), raw=raw, orientation=calibrate_orientation(raw), available=set(raw))


@pytest.mark.parametrize("left_positive", [True, False])
def test_the_sign_of_path_lateral_is_decided_from_the_data(left_positive):
    from server.track_surface import _sampled
    laps = [_sampled(_lap(left_positive, phase), False) for phase in (0.0, 1.0)]
    assert decide_sign(laps) == (1.0 if left_positive else -1.0)


@pytest.mark.parametrize("left_positive", [True, False])
def test_both_edges_are_found_and_lie_where_the_track_is(left_positive):
    laps = [_lap(left_positive, phase) for phase in (0.0, 2.0, 4.0)]
    surface = build_surface(laps, mirror=False)
    assert surface is not None and surface["laps"] == 3
    wl = np.array([np.nan if v is None else v for v in surface["wl"]])
    wr = np.array([np.nan if v is None else v for v in surface["wr"]])
    inner = slice(10, -10)
    assert np.nanmedian(wl[inner]) == pytest.approx(HALF_WIDTH, abs=0.05)
    assert np.nanmedian(wr[inner]) == pytest.approx(HALF_WIDTH, abs=0.05)
    # the centre path is where it is (not shifted by the racing line)
    s, cx, cy = _centre()
    d = surface["start"] + surface["bin"] * np.arange(surface["count"])
    tx = np.interp(d, s, cx)
    ty = np.interp(d, s, cy)
    gx = np.array([np.nan if v is None else v for v in surface["cx"]])
    gy = np.array([np.nan if v is None else v for v in surface["cy"]])
    error = np.hypot(gx - tx, gy - ty)[inner]
    assert np.nanmedian(error) < 0.2


def test_kerbs_are_placed_at_the_edge():
    surface = build_surface([_lap(True, phase) for phase in (0.0, 2.0, 4.0)], mirror=False)
    kl_in = np.array([np.nan if v is None else v for v in surface["kl_in"]])
    kl_out = np.array([np.nan if v is None else v for v in surface["kl_out"]])
    assert np.isfinite(kl_in).sum() > 10, "kerbs where the wheels went over them"
    assert np.nanmin(kl_in) > 4.0 and np.nanmax(kl_out) < 7.5
    assert np.all(kl_out[np.isfinite(kl_out)] >= HALF_WIDTH - 0.01)


def test_an_unknown_side_is_mirrored_and_marked_as_an_estimate():
    lap = _lap(True, 0.0, kerbs=False)
    raw = lap.raw
    raw["path_lateral"] = np.abs(raw["path_lateral"])           # only ever on the left
    lat = raw["path_lateral"]
    s, cx, cy = _centre()
    tx, ty = np.gradient(cx), np.gradient(cy)
    norm = np.hypot(tx, ty)
    raw["pos_x"], raw["pos_z"] = cx - ty / norm * lat, cy + tx / norm * lat
    raw["track_edge"] = np.full_like(s, HALF_WIDTH)
    surface = build_surface([lap], mirror=False)
    assert surface is not None
    assert all(v == 1 for v in surface["wr_est"][5:-5]), "the right side was never seen: estimated"
    # the left side is measured (short stretches where the car crossed the centre path are
    # interpolated between two measurements — not an estimate)
    assert not any(surface["wl_est"][5:-5])


def test_laps_without_the_columns_give_no_surface():
    lap = _lap(True)
    del lap.raw["path_lateral"]
    assert build_surface([lap], mirror=False) is None


# ---------------------------------------------------------------- the endpoint

def _consistent_lap(seed, **kw):
    """A synthetic lap whose path_lateral / track_edge / kerbs agree with its positions."""
    from tests.helpers.synthetic_lap import make_lap

    df = make_lap(seed=seed, **kw)
    x, z = df.pos_x.to_numpy(), df.pos_z.to_numpy()
    tx, tz = np.gradient(x), np.gradient(z)
    n = np.hypot(tx, tz)
    lat = 4.0 * np.sin(2 * np.pi * df.lap_dist.to_numpy() / 700.0 + seed)
    df["pos_x"], df["pos_z"] = x - tz / n * lat, z + tx / n * lat
    df["path_lateral"] = lat.astype("float32")
    df["track_edge"] = np.full(len(df), 6.0, "float32")
    for w, side in (("fl", 1), ("rl", 1), ("fr", -1), ("rr", -1)):
        df[f"surface_type_{w}"] = np.where(np.abs(lat + side * 0.8) > 4.6, 5.0, 0.0).astype(df[f"surface_type_{w}"].dtype)
    return df


def test_the_endpoint_returns_the_surface_in_map_coordinates(client):
    from tests.helpers.v093 import headers, register, upload_df

    me = register(client, "Surface")
    a = upload_df(client, me, _consistent_lap(1), lap_number=1, recorded_at=1_700_000_000)["lap_id"]
    b = upload_df(client, me, _consistent_lap(2), lap_number=2, recorded_at=1_700_000_100)["lap_id"]
    resp = client.get("/telemetry/analysis/track-surface", params={"lap_a": a, "lap_b": b}, headers=headers(me))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    surface = body["surface"]
    assert surface is not None and surface["laps"] >= 2
    wl = np.array([np.nan if v is None else v for v in surface["wl"]])
    assert np.nanmedian(wl) == pytest.approx(6.0, abs=0.1)
    assert any(v is not None for v in surface["kl_in"]), "kerbs found"
    # cached + ETag like /line
    again = client.get("/telemetry/analysis/track-surface", params={"lap_a": a, "lap_b": b},
                       headers={**headers(me), "If-None-Match": resp.headers["ETag"]})
    assert again.status_code == 304


def test_laps_from_an_old_client_get_no_surface_but_no_error(client):
    from tests.helpers.synthetic_lap import make_lap
    from tests.helpers.v093 import headers, register, upload_df

    me = register(client, "OldSurface")
    a = upload_df(client, me, make_lap(with_extended=False), lap_number=1, recorded_at=1_700_000_000, track="Old Track")["lap_id"]
    resp = client.get("/telemetry/analysis/track-surface", params={"lap_a": a}, headers=headers(me))
    assert resp.status_code == 200 and resp.json()["surface"] is None
