"""Integration test IT-05: Saga Happy Path with Inventory Stub.

Order CREATED -> Inventory Stub (RESERVE mode) emits stock.reservado ->
Consumer processes event -> Order transitions to IN_PREPARATION with in_preparation_at timestamp.

Satisfies: RF-18, RF-33, RNF-02, RNF-13, OP-11
Covers: IT-05
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem, ProcessedEvent
from app.domain.order_status import OrderStatus
from app.messaging.consumers.base import EventConsumerDispatcher, InMemoryDLQRouter
from app.messaging.events import EventName
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork
from app.stubs.config import InventoryStubMode
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

    async def notify_item_changed(self, order: Order, item: OrderItem) -> None:
        pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_it05_saga_happy_path_transitions_to_in_preparation() -> None:
    """IT-05: Order in CREATED transitions to IN_PREPARATION when Inventory stub reserves stock."""
    # 1. Setup in-memory DB and seed Order in CREATED state
    db = InMemoryDb()
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        status=OrderStatus.CREATED,
        total_amount=Decimal("180.00"),
    )
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    def uow_factory(factory: SessionFactory) -> UnitOfWork:
        return FakeUow(factory, db)

    notifier = TrackingNotifier()
    dlq_router = InMemoryDLQRouter()

    dispatcher = EventConsumerDispatcher(
        session_factory=session_factory,
        uow_factory=uow_factory,
        notifier=notifier,
        dlq_router=dlq_router,
    )

    # 2. Setup Inventory contract stub and emit reservation
    stub_bus = InMemoryStubPublisher()
    inventory_stub = InventoryStub(publisher=stub_bus, mode=InventoryStubMode.RESERVE)

    order_created_payload = {
        "orderId": str(order_id),
        "correlationId": str(uuid4()),
        "items": [{"offerId": "PLT-BURGER-01", "quantity": 2}],
    }
    reservation_event_id = await inventory_stub.handle_order_created(order_created_payload)
    assert reservation_event_id is not None

    # Verify event was published and conforms to schema
    events = stub_bus.get_events(EventName.STOCK_RESERVED)
    assert len(events) == 1
    reservation_msg = events[0]["payload"]

    # 3. Deliver reservation message to consumer dispatcher
    applied = await dispatcher.dispatch_raw(
        raw_message=json.dumps(reservation_msg),
        queue_name="ordenes.stock_reserved.queue",
        event_type=EventName.STOCK_RESERVED,
    )

    # 4. Assert order state transition
    assert applied is True
    persisted_order = db.orders[order_id]
    assert persisted_order.status == OrderStatus.RESERVED
    assert persisted_order.stock_reserved_at is not None
    initial_reserved_at = persisted_order.stock_reserved_at
    assert len(notifier.notifications) == 1

    # 5. Idempotent Redelivery Check: repeating delivery does not change timestamp or re-notify
    reapplied = await dispatcher.dispatch_raw(
        raw_message=json.dumps(reservation_msg),
        queue_name="ordenes.stock_reserved.queue",
        event_type=EventName.STOCK_RESERVED,
    )
    assert reapplied is False
    assert persisted_order.stock_reserved_at == initial_reserved_at
    assert len(notifier.notifications) == 1
