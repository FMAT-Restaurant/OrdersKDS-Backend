"""Add order details and item modifiers for order creation and modification.

Revision ID: task10_order_details
Revises: task07_processed_events
Satisfies: RF-12, RF-13, RF-14, RF-15, RNF-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "task10_order_details"
down_revision: str | None = "task07_processed_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Order aggregate additions (RF-12, RF-13, RF-15)
    op.add_column("orders", sa.Column("table_number", sa.Integer(), nullable=True))
    op.add_column(
        "orders",
        sa.Column("is_takeaway", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("orders", sa.Column("created_by", sa.Uuid(), nullable=True))
    op.create_index("ix_orders_table_number", "orders", ["table_number"])

    # Order item additions (RF-14)
    op.add_column("order_items", sa.Column("dish_id", sa.String(length=64), nullable=True))
    op.add_column(
        "order_items",
        sa.Column("modifiers", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )
    op.add_column("order_items", sa.Column("notes", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("order_items", "notes")
    op.drop_column("order_items", "modifiers")
    op.drop_column("order_items", "dish_id")

    op.drop_index("ix_orders_table_number", table_name="orders")
    op.drop_column("orders", "created_by")
    op.drop_column("orders", "is_takeaway")
    op.drop_column("orders", "table_number")
