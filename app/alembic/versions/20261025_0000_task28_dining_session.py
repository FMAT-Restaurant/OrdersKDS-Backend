"""Store the dining session correlation on orders."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "task28_dining_session"
down_revision: str | None = "task10_order_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("dining_session_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        op.f("uq_orders_dining_session_id"),
        "orders",
        ["dining_session_id"],
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_orders_dining_session_id"), "orders", type_="unique")
    op.drop_column("orders", "dining_session_id")
