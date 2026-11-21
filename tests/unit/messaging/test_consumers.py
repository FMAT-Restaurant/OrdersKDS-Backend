"""Unit tests for idempotent consumers and DLQ routing.

Satisfies: RF-18, RF-21, RF-33, RF-34, RF-35, RNF-02, RNF-13, OP-11
Covers: UT-BE-38, UT-BE-39, UT-BE-40, UT-BE-41, UT-BE-42, UT-BE-47
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from app.db.models import Order, OrderItem
from app.domain.order_status import ItemStatus, OrderStatus
from app.messaging.consumers.base import EventConsumerDispatcher, InMemoryDLQRouter
from app.messaging.consumers.payment_completed import handle_payment_completed
from app.messaging.consumers.redo_rejected import (
    _dedup_event_id as redo_dedup_event_id,
)
from app.messaging.consumers.redo_rejected import (
    handle_redo_rejected,
)
from app.messaging.consumers.reservation_expired import (
    _dedup_event_id,
    handle_reservation_expired,
)
from app.messaging.consumers.stock_insufficient import handle_stock_insufficient
from app.messaging.consumers.stock_reserved import handle_stock_reserved
from app.messaging.events import (
    EventName,
    PaymentCompletedMessage,
    RedoRejectedMessage,
    ReservationExpiredMessage,
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
    outbox_repo = Mock()
    outbox_repo.add = Mock()
    uow.outbox = outbox_repo
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
        event_id=uuid4(),
        order_id=order_id,
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

    event = StockReservedMessage(event_id=uuid4(), order_id=order_id)
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
        event_id=uuid4(),
        order_id=order_id,
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
    return StockReservedMessage(event_id=eid, order_id=oid)


def _build_stock_insufficient_msg(eid: UUID, oid: UUID) -> StockInsufficientMessage:
    return StockInsufficientMessage(event_id=eid, order_id=oid, faltantes=["ing-1"])


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

    event = StockReservedMessage(event_id=uuid4(), order_id=order_id)

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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stock_insufficient_with_option_a_partial_reservation() -> None:
    order_id = uuid4()
    order = _build_order(status=OrderStatus.CREATED, order_id=order_id)
    uow, _, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    event = StockInsufficientMessage(
        event_id=uuid4(),
        order_id=order_id,
        faltantes=["carne_res"],
        rejectedItems=[{"offerId": "PLT-BURGER", "requestedQuantity": 3, "availableQuantity": 1}],
        reservedItems=[{"offerId": "PLT-FRIES", "quantity": 2}],
    )

    applied = await handle_stock_insufficient(event, uow, notifier=notifier)

    assert applied is True
    assert order.status is OrderStatus.REJECTED
    assert order.rejection_details is not None
    details = order.rejection_details
    assert isinstance(details, dict)
    assert details["missingIngredients"] == ["carne_res"]
    rejected_items = details["rejectedItems"]
    assert isinstance(rejected_items, list)
    assert len(rejected_items) == 1
    rejected_0 = rejected_items[0]
    assert isinstance(rejected_0, dict)
    assert rejected_0["offerId"] == "PLT-BURGER"
    assert rejected_0["requestedQuantity"] == 3
    assert rejected_0["availableQuantity"] == 1
    reserved_items = details["reservedItems"]
    assert isinstance(reserved_items, list)
    assert len(reserved_items) == 1
    reserved_0 = reserved_items[0]
    assert isinstance(reserved_0, dict)
    assert reserved_0["offerId"] == "PLT-FRIES"
    assert reserved_0["quantity"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_reservation_confirmed_english_format() -> None:
    order_id = uuid4()
    order = _build_order(status=OrderStatus.CREATED, order_id=order_id)
    uow, _, _ = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    dlq = InMemoryDLQRouter()
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow,
    )

    # Inbound message as emitted by Hector's inventory-backend (no eventId, English routing key)
    payload = {
        "orderId": str(order_id),
        "status": "confirmed",
        "items": [{"itemId": "dish-123", "requestedQuantity": 2}],
        "timestamp": "2026-11-12T12:00:00.000Z",
    }

    applied = await dispatcher.dispatch_raw(
        raw_message=payload,
        queue_name="orders.inventory.inbound",
        event_type="inventory.reservation.confirmed",
    )

    assert applied is True
    assert order.status is OrderStatus.RESERVED
    assert len(notifier.notified_orders) == 1
    assert uow.outbox is not None
    outbox_add = cast(Mock, uow.outbox.add)
    assert outbox_add.called
    staged_event = outbox_add.call_args[0][0]
    assert staged_event.event_name == EventName.ORDER_CONFIRMED
    assert staged_event.order_id == order_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_reservation_rejected_english_format() -> None:
    order_id = uuid4()
    order = _build_order(status=OrderStatus.CREATED, order_id=order_id)
    uow, _, _ = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    dlq = InMemoryDLQRouter()
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow,
    )

    # Inbound message as emitted by Hector's inventory-backend
    # (reason string + items with availableStock)
    payload = {
        "orderId": str(order_id),
        "status": "rejected",
        "reason": "Stock insuficiente de carne y queso",
        "items": [
            {
                "itemId": "dish-burger",
                "itemName": "Hamburguesa Doble",
                "requestedQuantity": 4,
                "availableStock": 1,
            }
        ],
        "timestamp": "2026-11-12T12:00:00.000Z",
    }

    applied = await dispatcher.dispatch_raw(
        raw_message=payload,
        queue_name="orders.inventory.inbound",
        event_type="inventory.reservation.rejected",
    )

    assert applied is True
    assert order.status is OrderStatus.REJECTED
    assert order.rejection_details is not None
    details = order.rejection_details
    assert isinstance(details, dict)
    assert details["reason"] == "Stock insuficiente de carne y queso"
    rejected_items = details["rejectedItems"]
    assert isinstance(rejected_items, list)
    assert len(rejected_items) == 1
    first_item = rejected_items[0]
    assert isinstance(first_item, dict)
    assert first_item["offerId"] == "dish-burger"
    assert first_item["itemName"] == "Hamburguesa Doble"
    assert first_item["requestedQuantity"] == 4
    assert first_item["availableQuantity"] == 1
    assert len(notifier.notified_orders) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_reservation_expired_transitions_reserved_and_dedups_safely() -> None:
    order_id = UUID("44444444-4444-4444-4444-444444444444")
    shared_event_id = UUID("bb77ddcd-8d15-572c-9df5-332afbb9dc2d")
    order = _build_order(status=OrderStatus.RESERVED, order_id=order_id)
    uow, _, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    dlq = InMemoryDLQRouter()
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow,
    )

    payload = {
        "orderId": str(order_id),
        "status": "expired",
        "eventId": str(shared_event_id),
        "reason": "Reserva expirada por TTL",
        "items": [{"itemId": "6aff319efe2972550cae2a6a", "requestedQuantity": 0.15}],
        "timestamp": "2026-11-20T15:13:42.982Z",
    }

    applied = await dispatcher.dispatch_raw(
        raw_message=payload,
        queue_name="orders.inventory.inbound",
        event_type="inventory.reservation.expired",
    )

    assert applied is True
    assert dlq.dead_letters == []
    assert order.status is OrderStatus.EXPIRED
    assert len(notifier.notified_orders) == 1
    expected_dedup = _dedup_event_id(
        ReservationExpiredMessage.model_validate(payload)
    )
    assert expected_dedup != shared_event_id
    processed_repo.add.assert_called_once_with(
        event_id=expected_dedup,
        order_id=order_id,
        event_type=EventName.INVENTORY_RESERVATION_EXPIRED,
    )

    # Duplicate delivery must be ignored cleanly
    uow_dup, order_repo_dup, proc_dup = _build_mock_uow(order, is_duplicate=True)
    dup_applied = await handle_reservation_expired(
        ReservationExpiredMessage.model_validate(payload),
        uow_dup,
        notifier=notifier,
    )
    assert dup_applied is False
    order_repo_dup.get_by_public_id.assert_not_called()
    proc_dup.add.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_status", "expected_applied", "expected_status"),
    [
        (OrderStatus.CREATED, True, OrderStatus.EXPIRED),
        (OrderStatus.IN_PREPARATION, False, OrderStatus.IN_PREPARATION),
        (OrderStatus.DELIVERED, False, OrderStatus.DELIVERED),
        (OrderStatus.CANCELLED, False, OrderStatus.CANCELLED),
        (OrderStatus.EXPIRED, False, OrderStatus.EXPIRED),
    ],
)
async def test_reservation_expired_respects_order_lifecycle(
    initial_status: OrderStatus,
    expected_applied: bool,
    expected_status: OrderStatus,
) -> None:
    order_id = uuid4()
    order = _build_order(status=initial_status, order_id=order_id)
    uow, _, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()

    msg = ReservationExpiredMessage(event_id=uuid4(), order_id=order_id, reason="TTL")
    applied = await handle_reservation_expired(msg, uow, notifier=notifier)

    assert applied is expected_applied
    assert order.status is expected_status
    if expected_applied:
        assert len(notifier.notified_orders) == 1
        processed_repo.add.assert_called_once()
    else:
        assert len(notifier.notified_orders) == 0
        processed_repo.add.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reservation_expired_missing_order_and_fractional_insufficient_stock() -> None:
    uow_missing, _, proc_missing = _build_mock_uow(None, is_duplicate=False)
    notifier = FakeOrderNotifier()
    missing_applied = await handle_reservation_expired(
        ReservationExpiredMessage(event_id=uuid4(), order_id=uuid4()),
        uow_missing,
        notifier=notifier,
    )
    assert missing_applied is False
    proc_missing.add.assert_not_called()

    # Fractional ingredient quantities in stock insufficient (kg/L)
    order_id = uuid4()
    order = _build_order(status=OrderStatus.CREATED, order_id=order_id)
    uow_frac, _, _ = _build_mock_uow(order, is_duplicate=False)
    dlq = InMemoryDLQRouter()
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow_frac,
    )
    payload = {
        "orderId": str(order_id),
        "status": "rejected",
        "reason": "Stock insuficiente de harina",
        "items": [
            {
                "itemId": "6aff319efe2972550cae2a6a",
                "itemName": "Harina",
                "requestedQuantity": 0.15,
                "availableStock": 0.05,
            }
        ],
    }
    applied = await dispatcher.dispatch_raw(
        raw_message=payload,
        queue_name="orders.inventory.inbound",
        event_type="inventory.reservation.rejected",
    )
    assert applied is True
    assert dlq.dead_letters == []
    assert order.status is OrderStatus.REJECTED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_reservation_consumed_and_released_are_acknowledged_without_dlq(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order_id = UUID("f2160047-b303-4009-89a0-b7e8b41b39d1")
    order = _build_order(status=OrderStatus.IN_PREPARATION, order_id=order_id)
    uow, order_repo, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()
    dlq = InMemoryDLQRouter()
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow,
    )

    consumed_payload = {
        "orderId": str(order_id),
        "status": "consumed",
        "eventId": "7361245f-4a71-513e-abfd-844a264b37ac",
        "correlationId": str(order_id),
        "reason": "Started preparation in kitchen",
        "items": [
            {"itemId": "6aff319efe2972550cae2a6a", "requestedQuantity": 0.15},
            {"itemId": "6aff319efe2972550cae2a7a", "requestedQuantity": 0.13},
        ],
        "timestamp": "2026-11-21T03:10:05.447Z",
    }

    with caplog.at_level(logging.INFO):
        applied = await dispatcher.dispatch_raw(
            raw_message=consumed_payload,
            queue_name="orders.inventory.inbound",
            event_type="inventory.reservation.consumed",
        )

    assert applied is True
    assert dlq.dead_letters == []
    assert order.status is OrderStatus.IN_PREPARATION
    order_repo.get_by_public_id.assert_not_called()
    processed_repo.add.assert_not_called()
    assert len(notifier.notified_orders) == 0
    assert any("Acknowledged informational inventory event" in r.message for r in caplog.records)

    # Fallback inference without explicit event_type + reservation.released/cancelled support
    inferred_ok = await dispatcher.dispatch_raw(
        raw_message=consumed_payload,
        queue_name="orders.inventory.inbound",
        event_type=None,
    )
    released_ok = await dispatcher.dispatch_raw(
        raw_message={
            "orderId": str(order_id),
            "status": "released",
            "eventId": str(uuid4()),
            "correlationId": str(order_id),
        },
        queue_name="orders.inventory.inbound",
        event_type="inventory.reservation.released",
    )
    assert inferred_ok is True
    assert released_ok is True
    assert dlq.dead_letters == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_redo_confirmed_is_acknowledged_without_dlq(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order_id = uuid4()
    item_id = uuid4()
    redo_id = uuid4()
    order = _build_order(status=OrderStatus.DELIVERED, order_id=order_id)
    uow, order_repo, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()
    dlq = InMemoryDLQRouter()
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow,
    )
    payload = {
        "eventId": str(uuid4()),
        "redoId": str(redo_id),
        "orderId": str(order_id),
        "itemId": str(item_id),
        "correlationId": str(order_id),
        "status": "confirmed",
        "items": [{"itemId": "6aff319efe2972550cae2a6a", "consumedQuantity": 0.15}],
        "timestamp": "2026-11-21T03:25:00.000Z",
    }

    with caplog.at_level(logging.INFO):
        explicit_ok = await dispatcher.dispatch_raw(
            raw_message=payload,
            queue_name="orders.inventory.inbound",
            event_type=EventName.INVENTORY_REDO_CONFIRMED,
        )
        inferred_ok = await dispatcher.dispatch_raw(
            raw_message=payload,
            queue_name="orders.inventory.inbound",
            event_type=None,
        )

    assert explicit_ok is True
    assert inferred_ok is True
    assert dlq.dead_letters == []
    assert order.status is OrderStatus.DELIVERED
    order_repo.get_by_public_id.assert_not_called()
    processed_repo.add.assert_not_called()
    assert len(notifier.notified_orders) == 0
    assert any("Acknowledged inventory.redo.confirmed" in r.message for r in caplog.records)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_redo_rejected_reverts_item_restores_notes_and_notifies() -> None:
    order_id = uuid4()
    item_id = uuid4()
    redo_id = uuid4()
    event_id = uuid4()
    order = _build_order(status=OrderStatus.DELIVERED, order_id=order_id)
    item = OrderItem(
        public_id=item_id,
        dish_id="PLATE-BURGER-01",
        offer_id="OFFER-BURGER-01",
        name="Hamburguesa Clasica",
        quantity=1,
        unit_price=Decimal("165.00"),
        notes="Sin cebolla",
        status=ItemStatus.DELIVERED,
    )
    order.items.append(item)
    order.save_pre_redo_note(item)
    item.mark_pending_for_redo("Salió fría")

    uow, _, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()
    dlq = InMemoryDLQRouter()
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow,
    )

    payload = {
        "eventId": str(event_id),
        "redoId": str(redo_id),
        "orderId": str(order_id),
        "itemId": str(item_id),
        "correlationId": str(order_id),
        "status": "rejected",
        "errorCode": "INSUFFICIENT_STOCK",
        "reason": "Sin pan brioche para rehechura",
        "missingIngredients": ["Pan brioche"],
        "items": [
            {
                "itemId": "6aff319efe2972550cae2a6a",
                "itemName": "Pan brioche",
                "requestedQuantity": 1,
                "availableStock": 0,
            }
        ],
    }

    applied = await dispatcher.dispatch_raw(
        raw_message=payload,
        queue_name="orders.inventory.inbound",
        event_type=EventName.INVENTORY_REDO_REJECTED,
    )

    assert applied is True
    assert dlq.dead_letters == []
    assert order.status is OrderStatus.DELIVERED
    assert item.status is ItemStatus.DELIVERED
    assert item.notes == "Sin cebolla"
    assert order.rejection_details == {
        "redoRejection": {
            "source": "inventory_redo",
            "itemId": str(item_id),
            "itemName": "Hamburguesa Clasica",
            "redoId": str(redo_id),
            "reason": "Sin pan brioche para rehechura",
            "missingIngredients": ["Pan brioche"],
        }
    }
    assert notifier.notified_items == [(order, item)]
    assert notifier.notified_orders == [order]

    expected_msg = RedoRejectedMessage.model_validate(payload)
    processed_repo.add.assert_called_once_with(
        event_id=redo_dedup_event_id(expected_msg),
        order_id=order_id,
        event_type=EventName.INVENTORY_REDO_REJECTED,
    )

    # Duplicate delivery must be ignored without notifying again
    uow_dup, order_repo_dup, proc_dup = _build_mock_uow(order, is_duplicate=True)
    dup_applied = await handle_redo_rejected(expected_msg, uow_dup, notifier=notifier)
    assert dup_applied is False
    order_repo_dup.get_by_public_id.assert_not_called()
    proc_dup.add.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_redo_rejected_handles_guards_and_inferred_item_resolution() -> None:
    notifier = FakeOrderNotifier()

    # 1. Missing order -> False
    uow_missing, _, proc_missing = _build_mock_uow(None, is_duplicate=False)
    msg_missing = RedoRejectedMessage(eventId=uuid4(), orderId=uuid4(), itemId=uuid4())
    assert await handle_redo_rejected(msg_missing, uow_missing, notifier=notifier) is False
    proc_missing.add.assert_not_called()

    # 2. Order not in DELIVERED -> False
    order_prep = _build_order(status=OrderStatus.IN_PREPARATION)
    uow_prep, _, proc_prep = _build_mock_uow(order_prep, is_duplicate=False)
    assert (
        await handle_redo_rejected(
            RedoRejectedMessage(eventId=uuid4(), orderId=order_prep.public_id),
            uow_prep,
            notifier=notifier,
        )
        is False
    )
    proc_prep.add.assert_not_called()

    # 3. Multiple PENDING items without itemId -> ambiguous -> False
    order_multi = _build_order(status=OrderStatus.DELIVERED)
    order_multi.items.extend(
        [
            OrderItem(public_id=uuid4(), name="A", quantity=1, status=ItemStatus.PENDING),
            OrderItem(public_id=uuid4(), name="B", quantity=1, status=ItemStatus.PENDING),
        ]
    )
    uow_multi, _, proc_multi = _build_mock_uow(order_multi, is_duplicate=False)
    assert (
        await handle_redo_rejected(
            RedoRejectedMessage(eventId=uuid4(), orderId=order_multi.public_id),
            uow_multi,
            notifier=notifier,
        )
        is False
    )
    proc_multi.add.assert_not_called()

    # 4. Single PENDING item resolved cleanly via dispatcher inference when itemId omitted
    order_single = _build_order(status=OrderStatus.DELIVERED)
    sole_pending = OrderItem(
        public_id=uuid4(),
        name="Tacos",
        quantity=1,
        notes="Sin cilantro",
        status=ItemStatus.DELIVERED,
    )
    sole_pending.mark_pending_for_redo("Frios")
    order_single.items.append(sole_pending)
    uow_single, _, _ = _build_mock_uow(order_single, is_duplicate=False)
    dlq = InMemoryDLQRouter()
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow_single,
    )
    inferred_ok = await dispatcher.dispatch_raw(
        raw_message={
            "eventId": str(uuid4()),
            "redoId": str(uuid4()),
            "orderId": str(order_single.public_id),
            "status": "rejected",
            "reason": "Sin salsa",
        },
        queue_name="orders.inventory.inbound",
        event_type=None,
    )
    assert inferred_ok is True
    assert dlq.dead_letters == []
    assert sole_pending.status is ItemStatus.DELIVERED


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol_code",
    ["ALREADY_SERVED", "RESERVATION_NOT_FOUND", "RESERVATION_NOT_ACTIVE"],
)
async def test_stock_insufficient_ignores_protocol_error_codes_without_dlq(
    protocol_code: str,
) -> None:
    order_id = uuid4()
    order = _build_order(status=OrderStatus.CREATED, order_id=order_id)
    uow, order_repo, processed_repo = _build_mock_uow(order, is_duplicate=False)
    notifier = FakeOrderNotifier()
    dlq = InMemoryDLQRouter()
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow,
    )

    applied = await dispatcher.dispatch_raw(
        raw_message={
            "eventId": str(uuid4()),
            "orderId": str(order_id),
            "status": "rejected",
            "errorCode": protocol_code,
            "reason": "Out-of-order reservation notification",
        },
        queue_name="orders.inventory.inbound",
        event_type="inventory.reservation.rejected",
    )

    assert applied is False
    assert dlq.dead_letters == []
    assert order.status is OrderStatus.CREATED
    order_repo.get_by_public_id.assert_not_called()
    processed_repo.add.assert_not_called()
    assert len(notifier.notified_orders) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stock_insufficient_enriches_already_rejected_order_and_ignores_redundant() -> None:
    order_id = uuid4()
    order = _build_order(status=OrderStatus.CREATED, order_id=order_id)
    notifier = FakeOrderNotifier()
    dlq = InMemoryDLQRouter()

    # Step 1: raw inventory.reservation.rejected transitions CREATED -> REJECTED
    uow_raw, _, _ = _build_mock_uow(order, is_duplicate=False)
    dispatcher_raw = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow_raw,
    )
    first_applied = await dispatcher_raw.dispatch_raw(
        raw_message={
            "eventId": str(uuid4()),
            "orderId": str(order_id),
            "status": "rejected",
            "reason": "Stock insuficiente",
            "items": [
                {
                    "itemId": "6aff319efe2972550cae2a6a",
                    "requestedQuantity": 2,
                    "availableStock": 0,
                }
            ],
        },
        queue_name="orders.inventory.inbound",
        event_type="inventory.reservation.rejected",
    )
    assert first_applied is True
    assert order.status is OrderStatus.REJECTED

    # Step 2: enriched inventario.stock.insuficiente arrives next -> enriches without DLQ
    uow_enriched, _, proc_enriched = _build_mock_uow(order, is_duplicate=False)
    dispatcher_enriched = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow_enriched,
    )
    enriched_event_id = str(uuid4())
    second_applied = await dispatcher_enriched.dispatch_raw(
        raw_message={
            "eventId": enriched_event_id,
            "orderId": str(order_id),
            "reason": "Stock insuficiente",
            "missingIngredients": ["Harina", "Queso"],
            "rejectedItems": [
                {
                    "offerId": "OFFER-QUES-01",
                    "itemName": "Quesadilla",
                    "requestedQuantity": 2,
                    "availableQuantity": 0,
                }
            ],
            "reservedItems": [{"offerId": "OFFER-AGUA-01", "quantity": 1}],
        },
        queue_name="orders.inventory.inbound",
        event_type="inventario.stock.insuficiente",
    )
    assert second_applied is True
    assert dlq.dead_letters == []
    assert order.status is OrderStatus.REJECTED
    assert order.rejection_details is not None
    assert order.rejection_details["missingIngredients"] == ["Harina", "Queso"]
    assert len(notifier.notified_orders) == 2
    proc_enriched.add.assert_called_once()

    # Step 3: redundant uninformative rejection on already enriched REJECTED order -> False, no DLQ
    uow_redundant, _, proc_redundant = _build_mock_uow(order, is_duplicate=False)
    dispatcher_redundant = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=lambda _: uow_redundant,
    )
    third_applied = await dispatcher_redundant.dispatch_raw(
        raw_message={
            "eventId": str(uuid4()),
            "orderId": str(order_id),
            "reason": "Stock insuficiente",
            "items": [{"itemId": "6aff319efe2972550cae2a6a", "availableStock": 0}],
        },
        queue_name="orders.inventory.inbound",
        event_type="inventory.reservation.rejected",
    )
    assert third_applied is False
    assert dlq.dead_letters == []
    proc_redundant.add.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "late_status",
    [OrderStatus.IN_PREPARATION, OrderStatus.DELIVERED, OrderStatus.PAID],
)
async def test_stock_insufficient_ignores_late_non_rejectable_order_statuses(
    late_status: OrderStatus,
) -> None:
    order_id = uuid4()
    order = _build_order(status=late_status, order_id=order_id)
    uow, _, proc_repo = _build_mock_uow(order, is_duplicate=False)
    dlq = InMemoryDLQRouter()
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=dlq,
        uow_factory=lambda _: uow,
    )

    applied = await dispatcher.dispatch_raw(
        raw_message={
            "eventId": str(uuid4()),
            "orderId": str(order_id),
            "missingIngredients": ["Tomate"],
        },
        queue_name="orders.inventory.inbound",
        event_type="inventario.stock.insuficiente",
    )

    assert applied is False
    assert dlq.dead_letters == []
    assert order.status is late_status
    proc_repo.add.assert_not_called()
