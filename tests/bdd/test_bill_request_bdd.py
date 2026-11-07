"""Step definitions for AT-10 acceptance scenarios (Bill Request and Payment Flow).

Satisfies: RF-04, RF-18, RF-20, RF-21
Covers: AT-10
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final, cast
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

scenarios("bill_request.feature")

_EVENT_BILL_REQUESTED: Final[str] = "ordenes.cuenta.solicitada"
_STATUS_DELIVERED: Final[str] = "DELIVERED"
_STATUS_IN_PREPARATION: Final[str] = "IN_PREPARATION"


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
    order_id = uuid4()
    session_id = uuid4()
    waiter_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=5,
        status=OrderStatus(status),
        dining_session_id=session_id,
        responsible_waiter_id=waiter_id,
        total_amount=Decimal("220.00"),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-enchiladas-suizas",
        name="Enchiladas Suizas",
        quantity=2,
        unit_price=Decimal("110.00"),
        modifiers=["gratinadas"],
        notes="Salsa no muy picante",
        status=ItemStatus.DELIVERED,
    )
    order.items.append(item)
    bdd_context["db"][order.public_id] = order
    bdd_context["order"] = order


@given(parsers.parse('an order exists in "{status}" status'))
def an_order_exists_in_status(bdd_context: dict[str, Any], status: str) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=6,
        status=OrderStatus(status),
        total_amount=Decimal("120.00"),
    )
    bdd_context["db"][order.public_id] = order
    bdd_context["order"] = order


@when(parsers.parse('the waiter requests the bill via "{path_template}"'))
def waiter_requests_bill(bdd_context: dict[str, Any], path_template: str) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("POST ", "").replace("{orderId}", str(order_id))
    response = client.post(path, headers=_auth_header("waiter"))
    bdd_context["last_response"] = response


@when(parsers.parse('the administrator requests the bill via "{path_template}"'))
def admin_requests_bill(bdd_context: dict[str, Any], path_template: str) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("POST ", "").replace("{orderId}", str(order_id))
    response = client.post(path, headers=_auth_header("admin"))
    bdd_context["last_response"] = response


@when(parsers.parse('a kitchen user attempts to request the bill via "{path_template}"'))
def kitchen_attempts_request_bill(bdd_context: dict[str, Any], path_template: str) -> None:
    client: TestClient = bdd_context["client"]
    order_id = bdd_context["order"].public_id
    path = path_template.replace("POST ", "").replace("{orderId}", str(order_id))
    response = client.post(path, headers=_auth_header("kitchen"))
    bdd_context["last_response"] = response


@when(parsers.parse('the waiter attempts to request the bill via "{path_template}"'))
def waiter_attempts_request_bill(bdd_context: dict[str, Any], path_template: str) -> None:
    waiter_requests_bill(bdd_context, path_template)


@when(parsers.parse('payment completion event "{event_type}" is processed'))
def payment_completion_is_processed(bdd_context: dict[str, Any], event_type: str) -> None:
    order: Order = bdd_context["order"]
    session: BddSessionDouble = bdd_context["session"]
    uow = BddUow(lambda: session, session)  # type: ignore[arg-type, return-value]
    payment_event = PaymentCompletedMessage(
        eventId=uuid4(),
        orderId=order.public_id,
        sessionId=order.dining_session_id,
        montoTotal=Decimal("220.00"),
        totalPaid=Decimal("220.00"),
        status="PAID",
    )
    applied = asyncio.run(handle_payment_completed(payment_event, uow))
    assert applied is True


@then(parsers.parse('the order status remains "{expected_status}"'))
def verify_order_status_remains(bdd_context: dict[str, Any], expected_status: str) -> None:
    order = bdd_context["order"]
    assert bdd_context["db"][order.public_id].status == OrderStatus(expected_status)


@then(parsers.parse('the order status transitions to "{expected_status}"'))
def verify_order_status_transitions(bdd_context: dict[str, Any], expected_status: str) -> None:
    order = bdd_context["order"]
    assert bdd_context["db"][order.public_id].status == OrderStatus(expected_status)


@then(parsers.parse('an event "{event_type}" is staged in the outbox'))
def verify_bill_event_staged(bdd_context: dict[str, Any], event_type: str) -> None:
    session: BddSessionDouble = bdd_context["session"]
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1


@then("the bill event contains frozen items with prices and dual identifiers")
def verify_bill_event_details(bdd_context: dict[str, Any]) -> None:
    session: BddSessionDouble = bdd_context["session"]
    order = bdd_context["order"]
    matching = [e for e in session.outbox_rows if e.event_type == _EVENT_BILL_REQUESTED]
    assert len(matching) >= 1
    event = matching[0]
    payload = event.payload
    assert payload.get("orderId") == str(order.public_id)
    assert payload.get("sessionId") == str(order.dining_session_id)
    assert payload.get("status") == _STATUS_DELIVERED

    raw_total = payload.get("totalAmount")
    assert isinstance(raw_total, (int, float, str))
    assert float(raw_total) == 220.0

    items = cast(list[dict[str, Any]], payload.get("items", []))
    assert len(items) == 1
    assert items[0].get("plateId") == "dish-enchiladas-suizas"
    assert items[0].get("name") == "Enchiladas Suizas"
    assert items[0].get("quantity") == 2

    raw_unit_price = items[0].get("unitPrice")
    assert isinstance(raw_unit_price, (int, float, str))
    assert float(raw_unit_price) == 110.0


@then(parsers.parse("the system rejects the request with HTTP {status_code:d} Forbidden"))
def verify_http_403(bdd_context: dict[str, Any], status_code: int) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == status_code


@then(parsers.parse("the system rejects the request with HTTP {status_code:d} Conflict"))
def verify_http_409(bdd_context: dict[str, Any], status_code: int) -> None:
    response = bdd_context["last_response"]
    assert response.status_code == status_code


@then("no bill event is staged in the outbox")
def verify_no_bill_event_staged(bdd_context: dict[str, Any]) -> None:
    session: BddSessionDouble = bdd_context["session"]
    emitted = {e.event_type for e in session.outbox_rows}
    assert _EVENT_BILL_REQUESTED not in emitted
