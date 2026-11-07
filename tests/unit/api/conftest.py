"""Shared fixtures and doubles for API unit tests.

Provides in-memory session, catalog validator double, and notification spies.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, Final, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem, Outbox
from app.db.session import get_session
from app.domain.errors import DishUnavailableError
from app.domain.order_status import OrderStatus
from app.main import app
from app.services.catalog import CatalogValidatorProtocol, get_catalog_validator
from app.services.notifications import OrderNotifier
from app.services.orders import OrderService, get_order_service

EMPTY_MODIFIERS: Final[list[str]] = []


class FakeAsyncSession:
    """In-memory AsyncSession double for testing FastAPI endpoints without I/O."""

    def __init__(self, db: dict[UUID, Order] | None = None) -> None:
        self.db: dict[UUID, Order] = db if db is not None else {}
        self.outbox_rows: list[Outbox] = []
        self.committed = False
        self.rolled_back = False

    def add(self, instance: object) -> None:
        if isinstance(instance, Order):
            self.db[instance.public_id] = instance
        elif isinstance(instance, Outbox):
            self.outbox_rows.append(instance)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        params = statement.compile().params
        status_val = next((v for v in params.values() if isinstance(v, OrderStatus)), None)
        session_val = next((v for v in params.values() if isinstance(v, UUID)), None)
        orders = list(self.db.values())
        if status_val is not None:
            orders = [o for o in orders if o.status == status_val]
        if session_val is not None:
            orders = [o for o in orders if o.dining_session_id == session_val]
        orders.sort(
            key=lambda o: (
                o.created_at or datetime.min.replace(tzinfo=UTC),
                o.public_id,
            ),
            reverse=True,
        )

        class _Result:
            def unique(self) -> _Result:
                return self

            def all(self) -> list[Order]:
                return orders

            def __iter__(self) -> Any:
                return iter(orders)

        return _Result()

    async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        params = statement.compile().params
        order_id = next((v for v in params.values() if isinstance(v, UUID)), None)
        if order_id is not None:
            return self.db.get(order_id)
        return None

    async def close(self) -> None:
        pass


class FakeCatalogValidator(CatalogValidatorProtocol):
    """Catalog validator double allowing custom dish availability rules."""

    def __init__(self, unavailable_ids: set[str] | None = None) -> None:
        self.unavailable_ids: set[str] = set(unavailable_ids or ())

    async def validate_dish_available(self, dish_id: str) -> None:
        if dish_id in self.unavailable_ids:
            raise DishUnavailableError(f"Dish '{dish_id}' is currently unavailable or inactive.")

    async def is_dish_available(self, dish_id: str) -> bool:
        return dish_id not in self.unavailable_ids

    async def set_availability(self, dish_id: str, available: bool) -> None:
        if available:
            self.unavailable_ids.discard(dish_id)
        else:
            self.unavailable_ids.add(dish_id)


class SpyOrderNotifier(OrderNotifier):
    """Spy recorder for WebSocket notification broadcasts."""

    def __init__(self) -> None:
        self.notified_orders: list[Order] = []
        self.notified_items: list[tuple[Order, OrderItem]] = []

    async def notify_order_changed(self, order: Order) -> None:
        self.notified_orders.append(order)

    async def notify_item_changed(self, order: Order, item: OrderItem) -> None:
        self.notified_items.append((order, item))


@pytest.fixture
def test_db() -> dict[UUID, Order]:
    return {}


@pytest.fixture
def fake_session(test_db: dict[UUID, Order]) -> FakeAsyncSession:
    return FakeAsyncSession(test_db)


@pytest.fixture
def fake_catalog() -> FakeCatalogValidator:
    return FakeCatalogValidator()


@pytest.fixture
def spy_notifier() -> SpyOrderNotifier:
    return SpyOrderNotifier()


@pytest.fixture(autouse=True)
def override_dependencies(
    fake_session: FakeAsyncSession,
    fake_catalog: FakeCatalogValidator,
    spy_notifier: SpyOrderNotifier,
) -> Any:
    async def _get_test_session() -> AsyncGenerator[FakeAsyncSession, None]:
        yield fake_session

    app.dependency_overrides[get_session] = _get_test_session
    app.dependency_overrides[get_catalog_validator] = lambda: fake_catalog
    app.dependency_overrides[get_order_service] = lambda: OrderService(
        session=cast(AsyncSession, fake_session),
        notifier=spy_notifier,
    )
    yield
    app.dependency_overrides.clear()
