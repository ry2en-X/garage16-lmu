"""what each lap was judged with: laps.check_inputs (V0.9.3.1)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-11

The track-limits verdict (server/track_limits.py) depends on things that are NOT in the
lap's telemetry file: what the client concluded and why, its game summary, the samples
recorded after the finish line, LMU's step/point ratios of the session and the late window
after the line. Until now they were only used at upload and then lost, so a lap rejected
by a heuristic of an older client stayed rejected for good. laps.check_inputs keeps them,
so scripts/revalidate_laps.py can judge every stored lap again — with the CURRENT rules —
without a new upload.

Existing laps: what can still be recovered is copied in (no lap's validity changes here):
  * client_valid      from laps.client_claimed_valid
  * client_state      the 0.9.3 client's game summary, stored inside the verdict's
                      "client_invalid" finding (laps.validation)
  * steps_per_point / steps_per_penalty  from laps.lap_stats["session"]
  * sectors_ok        from laps.sector_times
What was never stored (the tail samples, the client's reasons) stays unknown (null).

Also an index for finding a lap's neighbour (the lap before / after it of the same
driver), used to attribute track-limits reports that LMU sends after the finish line.
"""
from __future__ import annotations

import json
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BATCH = 500


def _json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _sectors_ok(sectors) -> Optional[bool]:
    sectors = _json(sectors)
    if not isinstance(sectors, list) or len(sectors) < 2:
        return None
    try:
        return float(sectors[0]) > 0 and float(sectors[1]) > 0
    except (TypeError, ValueError):
        return None


def inputs_for(client_valid, validation, lap_stats, sector_times) -> dict:
    """KEEP IN SYNC with server/lap_check.py legacy_inputs (enforced by a test)."""
    state = None
    verdict = _json(validation)
    if isinstance(verdict, dict):
        for finding in verdict.get("findings") or []:
            if isinstance(finding, dict) and finding.get("code") == "client_invalid":
                detail = finding.get("detail")
                if isinstance(detail, dict) and detail and "state" not in detail and "reasons" not in detail:
                    state = detail
    stats = _json(lap_stats)
    session = stats.get("session") if isinstance(stats, dict) else None
    session = session if isinstance(session, dict) else {}
    return {
        "version": 1,
        "client_valid": bool(client_valid) if client_valid is not None else True,
        "client_reasons": None,
        "client_state": state,
        "late": None,
        "tail_samples": None,
        "steps_per_point": session.get("track_limits_steps_per_point"),
        "steps_per_penalty": session.get("track_limits_steps_per_penalty"),
        "sectors_ok": _sectors_ok(sector_times),
        "recovered": True,
    }


def upgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        batch_op.add_column(sa.Column("check_inputs", sa.JSON(), nullable=True))
        batch_op.create_index("ix_laps_driver_track_lapno", ["driver_id", "track_name", "lap_number"])

    conn = op.get_bind()
    # read untyped (raw text on SQLite, already decoded on PostgreSQL — _json handles both,
    # and a value that is not JSON at all is treated as missing instead of failing)
    source = sa.table(
        "laps", sa.column("id", sa.Integer), sa.column("client_claimed_valid", sa.Boolean),
        sa.column("validation"), sa.column("lap_stats"), sa.column("sector_times"),
    )
    laps = sa.table("laps", sa.column("id", sa.Integer), sa.column("check_inputs", sa.JSON))
    last_id = 0
    while True:
        rows = conn.execute(
            sa.select(source.c.id, source.c.client_claimed_valid, source.c.validation, source.c.lap_stats,
                      source.c.sector_times)
            .where(source.c.id > last_id).order_by(source.c.id).limit(BATCH)
        ).fetchall()
        if not rows:
            break
        for lap_id, client_valid, validation, lap_stats, sectors in rows:
            conn.execute(
                laps.update().where(laps.c.id == lap_id)
                .values(check_inputs=inputs_for(client_valid, validation, lap_stats, sectors))
            )
        last_id = rows[-1][0]


def downgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        batch_op.drop_index("ix_laps_driver_track_lapno")
        batch_op.drop_column("check_inputs")
