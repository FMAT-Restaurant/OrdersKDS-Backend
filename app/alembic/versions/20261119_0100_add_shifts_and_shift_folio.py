"""Create operational shifts table and add per-shift folio columns on orders.

Revision ID: 20261119_shifts_folio
Revises: 20261119_widen_dish_id_100
Satisfies: RF-12, RF-15, RNF-03, RNF-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261119_shifts_folio"
down_revision: str | None = "20261119_widen_dish_id_100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shifts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.Uuid(), nullable=False),
        sa.Column("shift_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("OPEN", "CLOSED", name="shift_status_enum", native_enum=False),
            nullable=False,
        ),
        sa.Column("last_order_folio", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_by", sa.Uuid(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("shift_number"),
    )
    op.create_index("ix_shifts_status", "shifts", ["status"], unique=False)

    op.add_column("orders", sa.Column("shift_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("shift_folio", sa.Integer(), nullable=True))
    op.create_index("ix_orders_shift_id", "orders", ["shift_id"], unique=False)
    op.create_foreign_key(
        "fk_orders_shift_id_shifts",
        "orders",
        "shifts",
        ["shift_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_orders_shift_id_shift_folio",
        "orders",
        ["shift_id", "shift_folio"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_orders_shift_id_shift_folio", "orders", type_="unique")
    op.drop_constraint("fk_orders_shift_id_shifts", "orders", type_="foreignkey")
    op.drop_index("ix_orders_shift_id", table_name="orders")
    op.drop_column("orders", "shift_folio")
    op.drop_column("orders", "shift_id")
    op.drop_index("ix_shifts_status", table_name="shifts")
    op.drop_table("shifts")
