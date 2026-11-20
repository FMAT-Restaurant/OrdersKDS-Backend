"""Shared fixtures and doubles for API unit tests.

Provides in-memory session, catalog validator double, and notification spies.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Final, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.errors import DishUnavailableError
from app.main import app
from app.services.catalog import CatalogValidatorProtocol, get_catalog_validator
from app.services.notifications import OrderNotifier, get_order_notifier
from app.services.orders import OrderService, get_order_service
from app.services.shifts import ShiftService, get_shift_service
from tests.unit.conftest import FakeAsyncSession

EMPTY_MODIFIERS: Final[list[str]] = []


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
    app.dependency_overrides[get_order_notifier] = lambda: spy_notifier
    app.dependency_overrides[get_order_service] = lambda: OrderService(
        session=cast(AsyncSession, fake_session),
        notifier=spy_notifier,
    )
    app.dependency_overrides[get_shift_service] = lambda: ShiftService(
        session=cast(AsyncSession, fake_session),
    )
    yield
    app.dependency_overrides.clear()
