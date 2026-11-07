"""Unit tests for Orders creation and retrieval endpoints.

Covers POST /orders, GET /orders/{orderId}, and GET /orders.
Satisfies: RF-12, RF-13, RF-15, RF-18, RNF-03, CT-01, CT-02, UT-BE-27, UT-BE-28, UT-BE-60
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.domain.order_status import OrderStatus

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-12", "RF-13", "RF-15", "RF-18", "RNF-03"),
]


# ==============================================================================
# POST /orders (RF-12, RF-13, RF-15, UT-BE-27, UT-BE-28, CT-02)
# ==============================================================================


def test_create_dining_table_order_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Create a dining table draft order (RF-12, UT-BE-27, CT-02)."""
    payload: dict[str, object] = {"tableNumber": 7, "isTakeaway": False}
    response = client.post("/orders", headers=auth_headers("waiter"), json=payload)

    assert response.status_code == 201
    data = response.json()
    order_id = UUID(data["orderId"])

    assert data["tableNumber"] == 7
    assert data["isTakeaway"] is False
    assert data["status"] == "CREATED"
    assert data["items"] == []
    assert data["totalAmount"] == "0.00"
    assert data["awaitingStockConfirmation"] is False
    assert data["confirmedAt"] is None
    assert data["inPreparationAt"] is None

    # Verify database persistence and actor assignment (UT-BE-28)
    assert order_id in test_db
    saved_order = test_db[order_id]
    assert saved_order.table_number == 7
    assert saved_order.is_takeaway is False
    assert saved_order.status == OrderStatus.CREATED
    assert saved_order.responsible_waiter_id is not None
    assert saved_order.created_by is not None


def test_create_takeaway_order_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Create a takeaway draft order without table number (RF-13, UT-BE-27, CT-02)."""
    payload: dict[str, object] = {"isTakeaway": True, "tableNumber": None}
    response = client.post("/orders", headers=auth_headers("waiter"), json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["isTakeaway"] is True
    assert data["tableNumber"] is None
    assert data["status"] == "CREATED"

    order_id = UUID(data["orderId"])
    saved_order = test_db[order_id]
    assert saved_order.is_takeaway is True
    assert saved_order.table_number is None


def test_create_dining_order_without_table_number_fails_validation(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Reject dining order when tableNumber is missing (RF-12 validation)."""
    payload: dict[str, object] = {"isTakeaway": False, "tableNumber": None}
    response = client.post("/orders", headers=auth_headers("waiter"), json=payload)

    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == ErrorCode.VALIDATION_ERROR
    assert data["error"]["message"] == "Request validation failed."


