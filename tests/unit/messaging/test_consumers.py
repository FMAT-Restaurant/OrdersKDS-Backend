"""Unit tests for idempotent consumers and DLQ routing.

Satisfies: RF-18, RF-21, RF-33, RF-34, RF-35, RNF-02, RNF-13, OP-11
Covers: UT-BE-38, UT-BE-39, UT-BE-40, UT-BE-41, UT-BE-42, UT-BE-47
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from app.db.models import Order
from app.domain.order_status import OrderStatus
from app.messaging.consumers.base import EventConsumerDispatcher, InMemoryDLQRouter
from app.messaging.consumers.payment_completed import handle_payment_completed
from app.messaging.consumers.stock_insufficient import handle_stock_insufficient
from app.messaging.consumers.stock_reserved import handle_stock_reserved
from app.messaging.events import (
    EventName,
    PaymentCompletedMessage,
    StockInsufficientMessage,
    StockReservedMessage,
)
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import UnitOfWork


def _build_order(
    status: OrderStatus = OrderStatus.CREATED,
    order_id: UUID | None = None,
    in_preparation_at: datetime | None = None,
    stock_reserved_at: datetime | None = None,
) -> Order:
    return Order(
        public_id=order_id or uuid4(),
        status=status,
        version=1,
        total_amount=Decimal("150.00"),
        in_preparation_at=in_preparation_at,
        stock_reserved_at=stock_reserved_at,
    )


def _build_mock_uow(
    order: Order | None,
    is_duplicate: bool = False,
) -> tuple[UnitOfWork, Mock, Mock]:
    """Create a mock UnitOfWork wired with order and processed_events repos."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    uow = UnitOfWork(session_factory=lambda: session)

    order_repo = Mock(spec=OrderRepository)
    order_repo.get_by_public_id = AsyncMock(return_value=order)
    order_repo.get_for_update = AsyncMock(return_value=order)

    processed_repo = Mock(spec=ProcessedEventsRepository)
    processed_repo.exists = AsyncMock(return_value=is_duplicate)
    processed_repo.add = Mock()

    # Pre-wire repos
    uow.orders = order_repo
    uow.processed_events = processed_repo

    # Return uow along with repo mocks for assertions
    return uow, order_repo, processed_repo


class FakeOrderNotifier:
    """Notifier test double collecting calls."""

    def __init__(self) -> None:
        self.notified_orders: list[Order] = []
        self.notified_items: list[tuple[Order, Any]] = []

    async def notify_order_changed(self, order: Order) -> None:
        self.notified_orders.append(order)

    async def notify_item_changed(self, order: Order, item: Any) -> None:
        self.notified_items.append((order, item))


