"""Integrity of the received originals and the audit log (V0.9.9)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-20

* laps.telemetry_bytes, laps.upload_request_id, laps.integrity_mac (all nullable): the size of the
  received file, the request id of the upload that stored it, and the server's MAC over the lap's
  identity and file hash (server/integrity.py). Existing laps keep NULL until
  `scripts/integrity_check.py --backfill` has verified their file — no file is read here.
* audit_events: chained integrity / validation / admin events (server/audit.py), plus a trigger
  that refuses changes to stored rows (only clearing driver_id, for a deleted driver, is allowed).

Additive. Downgrade drops the table, the trigger and the three columns (their values are lost; the
laps themselves, their files and verdicts are untouched).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQLITE_TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events "
    "WHEN NEW.id IS NOT OLD.id OR NEW.created_at IS NOT OLD.created_at OR NEW.kind IS NOT OLD.kind "
    "OR NEW.actor IS NOT OLD.actor OR NEW.lap_id IS NOT OLD.lap_id OR NEW.request_id IS NOT OLD.request_id "
    "OR NEW.detail IS NOT OLD.detail OR NEW.prev_mac IS NOT OLD.prev_mac OR NEW.mac IS NOT OLD.mac "
    "OR (NEW.driver_id IS NOT NULL AND NEW.driver_id IS NOT OLD.driver_id) "
    "BEGIN SELECT RAISE(ABORT, 'audit_events rows are append-only'); END"
)
PG_FUNCTION = (
    "CREATE OR REPLACE FUNCTION audit_events_no_update() RETURNS trigger AS $$ BEGIN "
    "IF NEW.id IS DISTINCT FROM OLD.id OR NEW.created_at IS DISTINCT FROM OLD.created_at "
    "OR NEW.kind IS DISTINCT FROM OLD.kind OR NEW.actor IS DISTINCT FROM OLD.actor "
    "OR NEW.lap_id IS DISTINCT FROM OLD.lap_id OR NEW.request_id IS DISTINCT FROM OLD.request_id "
    "OR NEW.detail::text IS DISTINCT FROM OLD.detail::text OR NEW.prev_mac IS DISTINCT FROM OLD.prev_mac "
    "OR NEW.mac IS DISTINCT FROM OLD.mac "
    "OR (NEW.driver_id IS NOT NULL AND NEW.driver_id IS DISTINCT FROM OLD.driver_id) THEN "
    "RAISE EXCEPTION 'audit_events rows are append-only'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
)
PG_TRIGGER = (
    "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION audit_events_no_update()"
)


def upgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        batch_op.add_column(sa.Column("telemetry_bytes", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("upload_request_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("integrity_mac", sa.String(length=120), nullable=True))

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=20), nullable=False),
        sa.Column("lap_id", sa.Integer(), nullable=True),
        sa.Column("driver_id", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("prev_mac", sa.String(length=120), nullable=True),
        sa.Column("mac", sa.String(length=120), nullable=False),
    )
    for column in ("created_at", "kind", "lap_id", "driver_id", "request_id"):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(SQLITE_TRIGGER)
    elif dialect == "postgresql":
        op.execute(PG_FUNCTION)
        op.execute(PG_TRIGGER)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS audit_events_no_update()")
    for column in ("request_id", "driver_id", "lap_id", "kind", "created_at"):
        op.drop_index(f"ix_audit_events_{column}", table_name="audit_events")
    op.drop_table("audit_events")
    with op.batch_alter_table("laps") as batch_op:
        batch_op.drop_column("integrity_mac")
        batch_op.drop_column("upload_request_id")
        batch_op.drop_column("telemetry_bytes")
