"""Integration test IT-09: Kitchen error void flow (unpaid cancellation).

Satisfies: RF-28, RF-29, RF-30, RF-31, ADR-0012
Covers: IT-09, UT-BE-18..22, CT-13, CT-17
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import AuthContext
from app.db.models import Order, OrderItem, Outbox, ProcessedEvent
from app.domain.errors import (
    ForbiddenRoleError,
    InvalidStateTransitionError,
    NoteRequiredError,
)
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role
from app.messaging.events import EventName
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork
from app.services.orders import OrderService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requirement("RF-28", "RF-29", "RF-30", "RF-31", "ADR-0012"),
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
async def test_it09_kitchen_error_void_flow_no_charge() -> None:
    """IT-09: Kitchen error void cancels entire order and stages wasted event with zero charge.

    1. An order is in IN_PREPARATION status with multiple active items.
    2. Chef voids the order specifying the mandatory kitchen incident note (RF-28, RF-29).
    3. Order transitions to VOIDED terminal status.
    4. Outbox contains exactly 'ordenes.comanda.mermada' with reason=KITCHEN_ERROR (RF-30).
    5. Prohibited: zero charge rows exist in outbox ('ordenes.cobro.solicitado' is absent) (RF-31).
    6. All items of the order are included in itemsAfectados.
    """
    db = InMemoryDb()
    order_id = uuid4()
    chef_id = uuid4()

    order = Order(
        public_id=order_id,
        table_number=5,
        status=OrderStatus.IN_PREPARATION,
        total_amount=Decimal("320.00"),
    )
    item1 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-salmon",
        name="Salmón a la Plancha",
        quantity=1,
        unit_price=Decimal("220.00"),
        status=ItemStatus.PENDING,
    )
    item2 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-soup",
        name="Crema de Chile Poblano",
        quantity=1,
        unit_price=Decimal("100.00"),
        status=ItemStatus.READY,
    )
    order.items.extend([item1, item2])
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    chef_auth = AuthContext(user_id=str(chef_id), role=Role.CHEF)
    service = OrderService(session_factory())

    # Step 1: Chef voids order for kitchen incident
    voided_order = await service.void_kitchen_error(
        order_id=order_id,
        auth=chef_auth,
        note="Salmón quemado en la plancha y crema derramada",
    )

    # Step 2: Verify order aggregate transitioned to VOIDED
    assert voided_order.status == OrderStatus.VOIDED
    assert db.orders[order_id].status == OrderStatus.VOIDED

    # Step 3: Verify Outbox contains comanda.mermada with KITCHEN_ERROR
    assert len(db.outbox) == 1
    wasted_entry = db.outbox[0]
    assert wasted_entry.event_type == EventName.ORDER_WASTED
    assert wasted_entry.payload["orderId"] == str(order_id)
    assert wasted_entry.payload["reason"] == "KITCHEN_ERROR"
    assert wasted_entry.payload["motivo"] == "error_cocina"
    assert wasted_entry.payload["note"] == "Salmón quemado en la plancha y crema derramada"

    items_afectados = cast(list[dict[str, Any]], wasted_entry.payload.get("itemsAfectados", []))
    assert len(items_afectados) == 2
    offers = {it["offerId"] for it in items_afectados}
    assert "dish-salmon" in offers
    assert "dish-soup" in offers

    # Step 4: Verify NO charge event is staged (RF-31: zero charge on kitchen error)
    charge_events = [e for e in db.outbox if e.event_type == EventName.CHARGE_REQUESTED]
    assert len(charge_events) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("forbidden_role", [Role.WAITER, Role.KITCHEN])
async def test_it09_void_by_forbidden_roles_is_rejected(forbidden_role: Role) -> None:
    """IT-09: Waiter and kitchen staff cannot void order for kitchen error."""
    db = InMemoryDb()
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=3,
        status=OrderStatus.IN_PREPARATION,
    )
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    auth = AuthContext(user_id=str(uuid4()), role=forbidden_role)
    service = OrderService(session_factory())

    with pytest.raises(ForbiddenRoleError):
        await service.void_kitchen_error(
            order_id=order_id,
            auth=auth,
            note="Intento de anulación no permitido",
        )

    assert db.orders[order_id].status == OrderStatus.IN_PREPARATION
    assert len(db.outbox) == 0


@pytest.mark.asyncio
async def test_it09_void_kitchen_error_validation_guards() -> None:
    """IT-09: Kitchen error void enforces non-empty note and valid status."""
    db = InMemoryDb()
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=7,
        status=OrderStatus.RESERVED,
    )
    db.orders[order_id] = order

    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    chef_auth = AuthContext(user_id=str(uuid4()), role=Role.CHEF)
    service = OrderService(session_factory())

    # Empty note raises NoteRequiredError
    with pytest.raises(NoteRequiredError):
        await service.void_kitchen_error(
            order_id=order_id,
            auth=chef_auth,
            note="   ",
        )

    # Ineligible status (RESERVED) raises InvalidStateTransitionError
    with pytest.raises(InvalidStateTransitionError):
        await service.void_kitchen_error(
            order_id=order_id,
            auth=chef_auth,
            note="Accidente en cocina",
        )
