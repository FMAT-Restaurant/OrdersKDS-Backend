"""Add order lifecycle timestamps and actor identifiers.

Revision ID: task03_order_columns
Revises: 8f2c4a1d9e33
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "task03_order_columns"
down_revision: str | None = "8f2c4a1d9e33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("confirmed_by", sa.Uuid(), nullable=True))
    op.add_column(
        "orders", sa.Column("in_preparation_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("orders", sa.Column("responsible_waiter_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_orders_created_confirmed",
        "orders",
        ["confirmed_at"],
        postgresql_where=sa.text("status = 'CREATED' AND confirmed_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_orders_created_confirmed", table_name="orders")
    op.drop_column("orders", "responsible_waiter_id")
    op.drop_column("orders", "in_preparation_at")
    op.drop_column("orders", "confirmed_by")
    op.drop_column("orders", "confirmed_at")
