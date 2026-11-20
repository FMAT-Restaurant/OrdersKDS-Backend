"""Regression tests for critical order creation, serialization, and concurrency fixes.

Covers Priority 2 audit fixes:
- Eager items=[] prevents MissingGreenlet post-commit on OrderResponse serialization.
- DiningSessionConsumer initializes items=[] cleanly without unloaded relationship state.
- All mutating OrderService transitions and REST handlers lock with FOR UPDATE and map
  StaleDataError to rollback() + 409 VERSION_CONFLICT.

Satisfies: RF-12, RF-20, RF-24, RF-26, RNF-03, RNF-09
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.api.dependencies.auth import AuthContext
from app.api.schemas.order import OrderResponse
from app.core.error_codes import ErrorCode
from app.db.base import Base
from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.errors import VersionConflictError
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role
from app.main import app
from app.messaging.consumers.dining_session import handle_dining_session_created
from app.messaging.events import DiningSessionCreatedMessage, EventName
from app.repositories.orders import OrderRepository
from app.repositories.unit_of_work import UnitOfWork
from app.services.catalog import get_catalog_validator
from app.services.notifications import get_order_notifier
from app.services.orders import OrderService, get_order_service
from tests.unit.api.conftest import FakeAsyncSession, FakeCatalogValidator, SpyOrderNotifier

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-12", "RF-20", "RF-24", "RF-26", "RNF-03", "RNF-09"),
]


class StaleCommitFakeSession(FakeAsyncSession):
    """In-memory session double capturing FOR UPDATE flags and raising StaleDataError on commit."""

    def __init__(self, db: dict[UUID, Order]) -> None:
        super().__init__(db)
        self.for_update_used: list[bool] = []

    async def flush(self) -> None:
        pass

    async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.for_update_used.append(getattr(statement, "_for_update_arg", None) is not None)
        return await super().scalar(statement, *args, **kwargs)

    async def commit(self) -> None:
        raise StaleDataError("Simulated optimistic lock failure during commit")


def _auth(role: Role, *permissions: str) -> AuthContext:
    return AuthContext(user_id=str(uuid4()), role=role, permissions=frozenset(permissions))


def _seed_order(
    status: OrderStatus = OrderStatus.IN_PREPARATION,
    *,
    item_status: ItemStatus = ItemStatus.PENDING,
) -> tuple[Order, OrderItem]:
    order = Order(
        public_id=uuid4(),
        table_number=9,
        status=status,
        version=2,
        items=[],
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="recipe-taco-01",
        offer_id="offer-taco-01",
        name="Taco Pastor",
        quantity=2,
        unit_price=Decimal("55.00"),
        status=item_status,
    )
    order.items.append(item)
    order.recalculate_total()
    return order, item


def test_order_creation_eager_items_prevents_unloaded_lazy_load_after_commit() -> None:
    """Verify real SQLAlchemy Session(expire_on_commit=False) keeps items loaded post-commit."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        order = Order(
            public_id=uuid4(),
            table_number=4,
            status=OrderStatus.CREATED,
            items=[],
        )
        session.add(order)
        session.commit()

        assert "items" not in inspect(order).unloaded
        empty_response = OrderResponse.from_model(order)
        assert empty_response.items == []
        assert empty_response.order_id == order.public_id

        order.items.append(
            OrderItem(
                public_id=uuid4(),
                dish_id="recipe-01",
                offer_id="offer-01",
                name="Sopa de Lima",
                quantity=1,
                unit_price=Decimal("95.00"),
                status=ItemStatus.PENDING,
            )
        )
        order.recalculate_total()
        session.commit()

        assert "items" not in inspect(order).unloaded
        full_response = OrderResponse.from_model(order)
        assert len(full_response.items) == 1
        assert full_response.total_amount == Decimal("95.00")


