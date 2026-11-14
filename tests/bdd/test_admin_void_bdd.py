"""Step definitions for AT-04 acceptance scenarios (Administrative Order Void).

Satisfies: RF-21, RF-26, RF-27, ADR-0012
Covers: AT-04
"""

from __future__ import annotations

import asyncio
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
from app.messaging.consumers.payment_completed import handle_payment_completed
from app.messaging.events import PaymentCompletedMessage
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork

scenarios("admin_void.feature")


def _jwt_segment(payload: dict[str, str | int]) -> str:
    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).rstrip(b"=").decode("ascii")


def _auth_header(role: str = "admin") -> dict[str, str]:
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
        self.processed: set[tuple[UUID, UUID]] = set()

    def add(self, instance: object) -> None:
        if isinstance(instance, Order):
            self.db[instance.public_id] = instance
        elif isinstance(instance, Outbox):
            self.outbox_rows.append(instance)
        elif isinstance(instance, ProcessedEvent):
            self.processed.add((instance.order_id, instance.event_id))

    async def scalar(self, statement: Any) -> Any:
        statement_str = str(statement)
        params = statement.compile().params
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
        total_amount=Decimal("160.00"),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-pozole",
        name="Pozole Rojo",
        quantity=2,
        unit_price=Decimal("80.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    bdd_context["db"][order.public_id] = order
    bdd_context["order"] = order


@given(parsers.parse('an order exists in "{status}" status'))
def an_order_exists_in_status(bdd_context: dict[str, Any], status: str) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=4,
        status=OrderStatus(status),
        total_amount=Decimal("100.00"),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-sopa",
        name="Sopa de Lima",
        quantity=1,
        unit_price=Decimal("100.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    bdd_context["db"][order.public_id] = order
    bdd_context["order"] = order


@when(parsers.parse('the administrator voids the order via "{path_template}"'))
def admin_voids_order(bdd_context: dict[str, Any], path_template: str) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order_id))
    response = client.patch(path, headers=_auth_header("admin"))
    bdd_context["last_response"] = response


@when(parsers.parse('a waiter attempts to void the order via "{path_template}"'))
def waiter_attempts_void_order(bdd_context: dict[str, Any], path_template: str) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order_id))
    response = client.patch(path, headers=_auth_header("waiter"))
    bdd_context["last_response"] = response


@when(parsers.parse('the administrator attempts to void the order via "{path_template}"'))
def admin_attempts_void_order(bdd_context: dict[str, Any], path_template: str) -> None:
    admin_voids_order(bdd_context, path_template)


@when(parsers.parse('payment completion event "{event_type}" is processed'))
def payment_completion_is_processed(bdd_context: dict[str, Any], event_type: str) -> None:
    order: Order = bdd_context["order"]
    session: BddSessionDouble = bdd_context["session"]
    uow = BddUow(lambda: session, session)  # type: ignore[arg-type, return-value]
    payment_event = PaymentCompletedMessage(
        eventId=uuid4(),
        orderId=order.public_id,
        montoTotal=Decimal("100.00"),
        totalPaid=Decimal("100.00"),
        status="PAID",
    )
    applied = asyncio.run(handle_payment_completed(payment_event, uow))
    assert applied is True


@then(parsers.parse('the order status becomes "{expected_status}"'))
def verify_order_status_becomes(bdd_context: dict[str, Any], expected_status: str) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    order = bdd_context["order"]
    assert bdd_context["db"][order.public_id].status == OrderStatus(expected_status)


@then(parsers.parse('the order status transitions to "{expected_status}"'))
def verify_order_status_transitions(bdd_context: dict[str, Any], expected_status: str) -> None:
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
    items = cast(list[object], event.payload.get("itemsAfectados", []))
    assert len(items) > 0


@then(parsers.parse('a billing event "{event_type}" is staged in the outbox with frozen prices'))
def verify_charge_event_staged(bdd_context: dict[str, Any], event_type: str) -> None:
    session: BddSessionDouble = bdd_context["session"]
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1
    event = matching[0]
    assert event.payload.get("status") == "WASTED"
    raw_total = event.payload.get("totalAmount")
    assert isinstance(raw_total, (int, float, str))
    assert float(raw_total) == 160.0
    items = cast(list[dict[str, Any]], event.payload.get("items", []))
    assert len(items) > 0
    raw_unit_price = items[0].get("unitPrice")
    assert isinstance(raw_unit_price, (int, float, str))
    assert float(raw_unit_price) == 80.0


@then(parsers.parse("the system rejects the request with HTTP {status_code:d} Forbidden"))
def verify_http_403(bdd_context: dict[str, Any], status_code: int) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == status_code


@then(parsers.parse("the system rejects the request with HTTP {status_code:d} Conflict"))
def verify_http_409(bdd_context: dict[str, Any], status_code: int) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == status_code


@then("no void or charge event is staged in the outbox")
def verify_no_void_or_charge_events(bdd_context: dict[str, Any]) -> None:
    session: BddSessionDouble = bdd_context["session"]
    disallowed = {"ordenes.comanda.mermada", "ordenes.cobro.solicitado"}
    emitted = {e.event_type for e in session.outbox_rows}
    assert disallowed.isdisjoint(emitted)
