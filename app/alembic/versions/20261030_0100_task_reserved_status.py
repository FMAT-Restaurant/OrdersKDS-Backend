"""Add RESERVED order status, CANCELLED item status, rejection details and 1:N dining session.

Revision ID: task_reserved_status
Revises: task28_dining_session
Satisfies: RF-09, RF-18, RF-24, RF-25, RF-34, OP-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "task_reserved_status"
down_revision: str | None = "task28_dining_session"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'RESERVED' AFTER 'CREATED'")
        op.execute("ALTER TYPE item_status ADD VALUE IF NOT EXISTS 'CANCELLED' AFTER 'DELIVERED'")

    op.add_column(
        "orders",
        sa.Column("stock_reserved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("rejection_details", sa.JSON(), nullable=True),
    )
    op.drop_constraint(op.f("uq_orders_dining_session_id"), "orders", type_="unique")
    op.create_index("ix_orders_dining_session_id", "orders", ["dining_session_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_dining_session_id", table_name="orders")
    op.create_unique_constraint(
        op.f("uq_orders_dining_session_id"),
        "orders",
        ["dining_session_id"],
    )
    op.drop_column("orders", "rejection_details")
    op.drop_column("orders", "stock_reserved_at")
