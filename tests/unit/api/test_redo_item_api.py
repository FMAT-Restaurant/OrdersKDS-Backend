"""Tests for returning delivered items to kitchen (RF-22, OP-12)."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.order_status import ItemStatus, OrderStatus
from app.main import app
from app.services.notifications import OrderNotifier
from app.services.orders import OrderService, get_order_service
from tests.unit.conftest import FakeAsyncSession

pytestmark = [pytest.mark.unit, pytest.mark.requirement("RF-22", "OP-12")]


class SpyOrderNotifier(OrderNotifier):
    """Capture item broadcasts without opening a WebSocket."""

    def __init__(self) -> None:
        self.notified_items: list[tuple[Order, OrderItem]] = []

    async def notify_order_changed(self, order: Order) -> None:
        pass

    async def notify_item_changed(self, order: Order, item: OrderItem) -> None:
        self.notified_items.append((order, item))


@pytest.fixture
def test_db() -> dict[UUID, Order]:
    return {}


@pytest.fixture
def fake_session(test_db: dict[UUID, Order]) -> FakeAsyncSession:
    return FakeAsyncSession(test_db)


@pytest.fixture
def spy_notifier() -> SpyOrderNotifier:
    return SpyOrderNotifier()


@pytest.fixture(autouse=True)
def override_dependencies(
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
) -> Any:
    async def _get_test_session() -> AsyncGenerator[FakeAsyncSession, None]:
        yield fake_session

    app.dependency_overrides[get_session] = _get_test_session
    app.dependency_overrides[get_order_service] = lambda: OrderService(
        session=cast(AsyncSession, fake_session),
        notifier=spy_notifier,
    )
    yield
    app.dependency_overrides.clear()


def _delivered_order() -> Order:
    order = Order(public_id=uuid4(), status=OrderStatus.DELIVERED, table_number=5)
    order.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="dish-redo",
            name="Platillo rehecho",
            quantity=2,
            unit_price=Decimal("50.00"),
            status=ItemStatus.DELIVERED,
        )
    )
    order.recalculate_total()
    return order


def test_redo_requires_non_blank_note(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    order = _delivered_order()
    test_db[order.public_id] = order
    url = f"/orders/{order.public_id}/items/{order.items[0].public_id}/redo"

    for payload in (None, {}, {"note": ""}, {"note": "   "}):
        response = client.patch(url, headers=auth_headers("waiter"), json=payload)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "NOTE_REQUIRED"
        assert order.status == OrderStatus.DELIVERED
        assert order.items[0].status == ItemStatus.DELIVERED


@pytest.mark.parametrize("note", ["x", "n" * 500])
def test_redo_item_preserves_order_and_pushes_pending_item(
    note: str,
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
) -> None:
    order = _delivered_order()
    item = order.items[0]
    test_db[order.public_id] = order
    url = f"/orders/{order.public_id}/items/{item.public_id}/redo"

    response = client.patch(url, headers=auth_headers("waiter"), json={"note": note})

    assert response.status_code == 200
    assert response.json()["status"] == "DELIVERED"
    assert response.json()["items"][0]["status"] == "PENDING"
    assert order.status == OrderStatus.DELIVERED
    assert item.status == ItemStatus.PENDING
    assert item.notes == note
    assert fake_session.committed is True
    assert len(fake_session.outbox_rows) == 0
    assert spy_notifier.notified_items == [(order, item)]


def test_redo_item_ready_emits_quantity_and_redo_flag(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    order = _delivered_order()
    item = order.items[0]
    item.status = ItemStatus.PENDING
    item.notes = "Cliente devolvió el platillo"
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/items/{item.public_id}/ready",
        headers=auth_headers("kitchen"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "DELIVERED"
    assert response.json()["items"][0]["status"] == "DELIVERED"
    assert order.status == OrderStatus.DELIVERED
    assert item.status == ItemStatus.DELIVERED
    assert len(fake_session.outbox_rows) == 1
    payload = fake_session.outbox_rows[0].payload
    assert payload["quantity"] == 2
    assert payload["isRedo"] is True


@pytest.mark.parametrize("original_note", ["BIEN COCIDO", None])
def test_redo_restores_original_notes_after_kitchen_completes_redo(
    original_note: str | None,
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Redo displays waiter complaint while PENDING and restores original note upon completion."""
    order = _delivered_order()
    item = order.items[0]
    item.notes = original_note
    test_db[order.public_id] = order

    redo_resp = client.patch(
        f"/orders/{order.public_id}/items/{item.public_id}/redo",
        headers=auth_headers("waiter"),
        json={"note": "HUBO UN ERROR, REHAGANLO"},
    )
    assert redo_resp.status_code == 200
    assert redo_resp.json()["items"][0]["notes"] == "HUBO UN ERROR, REHAGANLO"
    assert redo_resp.json()["rejectionDetails"] is None

    ready_resp = client.patch(
        f"/orders/{order.public_id}/items/{item.public_id}/ready",
        headers=auth_headers("kitchen"),
    )
    assert ready_resp.status_code == 200
    assert ready_resp.json()["items"][0]["status"] == "DELIVERED"
    assert ready_resp.json()["items"][0]["notes"] == original_note
    assert item.notes == original_note
    assert order.rejection_details is None

