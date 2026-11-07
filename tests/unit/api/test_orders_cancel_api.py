"""Unit tests for Orders cancellation endpoint.

Covers PATCH /orders/{orderId}/cancel and its POST alias.
Satisfies: RF-24, RF-25, RNF-03, UT-BE-11, UT-BE-15, CT-06, CT-16
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.domain.order_status import OrderStatus
from tests.unit.api.conftest import FakeAsyncSession, SpyOrderNotifier

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-24", "RF-25", "RNF-03"),
]


def test_cancel_order_in_created_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
) -> None:
    """UT-BE-15 & CT-06: Cancel unconfirmed draft order stages OrderCancelledEvent in outbox."""
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.CREATED,
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-101",
        name="Guacamole",
        quantity=2,
        unit_price=Decimal("45.00"),
    )
    order.items.append(item)
    order.recalculate_total()
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/cancel",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["orderId"] == str(order.public_id)
    assert data["status"] == "CANCELLED"

    # Verify DB state
    assert test_db[order.public_id].status == OrderStatus.CANCELLED

    # Verify staged event in outbox (RF-25)
    assert len(fake_session.outbox_rows) == 1
    outbox_entry = fake_session.outbox_rows[0]
    assert outbox_entry.event_type == "ordenes.orden.cancelada"
    payload = outbox_entry.payload
    assert payload["orderId"] == str(order.public_id)
    cancelled_items = cast(list[dict[str, Any]], payload.get("itemsCancelados", []))
    assert len(cancelled_items) == 1
    assert cancelled_items[0]["offerId"] == "dish-101"
    assert cancelled_items[0]["quantity"] == 2

    # Verify WebSocket broadcast
    assert any(o.public_id == order.public_id for o in spy_notifier.notified_orders)


def test_cancel_confirmed_order_awaiting_stock_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """RF-24 & OP-05: Cancelling a confirmed order awaiting stock confirmation is allowed."""
    order = Order(
        public_id=uuid4(),
        table_number=7,
        status=OrderStatus.CREATED,
        confirmed_at=datetime.now(UTC),
        confirmed_by=uuid4(),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-202",
        name="Ceviche",
        quantity=1,
        unit_price=Decimal("120.00"),
    )
    order.items.append(item)
    order.recalculate_total()
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/cancel",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert test_db[order.public_id].status == OrderStatus.CANCELLED

    # Verify outbox has compensation event
    assert len(fake_session.outbox_rows) == 1
    assert fake_session.outbox_rows[0].event_type == "ordenes.orden.cancelada"


def test_cancel_order_when_in_preparation_raises_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """UT-BE-11 & CT-06: Attempting to cancel an order in IN_PREPARATION raises 409 Conflict."""
    order = Order(
        public_id=uuid4(),
        table_number=5,
        status=OrderStatus.IN_PREPARATION,
        in_preparation_at=datetime.now(UTC),
    )
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/cancel",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION
    assert test_db[order.public_id].status == OrderStatus.IN_PREPARATION
    assert len(fake_session.outbox_rows) == 0


@pytest.mark.parametrize(
    "forbidden_status",
    [
        OrderStatus.IN_PREPARATION,
        OrderStatus.DELIVERED,
        OrderStatus.WASTED,
        OrderStatus.PAID,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
        OrderStatus.VOIDED,
    ],
)
def test_cancel_order_in_non_created_status_raises_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    forbidden_status: OrderStatus,
) -> None:
    """CT-06: IN_PREPARATION and terminal orders cannot be cancelled; yields 409."""
    order = Order(
        public_id=uuid4(),
        status=forbidden_status,
        table_number=1,
    )
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/cancel",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION


def test_cancel_order_in_reserved_status_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """RF-24: RESERVED orders can be cancelled by waiter, staging OrderCancelledEvent."""
    order = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus.RESERVED,
    )
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/cancel",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert test_db[order.public_id].status == OrderStatus.CANCELLED
    assert len(fake_session.outbox_rows) == 1


def test_cancel_order_in_rejected_status_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """RF-24: REJECTED orders can be cancelled by waiter, staging OrderCancelledEvent."""
    order = Order(
        public_id=uuid4(),
        table_number=4,
        status=OrderStatus.REJECTED,
    )
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/cancel",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert test_db[order.public_id].status == OrderStatus.CANCELLED
    assert len(fake_session.outbox_rows) == 1


def test_cancel_order_then_edit_items_raises_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """CT-16: Cancel order successfully, then attempting to edit items fails with 409."""
    order = Order(
        public_id=uuid4(),
        table_number=8,
        status=OrderStatus.CREATED,
    )
    test_db[order.public_id] = order

    # 1. Cancel order
    cancel_resp = client.patch(
        f"/orders/{order.public_id}/cancel",
        headers=auth_headers("waiter"),
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"

    # 2. Attempt to modify items on cancelled order
    empty_modifiers: list[dict[str, Any]] = []
    edit_payload: dict[str, Any] = {
        "items": [
            {
                "dishId": "dish-1",
                "name": "Tacos",
                "quantity": 1,
                "unitPrice": "50.00",
                "modifiers": empty_modifiers,
            }
        ]
    }
    edit_resp = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=edit_payload,
    )
    assert edit_resp.status_code == 409
    assert edit_resp.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION


def test_cancel_order_not_found_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Cancel unknown order ID returns 404 ORDER_NOT_FOUND."""
    missing_id = uuid4()
    response = client.patch(
        f"/orders/{missing_id}/cancel",
        headers=auth_headers("waiter"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND


def test_cancel_order_with_reason_payload(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Cancel order with optional reason payload succeeds; extra fields reject with 422."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED, table_number=2)
    test_db[order.public_id] = order

    # Valid payload with reason
    resp = client.patch(
        f"/orders/{order.public_id}/cancel",
        headers=auth_headers("waiter"),
        json={"reason": "Customer changed mind"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"

    # Extra forbidden field on new order
    order2 = Order(public_id=uuid4(), status=OrderStatus.CREATED, table_number=3)
    test_db[order2.public_id] = order2
    resp_extra = client.patch(
        f"/orders/{order2.public_id}/cancel",
        headers=auth_headers("waiter"),
        json={"reason": "reason", "extra": "not_allowed"},
    )
    assert resp_extra.status_code == 422


def test_cancel_order_post_alias(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """POST /orders/{orderId}/cancel alias works identically to PATCH."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED, table_number=4)
    test_db[order.public_id] = order

    resp = client.post(
        f"/orders/{order.public_id}/cancel",
        headers=auth_headers("waiter"),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


def test_cancel_order_role_matrix(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Waiters and admins can cancel orders; kitchen and chef are forbidden."""
    payload: dict[str, str] = {}

    for role in ["waiter", "admin"]:
        order = Order(public_id=uuid4(), status=OrderStatus.CREATED, table_number=1)
        test_db[order.public_id] = order
        resp = client.patch(
            f"/orders/{order.public_id}/cancel",
            headers=auth_headers(role),
            json=payload,
        )
        assert resp.status_code == 200

    for role in ["kitchen", "chef"]:
        order = Order(public_id=uuid4(), status=OrderStatus.CREATED, table_number=1)
        test_db[order.public_id] = order
        resp = client.patch(
            f"/orders/{order.public_id}/cancel",
            headers=auth_headers(role),
            json=payload,
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    order_unauth = Order(public_id=uuid4(), status=OrderStatus.CREATED, table_number=1)
    test_db[order_unauth.public_id] = order_unauth
    resp_unauth = client.patch(f"/orders/{order_unauth.public_id}/cancel")
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED

