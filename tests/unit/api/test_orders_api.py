"""Unit tests for the Orders API router and schemas.

Covers order creation, item replacement with modifiers and notes, catalog validation,
price freezing, state and confirmation guards, error envelopes, and role matrix.

Satisfies: RF-12, RF-13, RF-14, RF-15, RF-17, RF-23, RNF-03, OP-05
Covers: UT-BE-25, UT-BE-26, UT-BE-27, UT-BE-28, UT-BE-60, CT-02, CT-03
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem, Outbox
from app.db.session import get_session
from app.domain.errors import DishUnavailableError
from app.domain.order_status import OrderStatus
from app.main import app
from app.services.catalog import CatalogValidatorProtocol, get_catalog_validator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement(
        "RF-12", "RF-13", "RF-14", "RF-15", "RF-17", "RF-23", "RNF-03", "OP-05"
    ),
]

EMPTY_MODIFIERS: list[str] = []


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

    async def scalar(self, statement: Any) -> Any:
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
        self.unavailable_ids = unavailable_ids or set()

    async def validate_dish_available(self, dish_id: str) -> None:
        if dish_id in self.unavailable_ids:
            raise DishUnavailableError(f"Dish '{dish_id}' is currently unavailable or inactive.")

    async def is_dish_available(self, dish_id: str) -> bool:
        return dish_id not in self.unavailable_ids


@pytest.fixture
def test_db() -> dict[UUID, Order]:
    return {}


@pytest.fixture
def fake_session(test_db: dict[UUID, Order]) -> FakeAsyncSession:
    return FakeAsyncSession(test_db)


@pytest.fixture
def fake_catalog() -> FakeCatalogValidator:
    return FakeCatalogValidator()


@pytest.fixture(autouse=True)
def override_dependencies(
    fake_session: FakeAsyncSession,
    fake_catalog: FakeCatalogValidator,
) -> Any:
    async def _get_test_session() -> AsyncGenerator[FakeAsyncSession, None]:
        yield fake_session

    app.dependency_overrides[get_session] = _get_test_session
    app.dependency_overrides[get_catalog_validator] = lambda: fake_catalog
    yield
    app.dependency_overrides.clear()


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
# PATCH /orders/{orderId}/items (RF-14, RF-17, RF-23, OP-05, UT-BE-25, UT-BE-26, CT-03)
# ==============================================================================


def test_update_order_items_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Replace items in draft order and recalculate total (RF-14, UT-BE-26, CT-03)."""
    order = Order(
        public_id=uuid4(),
        table_number=5,
        is_takeaway=False,
        status=OrderStatus.CREATED,
    )
    test_db[order.public_id] = order

    items_payload: dict[str, object] = {
        "items": [
            {
                "dishId": "dish-01",
                "name": "Tacos al Pastor",
                "quantity": 3,
                "unitPrice": "45.00",
                "modifiers": ["sin cebolla", "con salsa verde"],
                "notes": "Bien dorada la carne",
            },
            {
                "dishId": "dish-02",
                "name": "Horchata 500ml",
                "quantity": 2,
                "unitPrice": "25.50",
                "modifiers": EMPTY_MODIFIERS,
                "notes": None,
            },
        ]
    }

    url = f"/orders/{order.public_id}/items"
    response = client.patch(url, headers=auth_headers("waiter"), json=items_payload)

    assert response.status_code == 200
    data = response.json()
    assert data["orderId"] == str(order.public_id)
    assert len(data["items"]) == 2
    # Total = 3 * 45.00 + 2 * 25.50 = 135.00 + 51.00 = 186.00
    assert data["totalAmount"] == "186.00"

    item1 = data["items"][0]
    assert item1["dishId"] == "dish-01"
    assert item1["name"] == "Tacos al Pastor"
    assert item1["quantity"] == 3
    assert item1["unitPrice"] == "45.00"
    assert item1["modifiers"] == ["sin cebolla", "con salsa verde"]
    assert item1["notes"] == "Bien dorada la carne"
    assert item1["status"] == "PENDING"
    assert "itemId" in item1

    item2 = data["items"][1]
    assert item2["dishId"] == "dish-02"
    assert item2["quantity"] == 2
    assert item2["unitPrice"] == "25.50"
    assert item2["modifiers"] == []
    assert item2["notes"] is None

    # Verify DB state
    assert len(order.items) == 2
    assert order.total_amount == Decimal("186.00")


