"""Unit tests for Orders bill request endpoint.

Covers POST /orders/{orderId}/request-bill and its PATCH alias.
Satisfies: RF-04, RF-18, RF-20, RF-21, RNF-03
Covers: UT-BE-24, UT-BE-40, UT-BE-41
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.domain.order_status import ItemStatus, OrderStatus
from tests.unit.api.conftest import FakeAsyncSession, SpyOrderNotifier

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-20", "RF-21", "RF-04", "RF-18", "RNF-03"),
]


def test_request_bill_publishes_frozen_prices_and_session_id(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
) -> None:
    """UT-BE-24: Waiter requests bill on DELIVERED order with frozen items."""
    order_id = uuid4()
    session_id = uuid4()
    waiter_id = uuid4()
    item_id = uuid4()

    order = Order(
        public_id=order_id,
        table_number=7,
        status=OrderStatus.DELIVERED,
        dining_session_id=session_id,
        responsible_waiter_id=waiter_id,
        total_amount=Decimal("250.00"),
    )
    item = OrderItem(
        public_id=item_id,
        dish_id="dish-pasta-truffle",
        name="Pasta Tartufo",
        quantity=2,
        unit_price=Decimal("125.00"),
        modifiers=["extra queso"],
        notes="Servir caliente",
        status=ItemStatus.DELIVERED,
    )
    order.items.append(item)
    test_db[order_id] = order

    response = client.post(
        f"/orders/{order_id}/request-bill",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["orderId"] == str(order_id)
    assert data["status"] == "DELIVERED"

    # Order remains in DELIVERED status until payment completion
    assert test_db[order_id].status == OrderStatus.DELIVERED

    # Exactly 1 outbox event staged: ordenes.cuenta.solicitada
    assert len(fake_session.outbox_rows) == 1
    bill_row = fake_session.outbox_rows[0]
    assert bill_row.event_type == "ordenes.cuenta.solicitada"

    payload = bill_row.payload
    assert payload["orderId"] == str(order_id)
    assert payload["sessionId"] == str(session_id)
    assert payload["tableId"] == "MESA-7"
    assert payload["waiterId"] == str(waiter_id)
    assert payload["status"] == "DELIVERED"
    assert payload["totalAmount"] == 250.0

    items = cast(list[dict[str, Any]], payload.get("items", []))
    assert len(items) == 1
    assert items[0]["plateId"] == "dish-pasta-truffle"
    assert items[0]["name"] == "Pasta Tartufo"
    assert items[0]["quantity"] == 2
    assert items[0]["unitPrice"] == 125.0
    assert items[0]["subtotal"] == 250.0
    assert items[0]["modifiers"] == ["extra queso"]
    assert items[0]["notes"] == "Servir caliente"

    # WebSocket notified
    assert any(o.public_id == order_id for o in spy_notifier.notified_orders)


def test_request_bill_by_admin_succeeds(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """Admin role is in WAITER_ROLES and can request the bill."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=3,
        status=OrderStatus.DELIVERED,
        total_amount=Decimal("100.00"),
    )
    test_db[order_id] = order

    response = client.post(
        f"/orders/{order_id}/request-bill",
        headers=auth_headers("admin"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "DELIVERED"
    assert len(fake_session.outbox_rows) == 1
    assert fake_session.outbox_rows[0].event_type == "ordenes.cuenta.solicitada"


@pytest.mark.parametrize("forbidden_role", ["kitchen", "chef"])
def test_request_bill_by_forbidden_roles(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    forbidden_role: str,
) -> None:
    """Kitchen and chef roles are forbidden from requesting bill (403 FORBIDDEN_ROLE)."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=2,
        status=OrderStatus.DELIVERED,
    )
    test_db[order_id] = order

    response = client.post(
        f"/orders/{order_id}/request-bill",
        headers=auth_headers(forbidden_role),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value


@pytest.mark.parametrize(
    "invalid_status",
    [
        OrderStatus.CREATED,
        OrderStatus.RESERVED,
        OrderStatus.IN_PREPARATION,
        OrderStatus.PAID,
        OrderStatus.CANCELLED,
        OrderStatus.WASTED,
        OrderStatus.VOIDED,
        OrderStatus.EXPIRED,
    ],
)
def test_request_bill_on_non_delivered_status_raises_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    invalid_status: OrderStatus,
) -> None:
    """Requesting bill on any status other than DELIVERED returns 409 INVALID_STATE_TRANSITION."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=4,
        status=invalid_status,
    )
    test_db[order_id] = order

    response = client.post(
        f"/orders/{order_id}/request-bill",
        headers=auth_headers("waiter"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION.value


def test_request_bill_not_found_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Requesting bill on non-existent order returns 404 ORDER_NOT_FOUND."""
    missing_id = uuid4()
    response = client.post(
        f"/orders/{missing_id}/request-bill",
        headers=auth_headers("waiter"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND.value


def test_request_bill_patch_alias(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """PATCH alias for request-bill works identically to POST."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=6,
        status=OrderStatus.DELIVERED,
        total_amount=Decimal("80.00"),
    )
    test_db[order_id] = order

    response = client.patch(
        f"/orders/{order_id}/request-bill",
        headers=auth_headers("waiter"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "DELIVERED"
    assert test_db[order_id].status == OrderStatus.DELIVERED
    assert len(fake_session.outbox_rows) == 1
    assert fake_session.outbox_rows[0].event_type == "ordenes.cuenta.solicitada"
