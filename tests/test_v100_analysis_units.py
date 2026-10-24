"""V1.0 — two small parts of server/telemetry_analysis.py the suite only reached indirectly:
the shared mirror decision for a map with two laps, and the strictly increasing distance mask."""

from __future__ import annotations

import numpy as np

from server import telemetry_analysis as ta


def test_resolve_mirror_takes_lap_a_then_lap_b_then_not_mirrored(monkeypatch):
    decisions = {"A": (True, False), "B": (True, True)}
    monkeypatch.setattr(ta, "map_mirror", lambda lap: decisions[lap])
    assert ta.resolve_mirror("A", "B") == (True, {"a_decided": False, "b_decided": True})
    decisions["B"] = (True, False)
    assert ta.resolve_mirror("A", "B") == (False, {"a_decided": False, "b_decided": False})
    assert ta.resolve_mirror("A") == (False, {"a_decided": False})
    decisions["A"] = (True, True)
    assert ta.resolve_mirror("A", "B") == (True, {"a_decided": True})


def test_monotonic_axis_keeps_only_rows_that_advance():
    dist = np.array([np.nan, 0.0, 5.0, 5.0, 4.0, 10.0, np.inf, 12.0])
    t = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, np.nan])
    keep = ta._monotonic_axis(dist, t)
    assert keep.tolist() == [False, True, True, False, False, True, False, False]
