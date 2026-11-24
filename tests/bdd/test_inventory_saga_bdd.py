"""Step definitions for AT-03 acceptance scenarios (Inventory Saga & Stock Reservation).

Satisfies: RF-32, RF-33, RF-34, RF-35
Covers: AT-03
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import SagaTimeoutSettings
from app.db.models import Order
from app.domain.order_status import OrderStatus
from app.messaging.consumers.stock_insufficient import handle_stock_insufficient
from app.messaging.consumers.stock_reserved import handle_stock_reserved
from app.messaging.events import (
    OrderCreatedItem,
    RejectedItemDetail,
    StockInsufficientMessage,
    StockReservedMessage,
)
from app.services.catalog import DefaultCatalogValidator
from app.services.notifications import OrderNotifier
from app.workers.saga_timeout_watcher import SagaTimeoutWatcher
from tests.bdd._support import (
    BddSessionDouble,
    DummyNotifier,
    auth_header,
    bdd_client_override,
    build_uow,
    find_outbox_events,
    item_input_payload,
    make_item,
    make_order,
    resolve_bdd_path,
)

pytestmark = [
    pytest.mark.bdd,
    pytest.mark.requirement("RF-32", "RF-33", "RF-34", "RF-35"),
]

scenarios("inventory_saga.feature")


def _persisted_order(ctx: dict[str, object]) -> Order:
    return cast(dict[UUID, Order], ctx["db"])[cast(Order, ctx["order"]).public_id]


def _seed(ctx: dict[str, object], order: Order) -> None:
    cast(dict[UUID, Order], ctx["db"])[order.public_id] = order
    ctx["order"] = order


@pytest.fixture
def bdd_context() -> dict[str, object]:
    initial_db: dict[UUID, Order] = {}
    return {
        "db": initial_db,
        "order": None,
        "last_response": None,
        "session": None,
        "notifier": DummyNotifier(),
        "catalog_validator": DefaultCatalogValidator(),
    }


@pytest.fixture(autouse=True)
def bdd_client_and_session(bdd_context: dict[str, object]) -> Generator[TestClient, None, None]:
    session = BddSessionDouble(cast(dict[UUID, Order], bdd_context["db"]))
    bdd_context["session"] = session
    catalog = cast(DefaultCatalogValidator, bdd_context["catalog_validator"])
    notifier = cast(OrderNotifier, bdd_context["notifier"])
    with bdd_client_override(session, catalog_validator=catalog, notifier=notifier) as tc:
        bdd_context["client"] = tc
        yield tc


@given('a confirmed order exists in "CREATED" status awaiting stock reservation')
def confirmed_order_awaiting_stock(bdd_context: dict[str, object]) -> None:
    _seed(
        bdd_context,
        make_order(
            table_number=1,
            status=OrderStatus.CREATED,
            confirmed_at=datetime.now(UTC) - timedelta(seconds=10),
            confirmed_by=uuid4(),
            items=[make_item("dish-tacos", "Tacos al Pastor", quantity=2, unit_price="30.00")],
        ),
    )


@given('a confirmed order exists in "CREATED" status with 3 requested tacos')
def confirmed_order_with_three_tacos(bdd_context: dict[str, object]) -> None:
    _seed(
        bdd_context,
        make_order(
            table_number=2,
            status=OrderStatus.CREATED,
            confirmed_at=datetime.now(UTC) - timedelta(seconds=15),
            confirmed_by=uuid4(),
            items=[make_item("dish-tacos", "Tacos al Pastor", quantity=3, unit_price="30.00")],
        ),
    )


@given('an order exists in "RESERVED" status with 3 tacos and 1 drink')
def reserved_order_with_tacos_and_drink(bdd_context: dict[str, object]) -> None:
    now = datetime.now(UTC)
    _seed(
        bdd_context,
        make_order(
            table_number=3,
            status=OrderStatus.RESERVED,
            confirmed_at=now - timedelta(seconds=60),
            stock_reserved_at=now - timedelta(seconds=40),
            items=[
                make_item("dish-tacos", "Tacos al Pastor", quantity=3, unit_price="30.00"),
                make_item("dish-horchata", "Agua de Horchata", quantity=1, unit_price="25.00"),
            ],
        ),
    )


@given("a reserved order exists past 15-minute TTL within the 10-minute Caducados grace window")
def reserved_order_past_ttl_in_grace(bdd_context: dict[str, object]) -> None:
    now = datetime.now(UTC)
    _seed(
        bdd_context,
        make_order(
            table_number=7,
            status=OrderStatus.RESERVED,
            confirmed_at=now - timedelta(seconds=980),
            stock_reserved_at=now - timedelta(seconds=960),
            items=[
                make_item("dish-tacos", "Tacos al Pastor", quantity=3, unit_price="30.00"),
                make_item("dish-horchata", "Agua de Horchata", quantity=1, unit_price="25.00"),
            ],
        ),
    )


@given('a confirmed order in "CREATED" status confirmed 900 seconds ago')
def confirmed_order_past_ttl(bdd_context: dict[str, object]) -> None:
    _seed(
        bdd_context,
        make_order(
            table_number=4,
            status=OrderStatus.CREATED,
            confirmed_at=datetime.now(UTC) - timedelta(seconds=905),
            confirmed_by=uuid4(),
            items=[make_item("dish-pizza", "Pizza Artesanal", unit_price="180.00")],
        ),
    )


@given('an order exists in "EXPIRED" status')
def expired_order_exists(bdd_context: dict[str, object]) -> None:
    _seed(
        bdd_context,
        make_order(
            table_number=5,
            status=OrderStatus.EXPIRED,
            confirmed_at=datetime.now(UTC) - timedelta(seconds=1200),
        ),
    )


@given('an order exists in "CANCELLED" status')
def cancelled_order_exists(bdd_context: dict[str, object]) -> None:
    _seed(
        bdd_context,
        make_order(
            table_number=8,
            status=OrderStatus.CANCELLED,
            confirmed_at=datetime.now(UTC) - timedelta(seconds=1200),
        ),
    )


@given('an order exists in "EXPIRED" status within the 10-minute Caducados grace window')
def expired_order_in_caducados_grace(bdd_context: dict[str, object]) -> None:
    _seed(
        bdd_context,
        make_order(
            table_number=6,
            status=OrderStatus.EXPIRED,
            confirmed_at=datetime.now(UTC) - timedelta(seconds=960),
            items=[make_item("dish-pasta", "Pasta Carbonara", unit_price="140.00")],
        ),
    )


@when(parsers.parse('inventory confirms stock reservation via "{event_name}"'))
def process_stock_reserved(bdd_context: dict[str, object], event_name: str) -> None:
    del event_name
    session = cast(BddSessionDouble, bdd_context["session"])
    notifier = cast(OrderNotifier, bdd_context["notifier"])
    msg = StockReservedMessage(
        event_id=uuid4(), order_id=_persisted_order(bdd_context).public_id, reservations=[]
    )
    asyncio.run(handle_stock_reserved(msg, build_uow(session), notifier=notifier))


@then(parsers.parse('the order status becomes "{expected_status}"'))
@then(parsers.parse('the order status transitions from "REJECTED" back to "{expected_status}"'))
@then(parsers.parse('the order status remains "{expected_status}"'))
def verify_order_status(bdd_context: dict[str, object], expected_status: str) -> None:
    assert _persisted_order(bdd_context).status == OrderStatus(expected_status)


@then("the order stock_reserved_at timestamp is recorded")
def verify_stock_reserved_at(bdd_context: dict[str, object]) -> None:
    assert _persisted_order(bdd_context).stock_reserved_at is not None


@when(
    parsers.parse(
        'inventory rejects reservation via "{event_name}" with availableQuantity {available_qty:d}'
    )
)
def process_stock_insufficient(
    bdd_context: dict[str, object],
    event_name: str,
    available_qty: int,
) -> None:
    del event_name
    order = _persisted_order(bdd_context)
    item = order.items[0]
    offer_id = item.dish_id or "dish-tacos"
    msg = StockInsufficientMessage(
        event_id=uuid4(),
        order_id=order.public_id,
        reason="Insufficient stock in warehouse",
        rejected_items=[
            RejectedItemDetail(
                offer_id=offer_id,
                item_name=item.name,
                requested_quantity=item.quantity,
                available_quantity=available_qty,
            )
        ],
        reserved_items=[OrderCreatedItem(offerId=offer_id, quantity=available_qty)],
    )
    session = cast(BddSessionDouble, bdd_context["session"])
    notifier = cast(OrderNotifier, bdd_context["notifier"])
    asyncio.run(handle_stock_insufficient(msg, build_uow(session), notifier=notifier))


@then(
    parsers.parse(
        "the rejectionDetails contain rejectedItems with availableQuantity "
        "{available_qty:d} and reservedItems"
    )
)
def verify_rejection_details_partial(bdd_context: dict[str, object], available_qty: int) -> None:
    details = _persisted_order(bdd_context).rejection_details
    assert details is not None
    rejected = cast(list[dict[str, object]], details["rejectedItems"])
    reserved = cast(list[dict[str, object]], details["reservedItems"])
    assert rejected[0]["availableQuantity"] == available_qty
    assert len(reserved) >= 1


@when(parsers.parse('the waiter updates the order items via "{path_template}" with {qty:d} tacos'))
def waiter_updates_rejected_items(
    bdd_context: dict[str, object],
    path_template: str,
    qty: int,
) -> None:
    payload = {
        "items": [
            item_input_payload("dish-tacos", "Tacos al Pastor", quantity=qty, unit_price="30.00")
        ]
    }
    resp = cast(TestClient, bdd_context["client"]).patch(
        resolve_bdd_path(path_template, _persisted_order(bdd_context)),
        headers=auth_header("waiter"),
        json=payload,
    )
    bdd_context["last_response"] = resp
    assert resp.status_code == 200


@then(parsers.parse("the order total amount is updated for {qty:d} items"))
def verify_total_updated(bdd_context: dict[str, object], qty: int) -> None:
    assert _persisted_order(bdd_context).total_amount == Decimal("30.00") * qty


@when(parsers.parse('the waiter confirms the order via "{path_template}"'))
def waiter_confirms_reopened_order(bdd_context: dict[str, object], path_template: str) -> None:
    resp = cast(TestClient, bdd_context["client"]).post(
        resolve_bdd_path(path_template, _persisted_order(bdd_context)),
        headers=auth_header("waiter"),
    )
    bdd_context["last_response"] = resp
    assert resp.status_code == 200


@then("the order confirmed_at timestamp is recorded")
def verify_order_confirmed_at(bdd_context: dict[str, object]) -> None:
    assert _persisted_order(bdd_context).confirmed_at is not None


@then(parsers.parse('a new stock reservation event "{event_type}" is staged in the outbox'))
@then(parsers.parse('the outbox stages "{event_type}"'))
@then(parsers.parse('a compensation event "{event_type}" is staged in the outbox'))
def verify_outbox_stages_event(bdd_context: dict[str, object], event_type: str) -> None:
    assert len(find_outbox_events(cast(BddSessionDouble, bdd_context["session"]), event_type)) >= 1


@when(
    parsers.parse(
        'the chef rejects the tacos with availableQuantity {avail_qty:d} via "{path_template}"'
    )
)
def chef_rejects_with_partial(
    bdd_context: dict[str, object],
    avail_qty: int,
    path_template: str,
) -> None:
    order = _persisted_order(bdd_context)
    payload = {"reason": "Stock available for only partial qty", "availableQuantity": avail_qty}
    resp = cast(TestClient, bdd_context["client"]).patch(
        resolve_bdd_path(path_template, order, order.items[0]),
        headers=auth_header("chef"),
        json=payload,
    )
    bdd_context["last_response"] = resp
    assert resp.status_code == 200


@then(
    parsers.parse(
        "the rejectionDetails reflect requestedQuantity {req_qty:d} "
        "and availableQuantity {avail_qty:d}"
    )
)
def verify_rejection_quantities(
    bdd_context: dict[str, object], req_qty: int, avail_qty: int
) -> None:
    details = _persisted_order(bdd_context).rejection_details
    assert details is not None
    rejected_list = cast(list[dict[str, object]], details["rejectedItems"])
    assert rejected_list[0]["requestedQuantity"] == req_qty
    assert rejected_list[0]["availableQuantity"] == avail_qty


@then("the staged rejection event includes plateId and sourceId")
def verify_rejection_event_aliases(bdd_context: dict[str, object]) -> None:
    events = find_outbox_events(
        cast(BddSessionDouble, bdd_context["session"]), "ordenes.platillo.rechazado_cocina"
    )
    assert events
    assert events[-1].payload.get("plateId") == "dish-tacos"
    assert events[-1].payload.get("sourceId") == "dish-tacos"


@when("the saga timeout watcher executes")
def saga_watcher_executes(bdd_context: dict[str, object]) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])

    def _session_factory() -> AsyncSession:
        return cast(AsyncSession, session)

    watcher = SagaTimeoutWatcher(
        _session_factory, settings=SagaTimeoutSettings(timeout_seconds=900)
    )
    assert asyncio.run(watcher.expire_stale_orders()) == 1


@when(parsers.parse('a late "{event_name}" event is received'))
def late_event_received(bdd_context: dict[str, object], event_name: str) -> None:
    del event_name
    session = cast(BddSessionDouble, bdd_context["session"])
    notifier = cast(OrderNotifier, bdd_context["notifier"])
    msg = StockReservedMessage(
        event_id=uuid4(), order_id=_persisted_order(bdd_context).public_id, reservations=[]
    )
    bdd_context["late_event_result"] = asyncio.run(
        handle_stock_reserved(msg, build_uow(session), notifier=notifier)
    )


@then("the late event is ignored")
def verify_late_event_ignored(bdd_context: dict[str, object]) -> None:
    assert bdd_context["late_event_result"] is False


@when(parsers.parse('the kitchen attempts to start preparation via "{path_template}"'))
@when(parsers.parse('the kitchen attempts to mark the order ready via "{path_template}"'))
def kitchen_attempts_expired_transition(
    bdd_context: dict[str, object], path_template: str
) -> None:
    path = path_template.replace("PATCH ", "").replace(
        "{orderId}", str(_persisted_order(bdd_context).public_id)
    )
    bdd_context["last_response"] = cast(TestClient, bdd_context["client"]).patch(
        path, headers=auth_header("kitchen")
    )


@then(parsers.parse("the system rejects the request with HTTP {status_code:d} Conflict"))
def verify_http_conflict(bdd_context: dict[str, object], status_code: int) -> None:
    assert cast(Response, bdd_context["last_response"]).status_code == status_code
