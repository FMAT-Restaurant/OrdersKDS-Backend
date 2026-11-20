"""Step definitions for AT-01 acceptance scenarios (Order Complete Lifecycle).

Satisfies: RF-09, RF-10, RF-12, RF-13, RF-14, RF-15, RF-19, RF-32, RF-39
Covers: AT-01
"""

from __future__ import annotations

import asyncio
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

from app.db.models import Order, OrderItem, Outbox, ProcessedEvent
from app.db.session import get_session
from app.domain.order_status import ItemStatus, OrderStatus
from app.main import app
from app.messaging.consumers.dining_session import handle_dining_session_created
from app.messaging.consumers.stock_reserved import handle_stock_reserved
from app.messaging.events import DiningSessionCreatedMessage, StockReservedMessage
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork
from app.services.catalog import DefaultCatalogValidator, get_catalog_validator
from app.services.orders import OrderService, get_order_service

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


def _jwt_segment(payload: dict[str, str | int]) -> str:
    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).rstrip(b"=").decode("ascii")


def _auth_header(role: str = "waiter", user_id: UUID | None = None) -> dict[str, str]:
    header = _jwt_segment({"alg": "HS256", "typ": "JWT"})
    caller_uuid = user_id or uuid4()
    claims = _jwt_segment(
        {
            "user_id": str(caller_uuid),
            "role": role,
            "exp": int(datetime.now(UTC).timestamp()) + 300,
        }
    )
    return {"Authorization": f"Bearer {header}.{claims}.c2ln"}


class BddSessionDouble:
    """In-memory AsyncSession test double for AT-01 BDD lifecycle tests."""

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

    async def flush(self) -> None:
        pass

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
            if "orders.dining_session_id =" in statement_str.lower():
                session_id = next(
                    (
                        v
                        for k, v in params.items()
                        if "dining_session" in k and isinstance(v, UUID)
                    ),
                    None,
                )
                if session_id is None:
                    session_id = next((v for v in params.values() if isinstance(v, UUID)), None)
                return next(
                    (o for o in self.db.values() if o.dining_session_id == session_id),
                    None,
                )
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


class BddUow(UnitOfWork):
    """Transactional UnitOfWork double over the BddSessionDouble."""

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
def bdd_client_and_session(
    bdd_context: dict[str, object],
) -> Generator[TestClient, None, None]:
    db = cast(dict[UUID, Order], bdd_context["db"])
    session = BddSessionDouble(db)
    bdd_context["session"] = session

    async def _get_test_session() -> AsyncGenerator[BddSessionDouble, None]:
        yield session

    app.dependency_overrides[get_session] = _get_test_session
    app.dependency_overrides[get_catalog_validator] = lambda: cast(
        DefaultCatalogValidator, bdd_context["catalog_validator"]
    )
    app.dependency_overrides[get_order_service] = lambda: OrderService(
        session=cast(AsyncSession, session),
    )

    with TestClient(app) as test_client:
        bdd_context["client"] = test_client
        yield test_client
    app.dependency_overrides.clear()


