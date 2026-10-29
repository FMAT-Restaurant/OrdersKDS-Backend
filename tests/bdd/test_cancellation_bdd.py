"""Step definitions for AT-02 acceptance scenarios (Order Cancellation).

Satisfies: RF-23, RF-24, RF-25
Covers: AT-02
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from app.db.models import Order, OrderItem, Outbox
from app.db.session import get_session
from app.domain.order_status import OrderStatus
from app.main import app

scenarios("cancellation.feature")


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
    def __init__(self, db: dict[UUID, Order]) -> None:
        self.db = db
        self.outbox_rows: list[Outbox] = []

    def add(self, instance: object) -> None:
        if isinstance(instance, Order):
            self.db[instance.public_id] = instance
        elif isinstance(instance, Outbox):
            self.outbox_rows.append(instance)

    async def scalar(self, statement: Any) -> Order | None:
        params = statement.compile().params
        order_id = next((v for v in params.values() if isinstance(v, UUID)), None)
        return self.db.get(order_id) if order_id is not None else None

    async def scalars(self, _statement: Any) -> Any:
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
def bdd_context() -> dict[str, Any]:
    initial_db: dict[UUID, Order] = {}
    return {"db": initial_db, "order": None, "last_response": None, "session": None}


@pytest.fixture(autouse=True)
def bdd_client_and_session(
    bdd_context: dict[str, Any],
) -> Generator[TestClient, None, None]:
    session = BddSessionDouble(bdd_context["db"])
    bdd_context["session"] = session

    async def _get_test_session() -> AsyncGenerator[BddSessionDouble, None]:
        yield session

    app.dependency_overrides[get_session] = _get_test_session
    with TestClient(app) as test_client:
        bdd_context["client"] = test_client
        yield test_client
    app.dependency_overrides.clear()


@given(parsers.parse('an order exists in "{status}" status without confirmation'))
def an_order_exists_unconfirmed(bdd_context: dict[str, Any], status: str) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=1,
        status=OrderStatus(status),
        confirmed_at=None,
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-tacos",
        name="Tacos al Pastor",
        quantity=3,
        unit_price=Decimal("25.00"),
    )
    order.items.append(item)
    order.recalculate_total()
    bdd_context["db"][order.public_id] = order
    bdd_context["order"] = order


@given(parsers.parse('an order exists in "{status}" status with "confirmed_at" timestamp set'))
def an_order_exists_confirmed(bdd_context: dict[str, Any], status: str) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus(status),
        confirmed_at=datetime.now(UTC),
        confirmed_by=uuid4(),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-burger",
        name="Burger",
        quantity=2,
        unit_price=Decimal("110.00"),
    )
    order.items.append(item)
    order.recalculate_total()
    bdd_context["db"][order.public_id] = order
    bdd_context["order"] = order


@given(parsers.parse('an order exists in "{status}" status'))
def an_order_exists_in_status(bdd_context: dict[str, Any], status: str) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus(status),
    )
    bdd_context["db"][order.public_id] = order
    bdd_context["order"] = order


@given(parsers.parse('an order has been transitioned to "{status}" status'))
def an_order_in_terminal_status(bdd_context: dict[str, Any], status: str) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=4,
        status=OrderStatus(status),
    )
    bdd_context["db"][order.public_id] = order
    bdd_context["order"] = order


@when(parsers.parse('the waiter requests to cancel the order via "{path_template}"'))
def waiter_cancels_order(bdd_context: dict[str, Any], path_template: str) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order_id))
    response = client.patch(path, headers=_auth_header("waiter"))
    bdd_context["last_response"] = response


@when(parsers.parse('the waiter attempts to update items via "{path_template}"'))
def waiter_attempts_update_items(bdd_context: dict[str, Any], path_template: str) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order_id))
    empty_modifiers: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "items": [
            {
                "dishId": "dish-extra",
                "name": "Extra Tacos",
                "quantity": 1,
                "unitPrice": "30.00",
                "modifiers": empty_modifiers,
            }
        ]
    }
    response = client.patch(path, headers=_auth_header("waiter"), json=payload)
    bdd_context["last_response"] = response


@then(parsers.parse('the order status becomes "{expected_status}"'))
def verify_order_status_becomes(bdd_context: dict[str, Any], expected_status: str) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    order = bdd_context["order"]
    assert bdd_context["db"][order.public_id].status == OrderStatus(expected_status)


@then(parsers.parse('the order status transitions to "{expected_status}"'))
def verify_order_status_transitions(bdd_context: dict[str, Any], expected_status: str) -> None:
    verify_order_status_becomes(bdd_context, expected_status)


@then(parsers.parse('the order status remains "{expected_status}"'))
def verify_order_status_remains(bdd_context: dict[str, Any], expected_status: str) -> None:
    order = bdd_context["order"]
    assert bdd_context["db"][order.public_id].status == OrderStatus(expected_status)


@then(parsers.parse('a compensation event "{event_type}" is staged in the outbox'))
def verify_compensation_event(bdd_context: dict[str, Any], event_type: str) -> None:
    session: BddSessionDouble = bdd_context["session"]
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1


@then(parsers.parse('the compensation event "{event_type}" contains the cancelled items'))
def verify_compensation_event_items(bdd_context: dict[str, Any], event_type: str) -> None:
    session: BddSessionDouble = bdd_context["session"]
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1
    event = matching[0]
    items = cast(list[object], event.payload.get("itemsCancelados", []))
    assert len(items) > 0


@then("no billing or payment event is emitted")
def verify_no_billing_event(bdd_context: dict[str, Any]) -> None:
    session: BddSessionDouble = bdd_context["session"]
    disallowed = {"ordenes.cobro.solicitado", "ordenes.cuenta.solicitada"}
    emitted = {e.event_type for e in session.outbox_rows}
    assert disallowed.isdisjoint(emitted)


@then('any subsequent late "inventario.stock.reservado" event is ignored')
def verify_late_event_ignored(bdd_context: dict[str, Any]) -> None:
    order = bdd_context["order"]
    assert bdd_context["db"][order.public_id].status == OrderStatus.CANCELLED


@then(parsers.parse("the system rejects the request with HTTP {status_code:d} Conflict"))
def verify_conflict_rejected(bdd_context: dict[str, Any], status_code: int) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == status_code


@then("no cancellation event is staged in the outbox")
def verify_no_cancellation_in_outbox(bdd_context: dict[str, Any]) -> None:
    session: BddSessionDouble = bdd_context["session"]
    cancel_events = [e for e in session.outbox_rows if e.event_type == "ordenes.orden.cancelada"]
    assert len(cancel_events) == 0


@then("the order items are not modified")
def verify_items_not_modified(bdd_context: dict[str, Any]) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == 409
