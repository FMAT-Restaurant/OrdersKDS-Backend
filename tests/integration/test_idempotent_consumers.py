"""Integration tests for consumer idempotency (IT-03) and DLQ health (IT-15).

Satisfies: RNF-02, RNF-13, RF-18, RF-21, RF-33, OP-11
Covers: IT-03, IT-15
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
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


class StatefulInMemoryDb:
    """In-memory database storing Order and ProcessedEvent records."""

    def __init__(self) -> None:
        self.orders: dict[UUID, Order] = {}
        self.processed_events: set[tuple[UUID, UUID]] = set()


class StatefulInMemorySession:
    """Stateful fake AsyncSession implementing repository queries for integration tests."""

    def __init__(self, db: StatefulInMemoryDb) -> None:
        self.db = db
        self.staged_processed: list[tuple[UUID, UUID]] = []
        self.committed = False
        self.rolled_back = False

    async def scalar(self, statement: Any) -> Any:
        statement_str = str(statement)
        if "FROM orders" in statement_str:
            for order in self.db.orders.values():
                return order
            return None
        return None

    def add(self, instance: object) -> None:
        if isinstance(instance, ProcessedEvent):
            self.staged_processed.append((instance.order_id, instance.event_id))
        elif isinstance(instance, Order):
            self.db.orders[instance.public_id] = instance

    async def commit(self) -> None:
        self.committed = True
        for order_id, event_id in self.staged_processed:
            self.db.processed_events.add((order_id, event_id))
        self.staged_processed.clear()

    async def rollback(self) -> None:
        self.rolled_back = True
        self.staged_processed.clear()

    async def close(self) -> None:
        pass


class TrackingNotifier:
    """Notification recorder."""

    def __init__(self) -> None:
        self.notifications: list[Order] = []

    async def notify_order_changed(self, order: Order) -> None:
        self.notifications.append(order)

    async def notify_item_changed(self, order: Order, item: OrderItem) -> None:
        pass


class StatefulOrderRepo(OrderRepository):
    """Repo routing Order lookups directly to StatefulInMemoryDb."""

    def __init__(self, session: AsyncSession, db: StatefulInMemoryDb) -> None:
        super().__init__(session)
        self._db = db

    async def get_by_public_id(
        self, public_id: UUID | str, *, for_update: bool = False
    ) -> Order | None:
        uid = public_id if isinstance(public_id, UUID) else UUID(public_id)
        return self._db.orders.get(uid)


class StatefulProcessedRepo(ProcessedEventsRepository):
    """Repo routing ProcessedEvents checks and additions to StatefulInMemoryDb."""

    def __init__(self, session: AsyncSession, db: StatefulInMemoryDb) -> None:
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


class StatefulIntegrationUow(UnitOfWork):
    """UnitOfWork connected to stateful in-memory database."""

    def __init__(self, session_factory: SessionFactory, db: StatefulInMemoryDb) -> None:
        super().__init__(session_factory)
        self._db = db

    async def __aenter__(self) -> StatefulIntegrationUow:
        await super().__aenter__()
        assert self.session is not None
        self.orders = StatefulOrderRepo(self.session, self._db)
        self.processed_events = StatefulProcessedRepo(self.session, self._db)
        return self


# ---------------------------------------------------------------------------
# IT-03: test_redelivered_message_is_applied_once
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redelivered_message_is_applied_once() -> None:
    """Verify that identical redelivered messages are processed idempotently.

    First delivery transitions order and stamps in_preparation_at.
    Second delivery is recognized as duplicate: state and in_preparation_at
    are untouched, and notification is not triggered twice.

    Satisfies: RNF-02, IT-03
    """
    order_id = uuid4()
    event_id = uuid4()

    order = Order(
        public_id=order_id,
        status=OrderStatus.CREATED,
        version=1,
        total_amount=Decimal("250.00"),
        in_preparation_at=None,
    )

    db = StatefulInMemoryDb()
    db.orders[order_id] = order

    notifier = TrackingNotifier()
    dlq = InMemoryDLQRouter()

    def session_factory() -> AsyncSession:
        return StatefulInMemorySession(db)  # type: ignore[return-value]

    def uow_factory(factory: SessionFactory) -> UnitOfWork:
        return StatefulIntegrationUow(factory, db)

    dispatcher = EventConsumerDispatcher(
        session_factory=session_factory,
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=uow_factory,
    )

    raw_payload = json.dumps(
        {
            "eventId": str(event_id),
            "orderId": str(order_id),
            "eventType": EventName.STOCK_RESERVED,
            "reservas": [{"offerId": "PLT-100", "quantity": 2}],
        }
    )

    # --- First Delivery ---
    first_result = await dispatcher.dispatch_raw(
        raw_message=raw_payload,
        queue_name="ordenes.stock_reserved.queue",
    )

    assert first_result is True
    assert order.status is OrderStatus.RESERVED
    assert order.stock_reserved_at is not None
    first_reserved_timestamp = order.stock_reserved_at
    assert len(notifier.notifications) == 1
    assert (order_id, event_id) in db.processed_events

    # --- Redelivery of the exact same message ---
    second_result = await dispatcher.dispatch_raw(
        raw_message=raw_payload,
        queue_name="ordenes.stock_reserved.queue",
    )

    # Redelivery is safely swallowed / not re-applied
    assert second_result is False
    assert order.status is OrderStatus.RESERVED
    # stock_reserved_at must remain identical (not overwritten)
    assert order.stock_reserved_at == first_reserved_timestamp
    # Notification was not duplicated
    assert len(notifier.notifications) == 1
    # No messages went to DLQ
    assert len(dlq.dead_letters) == 0


# ---------------------------------------------------------------------------
# IT-15: test_poison_message_goes_to_dlq_and_consumer_stays_healthy
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_poison_message_goes_to_dlq_and_consumer_stays_healthy() -> None:
    """Verify that poison messages are routed to DLQ while keeping consumer healthy.

    Sends N poison messages (malformed JSON, missing fields, invalid types),
    verifies each reaches the DLQ, and then verifies that a subsequent valid
    message is processed successfully without worker termination.

    Satisfies: RNF-02, RNF-13, IT-15
    """
    db = StatefulInMemoryDb()
    valid_order_id = uuid4()
    order = Order(
        public_id=valid_order_id,
        status=OrderStatus.CREATED,
        version=1,
        total_amount=Decimal("180.00"),
    )
    db.orders[valid_order_id] = order

    notifier = TrackingNotifier()
    dlq = InMemoryDLQRouter()

    def session_factory() -> AsyncSession:
        return StatefulInMemorySession(db)  # type: ignore[return-value]

    def uow_factory(factory: SessionFactory) -> UnitOfWork:
        return StatefulIntegrationUow(factory, db)

    dispatcher = EventConsumerDispatcher(
        session_factory=session_factory,
        dlq_router=dlq,
        notifier=notifier,
        uow_factory=uow_factory,
    )

    queue_name = "ordenes.stock_insufficient.queue"

    poison_messages: list[object] = [
        b"{bad_json: True",  # Malformed JSON
        {"randomKey": "no eventId or orderId"},  # Missing required fields
        {"eventId": "not-uuid", "orderId": str(valid_order_id)},  # Invalid UUID format
        {"eventId": str(uuid4())},  # Missing orderId
        b"",  # Empty payload
    ]

    # Dispatch N poison messages
    for idx, poison in enumerate(poison_messages):
        result = await dispatcher.dispatch_raw(
            raw_message=poison,
            queue_name=queue_name,
        )
        assert result is False
        assert len(dlq.dead_letters) == idx + 1
        assert dlq.dead_letters[-1]["dlq_queue"] == f"{queue_name}.dlq"
        assert order.status is OrderStatus.CREATED

    # Ensure consumer worker is still alive and processes valid message immediately after
    valid_event_id = uuid4()
    valid_payload = {
        "eventId": str(valid_event_id),
        "orderId": str(valid_order_id),
        "eventType": EventName.STOCK_INSUFFICIENT,
        "faltantes": ["tomate", "lechuga"],
    }

    recovery_result = await dispatcher.dispatch_raw(
        raw_message=valid_payload,
        queue_name=queue_name,
    )

    assert recovery_result is True
    assert order.status is OrderStatus.REJECTED
    assert len(notifier.notifications) == 1
    assert (valid_order_id, valid_event_id) in db.processed_events
    # Dead letters count did not increase on valid message
    assert len(dlq.dead_letters) == len(poison_messages)
