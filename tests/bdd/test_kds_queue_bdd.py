"""Step definitions for AT-06 acceptance scenarios (KDS Preparation Queue Management).

Satisfies: RF-01, RF-02, RF-03, RF-04, RF-05, RF-06, RF-07, RF-08, RF-11
Covers: AT-06
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pytest_bdd import given, parsers, scenarios, then, when

from app.db.models import Order
from app.domain.order_status import OrderStatus
from app.messaging.consumers.payment_completed import handle_payment_completed
from app.messaging.events import PaymentCompletedMessage
from app.services.catalog import clear_catalog_mock, register_catalog_mock
from tests.bdd._support import (
    BddSessionDouble,
    auth_header,
    bdd_client_override,
    build_uow,
    make_item,
    make_order,
)

pytestmark = [
    pytest.mark.bdd,
    pytest.mark.requirement(
        "RF-01", "RF-02", "RF-03", "RF-04", "RF-05", "RF-06", "RF-07", "RF-08", "RF-11"
    ),
]

scenarios("kds_queue.feature")


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
    with bdd_client_override(session) as test_client:
        bdd_context["client"] = test_client
        yield test_client


@given("three active orders exist with different preparation timestamps")
def three_active_orders_with_timestamps(bdd_context: dict[str, object]) -> None:
    now = datetime.now(UTC)
    db = cast(dict[UUID, Order], bdd_context["db"])
    orders_map = cast(dict[str, Order], bdd_context["orders_map"])

    specs = (
        ("A", 1, 35, 30, "dish-a", "Platillo A", "100.00"),
        ("B", 2, 20, 10, "dish-b", "Platillo B", "120.00"),
        ("C", 3, 25, 10, "dish-c", "Platillo C", "150.00"),
    )
    for key, table, created_mins, prep_mins, dish_id, name, price in specs:
        order = make_order(
            table_number=table,
            status=OrderStatus.IN_PREPARATION,
            created_at=now - timedelta(minutes=created_mins),
            in_preparation_at=now - timedelta(minutes=prep_mins),
            items=[make_item(dish_id, name, unit_price=price)],
        )
        db[order.public_id] = order
        orders_map[key] = order


@when(parsers.parse('the kitchen requests the preparation queue via "{path}"'))
def kitchen_requests_queue(bdd_context: dict[str, object], path: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    response = client.get(path.replace("GET ", ""), headers=auth_header("kitchen"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then("the orders are returned sorted by oldest kitchen elapsed time first")
def verify_sorted_by_elapsed(bdd_context: dict[str, object]) -> None:
    data = cast(list[dict[str, object]], cast(Response, bdd_context["last_response"]).json())
    order_a = cast(dict[str, Order], bdd_context["orders_map"])["A"]
    assert data[0]["orderId"] == str(order_a.public_id)


@then("ties are broken by order creation timestamp")
def verify_tie_breaking(bdd_context: dict[str, object]) -> None:
    data = cast(list[dict[str, object]], cast(Response, bdd_context["last_response"]).json())
    orders_map = cast(dict[str, Order], bdd_context["orders_map"])
    indices = [d["orderId"] for d in data]
    assert indices.index(str(orders_map["C"].public_id)) < indices.index(
        str(orders_map["B"].public_id)
    )


@given('an active order in "IN_PREPARATION" status exists in the kitchen queue')
def active_order_in_queue(bdd_context: dict[str, object]) -> None:
    now = datetime.now(UTC)
    order = make_order(
        table_number=5,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=10),
        in_preparation_at=now - timedelta(minutes=5),
        items=[make_item("dish-paid-test", "Paid Test Dish")],
    )
    cast(dict[UUID, Order], bdd_context["db"])[order.public_id] = order
    bdd_context["target_order"] = order


@when(parsers.parse('the kitchen marks the entire order ready via "{path_template}"'))
def kitchen_marks_entire_order_ready(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["target_order"])
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    response = client.patch(path, headers=auth_header("kitchen"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('the order status becomes "{expected_status}"'))
def verify_order_status_becomes(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["target_order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    assert db[order.public_id].status == OrderStatus(expected_status)


@when('the order receives payment confirmation and transitions to "PAID"')
def order_receives_payment_and_transitions_to_paid(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["target_order"])
    session = cast(BddSessionDouble, bdd_context["session"])
    msg = PaymentCompletedMessage(eventId=uuid4(), orderId=order.public_id)
    asyncio.run(handle_payment_completed(msg, build_uow(session)))


@then("the paid order is not present in the queue")
def verify_paid_order_excluded(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["target_order"])
    data = cast(list[dict[str, object]], cast(Response, bdd_context["last_response"]).json())
    assert str(order.public_id) not in [d["orderId"] for d in data]


@given('orders exist with items assigned to "grill" and "cold_station" stations')
def orders_with_stations(bdd_context: dict[str, object]) -> None:
    register_catalog_mock("dish-steak", station="Grill")
    register_catalog_mock("dish-salad", station="Cold Station")
    now = datetime.now(UTC)

    order1 = make_order(
        table_number=10,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=15),
        in_preparation_at=now - timedelta(minutes=10),
        items=[make_item("dish-steak", "Corte New York", unit_price="250.00")],
    )
    order2 = make_order(
        table_number=11,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=12),
        in_preparation_at=now - timedelta(minutes=8),
        items=[make_item("dish-salad", "Ensalada César", unit_price="90.00")],
    )
    order3 = make_order(
        table_number=12,
        status=OrderStatus.IN_PREPARATION,
        created_at=now - timedelta(minutes=10),
        in_preparation_at=now - timedelta(minutes=5),
        items=[
            make_item("dish-steak", "Corte Rib Eye", unit_price="280.00"),
            make_item("dish-salad", "Ensalada Griega", unit_price="95.00"),
        ],
    )

    db = cast(dict[UUID, Order], bdd_context["db"])
    orders_map = cast(dict[str, Order], bdd_context["orders_map"])
    for key, order in (("1", order1), ("2", order2), ("3", order3)):
        db[order.public_id] = order
        orders_map[key] = order


@when(parsers.parse('the kitchen requests the queue filtered by station "{station}" via "{path}"'))
def kitchen_requests_filtered_queue(
    bdd_context: dict[str, object], station: str, path: str
) -> None:
    del station
    kitchen_requests_queue(bdd_context, path)


@then(
    parsers.parse('only items belonging to station "{target_station}" are included in the results')
)
def verify_items_station_filter(bdd_context: dict[str, object], target_station: str) -> None:
    data = cast(list[dict[str, object]], cast(Response, bdd_context["last_response"]).json())
    norm_target = target_station.lower().replace("_", "")
    for order_data in data:
        for item in cast(list[dict[str, object]], order_data["items"]):
            norm_station = str(item["station"]).lower().replace(" ", "")
            assert norm_target in norm_station or norm_station in norm_target


@then(parsers.parse('orders with no items for "{station}" are omitted'))
def verify_orders_omitted_without_station(bdd_context: dict[str, object], station: str) -> None:
    del station
    data = cast(list[dict[str, object]], cast(Response, bdd_context["last_response"]).json())
    order2 = cast(dict[str, Order], bdd_context["orders_map"])["2"]
    assert str(order2.public_id) not in [d["orderId"] for d in data]


@given("an order is expired within the 10-minute grace window (KDS_EXPIRED_GRACE_MS = 600000)")
def expired_order_in_grace_window(bdd_context: dict[str, object]) -> None:
    now = datetime.now(UTC)
    order = make_order(
        table_number=20,
        status=OrderStatus.EXPIRED,
        created_at=now - timedelta(seconds=1100),
        confirmed_at=now - timedelta(seconds=950),
        items=[make_item("dish-caducado", "Platillo Caducado", unit_price="150.00")],
    )
    cast(dict[UUID, Order], bdd_context["db"])[order.public_id] = order
    bdd_context["target_order"] = order


@then("the expired order is excluded from the active queue")
def verify_expired_excluded_from_active_queue(bdd_context: dict[str, object]) -> None:
    verify_paid_order_excluded(bdd_context)


@when(parsers.parse('an operator requests expired orders via "{path}"'))
def operator_requests_expired_orders(bdd_context: dict[str, object], path: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    response = client.get(path.replace("GET ", ""), headers=auth_header("waiter"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then("the expired order is returned in the list")
def verify_expired_returned(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["target_order"])
    data = cast(list[dict[str, object]], cast(Response, bdd_context["last_response"]).json())
    assert str(order.public_id) in [d["orderId"] for d in data]
