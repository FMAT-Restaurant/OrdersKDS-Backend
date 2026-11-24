"""Step definitions for AT-07 acceptance scenarios (Redo Delivered Dish).

Satisfies: RF-22
Covers: AT-07
"""

from __future__ import annotations

from collections.abc import Generator
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pytest_bdd import given, parsers, scenarios, then, when

from app.db.models import Order, OrderItem
from app.domain.order_status import ItemStatus, OrderStatus
from tests.bdd._support import (
    BddSessionDouble,
    auth_header,
    bdd_client_override,
    find_outbox_events,
    make_item,
    make_order,
)

pytestmark = [pytest.mark.bdd, pytest.mark.requirement("RF-22")]

scenarios("redo_dish.feature")


@pytest.fixture
def bdd_context() -> dict[str, object]:
    initial_db: dict[UUID, Order] = {}
    return {
        "db": initial_db,
        "order": None,
        "item": None,
        "last_response": None,
        "session": None,
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


@given(
    parsers.parse(
        'an order exists in "DELIVERED" status with a delivered dish '
        'having original notes "{original_note}"'
    )
)
def delivered_order_with_notes(bdd_context: dict[str, object], original_note: str) -> None:
    item = make_item(
        "dish-tacos",
        "Tacos al Pastor",
        quantity=2,
        status=ItemStatus.DELIVERED,
        notes=original_note,
    )
    order = make_order(table_number=1, status=OrderStatus.DELIVERED, items=[item])
    cast(dict[UUID, Order], bdd_context["db"])[order.public_id] = order
    bdd_context["order"] = order
    bdd_context["item"] = item


@given(
    parsers.parse(
        'an order exists in "DELIVERED" status with a redone dish in "PENDING" '
        'status and original notes "{original_note}"'
    )
)
def delivered_order_with_pending_redo(
    bdd_context: dict[str, object],
    original_note: str,
) -> None:
    item = make_item(
        "dish-enchiladas",
        "Enchiladas Verdes",
        quantity=1,
        unit_price="75.00",
        status=ItemStatus.DELIVERED,
        notes=original_note,
    )
    item.mark_pending_for_redo("Redo: customer requested change")
    order = make_order(
        table_number=2,
        status=OrderStatus.DELIVERED,
        items=[item],
        rejection_details={"redoOriginalNotes": {str(item.public_id): original_note}},
    )
    cast(dict[UUID, Order], bdd_context["db"])[order.public_id] = order
    bdd_context["order"] = order
    bdd_context["item"] = item


@given('an order exists in "DELIVERED" status with a delivered dish')
def delivered_order_simple(bdd_context: dict[str, object]) -> None:
    delivered_order_with_notes(bdd_context, "Well done")


@given('an order exists in "DELIVERED" status with a redone dish in "PENDING" status')
def delivered_order_pending_redo_simple(bdd_context: dict[str, object]) -> None:
    delivered_order_with_pending_redo(bdd_context, "No salt")


def _item_path(path_template: str, method: str, bdd_context: dict[str, object]) -> str:
    order = cast(Order, bdd_context["order"])
    item = cast(OrderItem, bdd_context["item"])
    return (
        path_template.replace(f"{method} ", "")
        .replace("{orderId}", str(order.public_id))
        .replace("{itemId}", str(item.public_id))
    )


@when(
    parsers.parse('the waiter requests to redo the dish via "{path_template}" with note "{note}"')
)
def waiter_requests_redo(
    bdd_context: dict[str, object],
    path_template: str,
    note: str,
) -> None:
    client = cast(TestClient, bdd_context["client"])
    response = client.patch(
        _item_path(path_template, "PATCH", bdd_context),
        headers=auth_header("waiter"),
        json={"note": note},
    )
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('the order status remains "{expected_status}"'))
def verify_order_status_remains(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    assert cast(dict[UUID, Order], bdd_context["db"])[order.public_id].status == OrderStatus(
        expected_status
    )


@then(parsers.parse('the item status becomes "{expected_status}"'))
@then(parsers.parse('the item status remains "{expected_status}"'))
def verify_item_status(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    item = cast(OrderItem, bdd_context["item"])
    persisted = cast(dict[UUID, Order], bdd_context["db"])[order.public_id]
    persisted_item = next(i for i in persisted.items if i.public_id == item.public_id)
    assert persisted_item.status == ItemStatus(expected_status)


@then(parsers.parse('the item notes display "{expected_note}"'))
@then(parsers.parse('the dish preparation notes are restored to "{expected_note}"'))
def verify_item_notes(bdd_context: dict[str, object], expected_note: str) -> None:
    order = cast(Order, bdd_context["order"])
    item = cast(OrderItem, bdd_context["item"])
    persisted = cast(dict[UUID, Order], bdd_context["db"])[order.public_id]
    persisted_item = next(i for i in persisted.items if i.public_id == item.public_id)
    assert persisted_item.notes == expected_note


@when(parsers.parse('the kitchen marks the redone dish as ready via "{path_template}"'))
def kitchen_marks_redone_ready(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    response = client.patch(
        _item_path(path_template, "PATCH", bdd_context),
        headers=auth_header("kitchen"),
    )
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('a dish prepared event "{event_type}" is staged in the outbox with redo flag'))
def verify_dish_prepared_with_redo_flag(
    bdd_context: dict[str, object],
    event_type: str,
) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    matching = find_outbox_events(session, event_type)
    assert matching
    payload = matching[-1].payload
    assert payload.get("isRedo") is True
    assert payload.get("plateId") == "dish-enchiladas"
    assert payload.get("sourceId") == "dish-enchiladas"


@when(parsers.parse('the waiter attempts to redo the dish via "{path_template}" with empty note'))
def waiter_attempts_redo_empty_note(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    bdd_context["last_response"] = client.patch(
        _item_path(path_template, "PATCH", bdd_context),
        headers=auth_header("waiter"),
        json={"note": "   "},
    )


@then(
    parsers.parse(
        'the system rejects the request with HTTP {status_code:d} and error code "{error_code}"'
    )
)
def verify_http_error_code(
    bdd_context: dict[str, object],
    status_code: int,
    error_code: str,
) -> None:
    response = cast(Response, bdd_context["last_response"])
    assert response.status_code == status_code
    payload = response.json()
    assert isinstance(payload, dict)
    assert payload["error"]["code"] == error_code


@when(parsers.parse('a waiter attempts to mark the dish ready via "{path_template}"'))
def waiter_attempts_mark_ready(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    bdd_context["last_response"] = client.patch(
        _item_path(path_template, "PATCH", bdd_context),
        headers=auth_header("waiter"),
    )


@then("the system rejects the request with HTTP 403 Forbidden")
def verify_http_403(bdd_context: dict[str, object]) -> None:
    assert cast(Response, bdd_context["last_response"]).status_code == 403
