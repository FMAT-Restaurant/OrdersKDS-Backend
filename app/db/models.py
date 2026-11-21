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

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.db.base import Base
from app.domain.constants import MAX_CATALOG_ID_LENGTH, MAX_NOTE_LENGTH
from app.domain.errors import InvalidStateTransitionError, ShiftClosedError
from app.domain.order_status import (
    ItemStatus,
    OrderStatus,
    ensure_item_transition_allowed,
    ensure_transition_allowed,
)
from app.domain.shift import ShiftStatus, ensure_shift_can_close


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Shift(Base):
    """Operational kitchen shift scoping sequential per-shift order folios."""

    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(Uuid, default=uuid.uuid4, unique=True, nullable=False)
    shift_number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    status: Mapped[ShiftStatus] = mapped_column(
        Enum(ShiftStatus, name="shift_status_enum", native_enum=False, validate_strings=True),
        default=ShiftStatus.OPEN,
        nullable=False,
        index=True,
    )
    last_order_folio: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    opened_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(MAX_NOTE_LENGTH), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}

    def allocate_next_folio(self) -> int:
        """Increment and return the next 1-based folio within this open shift."""
        if self.status != ShiftStatus.OPEN:
            raise ShiftClosedError()
        self.last_order_folio = (self.last_order_folio or 0) + 1
        return self.last_order_folio

    def close(self, *, closed_by: UUID, notes: str | None = None) -> None:
        """Transition this open shift to CLOSED."""
        ensure_shift_can_close(self.status)
        self.status = ShiftStatus.CLOSED
        self.closed_at = _utcnow()
        self.closed_by = closed_by
        if notes is not None:
            self.notes = notes


