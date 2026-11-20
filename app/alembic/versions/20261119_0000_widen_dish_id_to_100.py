"""Widen order_items.dish_id column from 64 to 100 characters to align with Inventory plateId.

Revision ID: 20261119_widen_dish_id_100
Revises: task_order_item_offer_id
Satisfies: RF-14, RF-17, RNF-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261119_widen_dish_id_100"
down_revision: str | None = "task_order_item_offer_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "order_items",
        "dish_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=100),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "order_items",
        "dish_id",
        existing_type=sa.String(length=100),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
