"""Unit tests for the Orders API router and schemas.

Covers order creation, item replacement with modifiers and notes, catalog validation,
price freezing, state and confirmation guards, error envelopes, and role matrix.

Satisfies: RF-12, RF-13, RF-14, RF-15, RF-16, RF-17, RF-18, RF-23, RF-24, RF-25,
           RNF-03, RNF-09, OP-05
Covers: UT-BE-11, UT-BE-15, UT-BE-25, UT-BE-26, UT-BE-27, UT-BE-28, UT-BE-29,
        UT-BE-60, CT-01, CT-02, CT-03, CT-04, CT-06, CT-16
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem, Outbox
from app.db.session import get_session
from app.domain.errors import DishUnavailableError, VersionConflictError
from app.domain.order_status import OrderStatus
from app.main import app
from app.services.catalog import CatalogValidatorProtocol, get_catalog_validator
from app.services.notifications import OrderNotifier
from app.services.orders import OrderService, get_order_service

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement(
        "RF-12",
        "RF-13",
        "RF-14",
        "RF-15",
        "RF-16",
        "RF-17",
        "RF-18",
        "RF-23",
        "RF-24",
        "RF-25",
        "RNF-03",
        "RNF-09",
        "OP-05",
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

    async def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        params = statement.compile().params
        status_val = next((v for v in params.values() if isinstance(v, OrderStatus)), None)
        orders = list(self.db.values())
        if status_val is not None:
            orders = [o for o in orders if o.status == status_val]
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
    assert outbox.payload["items"] == [{"offerId": "dish-1", "quantity": 2}]


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


# ==============================================================================
# PATCH /orders/{orderId}/transfer (RF-16, RNF-09, UT-BE-29, CT-04)
# ==============================================================================


def test_transfer_order_changes_table_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    spy_notifier: SpyOrderNotifier,
) -> None:
    """UT-BE-29 & CT-04: Transfer changes table, clears takeaway, increments version, notifies."""
    order = Order(
        public_id=uuid4(),
        table_number=5,
        is_takeaway=True,
        status=OrderStatus.CREATED,
        version=1,
    )
    test_db[order.public_id] = order

    payload = {"tableNumber": 12}
    response = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["orderId"] == str(order.public_id)
    assert data["tableNumber"] == 12
    assert data["isTakeaway"] is False
    assert data["version"] == 2

    # Verify DB state
    persisted = test_db[order.public_id]
    assert persisted.table_number == 12
    assert persisted.is_takeaway is False
    assert persisted.version == 2

    # Verify real-time notification broadcast (UT-BE-29)
    assert len(spy_notifier.notified_orders) == 1
    assert spy_notifier.notified_orders[0].public_id == order.public_id


def test_transfer_order_with_matching_version(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Transfer succeeds when matching version is passed in payload (RNF-09, CT-04)."""
    order = Order(
        public_id=uuid4(),
        table_number=2,
        is_takeaway=False,
        status=OrderStatus.IN_PREPARATION,
        version=3,
    )
    test_db[order.public_id] = order

    payload = {"tableNumber": 9, "version": 3}
    response = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["tableNumber"] == 9
    assert data["version"] == 4


def test_transfer_order_stale_version_conflict_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Transfer rejects with 409 Conflict if payload version does not match (RNF-09, CT-04)."""
    order = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus.CREATED,
        version=3,
    )
    test_db[order.public_id] = order

    payload = {"tableNumber": 9, "version": 1}
    response = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == ErrorCode.VERSION_CONFLICT


def test_transfer_order_with_if_match_header(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """If-Match header is parsed as optimistic lock version (RNF-09, CT-04)."""
    order = Order(
        public_id=uuid4(),
        table_number=4,
        status=OrderStatus.CREATED,
        version=1,
    )
    test_db[order.public_id] = order

    # Matching If-Match header succeeds
    headers = {**auth_headers("waiter"), "If-Match": '"1"'}
    response = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=headers,
        json={"tableNumber": 6},
    )
    assert response.status_code == 200
    assert response.json()["tableNumber"] == 6
    assert response.json()["version"] == 2

    # Stale If-Match header rejects with 409
    stale_headers = {**auth_headers("waiter"), "If-Match": '"1"'}
    response2 = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=stale_headers,
        json={"tableNumber": 7},
    )
    assert response2.status_code == 409
    assert response2.json()["error"]["code"] == ErrorCode.VERSION_CONFLICT


@pytest.mark.parametrize(
    "terminal_status",
    [
        OrderStatus.PAID,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.VOIDED,
    ],
)
def test_transfer_order_terminal_status_rejects_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    terminal_status: OrderStatus,
) -> None:
    """Orders in terminal status cannot be transferred to a new table (RF-16, CT-04)."""
    order = Order(
        public_id=uuid4(),
        table_number=4,
        status=terminal_status,
        version=1,
    )
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": 8},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION


def test_transfer_order_not_found_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Transfer non-existent order returns 404 ORDER_NOT_FOUND (CT-04)."""
    missing_id = uuid4()
    response = client.patch(
        f"/orders/{missing_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": 8},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND


def test_transfer_order_invalid_payload_422(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Invalid payload values or extra fields return HTTP 422 (RNF-03, CT-04)."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED, table_number=1)
    test_db[order.public_id] = order

    # tableNumber <= 0
    resp_zero = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": 0},
    )
    assert resp_zero.status_code == 422

    # Extra fields forbidden
    resp_extra = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": 2, "forbiddenField": "not_allowed"},
    )
    assert resp_extra.status_code == 422

    # version < 1
    resp_ver_zero = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": 2, "version": 0},
    )
    assert resp_ver_zero.status_code == 422


def test_transfer_order_role_matrix(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Waiters and admins can transfer orders; kitchen/chef are forbidden (CT-04)."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED, table_number=1)
    test_db[order.public_id] = order

    payload = {"tableNumber": 15}

    for role in ["waiter", "admin"]:
        resp = client.patch(
            f"/orders/{order.public_id}/transfer",
            headers=auth_headers(role),
            json=payload,
        )
        assert resp.status_code == 200

    for role in ["kitchen", "chef"]:
        resp = client.patch(
            f"/orders/{order.public_id}/transfer",
            headers=auth_headers(role),
            json=payload,
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_unauth = client.patch(f"/orders/{order.public_id}/transfer", json=payload)
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


async def test_transfer_order_stale_data_error_raises_version_conflict(
    fake_session: FakeAsyncSession,
) -> None:
    """StaleDataError from concurrent DB update is mapped to VersionConflictError (RNF-09)."""
    order = Order(public_id=uuid4(), table_number=1, status=OrderStatus.CREATED, version=1)
    fake_session.add(order)

    service = OrderService(cast(AsyncSession, fake_session))

    async def _failing_commit() -> None:
        raise StaleDataError("Row was updated or deleted by another transaction")

    fake_session.commit = _failing_commit  # type: ignore[method-assign]

    with pytest.raises(VersionConflictError):
        await service.transfer_order(order.public_id, new_table_number=2)


# ==============================================================================
# PATCH /orders/{orderId}/cancel (RF-24, RF-25, UT-BE-11, UT-BE-15, CT-06, CT-16)
# ==============================================================================


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
