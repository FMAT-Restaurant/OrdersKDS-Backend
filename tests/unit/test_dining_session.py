"""Tests for Floor dining-session order creation."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.db.models import Order
from app.domain.order_status import OrderStatus
from app.messaging.consumers.dining_session import handle_dining_session_created
from app.messaging.events import DiningSessionCreatedMessage, EventName


def _message(**overrides: object) -> DiningSessionCreatedMessage:
    payload = {
        "eventId": str(uuid4()),
        "sessionId": str(uuid4()),
        "meseroId": str(uuid4()),
        "numComensales": 3,
    }
    payload.update(overrides)
    return DiningSessionCreatedMessage.model_validate(payload)


def test_dining_session_message_accepts_num_comensales_and_nested_payload() -> None:
    message = _message()
    assert message.diners == 3
    assert message.waiter_id is not None

    nested = DiningSessionCreatedMessage.model_validate(
        {
            "data": {
                "eventId": str(uuid4()),
                "sessionId": str(uuid4()),
                "comensales": 2,
            }
        }
    )
    assert nested.diners == 2
    assert nested.waiter_id is None


@pytest.mark.asyncio
async def test_dining_session_creates_created_order_with_waiter() -> None:
    message = _message()
    orders = Mock()
    orders.get_by_dining_session_id = AsyncMock(return_value=None)
    orders.save = Mock()
    processed = Mock()
    processed.add = AsyncMock()
    session = Mock()
    session.flush = AsyncMock()
    uow = Mock()
    uow.orders = orders
    uow.processed_events = processed
    uow.session = session
    uow.commit = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)

    assert await handle_dining_session_created(message, uow) is True

    order = orders.save.call_args.args[0]
    assert isinstance(order, Order)
    assert order.status is OrderStatus.CREATED
    assert order.dining_session_id == message.session_id
    assert order.responsible_waiter_id == message.waiter_id
    processed.add.assert_awaited_once_with(
        event_id=message.event_id,
        order_id=order.public_id,
        event_type=EventName.DINING_SESSION_CREATED,
    )
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_redelivery_does_not_create_another_order() -> None:
    message = _message()
    existing = Order(public_id=uuid4(), dining_session_id=message.session_id)
    orders = Mock()
    orders.get_by_dining_session_id = AsyncMock(return_value=existing)
    orders.save = Mock()
    uow = Mock()
    uow.orders = orders
    uow.processed_events = Mock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)

    assert await handle_dining_session_created(message, uow) is False
    orders.save.assert_not_called()
