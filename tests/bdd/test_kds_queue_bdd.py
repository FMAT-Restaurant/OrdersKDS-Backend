"""Step definitions for AT-06 acceptance scenarios (KDS Preparation Queue Management).

Satisfies: RF-01, RF-02, RF-03, RF-04, RF-05, RF-06, RF-07, RF-08, RF-11
Covers: AT-06
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem, Outbox, ProcessedEvent
from app.db.session import get_session
from app.domain.order_status import ItemStatus, OrderStatus
from app.main import app
from app.messaging.consumers.payment_completed import handle_payment_completed
from app.messaging.events import PaymentCompletedMessage
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork
from app.services.catalog import (
    clear_catalog_mock,
    register_catalog_mock,
)
from app.services.orders import OrderService, get_order_service

pytestmark = [
    pytest.mark.bdd,
    pytest.mark.requirement(
        "RF-01", "RF-02", "RF-03", "RF-04", "RF-05", "RF-06", "RF-07", "RF-08", "RF-11"
    ),
]

scenarios("kds_queue.feature")


def _jwt_segment(payload: dict[str, str | int]) -> str:
    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).rstrip(b"=").decode("ascii")


def _auth_header(role: str = "kitchen") -> dict[str, str]:
    header = _jwt_segment({"alg": "HS256", "typ": "JWT"})
    claims = _jwt_segment(
        {
            "user_id": str(uuid4()),
            "role": role,
            "exp": int(datetime.now(UTC).timestamp()) + 300,
        }
    )
    return {"Authorization": f"Bearer {header}.{claims}.c2ln"}


def _filter_kds_orders(db: dict[UUID, Order], is_delivered_view: bool) -> list[Order]:
    matched: list[Order] = []
    for order in db.values():
        if order.in_preparation_at is None and order.stock_reserved_at is None:
            continue
        if order.status == OrderStatus.PAID:
            continue

        if is_delivered_view:
            if order.status == OrderStatus.DELIVERED:
                matched.append(order)
        elif order.status in (OrderStatus.RESERVED, OrderStatus.IN_PREPARATION) or (
            order.status == OrderStatus.DELIVERED
            and any(i.status == ItemStatus.PENDING for i in order.items)
        ):
            matched.append(order)

    min_dt = datetime.min.replace(tzinfo=UTC)
    matched.sort(
        key=lambda o: (
            o.in_preparation_at or o.stock_reserved_at or o.created_at or min_dt,
            o.created_at or min_dt,
            o.public_id,
        )
    )
    return matched


class BddSessionDouble:
    """In-memory AsyncSession test double for KDS queue queries."""

    def __init__(self, db: dict[UUID, Order]) -> None:
        self.db = db
        self.outbox_rows: list[Outbox] = []
        self.processed: set[tuple[UUID, UUID]] = set()

    def add(self, instance: object) -> None:
        if isinstance(instance, Order):
            self.db[instance.public_id] = instance
        elif isinstance(instance, Outbox):
            self.outbox_rows.append(instance)
        elif isinstance(instance, ProcessedEvent):
            self.processed.add((instance.order_id, instance.event_id))

    async def scalar(self, statement: object) -> object:
        compile_fn = getattr(statement, "compile", None)
        if callable(compile_fn):
            statement_str = str(statement)
            params = compile_fn().params
            if "processed_events" in statement_str:
                order_id = next(
                    (v for k, v in params.items() if "order_id" in k and isinstance(v, UUID)),
                    None,
                )
                event_id = next(
                    (v for k, v in params.items() if "event_id" in k and isinstance(v, UUID)),
                    None,
                )
                if order_id and event_id and (order_id, event_id) in self.processed:
                    return 1
                return None
            order_id = next((v for v in params.values() if isinstance(v, UUID)), None)
            return self.db.get(order_id) if order_id is not None else None
        return None

    async def scalars(self, statement: object) -> object:
        statement_str = str(statement)
        compile_fn = getattr(statement, "compile", None)
        params: dict[str, object] = compile_fn().params if callable(compile_fn) else {}

        # 1. KDS queue query detection
        if "coalesce(orders.in_preparation_at" in statement_str.lower():
            is_delivered = (
                "orders.status = :status_" in statement_str
                and "orders.status = :status_1 OR" not in statement_str
                and "DELIVERED" in repr(params)
                and "IN_PREPARATION" not in repr(params)
            )
            orders = _filter_kds_orders(self.db, is_delivered)
        # 2. Status filtering detection (e.g. GET /orders?status=EXPIRED)
        elif "orders.status = :status_1" in statement_str:
            target_status_val = params.get("status_1")
            target_status = (
                OrderStatus(target_status_val)
                if isinstance(target_status_val, str)
                else target_status_val
            )
            orders = [o for o in self.db.values() if o.status == target_status]
        else:
            orders = list(self.db.values())

        class _Result:
            def unique(self) -> _Result:
                return self

            def all(self) -> list[Order]:
                return list(orders)

        return _Result()

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass


class BddUow(UnitOfWork):
    def __init__(self, session_factory: SessionFactory, session: BddSessionDouble) -> None:
        super().__init__(session_factory)
        self._session_instance = session

    async def __aenter__(self) -> BddUow:
        session = cast(AsyncSession, self._session_instance)
        self.session = session
        self.orders = OrderRepository(session)
        self.outbox = OutboxRepository(session)
        self.processed_events = ProcessedEventsRepository(session)
        return self

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@pytest.fixture
def bdd_context() -> dict[str, object]:
    clear_catalog_mock()
    initial_db: dict[UUID, Order] = {}
    return {
        "db": initial_db,
        "orders_map": {},
        "target_order": None,
        "last_response": None,
    }


@pytest.fixture(autouse=True)
def bdd_client_and_session(
    bdd_context: dict[str, object],
) -> Generator[TestClient, None, None]:
    db = cast(dict[UUID, Order], bdd_context["db"])
    session = BddSessionDouble(db)
    bdd_context["session"] = session

    async def _get_test_session() -> AsyncGenerator[BddSessionDouble, None]:
        yield session

    app.dependency_overrides[get_session] = _get_test_session
    app.dependency_overrides[get_order_service] = lambda: OrderService(
        session=cast(AsyncSession, session),
    )

    with TestClient(app) as test_client:
        bdd_context["client"] = test_client
        yield test_client
    app.dependency_overrides.clear()


@given("three active orders exist with different preparation timestamps")
def three_active_orders_with_timestamps(bdd_context: dict[str, object]) -> None:
    now = datetime.now(UTC)
    db = cast(dict[UUID, Order], bdd_context["db"])
    orders_map = cast(dict[str, Order], bdd_context["orders_map"])

    # Order A: in_preparation_at = now - 30m (Oldest preparation)
    order_a = Order(
        public_id=uuid4(),
        table_number=1,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=35),
        in_preparation_at=now - timedelta(minutes=30),
    )
    order_a.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="dish-a",
            name="Platillo A",
            quantity=1,
            unit_price=Decimal("100.00"),
            status=ItemStatus.PENDING,
        )
    )

    # Order B: in_preparation_at = now - 10m, created_at = now - 20m
    order_b = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=20),
        in_preparation_at=now - timedelta(minutes=10),
    )
    order_b.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="dish-b",
            name="Platillo B",
            quantity=1,
            unit_price=Decimal("120.00"),
            status=ItemStatus.PENDING,
        )
    )

    # Order C: in_preparation_at = now - 10m, created_at = now - 25m (Older creation)
    order_c = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=25),
        in_preparation_at=now - timedelta(minutes=10),
    )
    order_c.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="dish-c",
            name="Platillo C",
            quantity=1,
            unit_price=Decimal("150.00"),
            status=ItemStatus.PENDING,
        )
    )

    for order in (order_a, order_b, order_c):
        order.recalculate_total()
        db[order.public_id] = order

    orders_map["A"] = order_a
    orders_map["B"] = order_b
    orders_map["C"] = order_c


@when(parsers.parse('the kitchen requests the preparation queue via "{path}"'))
def kitchen_requests_queue(bdd_context: dict[str, object], path: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    endpoint = path.replace("GET ", "")
    response = client.get(endpoint, headers=_auth_header("kitchen"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then("the orders are returned sorted by oldest kitchen elapsed time first")
def verify_sorted_by_elapsed(bdd_context: dict[str, object]) -> None:
    response = cast(Response, bdd_context["last_response"])
    data = cast(list[dict[str, object]], response.json())
    orders_map = cast(dict[str, Order], bdd_context["orders_map"])
    order_a = orders_map["A"]
    assert data[0]["orderId"] == str(order_a.public_id)


@then("ties are broken by order creation timestamp")
def verify_tie_breaking(bdd_context: dict[str, object]) -> None:
    response = cast(Response, bdd_context["last_response"])
    data = cast(list[dict[str, object]], response.json())
    orders_map = cast(dict[str, Order], bdd_context["orders_map"])
    order_b = orders_map["B"]
    order_c = orders_map["C"]
    indices = [d["orderId"] for d in data]
    assert indices.index(str(order_c.public_id)) < indices.index(str(order_b.public_id))


@given('an active order in "IN_PREPARATION" status exists in the kitchen queue')
def active_order_in_queue(bdd_context: dict[str, object]) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=5,
        status=OrderStatus.IN_PREPARATION,
        created_at=datetime.now(UTC) - timedelta(minutes=10),
        in_preparation_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    order.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="dish-paid-test",
            name="Paid Test Dish",
            quantity=1,
            unit_price=Decimal("45.00"),
            status=ItemStatus.PENDING,
        )
    )
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["target_order"] = order


@when(parsers.parse('the kitchen marks the entire order ready via "{path_template}"'))
def kitchen_marks_entire_order_ready(
    bdd_context: dict[str, object],
    path_template: str,
) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["target_order"])
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    response = client.patch(path, headers=_auth_header("kitchen"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('the order status becomes "{expected_status}"'))
def verify_order_status_becomes(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["target_order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.status == OrderStatus(expected_status)


@when('the order receives payment confirmation and transitions to "PAID"')
def order_receives_payment_and_transitions_to_paid(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["target_order"])
    session = cast(BddSessionDouble, bdd_context["session"])
    uow = BddUow(lambda: cast(AsyncSession, session), session)
    msg = PaymentCompletedMessage(
        eventId=uuid4(),
        orderId=order.public_id,
    )
    asyncio.run(handle_payment_completed(msg, uow))


@then("the paid order is not present in the queue")
def verify_paid_order_excluded(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["target_order"])
    response = cast(Response, bdd_context["last_response"])
    data = cast(list[dict[str, object]], response.json())
    order_ids = [d["orderId"] for d in data]
    assert str(order.public_id) not in order_ids


@given('orders exist with items assigned to "grill" and "cold_station" stations')
def orders_with_stations(bdd_context: dict[str, object]) -> None:
    register_catalog_mock("dish-steak", station="Grill")
    register_catalog_mock("dish-salad", station="Cold Station")

    now = datetime.now(UTC)

    # Order 1: only Grill
    order1 = Order(
        public_id=uuid4(),
        table_number=10,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=15),
        in_preparation_at=now - timedelta(minutes=10),
    )
    order1.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="dish-steak",
            name="Corte New York",
            quantity=1,
            unit_price=Decimal("250.00"),
            status=ItemStatus.PENDING,
        )
    )

    # Order 2: only Cold Station
    order2 = Order(
        public_id=uuid4(),
        table_number=11,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=12),
        in_preparation_at=now - timedelta(minutes=8),
    )
    order2.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="dish-salad",
            name="Ensalada César",
            quantity=1,
            unit_price=Decimal("90.00"),
            status=ItemStatus.PENDING,
        )
    )

    # Order 3: both stations
    order3 = Order(
        public_id=uuid4(),
        table_number=12,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=10),
        in_preparation_at=now - timedelta(minutes=5),
    )
    order3.items.extend(
        [
            OrderItem(
                public_id=uuid4(),
                dish_id="dish-steak",
                name="Corte Rib Eye",
                quantity=1,
                unit_price=Decimal("280.00"),
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=uuid4(),
                dish_id="dish-salad",
                name="Ensalada Griega",
                quantity=1,
                unit_price=Decimal("95.00"),
                status=ItemStatus.PENDING,
            ),
        ]
    )

    db = cast(dict[UUID, Order], bdd_context["db"])
    orders_map = cast(dict[str, Order], bdd_context["orders_map"])
    for o in (order1, order2, order3):
        o.recalculate_total()
        db[o.public_id] = o

    orders_map["1"] = order1
    orders_map["2"] = order2
    orders_map["3"] = order3


@when(parsers.parse('the kitchen requests the queue filtered by station "{station}" via "{path}"'))
def kitchen_requests_filtered_queue(
    bdd_context: dict[str, object],
    station: str,
    path: str,
) -> None:
    client = cast(TestClient, bdd_context["client"])
    endpoint = path.replace("GET ", "")
    response = client.get(endpoint, headers=_auth_header("kitchen"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(
    parsers.parse('only items belonging to station "{target_station}" are included in the results')
)
def verify_items_station_filter(bdd_context: dict[str, object], target_station: str) -> None:
    response = cast(Response, bdd_context["last_response"])
    data = cast(list[dict[str, object]], response.json())
    for order_data in data:
        items = cast(list[dict[str, object]], order_data["items"])
        for item in items:
            station_val = str(item["station"]).lower()
            norm_target = target_station.lower().replace("_", "")
            norm_station = station_val.replace(" ", "")
            assert norm_target in norm_station or norm_station in norm_target


@then(parsers.parse('orders with no items for "{station}" are omitted'))
def verify_orders_omitted_without_station(
    bdd_context: dict[str, object],
    station: str,
) -> None:
    response = cast(Response, bdd_context["last_response"])
    data = cast(list[dict[str, object]], response.json())
    orders_map = cast(dict[str, Order], bdd_context["orders_map"])
    order2 = orders_map["2"]
    returned_ids = [d["orderId"] for d in data]
    assert str(order2.public_id) not in returned_ids


@given("an order is expired within the 10-minute grace window (KDS_EXPIRED_GRACE_MS = 600000)")
def expired_order_in_grace_window(bdd_context: dict[str, object]) -> None:
    now = datetime.now(UTC)
    order = Order(
        public_id=uuid4(),
        table_number=20,
        status=OrderStatus.EXPIRED,
        created_at=now - timedelta(seconds=1100),
        confirmed_at=now - timedelta(seconds=950),
    )
    order.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="dish-caducado",
            name="Platillo Caducado",
            quantity=1,
            unit_price=Decimal("150.00"),
            status=ItemStatus.PENDING,
        )
    )
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["target_order"] = order


@then("the expired order is excluded from the active queue")
def verify_expired_excluded_from_active_queue(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["target_order"])
    response = cast(Response, bdd_context["last_response"])
    data = cast(list[dict[str, object]], response.json())
    returned_ids = [d["orderId"] for d in data]
    assert str(order.public_id) not in returned_ids


@when(parsers.parse('an operator requests expired orders via "{path}"'))
def operator_requests_expired_orders(bdd_context: dict[str, object], path: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    endpoint = path.replace("GET ", "")
    response = client.get(endpoint, headers=_auth_header("waiter"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then("the expired order is returned in the list")
def verify_expired_returned(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["target_order"])
    response = cast(Response, bdd_context["last_response"])
    data = cast(list[dict[str, object]], response.json())
    returned_ids = [d["orderId"] for d in data]
    assert str(order.public_id) in returned_ids
