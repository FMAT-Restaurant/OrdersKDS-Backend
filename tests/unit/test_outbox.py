"""Unit tests for deterministic event identity and outbox persistence wiring."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.models import Outbox
from app.messaging.events import DishPreparedEvent, EventName, OrderCreatedEvent
from app.repositories.outbox import OutboxRepository
from app.repositories.unit_of_work import UnitOfWork


def test_outbox_table_is_registered() -> None:
    assert "outbox" in Base.metadata.tables
    assert Outbox.__table__.c.event_id.unique


def test_order_event_id_is_deterministic_per_order_and_type() -> None:
    order_id = uuid4()

    first = OrderCreatedEvent.for_order(order_id)
    retry = OrderCreatedEvent.for_order(order_id)

    assert first.event_id == retry.event_id
    assert first.event_name is EventName.ORDER_CREATED


def test_repeatable_event_gets_a_new_id_for_each_emission() -> None:
    order_id = uuid4()

    first = DishPreparedEvent.for_order(order_id)
    second = DishPreparedEvent.for_order(order_id)

    assert first.event_id != second.event_id


@pytest.mark.asyncio
async def test_add_stages_event_without_committing() -> None:
    session = Mock(spec=AsyncSession)
    session.add = Mock()
    event = OrderCreatedEvent.for_order(uuid4())

    row = OutboxRepository(session).add(event)

    session.add.assert_called_once_with(row)
    assert row.event_id == event.event_id
    assert row.event_type == EventName.ORDER_CREATED
    assert row.payload["eventId"] == str(event.event_id)
    session.commit = AsyncMock()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unit_of_work_rolls_back_state_and_outbox_on_failure() -> None:
    session = Mock(spec=AsyncSession)
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session_factory = Mock(return_value=session)

    with pytest.raises(RuntimeError):
        async with UnitOfWork(session_factory) as uow:
            assert uow.outbox is not None
            uow.outbox.add(OrderCreatedEvent.for_order(uuid4()))
            raise RuntimeError("simulated failure after staging the event")

    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()
