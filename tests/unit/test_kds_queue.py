"""Unit tests for KDS queue endpoint (GET /kds/queue).

Covers dynamic sorting by kitchen elapsed time and tie-breakers (UT-BE-32, UT-BE-33),
active view filtering including DELIVERED with PENDING items and PAID exclusion (UT-BE-34),
and station filtering with unaccented normalization and general station (UT-BE-36).

Satisfies: RF-01, RF-02, RF-03, RF-04, RF-08, RNF-03, OP-09, OP-11 (TASK-12)
Covers: UT-BE-32, UT-BE-33, UT-BE-34, UT-BE-36
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role
from app.main import app
from app.repositories.orders import build_kds_queue_query
from app.services.catalog import (
    clear_catalog_mock,
    get_catalog_info_mock,
    normalize_station_name,
    register_catalog_mock,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement(
        "RF-01", "RF-02", "RF-03", "RF-04", "RF-08", "RNF-03", "OP-09", "OP-11"
    ),
]


class FakeResult:
    """Mock query result supporting unique() and all()."""

    def __init__(self, items: list[Order]) -> None:
        self._items = items

    def unique(self) -> FakeResult:
        return self

    def all(self) -> list[Order]:
        return list(self._items)


class FakeKdsAsyncSession:
    """In-memory AsyncSession double that evaluates KDS queue queries against test_db."""

    def __init__(self, db: dict[UUID, Order] | None = None) -> None:
        self.db: dict[UUID, Order] = db if db is not None else {}

    async def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> FakeResult:
        # Determine view filter from compiled SQL text or params
        sql_str = str(statement)
        is_delivered_view = (
            "orders.status = :status_" in sql_str
            and "orders.status = :status_1 OR" not in sql_str
            and "DELIVERED" in repr(statement.compile().params)
            and "IN_PREPARATION" not in repr(statement.compile().params)
        )

        matched: list[Order] = []
        for order in self.db.values():
            # Discard any order where in_preparation_at is null
            if order.in_preparation_at is None:
                continue
            # Discard PAID orders explicitly
            if order.status == OrderStatus.PAID:
                continue

            if is_delivered_view:
                if order.status == OrderStatus.DELIVERED:
                    matched.append(order)
            else:
                # Active view: IN_PREPARATION or (DELIVERED with at least one PENDING item)
                if order.status == OrderStatus.IN_PREPARATION:
                    matched.append(order)
                elif order.status == OrderStatus.DELIVERED:
                    has_pending = any(item.status == ItemStatus.PENDING for item in order.items)
                    if has_pending:
                        matched.append(order)

        # Sort: in_preparation_at ASC, created_at ASC, public_id ASC
        min_dt = datetime.min.replace(tzinfo=UTC)
        matched.sort(
            key=lambda o: (
                o.in_preparation_at or min_dt,
                o.created_at or min_dt,
                o.public_id,
            )
        )
        return FakeResult(matched)

    async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        params = statement.compile().params
        order_id = next((v for v in params.values() if isinstance(v, UUID)), None)
        if order_id is not None:
            return self.db.get(order_id)
        return None

    async def close(self) -> None:
        pass


@pytest.fixture
def test_db() -> dict[UUID, Order]:
    return {}


@pytest.fixture
def fake_session(test_db: dict[UUID, Order]) -> FakeKdsAsyncSession:
    return FakeKdsAsyncSession(test_db)


@pytest.fixture(autouse=True)
def override_kds_dependencies(fake_session: FakeKdsAsyncSession) -> Any:
    clear_catalog_mock()

    async def _get_test_session() -> Any:
        yield fake_session

    app.dependency_overrides[get_session] = _get_test_session
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_session, None)
        clear_catalog_mock()


# ============================================================================
# 1. Normalization Utility Tests
# ============================================================================


def test_normalize_station_name() -> None:
    """Verify station normalization: whitespace strip, lowercase, accent removal."""
    assert normalize_station_name("  Parrilla  ") == "parrilla"
    assert normalize_station_name("Estación Fría") == "estacion fria"
    assert normalize_station_name("BEBIDAS") == "bebidas"
    assert normalize_station_name("Postres y Café") == "postres y cafe"
    assert normalize_station_name("") == ""
    assert normalize_station_name("   ") == ""
    assert normalize_station_name(None) is None


# ============================================================================
# 2. Mock Catalog Tests (TASK-21a Isolation)
# ============================================================================


def test_get_catalog_info_mock_unregistered_returns_none() -> None:
    """Rule of TASK-21a: if a dish has no station in catalog, returns station: None."""
    info = get_catalog_info_mock("dish-unknown")
    assert info == {"station": None, "preparation_time_text": None}


def test_get_catalog_info_mock_registered() -> None:
    """Verify registered mock entries are returned."""
    register_catalog_mock("dish-01", station="Parrilla", preparation_time_text="15-20 min")
    info = get_catalog_info_mock("dish-01")
    assert info == {"station": "Parrilla", "preparation_time_text": "15-20 min"}


# ============================================================================
# 3. SQLAlchemy Query Compilation Tests (Step 3 & Step 4)
# ============================================================================


def test_build_kds_queue_query_active_structure() -> None:
    """Verify generated SQL clauses for view='active' (in_prep, PAID, DELIVERED+PENDING)."""
    stmt = build_kds_queue_query(view="active")
    sql = str(stmt)
    params = stmt.compile().params

    assert "orders.in_preparation_at IS NOT NULL" in sql
    assert "orders.status !=" in sql
    assert "EXISTS (SELECT 1" in sql
    assert "FROM order_items" in sql
    assert (
        "ORDER BY orders.in_preparation_at ASC, orders.created_at ASC, orders.public_id ASC" in sql
    )

    # Verify bound parameter values
    param_values = set(params.values())
    assert OrderStatus.PAID in param_values
    assert OrderStatus.IN_PREPARATION in param_values
    assert OrderStatus.DELIVERED in param_values
    assert ItemStatus.PENDING in param_values


def test_build_kds_queue_query_delivered_structure() -> None:
    """Verify generated SQL clauses for view='delivered'."""
    stmt = build_kds_queue_query(view="delivered")
    sql = str(stmt)
    params = stmt.compile().params

    assert "orders.in_preparation_at IS NOT NULL" in sql
    assert "orders.status !=" in sql
    assert (
        "ORDER BY orders.in_preparation_at ASC, orders.created_at ASC, orders.public_id ASC" in sql
    )

    param_values = set(params.values())
    assert OrderStatus.PAID in param_values
    assert OrderStatus.DELIVERED in param_values
    assert OrderStatus.IN_PREPARATION not in param_values


# ============================================================================
# 4. UT-BE-32 & UT-BE-33: Dynamic Sorting and Tie-breaking
# ============================================================================


def test_kds_queue_sorting_longest_time_in_kitchen_first(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """UT-BE-32: Orders ordered by in_preparation_at ASC (greatest kitchen elapsed time first)."""
    now = datetime.now(UTC)

    # Order 1 entered prep 30 mins ago (longest in kitchen)
    id1 = uuid4()
    order1 = Order(
        public_id=id1,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=40),
        in_preparation_at=now - timedelta(minutes=30),
        items=[OrderItem(name="Item 1", status=ItemStatus.PENDING, quantity=1)],
    )

    # Order 2 entered prep 10 mins ago
    id2 = uuid4()
    order2 = Order(
        public_id=id2,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=15),
        in_preparation_at=now - timedelta(minutes=10),
        items=[OrderItem(name="Item 2", status=ItemStatus.PENDING, quantity=1)],
    )

    # Order 3 entered prep 20 mins ago
    id3 = uuid4()
    order3 = Order(
        public_id=id3,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=25),
        in_preparation_at=now - timedelta(minutes=20),
        items=[OrderItem(name="Item 3", status=ItemStatus.PENDING, quantity=1)],
    )

    test_db[id1] = order1
    test_db[id2] = order2
    test_db[id3] = order3

    response = client.get("/kds/queue", headers=auth_headers(Role.KITCHEN))
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 3
    # Expected order: order1 (30m ago), order3 (20m ago), order2 (10m ago)
    assert data[0]["orderId"] == str(id1)
    assert data[1]["orderId"] == str(id3)
    assert data[2]["orderId"] == str(id2)


def test_kds_queue_equal_in_prep_breaks_ties_by_created_at_then_order_id(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """UT-BE-33: Equal in_preparation_at breaks ties by created_at ASC, then order_id ASC."""
    now = datetime.now(UTC)
    same_in_prep = now - timedelta(minutes=15)

    # order A: created earlier (30m ago)
    id_a = uuid4()
    order_a = Order(
        public_id=id_a,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=30),
        in_preparation_at=same_in_prep,
        items=[OrderItem(name="Item A", status=ItemStatus.PENDING, quantity=1)],
    )

    # order B & C: same created_at (20m ago), tie broken by UUID
    u1, u2 = sorted([uuid4(), uuid4()])
    order_b = Order(
        public_id=u1,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=20),
        in_preparation_at=same_in_prep,
        items=[OrderItem(name="Item B", status=ItemStatus.PENDING, quantity=1)],
    )
    order_c = Order(
        public_id=u2,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=20),
        in_preparation_at=same_in_prep,
        items=[OrderItem(name="Item C", status=ItemStatus.PENDING, quantity=1)],
    )

    test_db[id_a] = order_a
    test_db[u1] = order_b
    test_db[u2] = order_c

    response = client.get("/kds/queue", headers=auth_headers(Role.CHEF))
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 3
    # order_a wins tie because created_at is older
    assert data[0]["orderId"] == str(id_a)
    # order_b and order_c tie broken by UUID
    assert data[1]["orderId"] == str(u1)
    assert data[2]["orderId"] == str(u2)


# ============================================================================
# 5. UT-BE-34: View Filtering (active including DELIVERED with PENDING vs delivered vs PAID)
# ============================================================================


def test_kds_queue_active_view_includes_delivered_with_pending_and_excludes_paid(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """UT-BE-34: Active view includes DELIVERED with PENDING items and excludes PAID."""
    now = datetime.now(UTC)

    # 1. Normal active order: IN_PREPARATION -> INCLUDED
    id_active = uuid4()
    order_active = Order(
        public_id=id_active,
        status=OrderStatus.IN_PREPARATION,
        in_preparation_at=now - timedelta(minutes=10),
        items=[OrderItem(name="Tacos", status=ItemStatus.PENDING, quantity=2)],
    )

    # 2. Order marked DELIVERED but one item was sent to redo (PENDING) -> INCLUDED
    id_redo = uuid4()
    order_redo = Order(
        public_id=id_redo,
        status=OrderStatus.DELIVERED,
        in_preparation_at=now - timedelta(minutes=20),
        items=[
            OrderItem(name="Sopa", status=ItemStatus.DELIVERED, quantity=1),
            OrderItem(
                name="Carne", status=ItemStatus.PENDING, notes="Rehacer bien cocida", quantity=1
            ),
        ],
    )

    # 3. Order marked DELIVERED and all items are DELIVERED / READY -> EXCLUDED from active view
    id_delivered_done = uuid4()
    order_delivered_done = Order(
        public_id=id_delivered_done,
        status=OrderStatus.DELIVERED,
        in_preparation_at=now - timedelta(minutes=25),
        items=[
            OrderItem(name="Ensalada", status=ItemStatus.DELIVERED, quantity=1),
            OrderItem(name="Agua", status=ItemStatus.READY, quantity=1),
        ],
    )

    # 4. Order marked PAID -> EXCLUDED definitively from KDS
    id_paid = uuid4()
    order_paid = Order(
        public_id=id_paid,
        status=OrderStatus.PAID,
        in_preparation_at=now - timedelta(minutes=30),
        items=[OrderItem(name="Postre", status=ItemStatus.DELIVERED, quantity=1)],
    )

    # 5. Order with null in_preparation_at (e.g. still CREATED) -> EXCLUDED
    id_created = uuid4()
    order_created = Order(
        public_id=id_created,
        status=OrderStatus.CREATED,
        in_preparation_at=None,
        items=[OrderItem(name="Cafe", status=ItemStatus.PENDING, quantity=1)],
    )

    test_db[id_active] = order_active
    test_db[id_redo] = order_redo
    test_db[id_delivered_done] = order_delivered_done
    test_db[id_paid] = order_paid
    test_db[id_created] = order_created

    # GET /kds/queue (default view='active')
    response = client.get("/kds/queue", headers=auth_headers(Role.KITCHEN))
    assert response.status_code == 200
    data = response.json()

    returned_ids = {item["orderId"] for item in data}
    assert returned_ids == {str(id_active), str(id_redo)}
    assert str(id_delivered_done) not in returned_ids
    assert str(id_paid) not in returned_ids
    assert str(id_created) not in returned_ids


def test_kds_queue_delivered_view_returns_delivered_pure(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """View='delivered' returns delivered orders, excluding PAID and IN_PREPARATION."""
    now = datetime.now(UTC)

    id_delivered = uuid4()
    order_delivered = Order(
        public_id=id_delivered,
        status=OrderStatus.DELIVERED,
        in_preparation_at=now - timedelta(minutes=15),
        items=[OrderItem(name="Pizza", status=ItemStatus.DELIVERED, quantity=1)],
    )

    id_active = uuid4()
    order_active = Order(
        public_id=id_active,
        status=OrderStatus.IN_PREPARATION,
        in_preparation_at=now - timedelta(minutes=10),
        items=[OrderItem(name="Burger", status=ItemStatus.PENDING, quantity=1)],
    )

    id_paid = uuid4()
    order_paid = Order(
        public_id=id_paid,
        status=OrderStatus.PAID,
        in_preparation_at=now - timedelta(minutes=30),
        items=[OrderItem(name="Soda", status=ItemStatus.DELIVERED, quantity=1)],
    )

    test_db[id_delivered] = order_delivered
    test_db[id_active] = order_active
    test_db[id_paid] = order_paid

    response = client.get("/kds/queue?view=delivered", headers=auth_headers(Role.KITCHEN))
    assert response.status_code == 200
    data = response.json()

    returned_ids = [item["orderId"] for item in data]
    assert returned_ids == [str(id_delivered)]


# ============================================================================
# 6. UT-BE-36: Station Filtering, Mock Injection, and Accent Normalization
# ============================================================================


def test_kds_queue_filters_by_normalized_station_without_accents(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """UT-BE-36: Filter by station matching normalized unaccented string and general fallback."""
    now = datetime.now(UTC)

    # Register dishes in mock
    register_catalog_mock("dish-grill", station="Parrilla", preparation_time_text="20 min")
    register_catalog_mock("dish-cold", station="Estación Fría", preparation_time_text="5-8 min")
    register_catalog_mock("dish-drinks", station="Bebidas", preparation_time_text="3 min")
    register_catalog_mock("dish-gen", station="general", preparation_time_text="10 min")
    # dish-none has no entry -> mock returns station=None (Rule TASK-21a)

    id1 = uuid4()
    order1 = Order(
        public_id=id1,
        status=OrderStatus.IN_PREPARATION,
        in_preparation_at=now - timedelta(minutes=10),
        items=[
            OrderItem(dish_id="dish-grill", name="Ribeye", quantity=1),
            OrderItem(dish_id="dish-cold", name="Ceviche", quantity=1),
        ],
    )

    id2 = uuid4()
    order2 = Order(
        public_id=id2,
        status=OrderStatus.IN_PREPARATION,
        in_preparation_at=now - timedelta(minutes=5),
        items=[
            OrderItem(dish_id="dish-cold", name="Ensalada Cesar", quantity=1),
            OrderItem(dish_id="dish-drinks", name="Limonada", quantity=2),
        ],
    )

    id3 = uuid4()
    order3 = Order(
        public_id=id3,
        status=OrderStatus.IN_PREPARATION,
        in_preparation_at=now - timedelta(minutes=2),
        items=[
            OrderItem(dish_id="dish-none", name="Pan artesanal", quantity=1),
            OrderItem(dish_id="dish-gen", name="Sopa del dia", quantity=1),
        ],
    )

    test_db[id1] = order1
    test_db[id2] = order2
    test_db[id3] = order3

    # Query 1: Filter station=parrilla
    res_grill = client.get("/kds/queue?station=parrilla", headers=auth_headers(Role.KITCHEN))
    assert res_grill.status_code == 200
    data_grill = res_grill.json()
    assert len(data_grill) == 1
    assert data_grill[0]["orderId"] == str(id1)
    # Order 1 items filtered to only grill
    assert len(data_grill[0]["items"]) == 1
    assert data_grill[0]["items"][0]["dishId"] == "dish-grill"
    assert data_grill[0]["items"][0]["station"] == "Parrilla"
    assert data_grill[0]["items"][0]["preparationTimeText"] == "20 min"

    # Query 2: Filter station without accents matches accented mock
    # (estacion fria == Estación Fría)
    res_cold = client.get("/kds/queue?station=estacion%20fria", headers=auth_headers(Role.KITCHEN))
    assert res_cold.status_code == 200
    data_cold = res_cold.json()
    assert len(data_cold) == 2
    # Both order1 and order2 contain dish-cold; drinks and grill items are filtered out
    for ord_data in data_cold:
        assert len(ord_data["items"]) == 1
        assert ord_data["items"][0]["dishId"] == "dish-cold"

    # Query 3: Filter with accents in query matches unaccented or accented station
    res_cold_accent = client.get(
        "/kds/queue?station=Estación%20Fría", headers=auth_headers(Role.KITCHEN)
    )
    assert res_cold_accent.status_code == 200
    assert len(res_cold_accent.json()) == 2

    # Query 4: station=general accepts dishes with station=None or station="general"
    res_gen = client.get("/kds/queue?station=general", headers=auth_headers(Role.KITCHEN))
    assert res_gen.status_code == 200
    data_gen = res_gen.json()
    assert len(data_gen) == 1
    assert data_gen[0]["orderId"] == str(id3)
    assert len(data_gen[0]["items"]) == 2
    gen_dish_ids = {it["dishId"] for it in data_gen[0]["items"]}
    assert gen_dish_ids == {"dish-none", "dish-gen"}

    # Query 5: No station filter returns all orders with all items
    res_all = client.get("/kds/queue", headers=auth_headers(Role.KITCHEN))
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert len(data_all) == 3


# ============================================================================
# 7. Rule OP-11: No Color Thresholds or Semaphores in Response
# ============================================================================


def test_kds_queue_response_excludes_color_thresholds(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Rule OP-11: Response must NOT include semaphores, traffic lights, or color thresholds."""
    now = datetime.now(UTC)
    id1 = uuid4()
    test_db[id1] = Order(
        public_id=id1,
        status=OrderStatus.IN_PREPARATION,
        in_preparation_at=now - timedelta(minutes=10),
        items=[OrderItem(name="Taco", quantity=1)],
    )

    response = client.get("/kds/queue", headers=auth_headers(Role.KITCHEN))
    assert response.status_code == 200
    data = response.json()
    order_data = data[0]

    forbidden_keys = {
        "semaphore",
        "traffic_light",
        "trafficLight",
        "color",
        "warning_after_seconds",
        "warningAfterSeconds",
        "late_after_seconds",
        "lateAfterSeconds",
        "alert_color",
    }
    assert not any(key in order_data for key in forbidden_keys)
    assert not any(key in order_data["items"][0] for key in forbidden_keys)


# ============================================================================
# 8. Role Guard Matrix & Validation
# ============================================================================


@pytest.mark.parametrize("role", [Role.KITCHEN, Role.CHEF, Role.ADMIN])
def test_kds_queue_kitchen_roles_allowed(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    role: str,
) -> None:
    """Kitchen, chef, and admin roles are allowed to access GET /kds/queue."""
    response = client.get("/kds/queue", headers=auth_headers(role))
    assert response.status_code == 200


def test_kds_queue_waiter_forbidden(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Waiter role is forbidden with 403 FORBIDDEN_ROLE."""
    response = client.get("/kds/queue", headers=auth_headers(Role.WAITER))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE


def test_kds_queue_unauthenticated(client: TestClient) -> None:
    """Missing token returns 401 UNAUTHENTICATED."""
    response = client.get("/kds/queue")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


def test_kds_queue_invalid_view_parameter(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Invalid view param returns 422 Unprocessable Entity."""
    response = client.get("/kds/queue?view=invalid_view", headers=auth_headers(Role.KITCHEN))
    assert response.status_code == 422
