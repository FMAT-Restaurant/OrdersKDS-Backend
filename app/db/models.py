"""SQLAlchemy ORM models for orders and order items.

Public identifiers are UUIDs (``public_id``); the integer ``id`` is an
internal surrogate key used only inside repositories. Money uses
``Decimal`` / ``NUMERIC(12,2)`` and datetimes are timezone-aware UTC.

Optimistic concurrency (RNF-09): every table carries a ``version``
column wired as ``version_id_col``, so a stale ``UPDATE`` raises
``StaleDataError``, which repositories translate to ``409
VERSION_CONFLICT``.

Satisfies: RF-18, RF-23, RNF-09
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.order_status import ItemStatus, OrderStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Order(Base):
    """Order aggregate root."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(Uuid, default=uuid.uuid4, unique=True, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status", validate_strings=True),
        default=OrderStatus.CREATED,
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )

    __mapper_args__ = {"version_id_col": version}


class OrderItem(Base):
    """Single line of an order."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(Uuid, default=uuid.uuid4, unique=True, nullable=False)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, name="item_status", validate_strings=True),
        default=ItemStatus.PENDING,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    order: Mapped[Order] = relationship(back_populates="items")

    __mapper_args__ = {"version_id_col": version}
