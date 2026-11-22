"""V1.0.3: laps whose stored figures an older analysis computed are recomputed in the background
after an update (server/ops/figures_refresh.py, started by docker/entrypoint.sh) — on the NAS 450
laps were left with old figures after the V1.0.2 update until someone ran the check by hand."""

from __future__ import annotations

from pathlib import Path

from server.core.database import SessionLocal
from server.core.models import Lap
from server.analysis.lap_insights import INSIGHTS_VERSION
from server.ops import figures_refresh
from server.ops.consistency import recompute_figures, stale_figure_ids
from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import register, upload_df

ROOT = Path(__file__).resolve().parents[2]


def _age(lap_ids, version=1):
    with SessionLocal() as db:
        for lap_id in lap_ids:
            lap = db.get(Lap, lap_id)
            stats = dict(lap.lap_stats or {})
            stats["version"] = version
            stats["session"] = {"kept": True}
            lap.lap_stats = stats
        db.commit()


def test_stale_laps_are_found_and_recomputed_in_batches(client):
    owner = register(client, "Figures")
    ids = [upload_df(client, owner, make_lap(seed=60 + n, n=1900, lap_time=90.0 + n * 0.1), lap_time=90.0 + n * 0.1, lap_number=n + 1)["lap_id"]
           for n in range(5)]
    with SessionLocal() as db:
        assert not set(ids) & set(stale_figure_ids(db)), "fresh uploads are current"
    _age(ids[:3])
    with SessionLocal() as db:
        assert set(stale_figure_ids(db)) >= set(ids[:3])
        seen = []
        result = recompute_figures(db, batch=2, progress=lambda done, total: seen.append((done, total)))
    assert result["recomputed"] >= 3 and result["failed"] == 0
    assert seen[0][0] == 2 and seen[-1][0] == seen[-1][1], "progress after every batch and at the end"
    with SessionLocal() as db:
        for lap_id in ids[:3]:
            stats = db.get(Lap, lap_id).lap_stats
            assert stats["version"] == INSIGHTS_VERSION
            assert stats["session"] == {"kept": True}, "the upload's session settings are kept"
        assert not set(ids) & set(stale_figure_ids(db))


def test_the_background_run_does_nothing_when_up_to_date_and_can_be_switched_off(client, monkeypatch):
    figures_refresh.run()   # whatever earlier tests left behind
    assert figures_refresh.run()["recomputed"] == 0, "a second run finds nothing"
    monkeypatch.setenv("GARAGE16_REFRESH_FIGURES", "0")
    assert figures_refresh.enabled() is False
    assert figures_refresh.main() == 0
    monkeypatch.setenv("GARAGE16_REFRESH_FIGURES", "1")
    assert figures_refresh.enabled() is True


def test_an_error_never_takes_the_server_down(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(figures_refresh, "run", boom)
    assert figures_refresh.main() == 1


def test_the_entrypoint_starts_it_in_the_background_with_low_priority():
    entry = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    line = next(x for x in entry.splitlines() if "server.ops.figures_refresh" in x and not x.lstrip().startswith("#"))
    assert line.rstrip().endswith("&") and "nice" in line and "gosu garage" in line
    assert entry.index("alembic upgrade head") < entry.index(line) < entry.index("exec gosu garage uvicorn")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    server = compose.split("\n  server:", 1)[1].split("\n  reverse_proxy:", 1)[0]
    assert "init: true" in server, "PID 1 must reap the finished background process"


def test_a_lap_deleted_during_the_run_only_skips_that_lap(client):
    """V1.0.3 review: the run takes minutes; a lap deleted meanwhile must not end it."""
    owner = register(client, "Figures2")
    ids = [upload_df(client, owner, make_lap(seed=80 + n, n=1900, lap_time=91.0 + n * 0.1), lap_time=91.0 + n * 0.1, lap_number=n + 1)["lap_id"]
           for n in range(3)]
    _age(ids)
    with SessionLocal() as db:
        result = recompute_figures(db, lap_ids=[ids[0], 999999, ids[2]], batch=1)
    assert result["recomputed"] == 2 and result["skipped_without_file"] == 1