# ---------------------------------------------------------------------------
# UT-BE-38: Stock reserved moves order to IN_PREPARATION and notifies
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stock_reserved_moves_order_to_reserved_and_notifies() -> None:
    order_id = uuid4()
    order = _build_order(status=OrderStatus.CREATED, order_id=order_id)
    assert order.stock_reserved_at is None

    uow, _, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    event = StockReservedMessage(
        eventId=uuid4(),
        orderId=order_id,
        reservas=[{"plateId": "PLT-1", "quantity": 1}],
    )

    applied = await handle_stock_reserved(event, uow, notifier=notifier)

    assert applied is True
    assert order.status is OrderStatus.RESERVED
    assert order.stock_reserved_at is not None
    assert isinstance(order.stock_reserved_at, datetime)
    assert order.stock_reserved_at.tzinfo is UTC
    assert order.in_preparation_at is None
    assert len(notifier.notified_orders) == 1
    assert notifier.notified_orders[0].public_id == order_id
    processed_repo.add.assert_called_once_with(
        event_id=event.event_id,
        order_id=order_id,
        event_type=EventName.STOCK_RESERVED,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stock_reserved_does_not_overwrite_existing_stock_reserved_at() -> None:
    order_id = uuid4()
    original_time = datetime.now(UTC) - timedelta(minutes=5)
    order = _build_order(
        status=OrderStatus.CREATED,
        order_id=order_id,
        stock_reserved_at=original_time,
    )

    uow, _, _ = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    event = StockReservedMessage(eventId=uuid4(), orderId=order_id)
    await handle_stock_reserved(event, uow, notifier=notifier)

    assert order.status is OrderStatus.RESERVED
    assert order.stock_reserved_at == original_time


# ---------------------------------------------------------------------------
# UT-BE-39: Stock insufficient moves order to REJECTED
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stock_insufficient_moves_order_to_rejected() -> None:
    order_id = uuid4()
    order = _build_order(status=OrderStatus.CREATED, order_id=order_id)
    uow, _, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    event = StockInsufficientMessage(
        eventId=uuid4(),
        orderId=order_id,
        faltantes=["carne_res", "pan_artesanal"],
    )

    applied = await handle_stock_insufficient(event, uow, notifier=notifier)

    assert applied is True
    assert order.status is OrderStatus.REJECTED
    assert len(notifier.notified_orders) == 1
    assert notifier.notified_orders[0].public_id == order_id
    processed_repo.add.assert_called_once_with(
        event_id=event.event_id,
        order_id=order_id,
        event_type=EventName.STOCK_INSUFFICIENT,
    )


# ---------------------------------------------------------------------------
# UT-BE-40: Payment completed moves DELIVERED or WASTED to PAID
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("initial_status", [OrderStatus.DELIVERED, OrderStatus.WASTED])
async def test_payment_completed_moves_delivered_or_wasted_to_paid(
    initial_status: OrderStatus,
) -> None:
    order_id = uuid4()
    order = _build_order(status=initial_status, order_id=order_id)
    uow, _, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    event = PaymentCompletedMessage(
        eventId=uuid4(),
        orderId=order_id,
        montoTotal=Decimal("150.00"),
    )

    applied = await handle_payment_completed(event, uow, notifier=notifier)

    assert applied is True
    assert order.status is OrderStatus.PAID
    assert len(notifier.notified_orders) == 1
    processed_repo.add.assert_called_once_with(
        event_id=event.event_id,
        order_id=order_id,
        event_type=EventName.PAYMENT_COMPLETED,
    )


# ---------------------------------------------------------------------------
# UT-BE-41: Payment completed on invalid status is ignored with warning
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_status",
    [
        OrderStatus.CREATED,
        OrderStatus.IN_PREPARATION,
        OrderStatus.CANCELLED,
        OrderStatus.VOIDED,
        OrderStatus.EXPIRED,
        OrderStatus.REJECTED,
        OrderStatus.PAID,
    ],
)
async def test_payment_completed_on_invalid_status_is_ignored_with_warning(
    invalid_status: OrderStatus,
    caplog: pytest.LogCaptureFixture,
) -> None:
    order_id = uuid4()
    order = _build_order(status=invalid_status, order_id=order_id)
    uow, _, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    event = PaymentCompletedMessage(
        eventId=uuid4(),
        orderId=order_id,
        montoTotal=Decimal("200.00"),
    )

    with caplog.at_level(logging.WARNING):
        applied = await handle_payment_completed(event, uow, notifier=notifier)

    assert applied is False
    assert order.status is invalid_status
    assert len(notifier.notified_orders) == 0
    processed_repo.add.assert_not_called()
    assert any("ineligible status" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# UT-BE-42: Duplicate event is ignored (parameterized across all consumers)
# ---------------------------------------------------------------------------


def _build_stock_reserved_msg(eid: UUID, oid: UUID) -> StockReservedMessage:
    return StockReservedMessage(eventId=eid, orderId=oid)


def _build_stock_insufficient_msg(eid: UUID, oid: UUID) -> StockInsufficientMessage:
    return StockInsufficientMessage(eventId=eid, orderId=oid, faltantes=["ing-1"])


def _build_payment_completed_msg(eid: UUID, oid: UUID) -> PaymentCompletedMessage:
    return PaymentCompletedMessage(eventId=eid, orderId=oid, montoTotal=Decimal("50.00"))


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("consumer_fn", "event_builder", "initial_status"),
    [
        (
            handle_stock_reserved,
            _build_stock_reserved_msg,
            OrderStatus.CREATED,
        ),
        (
            handle_stock_insufficient,
            _build_stock_insufficient_msg,
            OrderStatus.CREATED,
        ),
        (
            handle_payment_completed,
            _build_payment_completed_msg,
            OrderStatus.DELIVERED,
        ),
    ],
)
async def test_duplicate_event_is_ignored(
    consumer_fn: Any,
    event_builder: Callable[[UUID, UUID], Any],
    initial_status: OrderStatus,
) -> None:
    order_id = uuid4()
    event_id = uuid4()
    order = _build_order(status=initial_status, order_id=order_id)
    uow, order_repo, processed_repo = _build_mock_uow(order, is_duplicate=True)
    notifier = FakeOrderNotifier()

    event = event_builder(event_id, order_id)
    applied = await consumer_fn(event, uow, notifier=notifier)

    assert applied is False
    assert order.status is initial_status
    assert len(notifier.notified_orders) == 0
    # Did not mutate order and did not re-add event
    order_repo.get_by_public_id.assert_not_called()
    processed_repo.add.assert_not_called()


