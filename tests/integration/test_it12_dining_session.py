"""IT-12: consume the Floor dining-session event and create one order."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from app.db.models import Order
from app.domain.order_status import OrderStatus
from app.messaging.consumers.base import EventConsumerDispatcher
from app.repositories.unit_of_work import SessionFactory
from app.stubs.floor_stub import FloorStub
from app.stubs.protocol import InMemoryStubPublisher


class InMemoryOrders:
    def __init__(self) -> None:
        self.orders: list[Order] = []

    async def get_by_dining_session_id(self, session_id: UUID) -> Order | None:
        return next(
            (order for order in self.orders if order.dining_session_id == session_id),
            None,
        )

    def save(self, order: Order) -> None:
        self.orders.append(order)


class InMemoryDiningSessionUnitOfWork:
    def __init__(self, orders: InMemoryOrders) -> None:
        self.orders = orders
        self.processed_events = Mock()
        self.session = Mock()
        self.session.flush = AsyncMock()
        self.commit = AsyncMock()

    async def __aenter__(self) -> InMemoryDiningSessionUnitOfWork:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_it12_floor_event_creates_one_order_with_waiter_and_ignores_replay() -> None:
    publisher = InMemoryStubPublisher()
    floor = FloorStub(publisher=publisher)
    session_id = uuid4()
    waiter_id = uuid4()
    orders = InMemoryOrders()

    def uow_factory(_factory: SessionFactory) -> InMemoryDiningSessionUnitOfWork:
        return InMemoryDiningSessionUnitOfWork(orders)

    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: Mock(),
        uow_factory=uow_factory,
    )

    await floor.emit_session_started(
        session_id=session_id,
        waiter_id=waiter_id,
        diners_count=4,
    )
    event = publisher.get_events("sala.dining_session.iniciada")[0]["payload"]

    assert await dispatcher.dispatch_raw(event, "sala.dining_session.iniciada.queue") is True
    assert len(orders.orders) == 1
    assert orders.orders[0].status is OrderStatus.CREATED
    assert orders.orders[0].dining_session_id == session_id
    assert orders.orders[0].responsible_waiter_id == waiter_id

    await floor.emit_duplicate_session_started(session_id)
    replay = publisher.get_events("sala.dining_session.iniciada")[1]["payload"]
    assert await dispatcher.dispatch_raw(replay, "sala.dining_session.iniciada.queue") is False
    assert len(orders.orders) == 1
