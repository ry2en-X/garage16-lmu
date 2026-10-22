"""Lap files as a 0.9.4 client writes them, for tests that only need "an ordinary clean lap"
(V0.9.9).

Since V0.9.9 a lap is only VALID when it can actually be checked (server/lap_decision.py): the
track-limits signals of its client version are present, frames come at a realistic rate, and
speed and lap distance agree. Older tests built 50-row files with ten columns — such a file is
now (correctly) REVIEW. `clean_lap_df` builds the smallest file that a real client could have
written for a clean lap; `with_signals` adds clean-lap signal columns to an existing frame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

WHEELS = ("fl", "fr", "rl", "rr")


def signal_columns(n: int, *, edge: float = 6.0) -> dict:
    """LMU's track-limits columns for a lap without any offence."""
    return {
        "lap_invalidated": np.zeros(n, dtype=np.float32),
        "track_limits_steps": np.zeros(n, dtype=np.float32),
        "count_lap_flag": np.full(n, 2.0, dtype=np.float32),
        "num_penalties": np.zeros(n, dtype=np.float32),
        "path_lateral": np.zeros(n, dtype=np.float32),
        "track_edge": np.full(n, edge, dtype=np.float32),
        **{f"surface_type_{w}": np.zeros(n, dtype=np.float32) for w in WHEELS},
    }


def with_signals(df: pd.DataFrame) -> pd.DataFrame:
    missing = {k: v for k, v in signal_columns(len(df)).items() if k not in df.columns}
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(missing)], axis=1) if missing else df


def clean_lap_df(lap_time: float = 90.0, speed_ms: float = 50.0, hz: float = 20.0, **extra) -> pd.DataFrame:
    """A straight-line clean lap at constant speed: `hz` frames per second (>= 10 so the frame
    rate is checkable), lap_dist = speed x t, every signal present."""
    n = max(int(round(lap_time * hz)) + 1, 30)
    t = np.linspace(0.0, lap_time, n)
    df = pd.DataFrame({
        "t": t, "lap_dist": speed_ms * t, "speed": np.full(n, speed_ms), "throttle": np.full(n, 0.8),
        "brake": np.zeros(n), "clutch": np.zeros(n), "steering": np.zeros(n), "gear": np.full(n, 4.0),
        "rpm": np.full(n, 6000.0), "fuel": np.linspace(50.0, 48.0, n),
        **signal_columns(n),
    })
    for key, value in extra.items():
        df[key] = value
    return df
