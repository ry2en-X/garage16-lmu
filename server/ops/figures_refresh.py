"""
figures_refresh.py — brings the stored per-lap figures up to date after a server update (V1.0.3).

Every lap keeps the figures the analysis computed at upload (laps.lap_stats: fuel, tyres, pit lane,
traffic, …, with the analysis version). A newer server may compute more or compute better; until
V1.0.2 the old laps kept their old figures until an operator ran
`python -m scripts.ops.consistency_check --recompute-figures` (the check showed 450 such laps on
the NAS after the V1.0.2 update).

Now docker/entrypoint.sh starts this module in the background once the migrations are done:

    python -m server.ops.figures_refresh

It recomputes the laps whose figures are older than INSIGHTS_VERSION from their telemetry file,
in batches of 25 with a commit after each batch and a short pause after each lap, so uploads and
the pages stay quick on the NAS; it logs its progress and ends when nothing is left. A restart
simply continues with what is still old. Validity and verdicts are not touched (that is
scripts.ops.revalidate_laps). GARAGE16_REFRESH_FIGURES=0 switches it off.
"""

from __future__ import annotations

import logging
import sys

from server.core.database import SessionLocal
from server.core.env import env

logger = logging.getLogger("garage16.figures_refresh")

BATCH = 25
PAUSE_S = 0.05


def enabled() -> bool:
    return env("REFRESH_FIGURES", "1").strip().lower() not in ("0", "false", "no", "off")


def run(batch: int = BATCH, pause_s: float = PAUSE_S) -> dict:
    """Recompute every stale lap; returns the counts of server.ops.consistency.recompute_figures."""
    from server.ops.consistency import recompute_figures, stale_figure_ids

    db = SessionLocal()
    try:
        stale = stale_figure_ids(db)
        if not stale:
            logger.info("All lap figures are up to date.")
            return {"recomputed": 0, "failed": 0, "skipped_without_file": 0}
        logger.info("%s laps have figures of an older analysis; recomputing them in the background.", len(stale))

        def progress(done: int, total: int) -> None:
            logger.info("Lap figures: %s / %s recomputed.", done, total)

        result = recompute_figures(db, lap_ids=stale, batch=batch, pause_s=pause_s, progress=progress)
        logger.info("Lap figures up to date: %s", result)
        return result
    finally:
        db.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[figures] %(message)s")
    if not enabled():
        logger.info("GARAGE16_REFRESH_FIGURES=0: the stored lap figures are not recomputed.")
        return 0
    try:
        run()
    except Exception:  # noqa: BLE001 — a background helper must never take the server down
        logger.exception("Recomputing the lap figures failed; the server is not affected. "
                         "By hand: python -m scripts.ops.consistency_check --recompute-figures")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
