"""Integration test IT-08: Administrative void flow and payment completion.

Satisfies: RF-21, RF-26, RF-27, ADR-0012
Covers: IT-08, UT-BE-16, UT-BE-17, CT-12, CT-17
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
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork
from app.services.orders import OrderService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requirement("RF-21", "RF-26", "RF-27", "ADR-0012"),
]


class InMemoryDb:
    """In-memory state double for integration testing."""

    def __init__(self) -> None:
        self.orders: dict[UUID, Order] = {}
        self.processed_events: set[tuple[UUID, UUID]] = set()
        self.outbox: list[Outbox] = []


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
async def test_it08_administrative_void_flow_and_payment() -> None:
    """IT-08: Full administrative void flow with charge and payment settlement.

    1. An order is in IN_PREPARATION status with items and total_amount.
    2. Admin void transitions order to WASTED (RF-26).
    3. Both 'ordenes.comanda.mermada' and 'ordenes.cobro.solicitado' are staged in outbox (RF-27).
    4. Payments completes monetary settlement and emits 'pagos.pago.completado'.
    5. Consumer transitions order from WASTED to terminal state PAID (RF-21).
    6. Redelivery of 'pagos.pago.completado' is ignored idempotently (RNF-02).
    """
    db = InMemoryDb()
    order_id = uuid4()
    admin_id = uuid4()
    item_id = uuid4()

    order = Order(
        public_id=order_id,
        table_number=4,
        status=OrderStatus.IN_PREPARATION,
        total_amount=Decimal("190.00"),
    )
    item = OrderItem(
        public_id=item_id,
        dish_id="dish-enchiladas",
        name="Enchiladas Verdes",
        quantity=2,
        unit_price=Decimal("95.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    def uow_factory(factory: SessionFactory) -> UnitOfWork:
        return FakeUow(factory, db)

    dispatcher = EventConsumerDispatcher(
        session_factory=session_factory,
        uow_factory=uow_factory,
        dlq_router=InMemoryDLQRouter(),
    )

    admin_auth = AuthContext(user_id=str(admin_id), role=Role.ADMIN)
    service = OrderService(session_factory())

    # Step 1: Admin voids the in-preparation order
    voided_order = await service.void_order(
        order_id=order_id,
        auth=admin_auth,
        reason="Comensal cancela por emergencia personal",
    )
    assert voided_order.status == OrderStatus.WASTED
    assert db.orders[order_id].status == OrderStatus.WASTED

    # Step 2: Verify both outbox events staged atomically
    assert len(db.outbox) == 2
    outbox_types = [o.event_type for o in db.outbox]
    assert EventName.ORDER_WASTED in outbox_types
    assert EventName.CHARGE_REQUESTED in outbox_types

    # Inspect ordenes.comanda.mermada
    wasted_entry = next(o for o in db.outbox if o.event_type == EventName.ORDER_WASTED)
    assert wasted_entry.payload["orderId"] == str(order_id)
    assert wasted_entry.payload["motivo"] == "administrativo"
    assert wasted_entry.payload["reason"] == "ADMINISTRATIVE"
    items_afectados = cast(list[dict[str, Any]], wasted_entry.payload.get("itemsAfectados", []))
    assert len(items_afectados) == 1
    assert items_afectados[0]["offerId"] == "dish-enchiladas"
    assert items_afectados[0]["quantity"] == 2

    # Inspect ordenes.cobro.solicitado
    charge_entry = next(o for o in db.outbox if o.event_type == EventName.CHARGE_REQUESTED)
    assert charge_entry.payload["orderId"] == str(order_id)
    assert charge_entry.payload["status"] == "WASTED"
    raw_total = charge_entry.payload.get("totalAmount")
    assert isinstance(raw_total, (int, float, str))
    assert float(raw_total) == 190.0
    charge_items = cast(list[dict[str, Any]], charge_entry.payload.get("items", []))
    assert len(charge_items) == 1
    assert charge_items[0]["plateId"] == "dish-enchiladas"
    raw_unit_price = charge_items[0].get("unitPrice")
    assert isinstance(raw_unit_price, (int, float, str))
    assert float(raw_unit_price) == 95.0

    # Step 3: Payments module settles transaction and publishes pagos.pago.completado
    payment_event_id = uuid4()
    payment_payload = {
        "eventId": str(payment_event_id),
        "orderId": str(order_id),
        "montoTotal": 190.00,
        "totalPaid": 190.00,
        "status": "PAID",
    }

    applied = await dispatcher.dispatch_raw(
        raw_message=json.dumps(payment_payload),
        queue_name="ordenes.payment_completed.queue",
        event_type=EventName.PAYMENT_COMPLETED,
    )
    assert applied is True

    # Step 4: Verify order transitioned to terminal status PAID (RF-21)
    assert db.orders[order_id].status == OrderStatus.PAID
    assert (order_id, payment_event_id) in db.processed_events

    # Step 5: Redelivery of payment completed is ignored as duplicate
    duplicate_applied = await dispatcher.dispatch_raw(
        raw_message=json.dumps(payment_payload),
        queue_name="ordenes.payment_completed.queue",
        event_type=EventName.PAYMENT_COMPLETED,
    )
    assert duplicate_applied is False
    assert db.orders[order_id].status == OrderStatus.PAID


@pytest.mark.asyncio
async def test_it08_void_by_non_admin_is_rejected() -> None:
    """IT-08: Non-admin users cannot perform administrative void."""
    db = InMemoryDb()
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=2,
        status=OrderStatus.IN_PREPARATION,
    )
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    waiter_auth = AuthContext(user_id=str(uuid4()), role=Role.WAITER)
    service = OrderService(session_factory())

    with pytest.raises(ForbiddenRoleError):
        await service.void_order(order_id=order_id, auth=waiter_auth)

    assert db.orders[order_id].status == OrderStatus.IN_PREPARATION
    assert len(db.outbox) == 0


@pytest.mark.asyncio
async def test_it08_void_on_ineligible_status_is_rejected() -> None:
    """IT-08: Administrative void on RESERVED or CREATED status is rejected with 409."""
    db = InMemoryDb()
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=2,
        status=OrderStatus.RESERVED,
    )
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    admin_auth = AuthContext(user_id=str(uuid4()), role=Role.ADMIN)
    service = OrderService(session_factory())

    with pytest.raises(InvalidStateTransitionError):
        await service.void_order(order_id=order_id, auth=admin_auth)

    assert db.orders[order_id].status == OrderStatus.RESERVED
    assert len(db.outbox) == 0
