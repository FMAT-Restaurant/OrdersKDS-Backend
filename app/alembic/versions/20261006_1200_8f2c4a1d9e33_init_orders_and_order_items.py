"""init orders and order items

Revision ID: 8f2c4a1d9e33
Revises:
Create Date: 2026-10-06 12:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f2c4a1d9e33"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enum values are inlined (not imported from app.domain) so this
# migration stays immutable even if the domain enums change later.
ORDER_STATUSES = (
    "CREATED",
    "IN_PREPARATION",
    "DELIVERED",
    "PAID",
    "CANCELLED",
    "REJECTED",
    "WASTED",
    "VOIDED",
    "EXPIRED",
)
ITEM_STATUSES = ("PENDING", "READY", "DELIVERED")


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*ORDER_STATUSES, name="order_status"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("public_id", name=op.f("uq_orders_public_id")),
    )
    op.create_index(op.f("ix_orders_status"), "orders", ["status"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*ITEM_STATUSES, name="item_status"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_items_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
        sa.UniqueConstraint("public_id", name=op.f("uq_order_items_public_id")),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")
    sa.Enum(name="item_status").drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f("ix_orders_status"), table_name="orders")
    op.drop_table("orders")
    sa.Enum(name="order_status").drop(op.get_bind(), checkfirst=True)
