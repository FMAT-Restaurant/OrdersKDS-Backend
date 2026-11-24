"""Add composite performance indexes on orders and order_items.

Revision ID: 20261124_perf_indexes
Revises: 20261119_shifts_folio
Satisfies: DEF-E5-021, RNF-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261124_perf_indexes"
down_revision: str | None = "20261119_shifts_folio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_orders_status_in_prep_created",
        "orders",
        ["status", "in_preparation_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_orders_created_at_public_id",
        "orders",
        ["created_at", "public_id"],
        unique=False,
    )
    op.create_index(
        "ix_order_items_order_id_status",
        "order_items",
        ["order_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_order_items_order_id_status", table_name="order_items")
    op.drop_index("ix_orders_created_at_public_id", table_name="orders")
    op.drop_index("ix_orders_status_in_prep_created", table_name="orders")