def test_update_order_items_replaces_previous_items(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Full replacement of items when new items are submitted (RF-14)."""
    order = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus.CREATED,
    )
    old_item = OrderItem(
        public_id=uuid4(),
        dish_id="old-dish",
        name="Old Item",
        quantity=1,
        unit_price=Decimal("100.00"),
    )
    order.items.append(old_item)
    order.recalculate_total()
    test_db[order.public_id] = order

    replacement_payload: dict[str, object] = {
        "items": [
            {
                "dishId": "new-dish",
                "name": "New Item",
                "quantity": 1,
                "unitPrice": "50.00",
                "modifiers": EMPTY_MODIFIERS,
                "notes": None,
            }
        ]
    }
    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=replacement_payload,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["dishId"] == "new-dish"
    assert data["totalAmount"] == "50.00"
    assert len(order.items) == 1


def test_update_order_items_dish_unavailable_raises_422(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_catalog: FakeCatalogValidator,
) -> None:
    """Reject modification when dish is inactive/unavailable in catalog (RF-17, UT-BE-25)."""
    order = Order(
        public_id=uuid4(),
        table_number=1,
        status=OrderStatus.CREATED,
    )
    test_db[order.public_id] = order
    fake_catalog.unavailable_ids.add("dish-sold-out")

    payload: dict[str, object] = {
        "items": [
            {
                "dishId": "dish-sold-out",
                "name": "Sold Out Dish",
                "quantity": 1,
                "unitPrice": "60.00",
                "modifiers": EMPTY_MODIFIERS,
                "notes": None,
            }
        ]
    }
    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == ErrorCode.DISH_UNAVAILABLE
    assert "dish-sold-out" in data["error"]["message"]
    # Order items were not changed
    assert len(order.items) == 0


def test_update_order_items_rejected_when_order_confirmed(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Reject item modification if order is confirmed / awaiting stock (OP-05 / ADR-0004)."""
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.CREATED,
        confirmed_at=datetime.now(UTC),
    )
    test_db[order.public_id] = order

    payload: dict[str, object] = {
        "items": [
            {
                "dishId": "dish-1",
                "name": "Any Dish",
                "quantity": 1,
                "unitPrice": "20.00",
            }
        ]
    }
    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION
    assert "confirmed" in data["error"]["message"].lower()


def test_confirm_order_stages_deterministic_event_and_records_actor(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
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
    assert outbox.payload["items"] == [{"plateId": "dish-1", "quantity": 2}]


def test_confirm_order_rejects_items_without_inventory_identifier(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
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

    assert first.status_code == second.status_code == 200
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


@pytest.mark.parametrize(
    "invalid_status",
    [
        OrderStatus.IN_PREPARATION,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
        OrderStatus.PAID,
        OrderStatus.REJECTED,
    ],
)
def test_update_order_items_rejected_when_status_not_created(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    invalid_status: OrderStatus,
) -> None:
    """Reject item modification when order is past CREATED status (RF-23)."""
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=invalid_status,
    )
    test_db[order.public_id] = order

    payload: dict[str, object] = {
        "items": [
            {
                "dishId": "dish-1",
                "name": "Any Dish",
                "quantity": 1,
                "unitPrice": "20.00",
            }
        ]
    }
    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION


def test_update_order_items_non_existent_order_returns_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Return 404 ORDER_NOT_FOUND when order does not exist."""
    missing_id = uuid4()
    payload: dict[str, object] = {
        "items": [
            {
                "dishId": "dish-1",
                "name": "Any Dish",
                "quantity": 1,
                "unitPrice": "20.00",
            }
        ]
    }
    response = client.patch(
        f"/orders/{missing_id}/items",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == ErrorCode.ORDER_NOT_FOUND


def test_update_order_items_rejects_empty_items_list(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Reject empty items list with 422 VALIDATION_ERROR."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={"items": []},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize(
    "invalid_item",
    [
        {"dishId": "d1", "name": "A", "quantity": 0, "unitPrice": "10.00"},
        {"dishId": "d1", "name": "A", "quantity": -2, "unitPrice": "10.00"},
        {"dishId": "d1", "name": "A", "quantity": 1, "unitPrice": "-5.00"},
        {"dishId": "", "name": "A", "quantity": 1, "unitPrice": "10.00"},
        {"dishId": "d1", "name": "", "quantity": 1, "unitPrice": "10.00"},
    ],
)
def test_update_order_items_invalid_field_values_fail_validation(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    invalid_item: dict[str, Any],
) -> None:
    """Validate positive quantities and prices on item lines."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={"items": [invalid_item]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


def test_update_order_items_rejects_extra_fields_as_mass_assignment(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Reject forbidden fields inside items payload (UT-BE-60)."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    payload: dict[str, object] = {
        "items": [
            {
                "dishId": "d1",
                "name": "Tacos",
                "quantity": 1,
                "unitPrice": "10.00",
                "status": "READY",
            }
        ]
    }
    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


def test_update_order_items_role_matrix(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Role matrix for PATCH /orders/{orderId}/items (RNF-03)."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    payload: dict[str, object] = {
        "items": [{"dishId": "d1", "name": "Tacos", "quantity": 1, "unitPrice": "10.00"}]
    }

    assert (
        client.patch(
            f"/orders/{order.public_id}/items",
            headers=auth_headers("waiter"),
            json=payload,
        ).status_code
        == 200
    )

    assert (
        client.patch(
            f"/orders/{order.public_id}/items",
            headers=auth_headers("admin"),
            json=payload,
        ).status_code
        == 200
    )

    resp_kitchen = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("kitchen"),
        json=payload,
    )
    assert resp_kitchen.status_code == 403
    assert resp_kitchen.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_chef = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("chef"),
        json=payload,
    )
    assert resp_chef.status_code == 403
    assert resp_chef.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_unauth = client.patch(f"/orders/{order.public_id}/items", json=payload)
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


# ==============================================================================
# GET /orders/{orderId}
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
