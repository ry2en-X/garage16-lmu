"""V0.9.9.3 — the track surface on REAL LMU data, and the write lock file's owner.

1. The surface never appeared on the NAS. Measured on Moritz' Le Mans laps (60 laps, client 0.9.4):
   the V0.9.5 sign test (the right sign of path_lateral gives the SMOOTHER centre path) saw
   roughness ratios of 1.00-1.02 per lap against the required 1.15 — path_lateral changes far too
   slowly over 2 m to make a difference — so decide_sign() always returned None. The centre paths
   of 12 laps, however, scattered 0.23 m with the right sign and 1.12 m with the wrong one.
   The synthetic laps here reproduce exactly that: centimetre jitter in the positions (equal for
   both signs, drowns the smoothness test) and different lines per lap.

2. Every upload failed with 500 PermissionError: the backup's helper container (root) created
   `.garage16-write.lock` first, as root:root 0644, and the server user could not open it O_RDWR.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from server.telemetry_analysis import LapSeries
from server.telemetry_channels import calibrate_orientation
from server.track_surface import (
    REASON_NO_DATA,
    REASON_SIGN,
    _sampled,
    _sign_by_consistency,
    _sign_by_smoothness,
    build_surface_why,
    decide_sign,
)

ROOT = Path(__file__).resolve().parent.parent
HALF_WIDTH = 7.0


def _centre(length=4000.0, straight=1200.0):
    radius = (length - 2 * straight) / (2 * np.pi)
    s = np.arange(0.0, length, 0.2)                    # ~ telemetry rate at 250 km/h / 50 Hz... close enough
    x, y = np.empty_like(s), np.empty_like(s)
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


def _real_like_lap(left_positive: bool, seed: int, jitter_m: float = 0.03, same_line: bool = False) -> LapSeries:
    """Slow line changes (a few metres over hundreds of metres, like a real racing line), a line
    that differs from lap to lap, and position jitter of a few cm."""
    rng = np.random.default_rng(seed)
    s, cx, cy = _centre()
    tx, ty = np.gradient(cx), np.gradient(cy)
    norm = np.hypot(tx, ty)
    nx, ny = -ty / norm, tx / norm
    phase = 0.0 if same_line else rng.uniform(0, 2 * np.pi)
    amp = 4.0 if same_line else rng.uniform(2.5, 5.0)
    lat = amp * np.sin(2 * np.pi * s / 900.0 + phase) + (0.0 if same_line else rng.uniform(-1.0, 1.0))
    px = cx + lat * nx + rng.normal(0, jitter_m, s.size)
    py = cy + lat * ny + rng.normal(0, jitter_m, s.size)
    raw = {
        "t": s / 60.0, "lap_dist": s, "speed": np.full_like(s, 60.0),
        "pos_x": px, "pos_z": py,
        "path_lateral": lat if left_positive else -lat,
        # LMU (measured): track_edge carries the same sign as path_lateral, the code uses |track_edge|
        "track_edge": np.full_like(s, HALF_WIDTH) * np.sign(lat if left_positive else -lat),
    }
    for w in ("fl", "rl", "fr", "rr"):
        raw[f"surface_type_{w}"] = np.zeros_like(s)
    return LapSeries(dist=s.copy(), raw=raw, orientation=calibrate_orientation(raw), available=set(raw))


@pytest.mark.parametrize("left_positive", [True, False])
def test_the_old_smoothness_test_cannot_decide_on_real_like_data(left_positive):
    """The failure itself: what V0.9.5-V0.9.9.2 did, on data that looks like LMU's."""
    laps = [_sampled(_real_like_lap(left_positive, seed), False) for seed in range(6)]
    assert _sign_by_smoothness(laps) is None


@pytest.mark.parametrize("left_positive", [True, False])
@pytest.mark.parametrize("mirror", [False, True])
def test_the_laps_agreeing_decide_the_sign(left_positive, mirror):
    laps = [_sampled(_real_like_lap(left_positive, seed), mirror) for seed in range(6)]
    expected = 1.0 if left_positive else -1.0
    if mirror:
        expected = -expected        # the map is mirrored: left and right swap with it
    assert _sign_by_consistency(laps) == expected
    assert decide_sign(laps) == expected


@pytest.mark.parametrize("left_positive", [True, False])
def test_real_like_laps_give_a_surface_with_the_right_width(left_positive):
    surface, reason = build_surface_why([_real_like_lap(left_positive, seed) for seed in range(6)], mirror=False)
    assert reason is None and surface is not None
    assert surface["sign"] == (1 if left_positive else -1)
    wl = np.array([np.nan if v is None else v for v in surface["wl"]])
    wr = np.array([np.nan if v is None else v for v in surface["wr"]])
    assert np.nanmedian(wl[20:-20]) == pytest.approx(HALF_WIDTH, abs=0.1)
    assert np.nanmedian(wr[20:-20]) == pytest.approx(HALF_WIDTH, abs=0.1)
    # the centre path lies where the track's centre is
    s, cx, cy = _centre()
    d = surface["start"] + surface["bin"] * np.arange(surface["count"])
    gx = np.array([np.nan if v is None else v for v in surface["cx"]])
    gy = np.array([np.nan if v is None else v for v in surface["cy"]])
    err = np.hypot(gx - np.interp(d, s, cx), gy - np.interp(d, s, cy))[20:-20]
    assert np.nanmedian(err) < 0.3