@pytest.mark.asyncio
async def test_dining_session_consumer_initializes_empty_items_list() -> None:
    """Verify DiningSessionCreated consumer creates orders with items=[] eagerly."""

    class _ConsumerSession(FakeAsyncSession):
        async def flush(self) -> None:
            pass

    fake_session = _ConsumerSession()

    class _ProcessedEventsStub:
        def __init__(self) -> None:
            self.added: list[tuple[UUID, UUID, EventName]] = []

        def add(self, *, event_id: UUID, order_id: UUID, event_type: EventName) -> None:
            self.added.append((event_id, order_id, event_type))

    class _FakeUoW:
        def __init__(self) -> None:
            self.session = fake_session
            self.orders = OrderRepository(cast(AsyncSession, fake_session))
            self.processed_events = _ProcessedEventsStub()

        async def __aenter__(self) -> _FakeUoW:
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def commit(self) -> None:
            await self.session.commit()

    msg = DiningSessionCreatedMessage(
        eventId=uuid4(),
        sessionId=uuid4(),
        waiterId=uuid4(),
    )
    uow = _FakeUoW()
    handled = await handle_dining_session_created(msg, cast(UnitOfWork, uow))

    assert handled is True
    created_order = next(iter(fake_session.db.values()))
    assert created_order.items == []
    assert "items" not in inspect(created_order).unloaded
    assert len(uow.processed_events.added) == 1


def _tx_cancel(svc: OrderService, order: Order, _item: OrderItem) -> Awaitable[Order]:
    return svc.cancel_order(
        order.public_id, _auth(Role.WAITER, "orders:orders:cancel"), reason="Guest left"
    )


def _tx_confirm(svc: OrderService, order: Order, _item: OrderItem) -> Awaitable[Order]:
    return svc.confirm_order(order.public_id, uuid4())


def _tx_start_prep(svc: OrderService, order: Order, _item: OrderItem) -> Awaitable[Order]:
    return svc.start_order_preparation(
        order.public_id, _auth(Role.KITCHEN, "kitchen:orders:start_preparation")
    )


def _tx_item_ready(svc: OrderService, order: Order, item: OrderItem) -> Awaitable[Order]:
    return svc.mark_item_ready(
        order.public_id, item.public_id, _auth(Role.KITCHEN, "kitchen:items:ready")
    )


def _tx_order_ready(svc: OrderService, order: Order, _item: OrderItem) -> Awaitable[Order]:
    return svc.mark_order_ready(order.public_id, _auth(Role.KITCHEN, "kitchen:orders:ready"))


def _tx_reject_item(svc: OrderService, order: Order, item: OrderItem) -> Awaitable[Order]:
    return svc.kitchen_reject_item(
        order.public_id,
        item.public_id,
        _auth(Role.KITCHEN, "kitchen:items:reject"),
        reason="Sin tortilla",
    )


def _tx_cancel_item(svc: OrderService, order: Order, item: OrderItem) -> Awaitable[Order]:
    order.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="recipe-taco-02",
            offer_id="offer-taco-02",
            name="Taco Campechano",
            quantity=1,
            unit_price=Decimal("60.00"),
            status=ItemStatus.PENDING,
        )
    )
    return svc.cancel_item(
        order.public_id,
        item.public_id,
        _auth(Role.KITCHEN, "kitchen:items:cancel"),
        reason="Canceled item",
    )


def _tx_redo_item(svc: OrderService, order: Order, item: OrderItem) -> Awaitable[Order]:
    return svc.redo_order_item(
        order.public_id, item.public_id, _auth(Role.WAITER, "orders:items:redo"), note="Cold plate"
    )


def _tx_request_bill(svc: OrderService, order: Order, _item: OrderItem) -> Awaitable[Order]:
    return svc.request_bill(order.public_id, _auth(Role.WAITER, "orders:orders:request_bill"))


def _tx_transfer(svc: OrderService, order: Order, _item: OrderItem) -> Awaitable[Order]:
    return svc.transfer_order(order.public_id, new_table_number=14)


def _tx_void_admin(svc: OrderService, order: Order, _item: OrderItem) -> Awaitable[Order]:
    return svc.void_order(order.public_id, _auth(Role.ADMIN, "*"), reason="Admin adjustment")


