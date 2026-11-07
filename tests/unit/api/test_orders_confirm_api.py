"""Unit tests for Order confirmation endpoint.

Covers POST /orders/{orderId}/confirm.
Satisfies: RF-10, RF-19, RF-33, RF-34, OP-05, RNF-03, UT-BE-11, CT-16
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.domain.order_status import OrderStatus
from tests.unit.api.conftest import FakeAsyncSession

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-10", "RF-19", "RF-33", "RF-34", "OP-05", "RNF-03"),
]


def test_confirm_order_stages_deterministic_event_and_records_actor(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """Stage ordenes.orden.creada event and record confirmed_at / confirmed_by."""
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.CREATED,
        items=[
            OrderItem(
                public_id=uuid4(),
                dish_id="dish-1",
                name="Dish",
                quantity=2,
                unit_price=Decimal("12.50"),
            )
        ],
    )
    test_db[order.public_id] = order

    response = client.post(
        f"/orders/{order.public_id}/confirm",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == OrderStatus.CREATED
    assert response.json()["awaitingStockConfirmation"] is True
    assert order.confirmed_at is not None
    assert order.confirmed_by is not None
    assert fake_session.committed
    assert len(fake_session.outbox_rows) == 1
    outbox = fake_session.outbox_rows[0]
    assert outbox.event_type == "ordenes.orden.creada"
    assert outbox.payload["orderId"] == str(order.public_id)
    assert outbox.payload["items"] == [{"offerId": "dish-1", "quantity": 2}]


def test_confirm_order_rejects_items_without_inventory_identifier(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """Reject confirmation when items lack dish_id (offerId)."""
    order = Order(
        public_id=uuid4(),
        status=OrderStatus.CREATED,
        items=[
            OrderItem(
                public_id=uuid4(),
                dish_id=None,
                name="Dish",
                quantity=1,
                unit_price=Decimal("12.50"),
            )
        ],
    )
    test_db[order.public_id] = order

    response = client.post(
        f"/orders/{order.public_id}/confirm",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR
    assert order.confirmed_at is None
    assert order.confirmed_by is None
    assert fake_session.outbox_rows == []
    assert not fake_session.committed


def test_confirm_order_is_idempotent_for_awaiting_stock_order(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """Subsequent confirm calls on awaiting stock order do not duplicate events."""
    confirmed_at = datetime.now(UTC)
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.CREATED,
        confirmed_at=confirmed_at,
        confirmed_by=uuid4(),
    )
    test_db[order.public_id] = order

    first = client.post(
        f"/orders/{order.public_id}/confirm",
        headers=auth_headers("waiter"),
    )
    second = client.post(
        f"/orders/{order.public_id}/confirm",
        headers=auth_headers("waiter"),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert order.confirmed_at == confirmed_at
    assert len(fake_session.outbox_rows) == 0


@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.IN_PREPARATION,
        OrderStatus.DELIVERED,
        OrderStatus.WASTED,
        OrderStatus.PAID,
    ],
)
def test_confirm_order_returns_current_state_without_republishing(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    status: OrderStatus,
) -> None:
    """Confirm on non-draft active orders returns current state without republishing."""
    order = Order(public_id=uuid4(), status=status)
    test_db[order.public_id] = order

    response = client.post(
        f"/orders/{order.public_id}/confirm",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == status
    assert len(fake_session.outbox_rows) == 0


@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.VOIDED,
    ],
)
def test_confirm_order_rejects_terminal_state_without_publishing(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    status: OrderStatus,
) -> None:
    """Confirm on terminal states returns 409 INVALID_STATE_TRANSITION."""
    order = Order(public_id=uuid4(), status=status)
    test_db[order.public_id] = order

    response = client.post(
        f"/orders/{order.public_id}/confirm",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION
    assert len(fake_session.outbox_rows) == 0


def test_confirm_order_not_found_returns_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Return 404 ORDER_NOT_FOUND when confirming a non-existent order."""
    missing_id = uuid4()
    response = client.post(
        f"/orders/{missing_id}/confirm",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND


def test_confirm_order_role_matrix(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Role matrix for POST /orders/{orderId}/confirm (RNF-03)."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    assert (
        client.post(
            f"/orders/{order.public_id}/confirm",
            headers=auth_headers("waiter"),
        ).status_code
        == 200
    )

    assert (
        client.post(
            f"/orders/{order.public_id}/confirm",
            headers=auth_headers("admin"),
        ).status_code
        == 200
    )

    resp_kitchen = client.post(
        f"/orders/{order.public_id}/confirm",
        headers=auth_headers("kitchen"),
    )
    assert resp_kitchen.status_code == 403
    assert resp_kitchen.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_chef = client.post(
        f"/orders/{order.public_id}/confirm",
        headers=auth_headers("chef"),
    )
    assert resp_chef.status_code == 403
    assert resp_chef.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_unauth = client.post(f"/orders/{order.public_id}/confirm")
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


def test_reconfirm_reopened_order_generates_distinct_event_id(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """Re-confirming a reopened order (version > 1) stages a new event with distinct eventId."""
    order = Order(
        public_id=uuid4(),
        table_number=5,
        status=OrderStatus.CREATED,
        version=1,
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-101",
        name="Guacamole",
        quantity=2,
        unit_price=Decimal("50.00"),
    )
    order.items.append(item)
    test_db[order.public_id] = order

    # First confirmation (attempt 1)
    resp1 = client.post(
        f"/orders/{order.public_id}/confirm",
        headers=auth_headers("waiter"),
    )
    assert resp1.status_code == 200
    assert len(fake_session.outbox_rows) == 1
    event_1 = fake_session.outbox_rows[0]
    payload_1 = (
        event_1.payload if isinstance(event_1.payload, dict) else json.loads(event_1.payload)
    )
    event_id_1 = payload_1["eventId"]

    # Order gets rejected, version increments and order reopens
    order.mark_rejected(details={"reason": "Parcial"})
    order.reopen_as_draft()
    order.version = 2
    order.items[0].quantity = 1  # Waiter lowers quantity from 2 to 1

    # Second confirmation (attempt 2)
    resp2 = client.post(
        f"/orders/{order.public_id}/confirm",
        headers=auth_headers("waiter"),
    )
    assert resp2.status_code == 200
    assert len(fake_session.outbox_rows) == 2
    event_2 = fake_session.outbox_rows[1]
    payload_2 = (
        event_2.payload if isinstance(event_2.payload, dict) else json.loads(event_2.payload)
    )
    event_id_2 = payload_2["eventId"]

    assert event_id_1 != event_id_2
    assert payload_2["items"] == [{"offerId": "dish-101", "quantity": 1}]