@when(parsers.parse('the waiter creates a table order draft via "{path}"'))
def waiter_creates_table_order_via_api(bdd_context: dict[str, object], path: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    waiter_id = cast(UUID, bdd_context["waiter_user_id"])
    endpoint = path.replace("POST ", "")
    payload = {"tableNumber": 4, "isTakeaway": False}
    response = client.post(endpoint, headers=_auth_header("waiter", waiter_id), json=payload)
    bdd_context["last_response"] = response
    assert response.status_code == 201
    created_id = UUID(response.json()["orderId"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    bdd_context["order"] = db[created_id]


@when(parsers.parse('the waiter creates a takeaway order draft via "{path}"'))
def waiter_creates_takeaway_order_via_api(bdd_context: dict[str, object], path: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    waiter_id = cast(UUID, bdd_context["waiter_user_id"])
    endpoint = path.replace("POST ", "")
    payload = {"isTakeaway": True}
    response = client.post(endpoint, headers=_auth_header("waiter", waiter_id), json=payload)
    bdd_context["last_response"] = response
    assert response.status_code == 201
    created_id = UUID(response.json()["orderId"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    bdd_context["order"] = db[created_id]


@then(parsers.parse('the order status becomes "{expected_status}"'))
def verify_order_status_becomes(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.status == OrderStatus(expected_status)


@then("the created_by user matches the authenticated waiter")
def verify_created_by_matches(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["order"])
    waiter_id = cast(UUID, bdd_context["waiter_user_id"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.created_by == waiter_id


@then("the order is marked as takeaway")
def verify_order_is_takeaway(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.is_takeaway is True


@when(parsers.parse('the waiter adds {count:d} items via "{path_template}"'))
def waiter_adds_items_via_api(
    bdd_context: dict[str, object],
    count: int,
    path_template: str,
) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    payload = {
        "items": [
            {
                "dishId": "dish-tacos",
                "name": "Tacos al Pastor",
                "quantity": 2,
                "unitPrice": "45.00",
                "modifiers": [],
            },
            {
                "dishId": "dish-soda",
                "name": "Refresco",
                "quantity": 1,
                "unitPrice": "25.00",
                "modifiers": [],
            },
        ][:count]
    }
    response = client.patch(path, headers=_auth_header("waiter"), json=payload)
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse("the order contains {count:d} items"))
def verify_order_contains_count_items(bdd_context: dict[str, object], count: int) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert len(persisted.items) == count


@when(parsers.parse('the waiter confirms the order via "{path_template}"'))
def waiter_confirms_order(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    path = path_template.replace("POST ", "").replace("{orderId}", str(order.public_id))
    response = client.post(path, headers=_auth_header("waiter"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then("the order confirmed_at timestamp is recorded")
def verify_confirmed_at_set(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.confirmed_at is not None


@then(parsers.parse('the stock reservation event "{event_type}" is staged in the outbox'))
def verify_stock_reservation_staged(bdd_context: dict[str, object], event_type: str) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1


@when(parsers.parse('inventory confirms stock reservation via "{event_name}"'))
def inventory_confirms_stock(bdd_context: dict[str, object], event_name: str) -> None:
    order = cast(Order, bdd_context["order"])
    session = cast(BddSessionDouble, bdd_context["session"])
    uow = BddUow(lambda: cast(AsyncSession, session), session)
    msg = StockReservedMessage(
        event_id=uuid4(),
        order_id=order.public_id,
        reservations=[],
    )
    res = asyncio.run(handle_stock_reserved(msg, uow))
    assert res is True


@when(parsers.parse('the kitchen starts preparation via "{path_template}"'))
def kitchen_starts_prep(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    response = client.patch(path, headers=_auth_header("kitchen"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('the preparation event "{event_type}" is staged in the outbox'))
def verify_prep_event_staged(bdd_context: dict[str, object], event_type: str) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1


@when(parsers.parse('the kitchen marks item 1 as ready via "{path_template}"'))
def kitchen_marks_item_1_ready(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    item_1 = persisted.items[0]
    path = (
        path_template.replace("PATCH ", "")
        .replace("{orderId}", str(persisted.public_id))
        .replace("{itemId}", str(item_1.public_id))
    )
    response = client.patch(path, headers=_auth_header("kitchen"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('item 1 status becomes "{expected_status}"'))
def verify_item_1_ready(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.items[0].status == ItemStatus(expected_status)


@then(parsers.parse('the order status remains "{expected_status}"'))
def verify_order_status_remains(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.status == OrderStatus(expected_status)


@then(parsers.parse('a dish prepared event "{event_type}" is staged in the outbox'))
def verify_dish_prepared_staged(bdd_context: dict[str, object], event_type: str) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1


@when(parsers.parse('the kitchen marks item 2 as ready via "{path_template}"'))
def kitchen_marks_item_2_ready(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    item_2 = persisted.items[1]
    path = (
        path_template.replace("PATCH ", "")
        .replace("{orderId}", str(persisted.public_id))
        .replace("{itemId}", str(item_2.public_id))
    )
    response = client.patch(path, headers=_auth_header("kitchen"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('item 2 status becomes "{expected_status}"'))
def verify_item_2_delivered(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.items[1].status == ItemStatus(expected_status)


@when(parsers.parse('a dining session started event "{event_type}" is received'))
def dining_session_started_received(bdd_context: dict[str, object], event_type: str) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    uow = BddUow(lambda: cast(AsyncSession, session), session)
    session_id = cast(UUID, bdd_context["dining_session_id"])
    waiter_id = cast(UUID, bdd_context["waiter_user_id"])
    msg = DiningSessionCreatedMessage(
        eventId=uuid4(),
        sessionId=session_id,
        waiterId=waiter_id,
        dinersCount=4,
    )
    bdd_context["dining_message"] = msg
    res = asyncio.run(handle_dining_session_created(msg, uow))
    bdd_context["last_dining_result"] = res


@then('a new order is created in "CREATED" status linked to the dining session')
def verify_dining_order_created(bdd_context: dict[str, object]) -> None:
    assert bdd_context["last_dining_result"] is True
    session_id = cast(UUID, bdd_context["dining_session_id"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    created_order = next((o for o in db.values() if o.dining_session_id == session_id), None)
    assert created_order is not None
    assert created_order.status == OrderStatus.CREATED
    bdd_context["order"] = created_order


@when("the same dining session started event is redelivered")
def dining_session_redelivered(bdd_context: dict[str, object]) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    uow = BddUow(lambda: cast(AsyncSession, session), session)
    msg = cast(DiningSessionCreatedMessage, bdd_context["dining_message"])
    res = asyncio.run(handle_dining_session_created(msg, uow))
    bdd_context["last_dining_result"] = res


@then("the duplicate event is ignored without creating another order")
def verify_duplicate_dining_ignored(bdd_context: dict[str, object]) -> None:
    assert bdd_context["last_dining_result"] is False
    session_id = cast(UUID, bdd_context["dining_session_id"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    matching = [o for o in db.values() if o.dining_session_id == session_id]
    assert len(matching) == 1


@given(parsers.parse('an order exists in "{status}" status with "confirmed_at" timestamp set'))
def confirmed_order_exists(bdd_context: dict[str, object], status: str) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus(status),
        confirmed_at=datetime.now(UTC),
        confirmed_by=uuid4(),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-sopa",
        name="Sopa Azteca",
        quantity=1,
        unit_price=Decimal("50.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["order"] = order


@when(parsers.parse('the waiter confirms the order again via "{path_template}"'))
def waiter_confirms_order_again(bdd_context: dict[str, object], path_template: str) -> None:
    waiter_confirms_order(bdd_context, path_template)


@then("the system accepts the request with HTTP 200 OK")
def verify_http_200(bdd_context: dict[str, object]) -> None:
    response = cast(Response, bdd_context["last_response"])
    assert response.status_code == 200


@then(parsers.parse('no duplicate "{event_type}" event is staged in the outbox'))
def verify_no_duplicate_outbox_event(
    bdd_context: dict[str, object],
    event_type: str,
) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) == 0


@given('a draft order exists in "CREATED" status with 1 item')
def draft_order_with_one_item(bdd_context: dict[str, object]) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus.CREATED,
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-tacos",
        name="Tacos al Pastor",
        quantity=1,
        unit_price=Decimal("45.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["order"] = order


@when(
    parsers.parse(
        'the waiter updates the order items via "{path_template}" with {count:d} new items'
    )
)
def waiter_updates_items(
    bdd_context: dict[str, object],
    path_template: str,
    count: int,
) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    payload = {
        "items": [
            {
                "dishId": "dish-gringa",
                "name": "Gringa de Pastor",
                "quantity": 2,
                "unitPrice": "60.00",
                "modifiers": [],
            },
            {
                "dishId": "dish-agua",
                "name": "Agua Fresca",
                "quantity": 1,
                "unitPrice": "20.00",
                "modifiers": [],
            },
        ][:count]
    }
    response = client.patch(path, headers=_auth_header("waiter"), json=payload)
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then("the order total amount is recalculated")
def verify_order_total_recalculated(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    expected = (Decimal("60.00") * 2) + Decimal("20.00")
    assert persisted.total_amount == expected


@when(
    parsers.parse(
        'the waiter attempts to update items with an empty items list via "{path_template}"'
    )
)
def waiter_updates_with_empty_items(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    payload: dict[str, object] = {"items": []}
    response = client.patch(path, headers=_auth_header("waiter"), json=payload)
    bdd_context["last_response"] = response


@when(parsers.parse('the waiter attempts to update items via "{path_template}"'))
def waiter_attempts_update_items_on_confirmed(
    bdd_context: dict[str, object],
    path_template: str,
) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    payload = {
        "items": [
            {
                "dishId": "dish-tacos",
                "name": "Tacos al Pastor",
                "quantity": 1,
                "unitPrice": "45.00",
                "modifiers": [],
            }
        ]
    }
    response = client.patch(path, headers=_auth_header("waiter"), json=payload)
    bdd_context["last_response"] = response


@then("the system rejects the request with HTTP 422 Unprocessable Entity")
def verify_http_422(bdd_context: dict[str, object]) -> None:
    response = cast(Response, bdd_context["last_response"])
    assert response.status_code == 422


@then("the system rejects the request with HTTP 409 Conflict")
def verify_http_409_conflict(bdd_context: dict[str, object]) -> None:
    response = cast(Response, bdd_context["last_response"])
    assert response.status_code == 409


@then("the order items are not modified")
def verify_order_items_unmodified(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert len(persisted.items) == 1
