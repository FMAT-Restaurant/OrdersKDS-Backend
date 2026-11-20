"""Unit and SQLite ORM integration tests for ShiftRepository and ShiftService.

Satisfies: RF-12, RF-15, RNF-03, RNF-09
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.api.dependencies.auth import AuthContext
from app.api.schemas.order import OrderResponse
from app.db.base import Base
from app.db.models import Order, Shift
from app.domain.errors import (
    ForbiddenRoleError,
    NoActiveShiftError,
    ShiftAlreadyOpenError,
    ShiftClosedError,
    VersionConflictError,
)
from app.domain.order_status import OrderStatus
from app.domain.roles import Role
from app.domain.shift import ShiftStatus
from app.messaging.consumers.dining_session import handle_dining_session_created
from app.messaging.events import DiningSessionCreatedMessage, EventName
from app.repositories.orders import OrderRepository
from app.repositories.unit_of_work import UnitOfWork
from app.services.orders import OrderService
from app.services.shifts import ShiftService, get_shift_service
from tests.unit.api.conftest import FakeAsyncSession, SpyOrderNotifier

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-12", "RF-15", "RNF-03", "RNF-09"),
]


class SqliteAsyncSessionAdapter:
    """AsyncSession test adapter backed by a real in-memory SQLite ORM Session."""

    def __init__(self, sync_session: Session) -> None:
        self._sync = sync_session

    def add(self, instance: object) -> None:
        self._sync.add(instance)

    async def flush(self) -> None:
        self._sync.flush()

    async def commit(self) -> None:
        self._sync.commit()

    async def rollback(self) -> None:
        self._sync.rollback()

    async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return self._sync.scalar(statement)

    async def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return self._sync.scalars(statement)


def _auth(role: Role, *permissions: str, user_id: str | None = None) -> AuthContext:
    return AuthContext(
        user_id=user_id or str(uuid4()),
        role=role,
        permissions=frozenset(permissions) if permissions else None,
    )


def _create_sqlite_session() -> tuple[Session, SqliteAsyncSessionAdapter]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sync_session = Session(engine, expire_on_commit=False)
    return sync_session, SqliteAsyncSessionAdapter(sync_session)


@pytest.mark.asyncio
async def test_shift_lifecycle_resets_order_folio_while_preserving_db_identity() -> None:
    """Real SQLite ORM flow: Shift #1 folios 1,2 -> close -> open Shift #2 -> folio 1 again."""
    sync_session, adapter = _create_sqlite_session()
    try:
        async_session = cast(AsyncSession, adapter)
        shift_service = get_shift_service(async_session)
        order_service = OrderService(session=async_session, notifier=SpyOrderNotifier())
        chef = _auth(Role.CHEF, "kitchen:orders:void_error")
        waiter_uuid = uuid4()

        initial_status = await shift_service.get_current_status()
        assert initial_status.active_shift is None
        assert initial_status.can_accept_orders is True
        assert initial_status.next_shift_number == 1

        order1 = await order_service.create_order(
            table_number=1,
            is_takeaway=False,
            dining_session_id=None,
            actor_uuid=waiter_uuid,
        )
        order2 = await order_service.create_order(
            table_number=2,
            is_takeaway=False,
            dining_session_id=None,
            actor_uuid=waiter_uuid,
        )
        assert order1.shift_folio == 1
        assert order2.shift_folio == 2
        assert OrderResponse.from_model(order1).order_number == 1
        assert OrderResponse.from_model(order2).order_number == 2

        open_status = await shift_service.get_current_status()
        assert open_status.active_shift is not None
        assert open_status.active_shift.shift_number == 1
        assert open_status.active_shift.last_order_folio == 2

        closed_shift = await shift_service.close_current_shift(actor=chef, notes="Cierre comida")
        assert closed_shift.status == ShiftStatus.CLOSED
        assert closed_shift.last_order_folio == 2

        closed_status = await shift_service.get_current_status()
        assert closed_status.active_shift is None
        assert closed_status.can_accept_orders is False
        assert closed_status.last_closed_shift is not None
        assert closed_status.next_shift_number == 2

        with pytest.raises(ShiftClosedError):
            await order_service.create_order(
                table_number=3,
                is_takeaway=False,
                dining_session_id=None,
                actor_uuid=waiter_uuid,
            )

        shift2 = await shift_service.open_shift(actor=chef, notes="Apertura cena")
        assert shift2.shift_number == 2
        assert shift2.last_order_folio == 0

        order3 = await order_service.create_order(
            table_number=4,
            is_takeaway=False,
            dining_session_id=None,
            actor_uuid=waiter_uuid,
        )
        assert order3.shift_folio == 1
        assert OrderResponse.from_model(order3).order_number == 1
        assert order3.id != order1.id
        assert order3.public_id != order1.public_id
        assert order3.shift_id != order1.shift_id
    finally:
        sync_session.close()


def test_unique_shift_folio_constraint_blocks_duplicate_folio_in_same_shift() -> None:
    """Database unique constraint rejects duplicate (shift_id, shift_folio)."""
    sync_session, _ = _create_sqlite_session()
    try:
        shift = Shift(
            public_id=uuid4(),
            shift_number=1,
            status=ShiftStatus.OPEN,
            last_order_folio=1,
        )
        sync_session.add(shift)
        sync_session.flush()

        sync_session.add(
            Order(
                public_id=uuid4(),
                table_number=1,
                status=OrderStatus.CREATED,
                shift_id=shift.id,
                shift_folio=1,
                items=[],
            )
        )
        sync_session.commit()

        sync_session.add(
            Order(
                public_id=uuid4(),
                table_number=2,
                status=OrderStatus.CREATED,
                shift_id=shift.id,
                shift_folio=1,
                items=[],
            )
        )
        with pytest.raises(IntegrityError):
            sync_session.commit()
    finally:
        sync_session.rollback()
        sync_session.close()


@pytest.mark.asyncio
async def test_shift_service_defense_in_depth_pbac_and_conflict_handling() -> None:
    """Verify ForbiddenRoleError, ShiftAlreadyOpenError, NoActiveShiftError, and 409 rollback."""
    sync_session, adapter = _create_sqlite_session()
    try:
        service = ShiftService(cast(AsyncSession, adapter))
        kitchen = _auth(Role.KITCHEN, "kitchen:queues:view")
        waiter = _auth(Role.WAITER, "orders:orders:create")
        revoked_chef = AuthContext(user_id=str(uuid4()), role=Role.CHEF, permissions=frozenset())
        admin = _auth(Role.ADMIN, "*")

        with pytest.raises(ForbiddenRoleError):
            await service.open_shift(actor=kitchen)
        with pytest.raises(ForbiddenRoleError):
            await service.close_current_shift(actor=waiter)
        with pytest.raises(ForbiddenRoleError):
            await service.open_shift(actor=revoked_chef)
        with pytest.raises(NoActiveShiftError):
            await service.close_current_shift(actor=admin)

        await service.open_shift(actor=admin, notes="Turno inicial")
        with pytest.raises(ShiftAlreadyOpenError):
            await service.open_shift(actor=admin)
    finally:
        sync_session.close()


@pytest.mark.asyncio
async def test_shift_service_maps_stale_data_error_to_version_conflict() -> None:
    """StaleDataError on shift commit rolls back and raises VersionConflictError."""

    class _StaleShiftSession(FakeAsyncSession):
        async def commit(self) -> None:
            raise StaleDataError("concurrent shift close")

    session = _StaleShiftSession()
    service = ShiftService(cast(AsyncSession, session))
    chef = _auth(Role.CHEF, "kitchen:shifts:manage")

    with pytest.raises(VersionConflictError):
        await service.open_shift(actor=chef)

    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_dining_session_consumer_survives_when_shift_is_closed() -> None:
    """DiningSessionCreated does not bounce to DLQ when the active shift is manually closed."""

    class _ProcessedStub:
        def __init__(self) -> None:
            self.added: list[tuple[UUID, UUID, EventName]] = []

        def add(self, *, event_id: UUID, order_id: UUID, event_type: EventName) -> None:
            self.added.append((event_id, order_id, event_type))

    class _FakeUoW:
        def __init__(self, session: FakeAsyncSession) -> None:
            self.session = session
            self.orders = OrderRepository(cast(AsyncSession, session))
            self.processed_events = _ProcessedStub()

        async def __aenter__(self) -> _FakeUoW:
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def commit(self) -> None:
            await self.session.commit()

    session = FakeAsyncSession()
    session.add(
        Shift(
            id=1,
            public_id=uuid4(),
            shift_number=1,
            status=ShiftStatus.CLOSED,
            last_order_folio=4,
        )
    )
    msg = DiningSessionCreatedMessage(eventId=uuid4(), sessionId=uuid4(), waiterId=uuid4())
    uow = _FakeUoW(session)

    handled = await handle_dining_session_created(msg, cast(UnitOfWork, uow))

    assert handled is True
    created = next(iter(session.db.values()))
    assert created.shift_id is None
    assert created.shift_folio is None
    assert created.items == []


@pytest.mark.asyncio
async def test_ensure_shift_open_for_orders_branches() -> None:
    """ensure_shift_open_for_orders allows empty DB or OPEN shift and blocks CLOSED shift."""
    session = FakeAsyncSession()
    service = ShiftService(cast(AsyncSession, session))

    initial = await service.ensure_shift_open_for_orders()
    assert initial is None
    assert session.shifts == []
    assert session.committed is False

    open_shift = Shift(
        id=1,
        public_id=uuid4(),
        shift_number=1,
        status=ShiftStatus.OPEN,
        last_order_folio=0,
    )
    session.add(open_shift)

    active = await service.ensure_shift_open_for_orders()
    assert active is open_shift

    open_shift.status = ShiftStatus.CLOSED
    with pytest.raises(ShiftClosedError):
        await service.ensure_shift_open_for_orders()
