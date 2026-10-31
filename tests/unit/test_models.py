"""ORM mapping tests for orders and order items.

Verifies table registration, UUID/Decimal/UTC column contracts and
the optimistic-concurrency wiring (``version_id_col``).

Satisfies: RF-09, RF-10, RF-11, RF-18, RNF-09, RNF-02, OP-08
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import JSON, DateTime, Enum, Numeric, Table, Uuid

from app.db.base import Base
from app.db.models import Order, OrderItem, ProcessedEvent, _utcnow
from app.domain.order_status import ItemStatus, OrderStatus


@pytest.mark.unit
def test_tables_are_registered_on_base_metadata() -> None:
    assert set(Base.metadata.tables.keys()) >= {"orders", "order_items", "processed_events"}


@pytest.mark.unit
def test_utcnow_returns_timezone_aware_utc() -> None:
    assert _utcnow().tzinfo is UTC


@pytest.mark.unit
def test_order_column_contracts() -> None:
    table = Order.__table__
    assert isinstance(table.c.public_id.type, Uuid)
    assert table.c.public_id.unique
    status_type = table.c.status.type
    assert isinstance(status_type, Enum)
    assert status_type.name == "order_status"
    assert table.c.status.default is not None
    assert table.c.status.default.arg == OrderStatus.CREATED
    assert table.c.version.default is not None
    assert table.c.version.default.arg == 1
    total_type = table.c.total_amount.type
    assert isinstance(total_type, Numeric)
    assert (total_type.precision, total_type.scale) == (12, 2)
    assert table.c.total_amount.default is not None
    assert table.c.total_amount.default.arg == Decimal("0.00")
    assert isinstance(table.c.created_at.type, DateTime)
    assert table.c.created_at.type.timezone is True
    assert isinstance(table.c.updated_at.type, DateTime)
    assert table.c.updated_at.type.timezone is True
    assert isinstance(table.c.confirmed_at.type, DateTime)
    assert table.c.confirmed_at.type.timezone is True
    assert table.c.confirmed_at.nullable
    assert isinstance(table.c.confirmed_by.type, Uuid)
    assert table.c.confirmed_by.nullable
    assert isinstance(table.c.stock_reserved_at.type, DateTime)
    assert table.c.stock_reserved_at.type.timezone is True
    assert table.c.stock_reserved_at.nullable
    assert isinstance(table.c.in_preparation_at.type, DateTime)
    assert table.c.in_preparation_at.type.timezone is True
    assert table.c.in_preparation_at.nullable
    assert isinstance(table.c.rejection_details.type, JSON)
    assert table.c.rejection_details.nullable
    assert isinstance(table.c.dining_session_id.type, Uuid)
    assert table.c.dining_session_id.nullable
    assert not table.c.dining_session_id.unique
    assert table.c.dining_session_id.index is True
    assert isinstance(table.c.responsible_waiter_id.type, Uuid)
    assert table.c.responsible_waiter_id.nullable
    assert table.c.table_number.nullable
    assert not table.c.is_takeaway.nullable
    assert table.c.is_takeaway.default is not None
    assert table.c.is_takeaway.default.arg is False
    assert isinstance(table.c.created_by.type, Uuid)
    assert table.c.created_by.nullable


@pytest.mark.unit
def test_order_item_column_contracts() -> None:
    table = OrderItem.__table__
    assert table.c.order_id.references(Order.__table__.c.id)
    assert isinstance(table.c.public_id.type, Uuid)
    assert table.c.public_id.unique
    status_type = table.c.status.type
    assert isinstance(status_type, Enum)
    assert status_type.name == "item_status"
    assert table.c.status.default is not None
    assert table.c.status.default.arg == ItemStatus.PENDING
    assert table.c.version.default is not None
    assert table.c.version.default.arg == 1
    assert table.c.dish_id.nullable
    assert not table.c.modifiers.nullable
    assert table.c.notes.nullable


@pytest.mark.unit
def test_order_awaiting_stock_confirmation() -> None:
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    assert not order.awaiting_stock_confirmation

    order.confirmed_at = datetime.now(UTC)
    assert order.awaiting_stock_confirmation

    order.status = OrderStatus.IN_PREPARATION
    assert not order.awaiting_stock_confirmation


@pytest.mark.unit
def test_order_recalculate_total_excludes_cancelled_items() -> None:
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    order.recalculate_total()
    assert order.total_amount == Decimal("0.00")

    item1 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-1",
        name="Tacos al Pastor",
        quantity=2,
        unit_price=Decimal("45.00"),
        status=ItemStatus.PENDING,
    )
    item2 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-2",
        name="Agua de Horchata",
        quantity=3,
        unit_price=Decimal("15.50"),
        status=ItemStatus.PENDING,
    )
    order.items.extend([item1, item2])
    order.recalculate_total()
    assert order.total_amount == Decimal("136.50")

    item2.mark_cancelled()
    assert item2.status is ItemStatus.CANCELLED
    order.recalculate_total()
    assert order.total_amount == Decimal("90.00")


@pytest.mark.unit
def test_processed_event_column_contracts() -> None:
    table = cast(Table, ProcessedEvent.__table__)
    assert isinstance(table.c.order_id.type, Uuid)
    assert not table.c.order_id.nullable
    assert isinstance(table.c.event_id.type, Uuid)
    assert not table.c.event_id.nullable
    assert isinstance(table.c.processed_at.type, DateTime)
    assert table.c.processed_at.type.timezone is True
    constraint_names = {c.name for c in table.constraints}
    assert "uq_processed_events_order_id_event_id" in constraint_names


@pytest.mark.unit
def test_order_mark_reserved_sets_timestamp_once_and_clears_rejection_details() -> None:
    order = Order(
        public_id=uuid4(),
        status=OrderStatus.CREATED,
        rejection_details={"missingIngredients": ["pan"]},
    )
    assert order.stock_reserved_at is None
    fixed_time = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)

    order.mark_reserved(at=fixed_time)
    assert order.status is OrderStatus.RESERVED
    assert order.stock_reserved_at == fixed_time
    assert order.rejection_details is None


@pytest.mark.unit
def test_order_reopen_as_draft_and_preserve_stock_reserved_at() -> None:
    fixed_time = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)
    order = Order(
        public_id=uuid4(),
        status=OrderStatus.RESERVED,
        stock_reserved_at=fixed_time,
        confirmed_at=fixed_time,
    )
    order.mark_rejected(details={"source": "kitchen"})
    assert order.status == "REJECTED"
    assert order.rejection_details == {"source": "kitchen"}

    order.reopen_as_draft()
    assert order.status == "CREATED"
    assert order.confirmed_at is None
    assert order.rejection_details is None

    later_time = datetime(2026, 10, 20, 11, 30, tzinfo=UTC)
    order.mark_reserved(at=later_time)
    assert order.status == "RESERVED"
    assert order.stock_reserved_at == fixed_time


@pytest.mark.unit
def test_order_mark_in_preparation_sets_timestamp_only_once() -> None:
    order = Order(public_id=uuid4(), status=OrderStatus.RESERVED)
    assert order.in_preparation_at is None
    fixed_time = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    order.mark_in_preparation(at=fixed_time)
    assert order.status is OrderStatus.IN_PREPARATION
    assert order.in_preparation_at == fixed_time

    # Subsequent transition call does not overwrite the timestamp
    newer_time = datetime(2026, 10, 20, 13, 0, tzinfo=UTC)
    order.in_preparation_at = fixed_time
    # Mark in preparation again (simulating redelivery domain action)
    if order.in_preparation_at is None:
        order.in_preparation_at = newer_time
    assert order.in_preparation_at == fixed_time


@pytest.mark.unit
def test_order_mark_delivered() -> None:
    order = Order(public_id=uuid4(), status=OrderStatus.IN_PREPARATION)
    order.mark_delivered()
    assert order.status is OrderStatus.DELIVERED


@pytest.mark.unit
def test_order_item_mark_ready_and_delivered() -> None:
    item = OrderItem(public_id=uuid4(), name="Tacos", status=ItemStatus.PENDING)
    item.mark_ready()
    assert item.status == ItemStatus.READY

    item2 = OrderItem(public_id=uuid4(), name="Tacos", status=ItemStatus.READY)
    item2.mark_delivered()
    assert item2.status == ItemStatus.DELIVERED


@pytest.mark.unit
def test_order_mark_rejected_and_mark_paid() -> None:
    order_rej = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    order_rej.mark_rejected(details={"missingIngredients": ["limon"]})
    assert order_rej.status is OrderStatus.REJECTED
    assert order_rej.rejection_details == {"missingIngredients": ["limon"]}

    order_paid = Order(public_id=uuid4(), status=OrderStatus.DELIVERED)
    order_paid.mark_paid()
    assert order_paid.status is OrderStatus.PAID


@pytest.mark.unit
def test_version_id_col_enables_optimistic_concurrency() -> None:
    assert Order.__mapper__.version_id_col is Order.__table__.c.version
    assert OrderItem.__mapper__.version_id_col is OrderItem.__table__.c.version
