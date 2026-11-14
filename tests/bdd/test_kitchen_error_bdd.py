"""Step definitions for AT-05 acceptance scenarios (Kitchen Error Order Void).

Satisfies: RF-28, RF-29, RF-30, RF-31, ADR-0012
Covers: AT-05
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
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem, Outbox, ProcessedEvent
from app.db.session import get_session
from app.domain.order_status import ItemStatus, OrderStatus
from app.main import app
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork

scenarios("kitchen_error_void.feature")


def _jwt_segment(payload: dict[str, str | int]) -> str:
    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).rstrip(b"=").decode("ascii")


def _auth_header(role: str = "chef") -> dict[str, str]:
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
    """In-memory session double for BDD execution."""

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

    async def scalar(self, statement: Any) -> Any:
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


class BddUow(UnitOfWork):
    """UnitOfWork bound to BddSessionDouble."""

    def __init__(self, session_factory: SessionFactory, session: BddSessionDouble) -> None:
        super().__init__(session_factory)
        self._session_instance = session

    async def __aenter__(self) -> BddUow:
        session = cast(AsyncSession, self._session_instance)
        self.session = session
        self.orders = OrderRepository(session)
        self.processed_events = ProcessedEventsRepository(session)
        return self


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


@given(parsers.parse('an order exists in "{status}" status with items'))
def an_order_exists_with_items(bdd_context: dict[str, Any], status: str) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus(status),
        total_amount=Decimal("240.00"),
    )
    item1 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-tacos",
        name="Tacos de Cochinita",
        quantity=2,
        unit_price=Decimal("80.00"),
        status=ItemStatus.PENDING,
    )
    item2 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-soup",
        name="Sopa de Lima",
        quantity=1,
        unit_price=Decimal("80.00"),
        status=ItemStatus.READY,
    )
    order.items.extend([item1, item2])
    bdd_context["db"][order.public_id] = order
    bdd_context["order"] = order


@given(parsers.parse('an order exists in "{status}" status'))
def an_order_exists_in_status(bdd_context: dict[str, Any], status: str) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=4,
        status=OrderStatus(status),
        total_amount=Decimal("120.00"),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-salad",
        name="Ensalada Mixta",
        quantity=1,
        unit_price=Decimal("120.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    bdd_context["db"][order.public_id] = order
    bdd_context["order"] = order


@when(parsers.parse('the chef voids the order via "{path_template}" with note "{note}"'))
def chef_voids_order_with_note(bdd_context: dict[str, Any], path_template: str, note: str) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order_id))
    response = client.patch(path, headers=_auth_header("chef"), json={"note": note})
    bdd_context["last_response"] = response


@when(parsers.parse('the administrator voids the order via "{path_template}" with note "{note}"'))
def admin_voids_kitchen_error_with_note(
    bdd_context: dict[str, Any], path_template: str, note: str
) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order_id))
    response = client.patch(path, headers=_auth_header("admin"), json={"note": note})
    bdd_context["last_response"] = response


@when(parsers.parse('the chef attempts to void the order via "{path_template}" without note'))
def chef_attempts_void_without_note(bdd_context: dict[str, Any], path_template: str) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order_id))
    response = client.patch(path, headers=_auth_header("chef"), json={})
    bdd_context["last_response"] = response


@when(
    parsers.parse('a waiter attempts to void the order via "{path_template}" with note "{note}"')
)
def waiter_attempts_kitchen_error_void(
    bdd_context: dict[str, Any], path_template: str, note: str
) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order_id))
    response = client.patch(path, headers=_auth_header("waiter"), json={"note": note})
    bdd_context["last_response"] = response


@when(
    parsers.parse('the chef attempts to void the order via "{path_template}" with note "{note}"')
)
def chef_attempts_void_on_reserved(
    bdd_context: dict[str, Any], path_template: str, note: str
) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order_id))
    response = client.patch(path, headers=_auth_header("chef"), json={"note": note})
    bdd_context["last_response"] = response


@then(parsers.parse('the order status becomes "{expected_status}"'))
def verify_order_status_becomes(bdd_context: dict[str, Any], expected_status: str) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    order = bdd_context["order"]
    assert bdd_context["db"][order.public_id].status == OrderStatus(expected_status)


@then(parsers.parse('the order status remains "{expected_status}"'))
def verify_order_status_remains(bdd_context: dict[str, Any], expected_status: str) -> None:
    order = bdd_context["order"]
    assert bdd_context["db"][order.public_id].status == OrderStatus(expected_status)


@then(parsers.parse('an event "{event_type}" is staged in the outbox with motive "{motive}"'))
def verify_wasted_event_staged(bdd_context: dict[str, Any], event_type: str, motive: str) -> None:
    session: BddSessionDouble = bdd_context["session"]
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1
    event = matching[0]
    assert event.payload.get("motivo") == motive
    assert event.payload.get("reason") == "KITCHEN_ERROR"
    items = cast(list[object], event.payload.get("itemsAfectados", []))
    assert len(items) > 0


@then("no billing event is staged in the outbox")
def verify_no_billing_event_staged(bdd_context: dict[str, Any]) -> None:
    session: BddSessionDouble = bdd_context["session"]
    charge_events = [e for e in session.outbox_rows if e.event_type == "ordenes.cobro.solicitado"]
    assert len(charge_events) == 0


@then("no void event is staged in the outbox")
def verify_no_void_event_staged(bdd_context: dict[str, Any]) -> None:
    session: BddSessionDouble = bdd_context["session"]
    wasted_events = [e for e in session.outbox_rows if e.event_type == "ordenes.comanda.mermada"]
    assert len(wasted_events) == 0


@then(
    parsers.parse(
        'the system rejects the request with HTTP {status_code:d} and error code "{error_code}"'
    )
)
def verify_rejection_code(bdd_context: dict[str, Any], status_code: int, error_code: str) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == error_code


@then(parsers.parse("the system rejects the request with HTTP {status_code:d} Forbidden"))
def verify_http_403(bdd_context: dict[str, Any], status_code: int) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == status_code


@then(parsers.parse("the system rejects the request with HTTP {status_code:d} Conflict"))
def verify_http_409(bdd_context: dict[str, Any], status_code: int) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == status_code