class Order(Base):
    """Order aggregate root."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("shift_id", "shift_folio", name="uq_orders_shift_id_shift_folio"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(Uuid, default=uuid.uuid4, unique=True, nullable=False)
    shift_id: Mapped[int | None] = mapped_column(
        ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    shift_folio: Mapped[int | None] = mapped_column(Integer, nullable=True)
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

    def mark_wasted(self) -> None:
        """Transition order to WASTED (administrative void, RF-26)."""
        ensure_transition_allowed(self.status, OrderStatus.WASTED)
        self.status = OrderStatus.WASTED

    def mark_voided(self) -> None:
        """Transition order to VOIDED (kitchen error void, RF-28)."""
        ensure_transition_allowed(self.status, OrderStatus.VOIDED)
        self.status = OrderStatus.VOIDED

    def mark_paid(self) -> None:
        """Transition order to PAID."""
        ensure_transition_allowed(self.status, OrderStatus.PAID)
        self.status = OrderStatus.PAID

    def mark_cancelled(self) -> None:
        """Transition order to CANCELLED (RF-24)."""
        ensure_transition_allowed(self.status, OrderStatus.CANCELLED)
        self.status = OrderStatus.CANCELLED

    def save_pre_redo_note(self, item: OrderItem) -> None:
        """Persist pre-redo item notes in order metadata for later restoration."""
        details: dict[str, object] = (
            dict(self.rejection_details) if isinstance(self.rejection_details, dict) else {}
        )
        raw_map = details.get("redoOriginalNotes")
        redo_map: dict[str, str | None] = (
            {str(k): (str(v) if v is not None else None) for k, v in raw_map.items()}
            if isinstance(raw_map, dict)
            else {}
        )
        item_key = str(item.public_id)
        if item_key not in redo_map and not getattr(item, "_has_pre_redo_notes", False):
            redo_map[item_key] = item.notes
        details["redoOriginalNotes"] = redo_map
        self.rejection_details = details

    def restore_pre_redo_note(self, item: OrderItem) -> None:
        """Restore pre-redo item notes once a redo finishes or is compensated."""
        details: dict[str, object] = (
            dict(self.rejection_details) if isinstance(self.rejection_details, dict) else {}
        )
        raw_map = details.get("redoOriginalNotes")
        item_key = str(item.public_id)
        if isinstance(raw_map, dict) and item_key in raw_map:
            redo_map: dict[str, object] = dict(raw_map)
            raw_original = redo_map.pop(item_key)
            original_note = str(raw_original) if raw_original is not None else None
            item.restore_notes_after_redo(fallback_original=original_note, has_fallback=True)
            if redo_map:
                details["redoOriginalNotes"] = redo_map
            else:
                details.pop("redoOriginalNotes", None)
            self.rejection_details = details or None
            return
        if getattr(item, "_has_pre_redo_notes", False):
            item.restore_notes_after_redo()

    def record_redo_rejection(
        self,
        *,
        item: OrderItem,
        reason: str | None,
        missing_ingredients: list[str],
        redo_id: UUID | None = None,
    ) -> None:
        """Store asynchronous redo rejection metadata without leaving DELIVERED status."""
        details: dict[str, object] = (
            dict(self.rejection_details) if isinstance(self.rejection_details, dict) else {}
        )
        details["redoRejection"] = {
            "source": "inventory_redo",
            "itemId": str(item.public_id),
            "itemName": item.name,
            "redoId": str(redo_id) if redo_id else None,
            "reason": reason or "Stock insuficiente para rehechura",
            "missingIngredients": list(missing_ingredients),
        }
        self.rejection_details = details

    def clear_redo_rejection(self, item_id: UUID | None = None) -> None:
        """Remove stored redo rejection metadata globally or for one matching item."""
        if (
            not isinstance(self.rejection_details, dict)
            or "redoRejection" not in self.rejection_details
        ):
            return
        details = dict(self.rejection_details)
        raw_redo = details.get("redoRejection")
        if item_id is not None and isinstance(raw_redo, dict):
            recorded_item_id = raw_redo.get("itemId")
            if recorded_item_id and str(recorded_item_id) != str(item_id):
                return
        details.pop("redoRejection", None)
        self.rejection_details = details or None


class OrderItem(Base):
    """Single line of an order."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(Uuid, default=uuid.uuid4, unique=True, nullable=False)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dish_id: Mapped[str | None] = mapped_column(String(MAX_CATALOG_ID_LENGTH), nullable=True)
    offer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    modifiers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(String(MAX_NOTE_LENGTH), nullable=True)
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

    def mark_pending_for_redo(self, note: str) -> None:
        """Return a delivered item to kitchen with the waiter's explanation (RF-22)."""
        ensure_item_transition_allowed(self.status, ItemStatus.PENDING)
        if not getattr(self, "_has_pre_redo_notes", False):
            object.__setattr__(self, "_pre_redo_notes", self.notes)
            object.__setattr__(self, "_has_pre_redo_notes", True)
        self.status = ItemStatus.PENDING
        self.notes = note

    def restore_notes_after_redo(
        self,
        *,
        fallback_original: str | None = None,
        has_fallback: bool = False,
    ) -> None:
        """Restore pre-redo preparation notes after kitchen completes a redone item."""
        if getattr(self, "_has_pre_redo_notes", False):
            original = getattr(self, "_pre_redo_notes", None)
            self.notes = original if isinstance(original, str) or original is None else None
            object.__setattr__(self, "_has_pre_redo_notes", False)
        elif has_fallback:
            self.notes = fallback_original

    def mark_cancelled(self) -> None:
        """Transition item to CANCELLED."""
        ensure_item_transition_allowed(self.status, ItemStatus.CANCELLED)
        self.status = ItemStatus.CANCELLED

    def revert_redo_to_delivered(self) -> None:
        """Compensate a rejected redo by returning a PENDING item back to DELIVERED."""
        if self.status != ItemStatus.PENDING:
            raise InvalidStateTransitionError(self.status, ItemStatus.DELIVERED)
        self.status = ItemStatus.DELIVERED


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