# ---------------------------------------------------------------------------
# Late stock messages on EXPIRED and CANCELLED orders are ignored
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("late_status", [OrderStatus.EXPIRED, OrderStatus.CANCELLED])
async def test_late_stock_reserved_on_expired_or_cancelled_order_is_ignored(
    late_status: OrderStatus,
    caplog: pytest.LogCaptureFixture,
) -> None:
    order_id = uuid4()
    order = _build_order(status=late_status, order_id=order_id)
    uow, _, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    event = StockReservedMessage(eventId=uuid4(), orderId=order_id)

    with caplog.at_level(logging.WARNING):
        applied = await handle_stock_reserved(event, uow, notifier=notifier)

    assert applied is False
    assert order.status is late_status
    assert len(notifier.notified_orders) == 0
    processed_repo.add.assert_not_called()
    assert any("Late stock_reserved ignored" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# UT-BE-47: Malformed message is rejected without state change
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "queue_name", "reason_snippet"),
    [
        (b"not-json-at-all", "ordenes.stock_reserved.queue", "Invalid JSON"),
        ({"invalid": "missing order and event id"}, "ordenes.stock_reserved.queue", "validation"),
        (
            {"eventId": "not-a-uuid", "orderId": "also-not-a-uuid"},
            "ordenes.stock_reserved.queue",
            "validation",
        ),
        (
            {"eventId": str(uuid4())},  # missing orderId
            "ordenes.stock_insufficient.queue",
            "validation",
        ),
        (
            {"orderId": str(uuid4())},  # missing eventId
            "ordenes.payment_completed.queue",
            "validation",
        ),
        (12345, "ordenes.stock_reserved.queue", "Unsupported payload"),
    ],
)
async def test_malformed_message_is_rejected_without_state_change(
    payload: Any,
    queue_name: str,
    reason_snippet: str,
) -> None:
    dlq = InMemoryDLQRouter()
    session = AsyncMock()

    def session_factory() -> AsyncMock:
        return session

    dispatcher = EventConsumerDispatcher(
        session_factory=session_factory,
        dlq_router=dlq,
    )

    success = await dispatcher.dispatch_raw(
        raw_message=payload,
        queue_name=queue_name,
    )

    assert success is False
    assert len(dlq.dead_letters) == 1
    dlq_entry = dlq.dead_letters[0]
    assert dlq_entry["queue_name"] == queue_name
    assert dlq_entry["dlq_queue"] == f"{queue_name}.dlq"
    assert reason_snippet.lower() in str(dlq_entry["reason"]).lower()
    # Ensured no DB transaction was committed
    session.commit.assert_not_called()
