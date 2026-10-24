"""Integration test IT-07: Saga Timeout, Late Reservations, and Multi-Stub Flows.

Satisfies: RF-35, RF-21, RF-17, RNF-02, RNF-13, OP-11
Covers: IT-07, IT-LATE
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, ProcessedEvent
from app.domain.order_status import OrderStatus
from app.messaging.consumers.base import EventConsumerDispatcher, InMemoryDLQRouter
from app.messaging.events import EventName
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork
from app.stubs.billing_stub import BillingStub
from app.stubs.config import InventoryStubMode
from app.stubs.floor_stub import FloorStub
from app.stubs.inventory_stub import InventoryStub
from app.stubs.protocol import InMemoryStubPublisher


class InMemoryDb:
    def __init__(self) -> None:
        self.orders: dict[UUID, Order] = {}
        self.processed_events: set[tuple[UUID, UUID]] = set()


class FakeSession:
    def __init__(self, db: InMemoryDb) -> None:
        self.db = db
        self.staged_processed: list[tuple[UUID, UUID]] = []

    def add(self, instance: object) -> None:
        if isinstance(instance, ProcessedEvent):
            self.staged_processed.append((instance.order_id, instance.event_id))
        elif isinstance(instance, Order):
            self.db.orders[instance.public_id] = instance

    async def commit(self) -> None:
        for order_id, event_id in self.staged_processed:
            self.db.processed_events.add((order_id, event_id))
        self.staged_processed.clear()

    async def rollback(self) -> None:
        self.staged_processed.clear()

    async def close(self) -> None:
        pass


class FakeOrderRepo(OrderRepository):
    def __init__(self, session: AsyncSession, db: InMemoryDb) -> None:
        super().__init__(session)
        self._db = db

    async def get_by_public_id(
        self, public_id: UUID | str, *, for_update: bool = False
    ) -> Order | None:
        uid = public_id if isinstance(public_id, UUID) else UUID(public_id)
        return self._db.orders.get(uid)


class FakeProcessedRepo(ProcessedEventsRepository):
    def __init__(self, session: AsyncSession, db: InMemoryDb) -> None:
        super().__init__(session)
        self._db = db

    async def exists(self, event_id: UUID | str, order_id: UUID | str) -> bool:
        e_uuid = event_id if isinstance(event_id, UUID) else UUID(event_id)
        o_uuid = order_id if isinstance(order_id, UUID) else UUID(order_id)
        return (o_uuid, e_uuid) in self._db.processed_events

    def add(
        self,
        event_id: UUID | str,
        order_id: UUID | str,
        event_type: str | None = None,
    ) -> ProcessedEvent:
        e_uuid = event_id if isinstance(event_id, UUID) else UUID(event_id)
        o_uuid = order_id if isinstance(order_id, UUID) else UUID(order_id)
        row = ProcessedEvent(order_id=o_uuid, event_id=e_uuid, event_type=event_type)
        self._db.processed_events.add((o_uuid, e_uuid))
        return row


class FakeUow(UnitOfWork):
    def __init__(self, session_factory: SessionFactory, db: InMemoryDb) -> None:
        super().__init__(session_factory)
        self._db = db

    async def __aenter__(self) -> FakeUow:
        await super().__aenter__()
        assert self.session is not None
        self.orders = FakeOrderRepo(self.session, self._db)
        self.processed_events = FakeProcessedRepo(self.session, self._db)
        return self


class TrackingNotifier:
    def __init__(self) -> None:
        self.notifications: list[Order] = []

    async def notify_order_changed(self, order: Order) -> None:
        self.notifications.append(order)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_it07_saga_timeout_and_late_reservation_ignored() -> None:
    """IT-07: Silent Inventory stub leads to EXPIRED order, late reservation is discarded."""
    db = InMemoryDb()
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        status=OrderStatus.CREATED,
        total_amount=Decimal("300.00"),
    )
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    def uow_factory(factory: SessionFactory) -> UnitOfWork:
        return FakeUow(factory, db)

    notifier = TrackingNotifier()
    dispatcher = EventConsumerDispatcher(
        session_factory=session_factory,
        uow_factory=uow_factory,
        notifier=notifier,
        dlq_router=InMemoryDLQRouter(),
    )

    stub_bus = InMemoryStubPublisher()
    inventory_stub = InventoryStub(publisher=stub_bus, mode=InventoryStubMode.SILENT)

    # 1. Order created event sent, stub in SILENT mode does not respond
    ev = await inventory_stub.handle_order_created({"orderId": str(order_id)})
    assert ev is None
    assert len(stub_bus.get_events()) == 0

    # 2. Timeout watcher marks order as EXPIRED
    order.status = OrderStatus.EXPIRED

    # 3. Late reservation arrives from delayed/late stub
    inventory_stub.set_mode(InventoryStubMode.RESERVE)
    late_ev_id = await inventory_stub.handle_order_created({"orderId": str(order_id)})
    assert late_ev_id is not None
    late_msg = stub_bus.get_events(EventName.STOCK_RESERVED)[0]["payload"]

    applied = await dispatcher.dispatch_raw(
        raw_message=json.dumps(late_msg),
        queue_name="ordenes.stock_reserved.queue",
        event_type=EventName.STOCK_RESERVED,
    )

    # 4. Late reservation MUST be ignored and order status must remain EXPIRED
    assert applied is False
    assert db.orders[order_id].status == OrderStatus.EXPIRED
    assert db.orders[order_id].in_preparation_at is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_it07_late_reservation_on_cancelled_order_ignored() -> None:
    """Late reservation arriving after order was CANCELLED is discarded."""
    db = InMemoryDb()
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        status=OrderStatus.CANCELLED,
        total_amount=Decimal("150.00"),
    )
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    def uow_factory(factory: SessionFactory) -> UnitOfWork:
        return FakeUow(factory, db)

    notifier = TrackingNotifier()
    dispatcher = EventConsumerDispatcher(
        session_factory=session_factory,
        uow_factory=uow_factory,
        notifier=notifier,
        dlq_router=InMemoryDLQRouter(),
    )

    stub_bus = InMemoryStubPublisher()
    inventory_stub = InventoryStub(publisher=stub_bus, mode=InventoryStubMode.LATE_ON_CANCELLED)

    late_ev_id = await inventory_stub.handle_order_created({"orderId": str(order_id)})
    assert late_ev_id is not None
    late_msg = stub_bus.get_events(EventName.STOCK_RESERVED)[0]["payload"]

    applied = await dispatcher.dispatch_raw(
        raw_message=json.dumps(late_msg),
        queue_name="ordenes.stock_reserved.queue",
        event_type=EventName.STOCK_RESERVED,
    )

    assert applied is False
    assert db.orders[order_id].status == OrderStatus.CANCELLED

    # Compensation event handled by Inventory stub
    await inventory_stub.handle_order_cancelled({"orderId": str(order_id)})
    assert str(order_id) in inventory_stub.cancelled_orders


@pytest.mark.integration
@pytest.mark.asyncio
async def test_it07_multi_stub_e2e_flow_floor_inventory_billing() -> None:
    """Full multi-stub lifecycle: Floor opens session -> Inventory reserves -> Billing settles."""
    db = InMemoryDb()
    order_id = uuid4()
    session_id = uuid4()
    order = Order(
        public_id=order_id,
        status=OrderStatus.CREATED,
        total_amount=Decimal("250.00"),
    )
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    def uow_factory(factory: SessionFactory) -> UnitOfWork:
        return FakeUow(factory, db)

    notifier = TrackingNotifier()
    dispatcher = EventConsumerDispatcher(
        session_factory=session_factory,
        uow_factory=uow_factory,
        notifier=notifier,
        dlq_router=InMemoryDLQRouter(),
    )

    stub_bus = InMemoryStubPublisher()
    floor_stub = FloorStub(publisher=stub_bus)
    inventory_stub = InventoryStub(publisher=stub_bus, mode=InventoryStubMode.RESERVE)
    billing_stub = BillingStub(publisher=stub_bus)

    # 1. Floor stub emits dining session started
    await floor_stub.emit_session_started(
        session_id=session_id,
        table_id="MESA_8",
        diners_count=3,
    )
    assert len(stub_bus.get_events("sala.dining_session.iniciada")) == 1

    # 2. Inventory stub confirms stock -> order transitions to IN_PREPARATION
    await inventory_stub.handle_order_created({"orderId": str(order_id)})
    res_msg = stub_bus.get_events(EventName.STOCK_RESERVED)[0]["payload"]
    await dispatcher.dispatch_raw(
        raw_message=json.dumps(res_msg),
        queue_name="ordenes.stock_reserved.queue",
        event_type=EventName.STOCK_RESERVED,
    )
    assert db.orders[order_id].status == OrderStatus.IN_PREPARATION

    # 3. Order is prepared and delivered
    db.orders[order_id].status = OrderStatus.DELIVERED

    # 4. Bill is requested -> Billing stub auto-pays -> Consumer transitions order to PAID
    await billing_stub.handle_bill_requested(
        {
            "orderId": str(order_id),
            "sessionId": str(session_id),
            "totalAmount": 250.00,
        }
    )
    pay_msg = stub_bus.get_events(EventName.PAYMENT_COMPLETED)[0]["payload"]
    await dispatcher.dispatch_raw(
        raw_message=json.dumps(pay_msg),
        queue_name="ordenes.payment_completed.queue",
        event_type=EventName.PAYMENT_COMPLETED,
    )
    assert db.orders[order_id].status == OrderStatus.PAID
