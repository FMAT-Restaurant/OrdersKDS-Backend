"""Integration test IT-06: Saga Stock Insufficient with Inventory Stub.

Order CREATED -> Inventory Stub (REJECT mode) emits stock.insuficiente ->
Consumer processes event -> Order transitions to REJECTED.

Satisfies: RF-34, RNF-02, RNF-13, OP-11
Covers: IT-06
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_it06_saga_insufficient_stock_transitions_to_rejected() -> None:
    """IT-06: Order in CREATED transitions to REJECTED when Inventory stub rejects stock."""
    # 1. Setup in-memory DB and seed Order in CREATED state
    db = InMemoryDb()
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        status=OrderStatus.CREATED,
        total_amount=Decimal("210.00"),
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

    # 2. Setup Inventory contract stub in REJECT mode
    stub_bus = InMemoryStubPublisher()
    inventory_stub = InventoryStub(publisher=stub_bus, mode=InventoryStubMode.REJECT)

    order_created_payload = {
        "orderId": str(order_id),
        "correlationId": str(uuid4()),
        "items": [{"plateId": "PLT-RARE-01", "quantity": 1}],
    }
    rejection_event_id = await inventory_stub.handle_order_created(order_created_payload)
    assert rejection_event_id is not None

    events = stub_bus.get_events(EventName.STOCK_INSUFFICIENT)
    assert len(events) == 1
    rejection_msg = events[0]["payload"]
    assert "missingIngredients" in rejection_msg or "faltantes" in rejection_msg

    # 3. Deliver rejection message to consumer dispatcher
    applied = await dispatcher.dispatch_raw(
        raw_message=json.dumps(rejection_msg),
        queue_name="ordenes.stock_insufficient.queue",
        event_type=EventName.STOCK_INSUFFICIENT,
    )

    # 4. Assert order state transition to REJECTED
    assert applied is True
    persisted_order = db.orders[order_id]
    assert persisted_order.status == OrderStatus.REJECTED
    assert len(notifier.notifications) == 1