def _tx_void_kitchen(svc: OrderService, order: Order, _item: OrderItem) -> Awaitable[Order]:
    return svc.void_kitchen_error(
        order.public_id, _auth(Role.CHEF, "kitchen:orders:void_error"), note="Burnt in kitchen"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_status", "item_status", "invoke"),
    [
        (OrderStatus.CREATED, ItemStatus.PENDING, _tx_cancel),
        (OrderStatus.CREATED, ItemStatus.PENDING, _tx_confirm),
        (OrderStatus.RESERVED, ItemStatus.PENDING, _tx_start_prep),
        (OrderStatus.IN_PREPARATION, ItemStatus.PENDING, _tx_item_ready),
        (OrderStatus.IN_PREPARATION, ItemStatus.PENDING, _tx_order_ready),
        (OrderStatus.RESERVED, ItemStatus.PENDING, _tx_reject_item),
        (OrderStatus.RESERVED, ItemStatus.PENDING, _tx_cancel_item),
        (OrderStatus.DELIVERED, ItemStatus.DELIVERED, _tx_redo_item),
        (OrderStatus.DELIVERED, ItemStatus.DELIVERED, _tx_request_bill),
        (OrderStatus.CREATED, ItemStatus.PENDING, _tx_transfer),
        (OrderStatus.IN_PREPARATION, ItemStatus.PENDING, _tx_void_admin),
        (OrderStatus.IN_PREPARATION, ItemStatus.PENDING, _tx_void_kitchen),
    ],
)
async def test_mutating_service_methods_lock_and_rollback_on_stale_data_error(
    initial_status: OrderStatus,
    item_status: ItemStatus,
    invoke: Callable[[OrderService, Order, OrderItem], Awaitable[Order]],
) -> None:
    """Every mutating service method uses FOR UPDATE and maps StaleDataError to 409."""
    order, item = _seed_order(status=initial_status, item_status=item_status)
    session = StaleCommitFakeSession({order.public_id: order})
    service = OrderService(
        session=cast(AsyncSession, session),
        notifier=SpyOrderNotifier(),
        catalog_repo=FakeCatalogValidator(),
    )

    with pytest.raises(VersionConflictError):
        await invoke(service, order, item)

    assert session.rolled_back is True
    assert any(session.for_update_used)


def _assert_stale_patch_returns_409(
    client: TestClient,
    auth_headers: Any,
    path_suffix: str,
    json_body: dict[str, object] | None,
) -> None:
    order, _ = _seed_order(status=OrderStatus.CREATED)
    stale_session = StaleCommitFakeSession({order.public_id: order})
    catalog = FakeCatalogValidator()
    notifier = SpyOrderNotifier()

    async def _get_test_session() -> AsyncGenerator[StaleCommitFakeSession, None]:
        yield stale_session

    app.dependency_overrides[get_session] = _get_test_session
    app.dependency_overrides[get_catalog_validator] = lambda: catalog
    app.dependency_overrides[get_order_notifier] = lambda: notifier
    app.dependency_overrides[get_order_service] = lambda: OrderService(
        session=cast(AsyncSession, stale_session),
        notifier=notifier,
        catalog_repo=catalog,
    )

    try:
        resp = client.patch(
            f"/orders/{order.public_id}{path_suffix}",
            headers=auth_headers("waiter"),
            json=json_body,
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == ErrorCode.VERSION_CONFLICT.value
        assert stale_session.rolled_back is True
        assert any(stale_session.for_update_used)
    finally:
        app.dependency_overrides.clear()


def test_cancel_and_update_items_api_return_409_on_stale_commit(
    client: TestClient,
    auth_headers: Any,
) -> None:
    """HTTP PATCH /cancel and /items translate StaleDataError to 409 VERSION_CONFLICT."""
    _assert_stale_patch_returns_409(
        client,
        auth_headers,
        "/items",
        {
            "items": [
                {
                    "dishId": "recipe-taco-01",
                    "name": "Taco Pastor",
                    "quantity": 3,
                    "unitPrice": 55.0,
                }
            ]
        },
    )
    _assert_stale_patch_returns_409(client, auth_headers, "/cancel", None)