def test_laps_on_exactly_the_same_line_fall_back_and_never_guess():
    """Identical lines: the laps agree with either sign — the consistency test must not pick one."""
    laps = [_sampled(_real_like_lap(True, seed, same_line=True), False) for seed in range(4)]
    assert _sign_by_consistency(laps) is None
    surface, reason = build_surface_why([_real_like_lap(True, seed, same_line=True) for seed in range(4)], mirror=False)
    assert surface is None and reason == REASON_SIGN


def test_one_lap_alone_never_uses_the_consistency_test():
    assert _sign_by_consistency([_sampled(_real_like_lap(True, 1), False)]) is None


def test_without_the_columns_the_reason_says_so():
    lap = _real_like_lap(True, 0)
    del lap.raw["path_lateral"]
    assert build_surface_why([lap], mirror=False) == (None, REASON_NO_DATA)


def test_the_endpoint_tells_why_there_is_no_surface(client):
    from tests.helpers.synthetic_lap import make_lap
    from tests.helpers.v093 import headers, register, upload_df

    me = register(client, "WhyNoSurface")
    a = upload_df(client, me, make_lap(with_extended=False), lap_number=1, recorded_at=1_700_000_000, track="Old Track 2")["lap_id"]
    body = client.get("/telemetry/analysis/track-surface", params={"lap_a": a}, headers=headers(me)).json()
    assert body["surface"] is None and body["reason"] == REASON_NO_DATA


def test_the_surface_etag_changed_with_the_fix():
    """Browsers still hold V0.9.9's empty answer with its ETag; the key must differ so they get the new one."""
    from server.routers import analysis

    assert analysis.SURFACE_VERSION >= 2
    src = (ROOT / "server" / "routers" / "analysis.py").read_text(encoding="utf-8")
    assert 'make_key("surface", SURFACE_VERSION,' in src


# ---------------------------------------------------------------- the write lock file's owner

def test_a_lock_file_the_server_cannot_write_is_used_read_only(tmp_path, monkeypatch):
    """The NAS case: .garage16-write.lock belongs to root, the server (garage) cannot open it O_RDWR."""
    from server import write_lock

    if write_lock.fcntl is None:
        pytest.skip("no fcntl")
    (tmp_path / write_lock.LOCK_NAME).write_text("", encoding="utf-8")
    real_open = os.open

    def picky_open(path, flags, *args):
        if str(path).endswith(write_lock.LOCK_NAME) and flags & os.O_RDWR:
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, *args)

    monkeypatch.setattr(write_lock.os, "open", picky_open)
    with write_lock.writing(tmp_path):
        pass                                   # V0.9.9.2: PermissionError -> every upload 500


def test_a_read_only_shared_lock_still_blocks_the_backup(tmp_path, monkeypatch):
    """Read-only descriptors still take part in flock: the backup's exclusive lock waits for them."""
    from server import write_lock

    fcntl = write_lock.fcntl
    if fcntl is None:
        pytest.skip("no fcntl")
    lock = tmp_path / write_lock.LOCK_NAME
    lock.write_text("", encoding="utf-8")
    ro = os.open(lock, os.O_RDONLY)
    try:
        fcntl.flock(ro, fcntl.LOCK_SH | fcntl.LOCK_NB)
        other = os.open(lock, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)
    finally:
        os.close(ro)


def test_the_entrypoint_creates_the_lock_file_as_the_server_user():
    src = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    touch = src.index('touch "$LMU_GARAGE_STORAGE_DIR/.garage16-write.lock"')
    chown = src.index('chown -R garage:garage "$LMU_GARAGE_STORAGE_DIR"')
    start = src.index("exec gosu garage uvicorn")
    assert touch < chown < start


def test_both_backup_scripts_give_the_lock_file_the_volume_owner_before_locking():
    sh = (ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")
    helper = sh[sh.index('docker run -d --name "${HELPER}"'):]
    assert helper.index("stat -c %u:%g /data") < helper.index("exec flock -x")
    loop = (ROOT / "docker" / "backup-loop.sh").read_text(encoding="utf-8")
    body = loop[loop.index("one_backup() {"):]
    assert body.index("fix_lock_owner") < body.index('flock -x "$LOCK"')
    assert 'chown "$(stat -c %u:%g "$DATA")" "$LOCK"' in loop
