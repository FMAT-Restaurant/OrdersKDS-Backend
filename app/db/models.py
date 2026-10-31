"""SQLAlchemy ORM models for orders and order items.

Public identifiers are UUIDs (``public_id``); the integer ``id`` is an
internal surrogate key used only inside repositories. Money uses
``Decimal`` / ``NUMERIC(12,2)`` and datetimes are timezone-aware UTC.

Optimistic concurrency (RNF-09): every table carries a ``version``
column wired as ``version_id_col``, so a stale ``UPDATE`` raises
``StaleDataError``, which repositories translate to ``409
VERSION_CONFLICT``.

Satisfies: RF-09, RF-10, RF-11, RF-18, RF-21, RF-23, RF-33, RF-34, RNF-02, RNF-09, OP-08
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.db.base import Base
from app.domain.order_status import (
    ItemStatus,
    OrderStatus,
    ensure_item_transition_allowed,
    ensure_transition_allowed,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Order(Base):
    """Order aggregate root."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(Uuid, default=uuid.uuid4, unique=True, nullable=False)
    order_id = synonym("public_id")
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
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    stock_reserved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    in_preparation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_details: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    dining_session_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    responsible_waiter_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    table_number: Mapped[int | None] = mapped_column(nullable=True, index=True)
    is_takeaway: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )

    __mapper_args__ = {"version_id_col": version}

    @property
    def awaiting_stock_confirmation(self) -> bool:
        """Whether order was confirmed and is awaiting stock reservation (OP-05)."""
        return self.status == OrderStatus.CREATED and self.confirmed_at is not None

    def recalculate_total(self) -> None:
        """Recalculate total_amount from active (non-cancelled) item prices and quantities."""
        self.total_amount = sum(
            (
                item.unit_price * item.quantity
                for item in self.items
                if item.status != ItemStatus.CANCELLED
            ),
            start=Decimal("0.00"),
        )

    def mark_reserved(self, at: datetime | None = None) -> None:
        """Transition order to RESERVED and record timestamp once for KDS priority."""
        ensure_transition_allowed(self.status, OrderStatus.RESERVED)
        self.status = OrderStatus.RESERVED
        if self.stock_reserved_at is None:
            self.stock_reserved_at = at or _utcnow()
        self.rejection_details = None

    def mark_in_preparation(self, at: datetime | None = None) -> None:
        """Transition order to IN_PREPARATION and record timestamp once (OP-11)."""
        ensure_transition_allowed(self.status, OrderStatus.IN_PREPARATION)
        self.status = OrderStatus.IN_PREPARATION
        if self.in_preparation_at is None:
            self.in_preparation_at = at or _utcnow()

    def mark_delivered(self) -> None:
        """Transition order to DELIVERED (OP-08)."""
        ensure_transition_allowed(self.status, OrderStatus.DELIVERED)
        self.status = OrderStatus.DELIVERED

    def mark_rejected(self, details: dict[str, object] | None = None) -> None:
        """Transition order to REJECTED and record optional rejection details."""
        ensure_transition_allowed(self.status, OrderStatus.REJECTED)
        self.status = OrderStatus.REJECTED
        self.rejection_details = details

    def reopen_as_draft(self) -> None:
        """Reopen a REJECTED order back to CREATED draft state for editing."""
        ensure_transition_allowed(self.status, OrderStatus.CREATED)
        self.status = OrderStatus.CREATED
        self.confirmed_at = None
        self.confirmed_by = None
        self.rejection_details = None

    def mark_expired(self) -> None:
        """Transition order to EXPIRED after the saga timeout (RF-35)."""
        ensure_transition_allowed(self.status, OrderStatus.EXPIRED)
        self.status = OrderStatus.EXPIRED

    def mark_paid(self) -> None:
        """Transition order to PAID."""
        ensure_transition_allowed(self.status, OrderStatus.PAID)
        self.status = OrderStatus.PAID

    def mark_cancelled(self) -> None:
        """Transition order to CANCELLED (RF-24)."""
        ensure_transition_allowed(self.status, OrderStatus.CANCELLED)
        self.status = OrderStatus.CANCELLED


class OrderItem(Base):
    """Single line of an order."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(Uuid, default=uuid.uuid4, unique=True, nullable=False)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dish_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    modifiers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
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

    def mark_ready(self) -> None:
        """Transition item to READY (RF-09, RF-10)."""
        ensure_item_transition_allowed(self.status, ItemStatus.READY)
        self.status = ItemStatus.READY

    def mark_delivered(self) -> None:
        """Transition item to DELIVERED (OP-08)."""
        ensure_item_transition_allowed(self.status, ItemStatus.DELIVERED)
        self.status = ItemStatus.DELIVERED

    def mark_cancelled(self) -> None:
        """Transition item to CANCELLED."""
        ensure_item_transition_allowed(self.status, ItemStatus.CANCELLED)
        self.status = ItemStatus.CANCELLED


class Outbox(Base):
    """Event waiting to be published after its domain transaction commits."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(Uuid, unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessedEvent(Base):
    """Records consumed events to enforce consumer idempotency (RNF-02).

    Compound unique constraint on (order_id, event_id).
    """

    __tablename__ = "processed_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    event_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    event_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("order_id", "event_id", name="uq_processed_events_order_id_event_id"),
    )
