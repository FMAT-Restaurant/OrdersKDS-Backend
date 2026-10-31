"""Integration test IT-11: Order cancellation and late reservation handling.

Satisfies: RF-24, RF-25, OP-05
Covers: IT-11, UT-BE-11, UT-BE-15, CT-06
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import AuthContext
from app.db.models import Order, OrderItem, Outbox, ProcessedEvent
from app.domain.errors import InvalidStateTransitionError
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role
from app.messaging.consumers.base import EventConsumerDispatcher, InMemoryDLQRouter
from app.messaging.events import EventName
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork
from app.services.orders import OrderService
from app.stubs.config import InventoryStubMode
from app.stubs.inventory_stub import InventoryStub
from app.stubs.protocol import InMemoryStubPublisher

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requirement("RF-24", "RF-25", "OP-05"),
]


class InMemoryDb:
    def __init__(self) -> None:
        self.orders: dict[UUID, Order] = {}
        self.processed_events: set[tuple[UUID, UUID]] = set()
        self.outbox: list[Outbox] = []


class FakeSession:
    def __init__(self, db: InMemoryDb) -> None:
        self.db = db
        self.staged_processed: list[tuple[UUID, UUID]] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def add(self, instance: object) -> None:
        if isinstance(instance, ProcessedEvent):
            self.staged_processed.append((instance.order_id, instance.event_id))
        elif isinstance(instance, Order):
            self.db.orders[instance.public_id] = instance
        elif isinstance(instance, Outbox):
            self.db.outbox.append(instance)

    async def scalar(self, statement: Any) -> Order | None:
        params = statement.compile().params
        order_id = next((v for v in params.values() if isinstance(v, UUID)), None)
        if order_id is not None:
            return self.db.orders.get(order_id)
        return None

    async def scalars(self, _statement: object) -> list[Order]:
        return list(self.db.orders.values())

    async def commit(self) -> None:
        for order_id, event_id in self.staged_processed:
            self.db.processed_events.add((order_id, event_id))
        self.staged_processed.clear()

    async def rollback(self) -> None:
        self.staged_processed.clear()

    async def close(self) -> None:
        pass


class FakeUow(UnitOfWork):
    def __init__(self, session_factory: SessionFactory, db: InMemoryDb) -> None:
        super().__init__(session_factory)
        self._db = db

    async def __aenter__(self) -> FakeUow:
        self._session = self._session_factory()
        self.orders = OrderRepository(self._session)
        self.processed_events = ProcessedEventsRepository(self._session)
        return self


@pytest.mark.asyncio
async def test_it11_cancel_order_in_created_and_late_reservation_ignored() -> None:
    """IT-11: Full cancellation flow for confirmed order awaiting stock.

    1. Order is confirmed awaiting stock reservation (confirmed_at is set).
    2. Waiter cancels order -> status CANCELLED and 'ordenes.orden.cancelada' staged.
    3. Inventory stub consumes cancellation event and frees allocated stock.
    4. Late 'inventario.stock.reservado' arrives and is ignored with WARNING.
    5. Order status remains CANCELLED; no payment/billing event is emitted.
    """
    db = InMemoryDb()
    order_id = uuid4()
    actor_id = uuid4()

    order = Order(
        public_id=order_id,
        table_number=5,
        status=OrderStatus.CREATED,
        total_amount=Decimal("180.00"),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-hamburguesa",
        name="Hamburguesa Hawaiana",
        quantity=2,
        unit_price=Decimal("90.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    def uow_factory(factory: SessionFactory) -> UnitOfWork:
        return FakeUow(factory, db)

    stub_bus = InMemoryStubPublisher()
    inventory_stub = InventoryStub(publisher=stub_bus, mode=InventoryStubMode.LATE_ON_CANCELLED)
    dispatcher = EventConsumerDispatcher(
        session_factory=session_factory,
        uow_factory=uow_factory,
        dlq_router=InMemoryDLQRouter(),
    )

    auth = AuthContext(user_id=str(actor_id), role=Role.WAITER)
    session = session_factory()
    service = OrderService(session)

    # 1. Confirm order (stages ordenes.orden.creada)
    confirmed_order = await service.confirm_order(order_id, confirmed_by=actor_id)
    assert confirmed_order.status == OrderStatus.CREATED
    assert confirmed_order.confirmed_at is not None
    assert len(db.outbox) == 1
    assert db.outbox[0].event_type == "ordenes.orden.creada"

    # 2. Cancel order while still in CREATED (awaiting stock)
    cancelled_order = await service.cancel_order(order_id, auth=auth, reason="Cliente se retiró")
    assert cancelled_order.status == OrderStatus.CANCELLED
    assert db.orders[order_id].status == OrderStatus.CANCELLED

    # Check outbox has ordenes.orden.cancelada
    assert len(db.outbox) == 2
    cancel_outbox = db.outbox[1]
    assert cancel_outbox.event_type == "ordenes.orden.cancelada"
    assert cancel_outbox.payload["orderId"] == str(order_id)
    items_cancelados = cast(list[dict[str, Any]], cancel_outbox.payload.get("itemsCancelados", []))
    assert len(items_cancelados) == 1
    assert items_cancelados[0]["offerId"] == "dish-hamburguesa"

    # 3. Inventory stub consumes ordenes.orden.cancelada
    await inventory_stub.handle_order_cancelled({"orderId": str(order_id)})
    assert str(order_id) in inventory_stub.cancelled_orders

    # 4. Late stock reservation arrives from Inventory
    late_ev_id = await inventory_stub.handle_order_created({"orderId": str(order_id)})
    assert late_ev_id is not None
    late_msg = stub_bus.get_events(EventName.STOCK_RESERVED)[0]["payload"]

    applied = await dispatcher.dispatch_raw(
        raw_message=json.dumps(late_msg),
        queue_name="ordenes.stock_reserved.queue",
        event_type=EventName.STOCK_RESERVED,
    )

    # 5. Must be ignored; order status must remain CANCELLED
    assert applied is False
    assert db.orders[order_id].status == OrderStatus.CANCELLED

    # Verify zero billing events staged
    outbox_types = [o.event_type for o in db.outbox]
    assert "ordenes.cobro.solicitado" not in outbox_types
    assert "ordenes.cuenta.solicitada" not in outbox_types


@pytest.mark.asyncio
async def test_it11_cancel_in_preparation_rejected_with_409() -> None:
    """IT-11: Attempting to cancel an order already in IN_PREPARATION is rejected."""
    db = InMemoryDb()
    order_id = uuid4()
    actor_id = uuid4()

    order = Order(
        public_id=order_id,
        table_number=3,
        status=OrderStatus.IN_PREPARATION,
        in_preparation_at=datetime.now(UTC),
    )
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    auth = AuthContext(user_id=str(actor_id), role=Role.WAITER)
    service = OrderService(session_factory())

    with pytest.raises(InvalidStateTransitionError):
        await service.cancel_order(order_id, auth=auth)

    assert db.orders[order_id].status == OrderStatus.IN_PREPARATION
    assert len(db.outbox) == 0
