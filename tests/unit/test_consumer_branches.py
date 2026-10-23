"""Unit tests targeting repositories and consumer edge cases for maximum coverage."""

from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order
from app.domain.errors import OrderNotFoundError
from app.domain.order_status import OrderStatus
from app.messaging.consumers.payment_completed import handle_payment_completed
from app.messaging.consumers.stock_insufficient import handle_stock_insufficient
from app.messaging.consumers.stock_reserved import handle_stock_reserved
from app.messaging.events import (
    PaymentCompletedMessage,
    StockInsufficientMessage,
    StockReservedMessage,
)
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import UnitOfWork
from app.services.notifications import DefaultOrderNotifier, notify_order_changed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_processed_events_repository_exists_and_add() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar = AsyncMock(return_value=1)
    session.add = Mock()

    repo = ProcessedEventsRepository(session)
    event_id = uuid4()
    order_id = uuid4()

    exists = await repo.exists(event_id, order_id)
    assert exists is True
    session.scalar.assert_awaited_once()

    # Pass strings
    session.scalar.return_value = None
    exists_str = await repo.exists(str(event_id), str(order_id))
    assert exists_str is False

    row = await repo.add(event_id, order_id, event_type="test.event")
    session.add.assert_called_once_with(row)
    assert row.event_id == event_id
    assert row.order_id == order_id
    assert row.event_type == "test.event"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_order_repository_methods() -> None:
    session = AsyncMock(spec=AsyncSession)
    repo = OrderRepository(session)
    order_id = uuid4()
    mock_order = Order(public_id=order_id, status=OrderStatus.CREATED)
    session.scalar = AsyncMock(return_value=mock_order)

    # get_by_public_id with for_update
    order = await repo.get_by_public_id(order_id, for_update=True)
    assert order == mock_order

    # get_for_update found
    order_up = await repo.get_for_update(order_id)
    assert order_up == mock_order

    # get_for_update not found
    session.scalar = AsyncMock(return_value=None)
    with pytest.raises(OrderNotFoundError):
        await repo.get_for_update(order_id)

    # save stages order
    repo.save(mock_order)
    session.add.assert_called_with(mock_order)


@pytest.mark.unit
def test_payment_completed_nested_data_deserialization() -> None:
    event_id = uuid4()
    order_id = uuid4()
    session_id = uuid4()

    nested_payload = {
        "eventId": str(event_id),
        "data": {
            "orderId": str(order_id),
            "sessionId": str(session_id),
            "totalPaid": 320.00,
            "status": "PAID",
        },
    }

    msg = PaymentCompletedMessage.model_validate(nested_payload)
    assert msg.event_id == event_id
    assert msg.order_id == order_id
    assert msg.session_id == session_id
    assert msg.total_paid == Decimal("320.00")
    assert msg.status == "PAID"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_order_notifier_logs_without_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order = Order(public_id=uuid4(), status=OrderStatus.IN_PREPARATION)
    notifier = DefaultOrderNotifier()
    with caplog.at_level(logging.INFO):
        await notify_order_changed(order, notifier=notifier)
    assert any("notify_order_changed hook called" in r.message for r in caplog.records)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consumers_when_order_not_found_returns_false() -> None:
    session = AsyncMock()
    uow = UnitOfWork(lambda: session)

    order_repo = Mock(spec=OrderRepository)
    order_repo.get_by_public_id = AsyncMock(return_value=None)

    proc_repo = Mock(spec=ProcessedEventsRepository)
    proc_repo.exists = AsyncMock(return_value=False)

    uow.orders = order_repo
    uow.processed_events = proc_repo

    oid = uuid4()
    eid = uuid4()

    res1 = await handle_stock_reserved(StockReservedMessage(eventId=eid, orderId=oid), uow)
    assert res1 is False

    res2 = await handle_stock_insufficient(
        StockInsufficientMessage(eventId=eid, orderId=oid, faltantes=[]), uow
    )
    assert res2 is False

    res3 = await handle_payment_completed(
        PaymentCompletedMessage(eventId=eid, orderId=oid, montoTotal=Decimal("10")), uow
    )
    assert res3 is False


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("late_status", [OrderStatus.EXPIRED, OrderStatus.CANCELLED])
async def test_late_stock_insufficient_on_expired_or_cancelled_order_is_ignored(
    late_status: OrderStatus,
    caplog: pytest.LogCaptureFixture,
) -> None:
    order_id = uuid4()
    order = Order(public_id=order_id, status=late_status)

    session = AsyncMock()
    uow = UnitOfWork(lambda: session)

    order_repo = Mock(spec=OrderRepository)
    order_repo.get_by_public_id = AsyncMock(return_value=order)
    proc_repo = Mock(spec=ProcessedEventsRepository)
    proc_repo.exists = AsyncMock(return_value=False)

    uow.orders = order_repo
    uow.processed_events = proc_repo

    event = StockInsufficientMessage(eventId=uuid4(), orderId=order_id, faltantes=["harina"])

    with caplog.at_level(logging.WARNING):
        applied = await handle_stock_insufficient(event, uow)

    assert applied is False
    assert order.status is late_status
    assert any("Late stock_insufficient ignored" in record.message for record in caplog.records)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unit_of_work_error_handling() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    uow = UnitOfWork(lambda: session)

    # Calling commit or rollback before entering raises RuntimeError
    with pytest.raises(RuntimeError):
        await uow.commit()

    with pytest.raises(RuntimeError):
        await uow.rollback()

    async with uow:
        await uow.commit()
        session.commit.assert_awaited_once()
        await uow.rollback()
        session.rollback.assert_awaited_once()
