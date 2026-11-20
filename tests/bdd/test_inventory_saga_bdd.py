"""Step definitions for AT-03 acceptance scenarios (Inventory Saga & Stock Reservation).

Satisfies: RF-32, RF-33, RF-34, RF-35
Covers: AT-03
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncGenerator, Generator
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
from app.db.models import Order, OrderItem, Outbox, ProcessedEvent
from app.db.session import get_session
from app.domain.order_status import ItemStatus, OrderStatus
from app.main import app
from app.messaging.consumers.stock_insufficient import handle_stock_insufficient
from app.messaging.consumers.stock_reserved import handle_stock_reserved
from app.messaging.events import (
    OrderCreatedItem,
    RejectedItemDetail,
    StockInsufficientMessage,
    StockReservedMessage,
)
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork
from app.services.catalog import DefaultCatalogValidator, get_catalog_validator
from app.services.notifications import OrderNotifier
from app.services.orders import OrderService, get_order_service
from app.workers.saga_timeout_watcher import SagaTimeoutWatcher

pytestmark = [
    pytest.mark.bdd,
    pytest.mark.requirement("RF-32", "RF-33", "RF-34", "RF-35"),
]

scenarios("inventory_saga.feature")


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


class DummyNotifier(OrderNotifier):
    """No-op order notifier for tests."""

    async def notify_order_changed(self, order: Order) -> None:
        pass

    async def notify_item_changed(self, order: Order, item: OrderItem) -> None:
        pass


class BddSessionDouble:
    """In-memory AsyncSession double supporting Order, Outbox, and ProcessedEvent."""

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
            order_id = next((v for v in params.values() if isinstance(v, UUID)), None)
            return self.db.get(order_id) if order_id is not None else None
        return None

    async def scalars(self, statement: object) -> object:
        statement_str = str(statement)
        compile_fn = getattr(statement, "compile", None)
        params: dict[str, object] = compile_fn().params if callable(compile_fn) else {}

        if "confirmed_at" in statement_str:
            threshold = next((v for v in params.values() if isinstance(v, datetime)), None)
            candidates = [
                o
                for o in self.db.values()
                if o.status == OrderStatus.CREATED
                and o.confirmed_at is not None
                and (threshold is None or o.confirmed_at <= threshold)
            ]
            orders = candidates
        else:
            orders = list(self.db.values())

        class _Result:
            def unique(self) -> _Result:
                return self

            def all(self) -> list[Order]:
                return orders

        return _Result()

    async def __aenter__(self) -> BddSessionDouble:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        pass

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
        "notifier": DummyNotifier(),
        "catalog_validator": DefaultCatalogValidator(),
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
        notifier=cast(OrderNotifier, bdd_context["notifier"]),
    )

    with TestClient(app) as test_client:
        bdd_context["client"] = test_client
        yield test_client
    app.dependency_overrides.clear()


@given('a confirmed order exists in "CREATED" status awaiting stock reservation')
def confirmed_order_awaiting_stock(bdd_context: dict[str, object]) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=1,
        status=OrderStatus.CREATED,
        confirmed_at=datetime.now(UTC) - timedelta(seconds=10),
        confirmed_by=uuid4(),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-tacos",
        name="Tacos al Pastor",
        quantity=2,
        unit_price=Decimal("30.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["order"] = order


@given('a confirmed order exists in "CREATED" status with 3 requested tacos')
def confirmed_order_with_three_tacos(bdd_context: dict[str, object]) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus.CREATED,
        confirmed_at=datetime.now(UTC) - timedelta(seconds=15),
        confirmed_by=uuid4(),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-tacos",
        name="Tacos al Pastor",
        quantity=3,
        unit_price=Decimal("30.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["order"] = order


@given('an order exists in "RESERVED" status with 3 tacos and 1 drink')
def reserved_order_with_tacos_and_drink(bdd_context: dict[str, object]) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.RESERVED,
        confirmed_at=datetime.now(UTC) - timedelta(seconds=60),
        stock_reserved_at=datetime.now(UTC) - timedelta(seconds=40),
    )
    tacos = OrderItem(
        public_id=uuid4(),
        dish_id="dish-tacos",
        name="Tacos al Pastor",
        quantity=3,
        unit_price=Decimal("30.00"),
        status=ItemStatus.PENDING,
    )
    drink = OrderItem(
        public_id=uuid4(),
        dish_id="dish-horchata",
        name="Agua de Horchata",
        quantity=1,
        unit_price=Decimal("25.00"),
        status=ItemStatus.PENDING,
    )
    order.items.extend([tacos, drink])
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["order"] = order


@given('a confirmed order in "CREATED" status confirmed 900 seconds ago')
def confirmed_order_past_ttl(bdd_context: dict[str, object]) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=4,
        status=OrderStatus.CREATED,
        confirmed_at=datetime.now(UTC) - timedelta(seconds=905),
        confirmed_by=uuid4(),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-pizza",
        name="Pizza Artesanal",
        quantity=1,
        unit_price=Decimal("180.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["order"] = order


@given('an order exists in "EXPIRED" status')
def expired_order_exists(bdd_context: dict[str, object]) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=5,
        status=OrderStatus.EXPIRED,
        confirmed_at=datetime.now(UTC) - timedelta(seconds=1200),
    )
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["order"] = order


@given('an order exists in "CANCELLED" status')
def cancelled_order_exists(bdd_context: dict[str, object]) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=8,
        status=OrderStatus.CANCELLED,
        confirmed_at=datetime.now(UTC) - timedelta(seconds=1200),
    )
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["order"] = order


@given('an order exists in "EXPIRED" status within the 10-minute Caducados grace window')
def expired_order_in_caducados_grace(bdd_context: dict[str, object]) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=6,
        status=OrderStatus.EXPIRED,
        confirmed_at=datetime.now(UTC) - timedelta(seconds=960),
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-pasta",
        name="Pasta Carbonara",
        quantity=1,
        unit_price=Decimal("140.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    order.recalculate_total()
    db = cast(dict[UUID, Order], bdd_context["db"])
    db[order.public_id] = order
    bdd_context["order"] = order


@when(parsers.parse('inventory confirms stock reservation via "{event_name}"'))
def process_stock_reserved(bdd_context: dict[str, object], event_name: str) -> None:
    order = cast(Order, bdd_context["order"])
    session = cast(BddSessionDouble, bdd_context["session"])
    uow = BddUow(lambda: cast(AsyncSession, session), session)
    msg = StockReservedMessage(
        event_id=uuid4(),
        order_id=order.public_id,
        reservations=[],
    )
    notifier = cast(OrderNotifier, bdd_context["notifier"])
    asyncio.run(handle_stock_reserved(msg, uow, notifier=notifier))


@then(parsers.parse('the order status becomes "{expected_status}"'))
def verify_order_status_becomes(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.status == OrderStatus(expected_status)


@then("the order stock_reserved_at timestamp is recorded")
def verify_stock_reserved_at(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.stock_reserved_at is not None


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
    order = cast(Order, bdd_context["order"])
    session = cast(BddSessionDouble, bdd_context["session"])
    uow = BddUow(lambda: cast(AsyncSession, session), session)
    item = order.items[0]
    offer_id = str(item.dish_id or "dish-tacos")

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
        reserved_items=[
            OrderCreatedItem(
                offerId=offer_id,
                quantity=available_qty,
            )
        ],
    )
    notifier = cast(OrderNotifier, bdd_context["notifier"])
    asyncio.run(handle_stock_insufficient(msg, uow, notifier=notifier))


@then(
    parsers.parse(
        "the rejectionDetails contain rejectedItems with availableQuantity "
        "{available_qty:d} and reservedItems"
    )
)
def verify_rejection_details_partial(bdd_context: dict[str, object], available_qty: int) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.rejection_details is not None
    rejected = cast(list[dict[str, object]], persisted.rejection_details["rejectedItems"])
    assert rejected[0]["availableQuantity"] == available_qty
    reserved = cast(list[dict[str, object]], persisted.rejection_details["reservedItems"])
    assert len(reserved) >= 1


@when(parsers.parse('the waiter updates the order items via "{path_template}" with {qty:d} tacos'))
def waiter_updates_rejected_items(
    bdd_context: dict[str, object],
    path_template: str,
    qty: int,
) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    payload = {
        "items": [
            {
                "dishId": "dish-tacos",
                "name": "Tacos al Pastor",
                "quantity": qty,
                "unitPrice": "30.00",
                "modifiers": [],
            }
        ]
    }
    response = client.patch(path, headers=_auth_header("waiter"), json=payload)
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('the order status transitions from "REJECTED" back to "{expected_status}"'))
def verify_reopened_to_created(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.status == OrderStatus(expected_status)


@then(parsers.parse("the order total amount is updated for {qty:d} items"))
def verify_total_updated(bdd_context: dict[str, object], qty: int) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.total_amount == Decimal("30.00") * qty


@when(parsers.parse('the waiter confirms the order via "{path_template}"'))
def waiter_confirms_reopened_order(
    bdd_context: dict[str, object],
    path_template: str,
) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    path = path_template.replace("POST ", "").replace("{orderId}", str(order.public_id))
    response = client.post(path, headers=_auth_header("waiter"))
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then("the order confirmed_at timestamp is recorded")
def verify_order_confirmed_at(bdd_context: dict[str, object]) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.confirmed_at is not None


@then(parsers.parse('a new stock reservation event "{event_type}" is staged in the outbox'))
def verify_new_reservation_staged(bdd_context: dict[str, object], event_type: str) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1


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
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    tacos_item = order.items[0]
    path = (
        path_template.replace("PATCH ", "")
        .replace("{orderId}", str(order.public_id))
        .replace("{itemId}", str(tacos_item.public_id))
    )
    payload = {
        "reason": "Stock available for only 1 order",
        "availableQuantity": avail_qty,
    }
    response = client.patch(path, headers=_auth_header("chef"), json=payload)
    bdd_context["last_response"] = response
    assert response.status_code == 200


@then(parsers.parse('the outbox stages "{event_type}"'))
def verify_outbox_stages_event(bdd_context: dict[str, object], event_type: str) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1


@then(
    parsers.parse(
        "the rejectionDetails reflect requestedQuantity {req_qty:d} "
        "and availableQuantity {avail_qty:d}"
    )
)
def verify_rejection_quantities(
    bdd_context: dict[str, object],
    req_qty: int,
    avail_qty: int,
) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    details = persisted.rejection_details
    assert details is not None
    rejected_list = cast(list[dict[str, object]], details["rejectedItems"])
    assert rejected_list[0]["requestedQuantity"] == req_qty
    assert rejected_list[0]["availableQuantity"] == avail_qty


@when("the saga timeout watcher executes")
def saga_watcher_executes(bdd_context: dict[str, object]) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    settings = SagaTimeoutSettings(timeout_seconds=900)
    watcher = SagaTimeoutWatcher(
        lambda: cast(AsyncSession, session),
        settings=settings,
    )
    expired_count = asyncio.run(watcher.expire_stale_orders())
    assert expired_count == 1


@then(parsers.parse('a compensation event "{event_type}" is staged in the outbox'))
def verify_compensation_event_staged(bdd_context: dict[str, object], event_type: str) -> None:
    session = cast(BddSessionDouble, bdd_context["session"])
    matching = [e for e in session.outbox_rows if e.event_type == event_type]
    assert len(matching) >= 1


@when(parsers.parse('a late "{event_name}" event is received'))
def late_event_received(bdd_context: dict[str, object], event_name: str) -> None:
    order = cast(Order, bdd_context["order"])
    session = cast(BddSessionDouble, bdd_context["session"])
    uow = BddUow(lambda: cast(AsyncSession, session), session)
    msg = StockReservedMessage(
        event_id=uuid4(),
        order_id=order.public_id,
        reservations=[],
    )
    notifier = cast(OrderNotifier, bdd_context["notifier"])
    res = asyncio.run(handle_stock_reserved(msg, uow, notifier=notifier))
    bdd_context["late_event_result"] = res


@then("the late event is ignored")
def verify_late_event_ignored(bdd_context: dict[str, object]) -> None:
    assert bdd_context["late_event_result"] is False


@then(parsers.parse('the order status remains "{expected_status}"'))
def verify_order_status_remains(bdd_context: dict[str, object], expected_status: str) -> None:
    order = cast(Order, bdd_context["order"])
    db = cast(dict[UUID, Order], bdd_context["db"])
    persisted = db[order.public_id]
    assert persisted.status == OrderStatus(expected_status)


@when(parsers.parse('the kitchen attempts to start preparation via "{path_template}"'))
def kitchen_attempts_start_preparation(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    response = client.patch(path, headers=_auth_header("kitchen"))
    bdd_context["last_response"] = response


@when(parsers.parse('the kitchen attempts to mark the order ready via "{path_template}"'))
def kitchen_attempts_mark_ready(bdd_context: dict[str, object], path_template: str) -> None:
    client = cast(TestClient, bdd_context["client"])
    order = cast(Order, bdd_context["order"])
    path = path_template.replace("PATCH ", "").replace("{orderId}", str(order.public_id))
    response = client.patch(path, headers=_auth_header("kitchen"))
    bdd_context["last_response"] = response


@then(parsers.parse("the system rejects the request with HTTP {status_code:d} Conflict"))
def verify_http_conflict(bdd_context: dict[str, object], status_code: int) -> None:
    response = cast(Response, bdd_context["last_response"])
    assert response.status_code == status_code
