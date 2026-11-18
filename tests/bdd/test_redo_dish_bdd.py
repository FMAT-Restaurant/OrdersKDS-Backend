"""Step definitions for AT-07 acceptance scenarios (Redo Delivered Dish).

Satisfies: RF-22
Covers: AT-07
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem, Outbox
from app.db.session import get_session
from app.domain.order_status import ItemStatus, OrderStatus
from app.main import app
from app.services.orders import OrderService, get_order_service

pytestmark = [pytest.mark.bdd, pytest.mark.requirement("RF-22")]

scenarios("redo_dish.feature")


def _jwt_segment(payload: dict[str, str | int]) -> str:
    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).rstrip(b"=").decode("ascii")


def _auth_header(role: str = "waiter") -> dict[str, str]:
    header = _jwt_segment({"alg": "HS256", "typ": "JWT"})
    claims = _jwt_segment(
        {
            "user_id": str(uuid4()),
            "role": role,
            "exp": int(datetime.now(UTC).timestamp()) + 300,
        }
    )
    return {"Authorization": f"Bearer {header}.{claims}.c2ln"}


class BddSessionDouble:
    """In-memory AsyncSession test double for AT-07 redo tests."""

    def __init__(self, db: dict[UUID, Order]) -> None:
        self.db = db
        self.outbox_rows: list[Outbox] = []

    def add(self, instance: object) -> None:
        if isinstance(instance, Order):
            self.db[instance.public_id] = instance
        elif isinstance(instance, Outbox):
            self.outbox_rows.append(instance)

    async def scalar(self, statement: object) -> object:
        compile_fn = getattr(statement, "compile", None)
        if callable(compile_fn):
            params = compile_fn().params
            order_id = next((v for v in params.values() if isinstance(v, UUID)), None)
            return self.db.get(order_id) if order_id is not None else None
        return None

    async def scalars(self, _statement: object) -> object:
        orders = list(self.db.values())

        class _Result:
            def unique(self) -> _Result:
                return self

            def all(self) -> list[Order]:
                return orders

        return _Result()

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass


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


@given(
    parsers.parse(
        'an order exists in "DELIVERED" status with a delivered dish '
        'having original notes "{original_note}"'
    )
)
def delivered_order_with_notes(bdd_context: dict[str, object], original_note: str) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=1,
        status=OrderStatus.DELIVERED,
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-tacos",
        name="Tacos al Pastor",
        quantity=2,
        unit_price=Decimal("45.00"),
        status=ItemStatus.DELIVERED,
        notes=original_note,
    )
    order.items.append(item)
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
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
    order = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus.DELIVERED,
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-enchiladas",
        name="Enchiladas Verdes",
        quantity=1,
        unit_price=Decimal("75.00"),
        status=ItemStatus.DELIVERED,
        notes=original_note,
    )
    item.mark_pending_for_redo("Redo: customer requested change")
    order.rejection_details = {"redoOriginalNotes": {str(item.public_id): original_note}}
    order.items.append(item)
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["order"] = order
    bdd_context["item"] = item


@given('an order exists in "DELIVERED" status with a delivered dish')
def delivered_order_simple(bdd_context: dict[str, object]) -> None:
    delivered_order_with_notes(bdd_context, "Well done")


@given('an order exists in "DELIVERED" status with a redone dish in "PENDING" status')
def delivered_order_pending_redo_simple(bdd_context: dict[str, object]) -> None:
    delivered_order_with_pending_redo(bdd_context, "No salt")


@when(
    parsers.parse('the waiter requests to redo the dish via "{path_template}" with note "{note}"')
)
def waiter_requests_redo(
    bdd_context: dict[str, object],
    path_template: str,
    note: str,
) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    item = cast(OrderItem, bdd_context["item"])
    path = (
        path_template.replace("PATCH ", "")
        .replace("{orderId}", str(order.public_id))
        .replace("{itemId}", str(item.public_id))
    )
    response = client.patch(path, headers=_auth_header("waiter"), json={"note": note})
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('the order status remains "{expected_status}"'))
def verify_order_status_remains(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.status == OrderStatus(expected_status)


@then(parsers.parse('the item status becomes "{expected_status}"'))
def verify_item_status(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    item = cast(OrderItem, bdd_context["item"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    persisted_item = next(i for i in persisted.items if i.public_id == item.public_id)
    assert persisted_item.status == ItemStatus(expected_status)


@then(parsers.parse('the item status remains "{expected_status}"'))
def verify_item_status_remains(bdd_context: dict[str, object], expected_status: str) -> None:
    verify_item_status(bdd_context, expected_status)


@then(parsers.parse('the item notes display "{expected_note}"'))
def verify_item_notes(bdd_context: dict[str, object], expected_note: str) -> None:
    order = cast(Order, bdd_context["order"])
    item = cast(OrderItem, bdd_context["item"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    persisted_item = next(i for i in persisted.items if i.public_id == item.public_id)
    assert persisted_item.notes == expected_note


@when(parsers.parse('the kitchen marks the redone dish as ready via "{path_template}"'))
def kitchen_marks_redone_ready(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    item = cast(OrderItem, bdd_context["item"])
    path = (
        path_template.replace("PATCH ", "")
        .replace("{orderId}", str(order.public_id))
        .replace("{itemId}", str(item.public_id))
    )
    response = client.patch(path, headers=_auth_header("kitchen"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('the dish preparation notes are restored to "{expected_note}"'))
def verify_dish_notes_restored(bdd_context: dict[str, object], expected_note: str) -> None:
    order = cast(Order, bdd_context["order"])
    item = cast(OrderItem, bdd_context["item"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    persisted_item = next(i for i in persisted.items if i.public_id == item.public_id)
    assert persisted_item.notes == expected_note


@then(parsers.parse('a dish prepared event "{event_type}" is staged in the outbox with redo flag'))
def verify_dish_prepared_with_redo_flag(
    bdd_context: dict[str, object],
    event_type: str,
) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1
    event = matching[-1]
    assert event.payload.get("isRedo") is True


@when(parsers.parse('the waiter attempts to redo the dish via "{path_template}" with empty note'))
def waiter_attempts_redo_empty_note(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    item = cast(OrderItem, bdd_context["item"])
    path = (
        path_template.replace("PATCH ", "")
        .replace("{orderId}", str(order.public_id))
        .replace("{itemId}", str(item.public_id))
    )
    response = client.patch(path, headers=_auth_header("waiter"), json={"note": "   "})
    bdd_context["last_response"] = response


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
    order = cast(Order, bdd_context["order"])
    item = cast(OrderItem, bdd_context["item"])
    path = (
        path_template.replace("PATCH ", "")
        .replace("{orderId}", str(order.public_id))
        .replace("{itemId}", str(item.public_id))
    )
    response = client.patch(path, headers=_auth_header("waiter"))
    bdd_context["last_response"] = response


@then("the system rejects the request with HTTP 403 Forbidden")
def verify_http_403(bdd_context: dict[str, object]) -> None:
    response = cast(Response, bdd_context["last_response"])
    assert response.status_code == 403
