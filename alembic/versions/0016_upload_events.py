"""Upload events for the operations view (V0.9.7)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-17

* upload_events: one row per upload attempt — outcome (accepted / duplicate / rejected / error),
  HTTP status, a short reason, the request id (X-Request-ID), the lap id when one was stored,
  client version, file size and duration. Read by GET /admin/operations (error rates, reasons,
  slow uploads). Rows are kept for 30 days.

Purely additive. Downgrade drops the table.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "upload_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("driver_id", sa.Integer(), nullable=True),
        sa.Column("lap_id", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("client_version", sa.String(), nullable=True),
        sa.Column("track_name", sa.String(), nullable=True),
        sa.Column("file_bytes", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
    )
    op.create_index("ix_upload_events_created_at", "upload_events", ["created_at"])
    op.create_index("ix_upload_events_request_id", "upload_events", ["request_id"])
    op.create_index("ix_upload_events_driver_id", "upload_events", ["driver_id"])


def downgrade() -> None:
    op.drop_index("ix_upload_events_driver_id", table_name="upload_events")
    op.drop_index("ix_upload_events_request_id", table_name="upload_events")
    op.drop_index("ix_upload_events_created_at", table_name="upload_events")
    op.drop_table("upload_events")
