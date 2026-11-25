"""Step definitions for AT-01 acceptance scenarios (Order Complete Lifecycle).

Satisfies: RF-09, RF-10, RF-12, RF-13, RF-14, RF-15, RF-19, RF-32, RF-39
Covers: AT-01
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pytest_bdd import given, parsers, scenarios, then, when

from app.db.models import Order
from app.domain.order_status import ItemStatus, OrderStatus
from app.messaging.consumers.dining_session import handle_dining_session_created
from app.messaging.consumers.stock_reserved import handle_stock_reserved
from app.messaging.events import DiningSessionCreatedMessage, StockReservedMessage
from app.services.catalog import DefaultCatalogValidator
from tests.bdd._support import (
    BddSessionDouble,
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
    pytest.mark.requirement(
        "RF-09",
        "RF-10",
        "RF-12",
        "RF-13",
        "RF-14",
        "RF-15",
        "RF-19",
        "RF-32",
        "RF-39",
    ),
]

scenarios("order_lifecycle.feature")


def _persisted_order(ctx: dict[str, object]) -> Order:
    order = cast(Order, ctx["order"])
    return cast(dict[UUID, Order], ctx["db"])[order.public_id]


@pytest.fixture
def bdd_context() -> dict[str, object]:
    initial_db: dict[UUID, Order] = {}
    return {
        "db": initial_db,
        "order": None,
        "last_response": None,
        "session": None,
        "waiter_user_id": uuid4(),
        "catalog_validator": DefaultCatalogValidator(),
        "dining_session_id": uuid4(),
        "dining_message": None,
        "last_dining_result": None,
    }


@pytest.fixture(autouse=True)
def bdd_client_and_session(bdd_context: dict[str, object]) -> Generator[TestClient, None, None]:
    session = BddSessionDouble(cast(dict[UUID, Order], bdd_context["db"]))
    bdd_context["session"] = session
    catalog = cast(DefaultCatalogValidator, bdd_context["catalog_validator"])
    with bdd_client_override(session, catalog_validator=catalog) as test_client:
        bdd_context["client"] = test_client
        yield test_client


@when(parsers.parse('the waiter creates a table order draft via "{path}"'))
def waiter_creates_table_order(bdd_context: dict[str, object], path: str) -> None:
    response = cast(TestClient, bdd_context["client"]).post(
        path.replace("POST ", ""),
        headers=auth_header("waiter", cast(UUID, bdd_context["waiter_user_id"])),
        json={"tableNumber": 4, "isTakeaway": False},
    )
    bdd_context["last_response"] = response
    assert response.status_code == 201
    bdd_context["order"] = cast(dict[UUID, Order], bdd_context["db"])[
        UUID(response.json()["orderId"])
    ]


@when(parsers.parse('the waiter creates a takeaway order draft via "{path}"'))
def waiter_creates_takeaway_order(bdd_context: dict[str, object], path: str) -> None:
    response = cast(TestClient, bdd_context["client"]).post(
        path.replace("POST ", ""),
        headers=auth_header("waiter", cast(UUID, bdd_context["waiter_user_id"])),
        json={"isTakeaway": True},
    )
    bdd_context["last_response"] = response
    assert response.status_code == 201
    bdd_context["order"] = cast(dict[UUID, Order], bdd_context["db"])[
        UUID(response.json()["orderId"])
    ]


@then(parsers.parse('the order status becomes "{expected_status}"'))
@then(parsers.parse('the order status remains "{expected_status}"'))
def verify_order_status(bdd_context: dict[str, object], expected_status: str) -> None:
    assert _persisted_order(bdd_context).status == OrderStatus(expected_status)


@then("the created_by user matches the authenticated waiter")
def verify_created_by(bdd_context: dict[str, object]) -> None:
    assert _persisted_order(bdd_context).created_by == cast(UUID, bdd_context["waiter_user_id"])


@then(parsers.parse("the active shift has issued folio {expected_folio:d}"))
def verify_shift_folio_issued(bdd_context: dict[str, object], expected_folio: int) -> None:
    order = _persisted_order(bdd_context)
    session = cast(BddSessionDouble, bdd_context["session"])
    payload = cast(Response, bdd_context["last_response"]).json()
    assert payload["shiftFolio"] == expected_folio
    assert payload["orderNumber"] == expected_folio
    assert len(session.shifts) == 1
    assert session.shifts[0].last_order_folio == expected_folio
    assert order.shift_id == session.shifts[0].id


@then("the order is marked as takeaway")
def verify_takeaway(bdd_context: dict[str, object]) -> None:
    assert _persisted_order(bdd_context).is_takeaway is True


@when(parsers.parse('the waiter adds {count:d} items via "{path_template}"'))
def waiter_adds_items(bdd_context: dict[str, object], count: int, path_template: str) -> None:
    order = _persisted_order(bdd_context)
    items_payload = [
        item_input_payload(
            "dish-tacos",
            "Tacos al Pastor",
            quantity=2,
            unit_price="45.00",
            offer_id="offer-tacos-combo",
        ),
        item_input_payload("dish-soda", "Refresco", unit_price="25.00", offer_id="offer-soda"),
    ][:count]
    resp = cast(TestClient, bdd_context["client"]).patch(
        resolve_bdd_path(path_template, order),
        headers=auth_header("waiter", cast(UUID, bdd_context["waiter_user_id"])),
        json={"items": items_payload},
    )
    bdd_context["last_response"] = resp
    assert resp.status_code == 200


@then(parsers.parse("the order contains {count:d} items"))
def verify_item_count(bdd_context: dict[str, object], count: int) -> None:
    assert len(_persisted_order(bdd_context).items) == count


@when(parsers.parse('the waiter confirms the order via "{path_template}"'))
@when(parsers.parse('the waiter confirms the order again via "{path_template}"'))
def waiter_confirms_order(bdd_context: dict[str, object], path_template: str) -> None:
    order = _persisted_order(bdd_context)
    path = path_template.replace("POST ", "").replace("{orderId}", str(order.public_id))
    resp = cast(TestClient, bdd_context["client"]).post(
        path,
        headers=auth_header("waiter", cast(UUID, bdd_context["waiter_user_id"])),
    )
    bdd_context["last_response"] = resp
    assert resp.status_code == 200


@then("the order confirmed_at timestamp is recorded")
def verify_confirmed_at(bdd_context: dict[str, object]) -> None:
    assert _persisted_order(bdd_context).confirmed_at is not None


@then(parsers.parse('the stock reservation event "{event_type}" is staged in the outbox'))
@then(parsers.parse('the preparation event "{event_type}" is staged in the outbox'))
@then(parsers.parse('a dish prepared event "{event_type}" is staged in the outbox'))
def verify_event_staged(bdd_context: dict[str, object], event_type: str) -> None:
    assert len(find_outbox_events(cast(BddSessionDouble, bdd_context["session"]), event_type)) >= 1


@then(parsers.parse('the stock reservation event "{event_type}" includes plateId and sourceId'))
def verify_created_event_aliases(bdd_context: dict[str, object], event_type: str) -> None:
    events = find_outbox_events(cast(BddSessionDouble, bdd_context["session"]), event_type)
    items = cast(list[dict[str, object]], events[-1].payload["items"])
    assert all(entry.get("plateId") and entry.get("sourceId") for entry in items)


@then("the outbox dish prepared events include plateId and sourceId")
def verify_prepared_event_aliases(bdd_context: dict[str, object]) -> None:
    events = find_outbox_events(
        cast(BddSessionDouble, bdd_context["session"]), "ordenes.platillo.preparado"
    )
    assert events
    assert all(ev.payload.get("plateId") for ev in events)
    assert all(ev.payload.get("sourceId") for ev in events)


@when(parsers.parse('inventory confirms stock reservation via "{event_name}"'))
def inventory_confirms_stock(bdd_context: dict[str, object], event_name: str) -> None:
    del event_name
    session = cast(BddSessionDouble, bdd_context["session"])
    msg = StockReservedMessage(
        event_id=uuid4(), order_id=_persisted_order(bdd_context).public_id, reservations=[]
    )
    assert asyncio.run(handle_stock_reserved(msg, build_uow(session))) is True


@when(parsers.parse('the kitchen starts preparation via "{path_template}"'))
def kitchen_starts_prep(bdd_context: dict[str, object], path_template: str) -> None:
    order = _persisted_order(bdd_context)
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    resp = cast(TestClient, bdd_context["client"]).patch(path, headers=auth_header("kitchen"))
    bdd_context["last_response"] = resp
    assert resp.status_code == 200


@when(parsers.parse('the kitchen marks item {idx:d} as ready via "{path_template}"'))
def kitchen_marks_item_ready(bdd_context: dict[str, object], idx: int, path_template: str) -> None:
    order = _persisted_order(bdd_context)
    path = (
        path_template.replace("PATCH ", "")
        .replace("{orderId}", str(order.public_id))
        .replace("{itemId}", str(order.items[idx - 1].public_id))
    )
    resp = cast(TestClient, bdd_context["client"]).patch(path, headers=auth_header("kitchen"))
    bdd_context["last_response"] = resp
    assert resp.status_code == 200


@then(parsers.parse('item {idx:d} status becomes "{expected_status}"'))
def verify_item_status(bdd_context: dict[str, object], idx: int, expected_status: str) -> None:
    assert _persisted_order(bdd_context).items[idx - 1].status == ItemStatus(expected_status)


@when(parsers.parse('a dining session started event "{event_type}" is received'))
def dining_session_started(bdd_context: dict[str, object], event_type: str) -> None:
    del event_type
    msg = DiningSessionCreatedMessage(
        eventId=uuid4(),
        sessionId=cast(UUID, bdd_context["dining_session_id"]),
        waiterId=cast(UUID, bdd_context["waiter_user_id"]),
        dinersCount=4,
    )
    bdd_context["dining_message"] = msg
    bdd_context["last_dining_result"] = asyncio.run(
        handle_dining_session_created(
            msg, build_uow(cast(BddSessionDouble, bdd_context["session"]))
        )
    )


@then('a new order is created in "CREATED" status linked to the dining session')
def verify_dining_order(bdd_context: dict[str, object]) -> None:
    assert bdd_context["last_dining_result"] is True
    session_id = cast(UUID, bdd_context["dining_session_id"])
    created = next(
        (
            o
            for o in cast(dict[UUID, Order], bdd_context["db"]).values()
            if o.dining_session_id == session_id
        ),
        None,
    )
    assert created is not None
    assert created.status == OrderStatus.CREATED
    bdd_context["order"] = created


@when("the same dining session started event is redelivered")
def dining_session_redelivered(bdd_context: dict[str, object]) -> None:
    msg = cast(DiningSessionCreatedMessage, bdd_context["dining_message"])
    bdd_context["last_dining_result"] = asyncio.run(
        handle_dining_session_created(
            msg, build_uow(cast(BddSessionDouble, bdd_context["session"]))
        )
    )


@then("the duplicate event is ignored without creating another order")
def verify_duplicate_dining_ignored(bdd_context: dict[str, object]) -> None:
    assert bdd_context["last_dining_result"] is False
    session_id = cast(UUID, bdd_context["dining_session_id"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    assert len([o for o in db.values() if o.dining_session_id == session_id]) == 1


@given(parsers.parse('an order exists in "{status}" status with "confirmed_at" timestamp set'))
def confirmed_order_exists(bdd_context: dict[str, object], status: str) -> None:
    order = make_order(
        table_number=3,
        status=OrderStatus(status),
        confirmed_at=datetime.now(UTC),
        confirmed_by=uuid4(),
        items=[make_item("dish-sopa", "Sopa Azteca", unit_price="50.00")],
    )
    cast(dict[UUID, Order], bdd_context["db"])[order.public_id] = order
    bdd_context["order"] = order


@then("the system accepts the request with HTTP 200 OK")
def verify_http_200(bdd_context: dict[str, object]) -> None:
    assert cast(Response, bdd_context["last_response"]).status_code == 200


@then(parsers.parse('no duplicate "{event_type}" event is staged in the outbox'))
def verify_no_duplicate_outbox(bdd_context: dict[str, object], event_type: str) -> None:
    assert len(find_outbox_events(cast(BddSessionDouble, bdd_context["session"]), event_type)) == 0


@given('a draft order exists in "CREATED" status with 1 item')
def draft_order_one_item(bdd_context: dict[str, object]) -> None:
    order = make_order(
        table_number=2,
        status=OrderStatus.CREATED,
        items=[make_item("dish-tacos", "Tacos al Pastor")],
    )
    cast(dict[UUID, Order], bdd_context["db"])[order.public_id] = order
    bdd_context["order"] = order


@when(
    parsers.parse(
        'the waiter updates the order items via "{path_template}" with {count:d} new items'
    )
)
def waiter_updates_items(bdd_context: dict[str, object], path_template: str, count: int) -> None:
    order = _persisted_order(bdd_context)
    items_payload = [
        item_input_payload("dish-gringa", "Gringa de Pastor", quantity=2, unit_price="60.00"),
        item_input_payload("dish-agua", "Agua Fresca", unit_price="20.00"),
    ][:count]
    resp = cast(TestClient, bdd_context["client"]).patch(
        resolve_bdd_path(path_template, order),
        headers=auth_header("waiter"),
        json={"items": items_payload},
    )
    bdd_context["last_response"] = resp
    assert resp.status_code == 200


@then("the order total amount is recalculated")
def verify_total_recalculated(bdd_context: dict[str, object]) -> None:
    assert _persisted_order(bdd_context).total_amount == (Decimal("60.00") * 2) + Decimal("20.00")


@when(
    parsers.parse(
        'the waiter attempts to update items with an empty items list via "{path_template}"'
    )
)
def waiter_updates_empty(bdd_context: dict[str, object], path_template: str) -> None:
    bdd_context["last_response"] = cast(TestClient, bdd_context["client"]).patch(
        resolve_bdd_path(path_template, _persisted_order(bdd_context)),
        headers=auth_header("waiter"),
        json={"items": []},
    )


@when(parsers.parse('the waiter attempts to update items via "{path_template}"'))
def waiter_updates_confirmed(bdd_context: dict[str, object], path_template: str) -> None:
    payload = {"items": [item_input_payload("dish-tacos", "Tacos al Pastor")]}
    bdd_context["last_response"] = cast(TestClient, bdd_context["client"]).patch(
        resolve_bdd_path(path_template, _persisted_order(bdd_context)),
        headers=auth_header("waiter"),
        json=payload,
    )


@then("the system rejects the request with HTTP 422 Unprocessable Entity")
def verify_http_422(bdd_context: dict[str, object]) -> None:
    assert cast(Response, bdd_context["last_response"]).status_code == 422


@then("the system rejects the request with HTTP 409 Conflict")
def verify_http_409(bdd_context: dict[str, object]) -> None:
    assert cast(Response, bdd_context["last_response"]).status_code == 409


@then("the order items are not modified")
def verify_items_unmodified(bdd_context: dict[str, object]) -> None:
    assert len(_persisted_order(bdd_context).items) == 1
