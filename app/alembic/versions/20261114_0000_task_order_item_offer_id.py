"""Add offer_id to order_items to separate commercial offerId from recipe sourceId.

Revision ID: task_order_item_offer_id
Revises: task_reserved_status
Satisfies: RF-14, RF-17, RF-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "task_order_item_offer_id"
down_revision: str | None = "task_reserved_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "order_items",
        sa.Column("offer_id", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_items", "offer_id")
