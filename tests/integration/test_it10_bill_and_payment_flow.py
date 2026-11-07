"""Integration test IT-10 and IT-18: Bill request and payment settlement flow.

Satisfies: RF-04, RF-18, RF-20, RF-21, RNF-02, RNF-13
Covers: IT-10, IT-18, UT-BE-24, UT-BE-40, UT-BE-41, CT-08, CT-15
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import AuthContext
from app.db.models import Order, OrderItem, Outbox, ProcessedEvent
from app.domain.errors import ForbiddenRoleError, InvalidStateTransitionError
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role
from app.messaging.consumers.base import EventConsumerDispatcher, InMemoryDLQRouter
from app.messaging.events import EventName
from app.messaging.validator import validate_event_payload
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork
from app.services.orders import OrderService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requirement("RF-04", "RF-18", "RF-20", "RF-21", "RNF-02", "RNF-13"),
]


class InMemoryDb:
    """In-memory state double for integration testing."""

    def __init__(self) -> None:
        self.orders: dict[UUID, Order] = {}
        self.processed_events: set[tuple[UUID, UUID]] = set()
        self.outbox: list[Outbox] = []


class FakeResult:
    """Result double with unique() and all() methods."""

    def __init__(self, items: list[Order]) -> None:
        self._items = items

    def unique(self) -> FakeResult:
        return self

    def all(self) -> list[Order]:
        return self._items

    def __iter__(self) -> Any:
        return iter(self._items)


class FakeSession:
    """In-memory AsyncSession double tracking staged records and commits."""

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

    async def scalar(self, statement: Any) -> Any:
        statement_str = str(statement)
        params = statement.compile().params
        if "processed_events" in statement_str:
            order_id = next(
                (v for k, v in params.items() if "order_id" in k and isinstance(v, UUID)),
                None,
            )
            event_id = next(
                (v for k, v in params.items() if "event_id" in k and isinstance(v, UUID)),
                None,
            )
            if order_id and event_id and (order_id, event_id) in self.db.processed_events:
                return 1
            return None
        order_id = next((v for v in params.values() if isinstance(v, UUID)), None)
        if order_id is not None:
            return self.db.orders.get(order_id)
        return None

    async def scalars(self, statement: object) -> FakeResult:
        statement_str = str(statement)
        orders = list(self.db.orders.values())
        if "orders.status !=" in statement_str:
            orders = [o for o in orders if o.status != OrderStatus.PAID]
        return FakeResult(orders)

    async def commit(self) -> None:
        for order_id, event_id in self.staged_processed:
            self.db.processed_events.add((order_id, event_id))
        self.staged_processed.clear()

    async def rollback(self) -> None:
        self.staged_processed.clear()

    async def close(self) -> None:
        pass


class FakeUow(UnitOfWork):
    """UnitOfWork bound to in-memory session factory."""

    def __init__(self, session_factory: SessionFactory, db: InMemoryDb) -> None:
        super().__init__(session_factory)
        self._db = db

    async def __aenter__(self) -> FakeUow:
        session = self._session_factory()
        self.session = session
        self.orders = OrderRepository(session)
        self.processed_events = ProcessedEventsRepository(session)
        return self


@pytest.mark.asyncio
async def test_it10_bill_request_and_payment_settlement_flow() -> None:
    """IT-10 & IT-18: Bill request stages event with frozen prices and payment transitions to PAID.

    1. An order is in DELIVERED status with frozen dish prices, modifiers, and table.
    2. Waiter requests bill via OrderService.request_bill (RF-20).
    3. The order remains in DELIVERED status until payment settlement.
    4. Outbox contains 'ordenes.cuenta.solicitada' with dual identifiers (orderId, sessionId).
    5. The event payload strictly validates against its Draft-07 JSON Schema.
    6. Payments confirms settlement via 'pagos.pago.completado'.
    7. Order transitions to terminal status PAID (RF-21).
    8. Order in PAID status disappears from the active KDS queue (RF-04, RF-18).
    9. Redelivery of payment completed is idempotent.
    """
    db = InMemoryDb()
    order_id = uuid4()
    session_id = uuid4()
    waiter_id = uuid4()
    item_id = uuid4()

    order = Order(
        public_id=order_id,
        table_number=4,
        status=OrderStatus.DELIVERED,
        dining_session_id=session_id,
        responsible_waiter_id=waiter_id,
        total_amount=Decimal("380.00"),
    )
    item = OrderItem(
        public_id=item_id,
        dish_id="dish-steak-fmat",
        name="Corte Especial FMAT",
        quantity=2,
        unit_price=Decimal("190.00"),
        modifiers=["término tres cuartos"],
        notes="Servir con chimichurri",
        status=ItemStatus.DELIVERED,
    )
    order.items.append(item)
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return cast(AsyncSession, FakeSession(db))

    def uow_factory(factory: SessionFactory) -> UnitOfWork:
        return FakeUow(factory, db)

    dispatcher = EventConsumerDispatcher(
        session_factory=session_factory,
        uow_factory=uow_factory,
        dlq_router=InMemoryDLQRouter(),
    )

    waiter_auth = AuthContext(user_id=str(waiter_id), role=Role.WAITER)
    service = OrderService(session_factory())

    # Step 1: Waiter requests bill
    delivered_order = await service.request_bill(
        order_id=order_id,
        auth=waiter_auth,
    )
    # Status remains DELIVERED
    assert delivered_order.status == OrderStatus.DELIVERED
    assert db.orders[order_id].status == OrderStatus.DELIVERED

    # Step 2: Verify outbox row staged for ordenes.cuenta.solicitada
    assert len(db.outbox) == 1
    bill_row = db.outbox[0]
    assert bill_row.event_type == EventName.BILL_REQUESTED

    # Validate against JSON schema
    validate_event_payload(EventName.BILL_REQUESTED, bill_row.payload)

    # Check payload contents
    payload = bill_row.payload
    assert payload["orderId"] == str(order_id)
    assert payload["sessionId"] == str(session_id)
    assert payload["tableId"] == "MESA-4"
    assert payload["waiterId"] == str(waiter_id)
    assert payload["status"] == "DELIVERED"
    raw_total = payload.get("totalAmount")
    assert isinstance(raw_total, (int, float, str))
    assert float(raw_total) == 380.0

    items_payload = cast(list[dict[str, Any]], payload.get("items", []))
    assert len(items_payload) == 1
    assert items_payload[0]["plateId"] == "dish-steak-fmat"
    assert items_payload[0]["name"] == "Corte Especial FMAT"
    assert items_payload[0]["quantity"] == 2
    raw_unit_price = items_payload[0].get("unitPrice")
    assert isinstance(raw_unit_price, (int, float, str))
    assert float(raw_unit_price) == 190.0
    assert items_payload[0]["modifiers"] == ["término tres cuartos"]
    assert items_payload[0]["notes"] == "Servir con chimichurri"

    # Step 3: Payments emits pagos.pago.completado
    payment_event_id = uuid4()
    payment_payload = {
        "eventId": str(payment_event_id),
        "orderId": str(order_id),
        "sessionId": str(session_id),
        "montoTotal": 380.00,
        "totalPaid": 380.00,
        "status": "PAID",
    }

    applied = await dispatcher.dispatch_raw(
        raw_message=json.dumps(payment_payload),
        queue_name="ordenes.payment_completed.queue",
        event_type=EventName.PAYMENT_COMPLETED,
    )
    assert applied is True

    # Step 4: Verify order moved to PAID
    assert db.orders[order_id].status == OrderStatus.PAID
    assert (order_id, payment_event_id) in db.processed_events

    # Step 5: Verify KDS queue excludes orders in PAID
    repo = OrderRepository(session_factory())
    kds_orders = await repo.get_kds_queue(view="active")
    assert all(o.public_id != order_id for o in kds_orders)

    # Step 6: Idempotent redelivery of pagos.pago.completado
    duplicate_applied = await dispatcher.dispatch_raw(
        raw_message=json.dumps(payment_payload),
        queue_name="ordenes.payment_completed.queue",
        event_type=EventName.PAYMENT_COMPLETED,
    )
    assert duplicate_applied is False
    assert db.orders[order_id].status == OrderStatus.PAID


@pytest.mark.asyncio
async def test_it10_request_bill_role_and_state_guards() -> None:
    """Verify role authorization and state transition guards for request_bill."""
    db = InMemoryDb()
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=2,
        status=OrderStatus.IN_PREPARATION,
        total_amount=Decimal("150.00"),
    )
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return cast(AsyncSession, FakeSession(db))

    service = OrderService(session_factory())

    # Chef role is forbidden (403)
    chef_auth = AuthContext(user_id=str(uuid4()), role=Role.CHEF)
    with pytest.raises(ForbiddenRoleError):
        await service.request_bill(order_id=order_id, auth=chef_auth)

    # Kitchen role is forbidden (403)
    kitchen_auth = AuthContext(user_id=str(uuid4()), role=Role.KITCHEN)
    with pytest.raises(ForbiddenRoleError):
        await service.request_bill(order_id=order_id, auth=kitchen_auth)

    # Waiter role allowed, but order is in IN_PREPARATION -> 409
    waiter_auth = AuthContext(user_id=str(uuid4()), role=Role.WAITER)
    with pytest.raises(InvalidStateTransitionError):
        await service.request_bill(order_id=order_id, auth=waiter_auth)