@pytest.mark.parametrize("invalid_table", [0, -1, -10])
def test_create_order_with_invalid_table_number_fails_validation(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    invalid_table: int,
) -> None:
    """Reject non-positive table numbers."""
    payload: dict[str, object] = {"isTakeaway": False, "tableNumber": invalid_table}
    response = client.post("/orders", headers=auth_headers("waiter"), json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize(
    "forbidden_payload",
    [
        {"tableNumber": 3, "status": "DELIVERED"},
        {"tableNumber": 3, "userId": "attacker"},
        {"tableNumber": 3, "confirmedAt": "2026-10-24T12:00:00Z"},
        {"tableNumber": 3, "totalAmount": "9999.00"},
    ],
)
def test_create_order_rejects_extra_fields_as_mass_assignment(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    forbidden_payload: dict[str, Any],
) -> None:
    """Reject mass assignment on order creation (UT-BE-60)."""
    response = client.post("/orders", headers=auth_headers("waiter"), json=forbidden_payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


def test_create_order_role_matrix(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Verify role matrix: WAITER and ADMIN succeed; KITCHEN and CHEF return 403 (RNF-03)."""
    payload: dict[str, object] = {"tableNumber": 4, "isTakeaway": False}

    # Allowed roles
    assert client.post("/orders", headers=auth_headers("waiter"), json=payload).status_code == 201
    assert client.post("/orders", headers=auth_headers("admin"), json=payload).status_code == 201

    # Forbidden roles
    resp_kitchen = client.post("/orders", headers=auth_headers("kitchen"), json=payload)
    assert resp_kitchen.status_code == 403
    assert resp_kitchen.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_chef = client.post("/orders", headers=auth_headers("chef"), json=payload)
    assert resp_chef.status_code == 403
    assert resp_chef.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    # Unauthenticated
    resp_unauth = client.post("/orders", json=payload)
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


# ==============================================================================
# GET /orders/{orderId} (RF-18, CT-01)
# ==============================================================================


def test_get_order_by_id_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Retrieve existing order details by public UUID."""
    order = Order(
        public_id=uuid4(),
        table_number=10,
        is_takeaway=False,
        status=OrderStatus.CREATED,
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-1",
        name="Tacos",
        quantity=2,
        unit_price=Decimal("40.00"),
    )
    order.items.append(item)
    order.recalculate_total()
    test_db[order.public_id] = order

    response = client.get(f"/orders/{order.public_id}", headers=auth_headers("waiter"))
    assert response.status_code == 200
    data = response.json()

    assert data["orderId"] == str(order.public_id)
    assert data["tableNumber"] == 10
    assert data["isTakeaway"] is False
    assert data["status"] == "CREATED"
    assert data["totalAmount"] == "80.00"
    assert len(data["items"]) == 1
    assert data["items"][0]["dishId"] == "dish-1"


def test_get_order_by_id_not_found(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Return 404 for unknown order ID."""
    missing_id = uuid4()
    response = client.get(f"/orders/{missing_id}", headers=auth_headers("waiter"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND


def test_get_order_by_id_role_matrix(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """All authenticated operational roles can read order details."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    for role in ["waiter", "admin", "kitchen", "chef"]:
        resp = client.get(f"/orders/{order.public_id}", headers=auth_headers(role))
        assert resp.status_code == 200

    resp_unauth = client.get(f"/orders/{order.public_id}")
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


# ==============================================================================
# GET /orders (RF-18, CT-01)
# ==============================================================================


def test_list_orders_empty(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Empty database returns empty order list with 200 OK (RF-18, CT-01)."""
    response = client.get("/orders", headers=auth_headers("waiter"))
    assert response.status_code == 200
    assert response.json() == []


def test_list_orders_all_sorted_by_created_at_desc(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Return all orders sorted newest first (RF-18, CT-01)."""
    order1 = Order(
        public_id=uuid4(),
        table_number=1,
        status=OrderStatus.CREATED,
        created_at=datetime(2026, 10, 29, 10, 0, tzinfo=UTC),
    )
    order2 = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus.IN_PREPARATION,
        created_at=datetime(2026, 10, 29, 10, 30, tzinfo=UTC),
    )
    test_db[order1.public_id] = order1
    test_db[order2.public_id] = order2

    response = client.get("/orders", headers=auth_headers("waiter"))
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["orderId"] == str(order2.public_id)
    assert data[1]["orderId"] == str(order1.public_id)


def test_list_orders_filter_by_status(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Filter orders by OrderStatus query parameter (RF-18, CT-01)."""
    order_created = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.CREATED,
        created_at=datetime(2026, 10, 29, 9, 0, tzinfo=UTC),
    )
    order_in_prep = Order(
        public_id=uuid4(),
        table_number=4,
        status=OrderStatus.IN_PREPARATION,
        created_at=datetime(2026, 10, 29, 9, 15, tzinfo=UTC),
    )
    test_db[order_created.public_id] = order_created
    test_db[order_in_prep.public_id] = order_in_prep

    resp_created = client.get("/orders?status=CREATED", headers=auth_headers("waiter"))
    assert resp_created.status_code == 200
    created_data = resp_created.json()
    assert len(created_data) == 1
    assert created_data[0]["orderId"] == str(order_created.public_id)

    resp_prep = client.get("/orders?status=IN_PREPARATION", headers=auth_headers("waiter"))
    assert resp_prep.status_code == 200
    prep_data = resp_prep.json()
    assert len(prep_data) == 1
    assert prep_data[0]["orderId"] == str(order_in_prep.public_id)

    resp_delivered = client.get("/orders?status=DELIVERED", headers=auth_headers("waiter"))
    assert resp_delivered.status_code == 200
    assert resp_delivered.json() == []


def test_list_orders_invalid_status_rejects_422(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Invalid status query parameter value returns HTTP 422 (RF-18, RNF-03, CT-01)."""
    response = client.get("/orders?status=NON_EXISTENT_STATUS", headers=auth_headers("waiter"))
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == ErrorCode.VALIDATION_ERROR


def test_list_orders_role_matrix(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Waiters and admins can list orders; kitchen/chef roles are forbidden (CT-01)."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    for role in ["waiter", "admin"]:
        resp = client.get("/orders", headers=auth_headers(role))
        assert resp.status_code == 200

    for role in ["kitchen", "chef"]:
        resp = client.get("/orders", headers=auth_headers(role))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_unauth = client.get("/orders")
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


def test_create_and_list_orders_with_dining_session_id(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Orders can be created with diningSessionId and filtered via GET /orders?diningSessionId=."""
    session_a = uuid4()
    session_b = uuid4()

    # Create order 1 for session A
    resp1 = client.post(
        "/orders",
        headers=auth_headers("waiter"),
        json={"tableNumber": 1, "diningSessionId": str(session_a)},
    )
    assert resp1.status_code == 201
    assert resp1.json()["diningSessionId"] == str(session_a)

    # Create order 2 for session A
    resp2 = client.post(
        "/orders",
        headers=auth_headers("waiter"),
        json={"tableNumber": 1, "diningSessionId": str(session_a)},
    )
    assert resp2.status_code == 201
    assert resp2.json()["diningSessionId"] == str(session_a)

    # Create order 3 for session B
    resp3 = client.post(
        "/orders",
        headers=auth_headers("waiter"),
        json={"tableNumber": 2, "diningSessionId": str(session_b)},
    )
    assert resp3.status_code == 201
    assert resp3.json()["diningSessionId"] == str(session_b)

    # Filter by session A
    list_resp = client.get(
        f"/orders?diningSessionId={session_a}",
        headers=auth_headers("waiter"),
    )
    assert list_resp.status_code == 200
    results = list_resp.json()
    assert len(results) == 2
    assert all(r["diningSessionId"] == str(session_a) for r in results)

