"""Unit tests for KDS ready endpoints and kitchen actions.

Covers marking items ready, whole order ready transitions, outbox events,
role authorization matrix, and idempotent no-op handling.

Satisfies: RF-09, RF-10, RF-11, RF-18, RNF-01, RNF-03, OP-08
Covers: UT-BE-30, UT-BE-31, CT-10, CT-11
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import AuthContext
from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.errors import ForbiddenRoleError, InvalidStateTransitionError
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role
from app.main import app
from app.messaging.events import EventName
from app.services.notifications import OrderNotifier
from app.services.orders import OrderService, get_order_service
from tests.unit.conftest import FakeAsyncSession

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-09", "RF-10", "RF-11", "RF-18", "RNF-01", "RNF-03", "OP-08"),
]


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


class FakeCatalogValidator:
    def __init__(self) -> None:
        self.availabilities: dict[str, bool] = {}

    async def set_availability(self, dish_id: str, available: bool) -> None:
        self.availabilities[dish_id] = available

    async def is_dish_available(self, dish_id: str) -> bool:
        return self.availabilities.get(dish_id, True)

    async def validate_dishes(self, dish_ids: list[str]) -> list[str]:
        return []


@pytest.fixture
def fake_catalog() -> FakeCatalogValidator:
    return FakeCatalogValidator()


@pytest.fixture
def spy_notifier() -> SpyOrderNotifier:
    return SpyOrderNotifier()


@pytest.fixture(autouse=True)
def override_dependencies(
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
    fake_catalog: FakeCatalogValidator,
) -> Any:
    async def _get_test_session() -> AsyncGenerator[FakeAsyncSession, None]:
        yield fake_session

    app.dependency_overrides[get_session] = _get_test_session
    app.dependency_overrides[get_order_service] = lambda: OrderService(
        session=cast(AsyncSession, fake_session),
        notifier=spy_notifier,
        catalog_repo=fake_catalog,
    )
    yield
    app.dependency_overrides.clear()


def _create_in_prep_order(num_items: int = 2) -> Order:
    order = Order(
        public_id=uuid4(),
        table_number=5,
        status=OrderStatus.IN_PREPARATION,
    )
    for i in range(num_items):
        item = OrderItem(
            public_id=uuid4(),
            dish_id=f"dish-{i + 1}",
            name=f"Platillo {i + 1}",
            quantity=1,
            unit_price=Decimal("50.00"),
            status=ItemStatus.PENDING,
        )
        order.items.append(item)
    order.recalculate_total()
    return order


# ==============================================================================
# UT-BE-30 / CT-10: PATCH /orders/{orderId}/items/{itemId}/ready
# ==============================================================================


def test_mark_item_ready_publishes_dish_prepared(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
) -> None:
    """UT-BE-30 & CT-10: Item PENDING -> READY stages DishPreparedEvent in outbox."""
    order = _create_in_prep_order(num_items=2)
    test_db[order.public_id] = order
    item1 = order.items[0]

    url = f"/orders/{order.public_id}/items/{item1.public_id}/ready"
    response = client.patch(url, headers=auth_headers("kitchen"))

    assert response.status_code == 200
    data = response.json()
    assert data["orderId"] == str(order.public_id)
    assert data["status"] == "IN_PREPARATION"

    # item1 transitioned to READY, item2 is still PENDING
    assert order.items[0].status == ItemStatus.READY
    assert order.items[1].status == ItemStatus.PENDING
    assert order.status == OrderStatus.IN_PREPARATION

    # Event staged in outbox
    assert len(fake_session.outbox_rows) == 1
    event_row = fake_session.outbox_rows[0]
    assert event_row.event_type == "ordenes.platillo.preparado"
    assert event_row.payload["orderId"] == str(order.public_id)
    assert event_row.payload["itemId"] == str(item1.public_id)
    assert fake_session.committed is True

    # Order has not reached DELIVERED yet, so order notification not sent
    assert len(spy_notifier.notified_orders) == 0
    assert len(spy_notifier.notified_items) == 1
    assert spy_notifier.notified_items[0][0].public_id == order.public_id
    assert spy_notifier.notified_items[0][1].public_id == item1.public_id


def test_mark_item_ready_idempotent_no_duplicate_publish(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """Marking an item that is already READY does nothing and does not publish again."""
    order = _create_in_prep_order(num_items=2)
    order.items[0].status = ItemStatus.READY
    test_db[order.public_id] = order
    item1 = order.items[0]

    url = f"/orders/{order.public_id}/items/{item1.public_id}/ready"
    response = client.patch(url, headers=auth_headers("kitchen"))

    assert response.status_code == 200
    # No new event staged
    assert len(fake_session.outbox_rows) == 0
    assert order.items[0].status == ItemStatus.READY


def test_mark_item_ready_last_item_transitions_order_and_items_to_delivered(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
) -> None:
    """When the last item is marked READY, order and all items move to DELIVERED in same tx."""
    order = _create_in_prep_order(num_items=2)
    order.items[0].status = ItemStatus.READY
    test_db[order.public_id] = order
    item2 = order.items[1]

    url = f"/orders/{order.public_id}/items/{item2.public_id}/ready"
    response = client.patch(url, headers=auth_headers("kitchen"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "DELIVERED"
    for it in data["items"]:
        assert it["status"] == "DELIVERED"

    # Database verification
    assert order.status == OrderStatus.DELIVERED
    assert order.items[0].status == ItemStatus.DELIVERED
    assert order.items[1].status == ItemStatus.DELIVERED

    # Outbox event staged for the item that just became ready
    assert len(fake_session.outbox_rows) == 1
    assert fake_session.outbox_rows[0].payload["itemId"] == str(item2.public_id)

    # Notification broadcast triggered after commit
    assert len(spy_notifier.notified_items) == 1
    assert spy_notifier.notified_items[0][1].public_id == item2.public_id
    assert len(spy_notifier.notified_orders) == 1
    assert spy_notifier.notified_orders[0].public_id == order.public_id


# ==============================================================================
# UT-BE-31 / CT-11: PATCH /orders/{orderId}/ready
# ==============================================================================


def test_mark_order_ready_marks_every_item_ready(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
) -> None:
    """UT-BE-31 & CT-11: Whole order ready transitions all items and order to DELIVERED."""
    order = _create_in_prep_order(num_items=2)
    test_db[order.public_id] = order

    url = f"/orders/{order.public_id}/ready"
    response = client.patch(url, headers=auth_headers("kitchen"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "DELIVERED"
    for it in data["items"]:
        assert it["status"] == "DELIVERED"

    # Outbox contains an event for each changed item
    assert len(fake_session.outbox_rows) == 2
    emitted_item_ids = {r.payload["itemId"] for r in fake_session.outbox_rows}
    assert emitted_item_ids == {str(order.items[0].public_id), str(order.items[1].public_id)}

    assert order.status == OrderStatus.DELIVERED
    assert order.items[0].status == ItemStatus.DELIVERED
    assert order.items[1].status == ItemStatus.DELIVERED

    assert len(spy_notifier.notified_orders) == 1
    assert spy_notifier.notified_orders[0].status == OrderStatus.DELIVERED


def test_mark_order_ready_only_publishes_for_items_that_were_pending(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """Only items transitioning PENDING -> READY produce outbox events."""
    order = _create_in_prep_order(num_items=2)
    order.items[0].status = ItemStatus.READY
    test_db[order.public_id] = order

    url = f"/orders/{order.public_id}/ready"
    response = client.patch(url, headers=auth_headers("kitchen"))

    assert response.status_code == 200
    # Exactly 1 event for item 2, none for item 1
    assert len(fake_session.outbox_rows) == 1
    assert fake_session.outbox_rows[0].payload["itemId"] == str(order.items[1].public_id)


def test_mark_order_ready_idempotent_when_already_delivered(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """Calling mark order ready on an already DELIVERED order is a 200 no-op."""
    order = _create_in_prep_order(num_items=2)
    order.status = OrderStatus.DELIVERED
    order.items[0].status = ItemStatus.DELIVERED
    order.items[1].status = ItemStatus.DELIVERED
    test_db[order.public_id] = order

    url = f"/orders/{order.public_id}/ready"
    response = client.patch(url, headers=auth_headers("kitchen"))

    assert response.status_code == 200
    assert len(fake_session.outbox_rows) == 0


# ==============================================================================
# Role Matrix: Waiter -> 403 FORBIDDEN_ROLE, Kitchen/Chef/Admin -> 200
# ==============================================================================


@pytest.mark.parametrize("role", ["kitchen", "chef", "admin"])
def test_mark_ready_endpoints_allowed_roles(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    role: str,
) -> None:
    """Kitchen, chef, and admin roles can call ready endpoints (RNF-03)."""
    order = _create_in_prep_order(num_items=1)
    test_db[order.public_id] = order

    resp_item = client.patch(
        f"/orders/{order.public_id}/items/{order.items[0].public_id}/ready",
        headers=auth_headers(role),
    )
    assert resp_item.status_code == 200

    resp_order = client.patch(f"/orders/{order.public_id}/ready", headers=auth_headers(role))
    assert resp_order.status_code == 200


def test_mark_ready_endpoints_reject_waiter_with_403(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Waiter calling kitchen endpoints receives 403 FORBIDDEN_ROLE (OP-08)."""
    order = _create_in_prep_order(num_items=1)
    test_db[order.public_id] = order

    # Mark item ready
    resp_item = client.patch(
        f"/orders/{order.public_id}/items/{order.items[0].public_id}/ready",
        headers=auth_headers("waiter"),
    )
    assert resp_item.status_code == 403
    assert resp_item.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    # Mark order ready
    resp_order = client.patch(f"/orders/{order.public_id}/ready", headers=auth_headers("waiter"))
    assert resp_order.status_code == 403
    assert resp_order.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE


def test_mark_ready_endpoints_unauthenticated_return_401(
    client: TestClient,
    test_db: dict[UUID, Order],
) -> None:
    """Unauthenticated requests receive 401 UNAUTHENTICATED."""
    order = _create_in_prep_order(num_items=1)
    test_db[order.public_id] = order

    resp_item = client.patch(f"/orders/{order.public_id}/items/{order.items[0].public_id}/ready")
    assert resp_item.status_code == 401
    assert resp_item.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED

    resp_order = client.patch(f"/orders/{order.public_id}/ready")
    assert resp_order.status_code == 401
    assert resp_order.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


# ==============================================================================
# Error Handling: 404, 409, 422
# ==============================================================================


def test_mark_ready_endpoints_order_not_found_returns_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Non-existent order returns 404 ORDER_NOT_FOUND."""
    missing_id = uuid4()
    item_id = uuid4()

    resp_item = client.patch(
        f"/orders/{missing_id}/items/{item_id}/ready", headers=auth_headers("kitchen")
    )
    assert resp_item.status_code == 404
    assert resp_item.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND

    resp_order = client.patch(f"/orders/{missing_id}/ready", headers=auth_headers("kitchen"))
    assert resp_order.status_code == 404
    assert resp_order.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND


def test_mark_item_ready_item_not_found_in_order_returns_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Existing order with non-existent item returns 404 ORDER_NOT_FOUND."""
    order = _create_in_prep_order(num_items=1)
    test_db[order.public_id] = order
    missing_item_id = uuid4()

    resp = client.patch(
        f"/orders/{order.public_id}/items/{missing_item_id}/ready",
        headers=auth_headers("kitchen"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND


@pytest.mark.parametrize(
    "invalid_status",
    [
        OrderStatus.CREATED,
        OrderStatus.CANCELLED,
        OrderStatus.PAID,
        OrderStatus.REJECTED,
        OrderStatus.VOIDED,
    ],
)
def test_mark_ready_endpoints_invalid_status_returns_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    invalid_status: OrderStatus,
) -> None:
    """Orders not in kitchen (e.g. CREATED, CANCELLED) cannot transition to DELIVERED (409)."""
    order = _create_in_prep_order(num_items=1)
    order.status = invalid_status
    test_db[order.public_id] = order

    resp_item = client.patch(
        f"/orders/{order.public_id}/items/{order.items[0].public_id}/ready",
        headers=auth_headers("kitchen"),
    )
    assert resp_item.status_code == 409
    assert resp_item.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION

    resp_order = client.patch(f"/orders/{order.public_id}/ready", headers=auth_headers("kitchen"))
    assert resp_order.status_code == 409
    assert resp_order.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION


@pytest.mark.parametrize(
    "bad_body",
    [{"userId": "attacker"}, {"status": "DELIVERED"}, {"notes": "x"}],
)
def test_mark_ready_endpoints_reject_unexpected_body_with_422(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    bad_body: dict[str, str],
) -> None:
    """Mass assignment attempts are rejected with 422 VALIDATION_ERROR."""
    order = _create_in_prep_order(num_items=1)
    test_db[order.public_id] = order

    resp_item = client.patch(
        f"/orders/{order.public_id}/items/{order.items[0].public_id}/ready",
        headers=auth_headers("kitchen"),
        json=bad_body,
    )
    assert resp_item.status_code == 422
    assert resp_item.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR

    resp_order = client.patch(
        f"/orders/{order.public_id}/ready",
        headers=auth_headers("kitchen"),
        json=bad_body,
    )
    assert resp_order.status_code == 422
    assert resp_order.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize("empty_body", [None, {}])
def test_mark_ready_endpoints_accept_empty_or_absent_body(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    empty_body: dict[str, str] | None,
) -> None:
    """Empty JSON body or absent body are both accepted (200 OK)."""
    order = _create_in_prep_order(num_items=1)
    test_db[order.public_id] = order

    resp_item = client.patch(
        f"/orders/{order.public_id}/items/{order.items[0].public_id}/ready",
        headers=auth_headers("kitchen"),
        json=empty_body,
    )
    assert resp_item.status_code == 200


# ==============================================================================
# Direct OrderService unit tests (defense in depth & branch coverage)
# ==============================================================================


@pytest.mark.asyncio
async def test_order_service_defense_in_depth_rejects_waiter(
    fake_session: FakeAsyncSession,
    test_db: dict[UUID, Order],
) -> None:
    """Service directly rejects waiter with ForbiddenRoleError (defense in depth)."""
    order = _create_in_prep_order(num_items=1)
    test_db[order.public_id] = order
    service = OrderService(session=cast(AsyncSession, fake_session))
    waiter_auth = AuthContext(user_id="waiter-1", role=Role.WAITER)

    with pytest.raises(ForbiddenRoleError):
        await service.mark_item_ready(order.public_id, order.items[0].public_id, waiter_auth)

    with pytest.raises(ForbiddenRoleError):
        await service.mark_order_ready(order.public_id, waiter_auth)


@pytest.mark.asyncio
async def test_order_service_mark_item_ready_when_already_ready_on_created_order(
    fake_session: FakeAsyncSession,
    test_db: dict[UUID, Order],
) -> None:
    """Item already READY on a CREATED order raises InvalidStateTransitionError."""
    order = _create_in_prep_order(num_items=1)
    order.status = OrderStatus.CREATED
    order.items[0].status = ItemStatus.READY
    test_db[order.public_id] = order
    service = OrderService(session=cast(AsyncSession, fake_session))
    kitchen_auth = AuthContext(user_id="kitchen-1", role=Role.KITCHEN)

    with pytest.raises(InvalidStateTransitionError):
        await service.mark_item_ready(order.public_id, order.items[0].public_id, kitchen_auth)


@pytest.mark.asyncio
async def test_order_service_sync_delivery_when_order_has_no_items(
    fake_session: FakeAsyncSession,
) -> None:
    """Order with empty items returns False from _sync_delivery_if_all_ready."""
    order = Order(public_id=uuid4(), status=OrderStatus.IN_PREPARATION)
    service = OrderService(session=cast(AsyncSession, fake_session))
    assert service._sync_delivery_if_all_ready(order) is False


# ==============================================================================
# Sprint 4 (RF-09, RF-10, RF-18, OP-11): Start preparation, Kitchen reject & Cancel item
# ==============================================================================


def test_start_order_preparation_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
) -> None:
    """Transition order from RESERVED to IN_PREPARATION and stage OrderInPreparationEvent."""
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.RESERVED,
        stock_reserved_at=datetime.now(UTC),
    )
    item1 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-1",
        name="Burger",
        quantity=2,
        unit_price=Decimal("100.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item1)
    order.recalculate_total()
    test_db[order.public_id] = order

    resp = client.patch(
        f"/orders/{order.public_id}/start-preparation",
        headers=auth_headers("kitchen"),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "IN_PREPARATION"
    assert data["inPreparationAt"] is not None

    # Verify DB state
    assert test_db[order.public_id].status == OrderStatus.IN_PREPARATION
    assert test_db[order.public_id].in_preparation_at is not None

    # Verify Outbox event staged
    assert len(fake_session.outbox_rows) == 1
    event_row = fake_session.outbox_rows[0]
    assert event_row.event_type == EventName.ORDER_IN_PREPARATION
    payload = (
        event_row.payload if isinstance(event_row.payload, dict) else json.loads(event_row.payload)
    )
    assert payload["orderId"] == str(order.public_id)
    assert payload["items"] == [{"offerId": "dish-1", "quantity": 2}]

    # Verify WebSocket notification
    assert len(spy_notifier.notified_orders) == 1
    assert spy_notifier.notified_orders[0].public_id == order.public_id


def test_start_order_preparation_post_alias_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """POST alias also initiates order preparation."""
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.RESERVED,
        stock_reserved_at=datetime.now(UTC),
    )
    test_db[order.public_id] = order

    resp = client.post(
        f"/orders/{order.public_id}/start-preparation",
        headers=auth_headers("chef"),
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "IN_PREPARATION"
    assert len(fake_session.outbox_rows) == 1


def test_start_order_preparation_idempotent_when_already_in_preparation(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """Already IN_PREPARATION order is a safe no-op (200 OK without re-staging event)."""
    order = _create_in_prep_order(num_items=1)
    test_db[order.public_id] = order

    resp = client.patch(
        f"/orders/{order.public_id}/start-preparation",
        headers=auth_headers("kitchen"),
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "IN_PREPARATION"
    assert len(fake_session.outbox_rows) == 0


def test_start_order_preparation_forbidden_for_waiter(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Waiters are rejected with 403 FORBIDDEN_ROLE."""
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.RESERVED,
    )
    test_db[order.public_id] = order

    resp = client.patch(
        f"/orders/{order.public_id}/start-preparation",
        headers=auth_headers("waiter"),
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE


def test_start_order_preparation_invalid_state_conflict(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Orders in CREATED status cannot start preparation (409 Conflict)."""
    order = Order(
        public_id=uuid4(),
        table_number=1,
        status=OrderStatus.CREATED,
    )
    test_db[order.public_id] = order

    resp = client.patch(
        f"/orders/{order.public_id}/start-preparation",
        headers=auth_headers("kitchen"),
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION


def test_kitchen_reject_item_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
    fake_catalog: FakeCatalogValidator,
) -> None:
    """Kitchen rejects item from RESERVED: sets catalog False, stages event, sets REJECTED."""
    order = Order(
        public_id=uuid4(),
        table_number=7,
        status=OrderStatus.RESERVED,
        stock_reserved_at=datetime.now(UTC),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-avocado-salad",
        name="Avocado Salad",
        quantity=1,
        unit_price=Decimal("80.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    order.recalculate_total()
    test_db[order.public_id] = order

    resp = client.patch(
        f"/orders/{order.public_id}/items/{item.public_id}/kitchen-reject",
        headers=auth_headers("chef"),
        json={"reason": "Se acabaron los aguacates frescos"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REJECTED"
    assert data["rejectionDetails"] is not None
    assert data["rejectionDetails"]["source"] == "kitchen"
    assert len(data["rejectionDetails"]["rejectedItems"]) == 1
    assert data["rejectionDetails"]["rejectedItems"][0]["offerId"] == "dish-avocado-salad"

    # Catalog availability in Redis set to False
    assert fake_catalog.availabilities.get("dish-avocado-salad") is False

    # Outbox contains KitchenDishRejectedEvent
    assert len(fake_session.outbox_rows) == 1
    event_row = fake_session.outbox_rows[0]
    assert event_row.event_type == EventName.KITCHEN_DISH_REJECTED
    payload = (
        event_row.payload if isinstance(event_row.payload, dict) else json.loads(event_row.payload)
    )
    assert payload["orderId"] == str(order.public_id)
    assert payload["itemId"] == str(item.public_id)
    assert payload["offerId"] == "dish-avocado-salad"
    assert payload["quantity"] == 1
    assert payload["reason"] == "Se acabaron los aguacates frescos"

    # WebSocket notified
    assert len(spy_notifier.notified_orders) == 1


def test_kitchen_reject_item_forbidden_for_waiter(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Waiters cannot execute kitchen reject (403 Forbidden)."""
    order = Order(public_id=uuid4(), status=OrderStatus.RESERVED)
    item = OrderItem(public_id=uuid4(), dish_id="dish-1", name="Dish", quantity=1)
    order.items.append(item)
    test_db[order.public_id] = order

    resp = client.patch(
        f"/orders/{order.public_id}/items/{item.public_id}/kitchen-reject",
        headers=auth_headers("waiter"),
    )
    assert resp.status_code == 403


def test_kitchen_reject_item_when_order_not_reserved_raises_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Cannot reject item from an order that is already IN_PREPARATION (409 Conflict)."""
    order = _create_in_prep_order(num_items=1)
    test_db[order.public_id] = order

    resp = client.patch(
        f"/orders/{order.public_id}/items/{order.items[0].public_id}/kitchen-reject",
        headers=auth_headers("kitchen"),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION


def test_cancel_item_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    spy_notifier: SpyOrderNotifier,
) -> None:
    """Kitchen cancels a pending item in RESERVED order; order total is recalculated."""
    order = _create_in_prep_order(num_items=2)
    order.status = OrderStatus.RESERVED
    order.recalculate_total()
    assert order.total_amount == Decimal("100.00")
    test_db[order.public_id] = order
    item_to_cancel = order.items[0]

    resp = client.patch(
        f"/orders/{order.public_id}/items/{item_to_cancel.public_id}/cancel",
        headers=auth_headers("kitchen"),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert Decimal(str(data["totalAmount"])) == Decimal("50.00")
    cancelled_item = next(i for i in data["items"] if i["itemId"] == str(item_to_cancel.public_id))
    assert cancelled_item["status"] == "CANCELLED"
    assert test_db[order.public_id].items[0].status == ItemStatus.CANCELLED
    assert test_db[order.public_id].total_amount == Decimal("50.00")

    # Item and order change notifications sent
    assert len(spy_notifier.notified_items) == 1
    assert len(spy_notifier.notified_orders) == 1


def test_cancel_item_already_ready_conflict(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Cannot cancel an item that is already READY (409 Conflict)."""
    order = _create_in_prep_order(num_items=1)
    order.items[0].status = ItemStatus.READY
    test_db[order.public_id] = order

    resp = client.patch(
        f"/orders/{order.public_id}/items/{order.items[0].public_id}/cancel",
        headers=auth_headers("kitchen"),
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION
