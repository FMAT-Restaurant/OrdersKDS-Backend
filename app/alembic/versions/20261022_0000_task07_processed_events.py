"""Create processed_events table for consumer idempotency.

Revision ID: task07_processed_events
Revises: task04_outbox
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "task07_processed_events"
down_revision: str | None = "task04_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processed_events")),
        sa.UniqueConstraint(
            "order_id",
            "event_id",
            name=op.f("uq_processed_events_order_id_event_id"),
        ),
    )
    op.create_index(
        "ix_processed_events_order_id",
        "processed_events",
        ["order_id"],
    )
    op.create_index(
        "ix_processed_events_event_id",
        "processed_events",
        ["event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_processed_events_event_id", table_name="processed_events")
    op.drop_index("ix_processed_events_order_id", table_name="processed_events")
    op.drop_table("processed_events")
