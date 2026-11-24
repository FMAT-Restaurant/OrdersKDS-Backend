"""Shared test doubles, HTTP dependency overrides, and builders for BDD suites.

Centralizes JWT stubbing, in-memory AsyncSession/UnitOfWork doubles (supporting Order,
Shift, Outbox, and ProcessedEvent), and domain factories to prevent duplication across
AT-01..AT-10 step modules.

Satisfies: RF-01..RF-35, RF-39, RNF-03, RNF-09
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import jwt
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem, Outbox, ProcessedEvent, Shift
from app.db.session import get_session
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.shift import ShiftStatus
from app.main import app
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import SessionFactory, UnitOfWork
from app.services.catalog import DefaultCatalogValidator, get_catalog_validator
from app.services.notifications import OrderNotifier
from app.services.orders import OrderService, get_order_service
from app.services.shifts import ShiftService, get_shift_service
from tests.conftest import TEST_JWT_SECRET


def auth_header(role: str = "waiter", user_id: UUID | None = None) -> dict[str, str]:
    """Build a signed Bearer JWT header matching gateway verification."""
    caller_uuid = user_id or uuid4()
    token = jwt.encode(
        {
            "user_id": str(caller_uuid),
            "role": role,
            "exp": int(datetime.now(UTC).timestamp()) + 300,
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class DummyNotifier(OrderNotifier):
    """No-op WebSocket notifier double for synchronous TestClient BDD runs."""

    async def notify_order_changed(self, order: Order) -> None:
        pass

    async def notify_item_changed(self, order: Order, item: OrderItem) -> None:
        pass


def filter_kds_orders(db: dict[UUID, Order], is_delivered_view: bool) -> list[Order]:
    """Mirror OrderRepository KDS queue filtering and FIFO kitchen sorting."""
    matched: list[Order] = []
    for order in db.values():
        if order.in_preparation_at is None and order.stock_reserved_at is None:
            continue
        if order.status == OrderStatus.PAID:
            continue
        if is_delivered_view:
            if order.status == OrderStatus.DELIVERED:
                matched.append(order)
        elif order.status in (OrderStatus.RESERVED, OrderStatus.IN_PREPARATION) or (
            order.status == OrderStatus.DELIVERED
            and any(item.status == ItemStatus.PENDING for item in order.items)
        ):
            matched.append(order)

    min_dt = datetime.min.replace(tzinfo=UTC)
    matched.sort(
        key=lambda o: (
            o.in_preparation_at or o.stock_reserved_at or o.created_at or min_dt,
            o.created_at or min_dt,
            o.public_id,
        )
    )
    return matched


class BddSessionDouble:
    """In-memory AsyncSession double supporting Order, Shift, Outbox, and ProcessedEvent."""

    def __init__(self, db: dict[UUID, Order]) -> None:
        self.db = db
        self.shifts: list[Shift] = []
        self.outbox_rows: list[Outbox] = []
        self.processed: set[tuple[UUID, UUID]] = set()

    @staticmethod
    def _assign_shift_id_if_missing(shift: Shift, next_id: int) -> None:
        if getattr(shift, "id", None) is None:
            shift.id = next_id

    def add(self, instance: object) -> None:
        if isinstance(instance, Order):
            self.db[instance.public_id] = instance
        elif isinstance(instance, Shift):
            if instance not in self.shifts:
                self.shifts.append(instance)
            self._assign_shift_id_if_missing(instance, len(self.shifts))
        elif isinstance(instance, Outbox):
            self.outbox_rows.append(instance)
        elif isinstance(instance, ProcessedEvent):
            self.processed.add((instance.order_id, instance.event_id))

    async def flush(self) -> None:
        for index, shift in enumerate(self.shifts, start=1):
            self._assign_shift_id_if_missing(shift, index)

    def _scalar_shift(self, params: dict[str, object]) -> Shift | None:
        valid_statuses = {s.value for s in ShiftStatus}
        status_val = next(
            (
                v
                for v in params.values()
                if isinstance(v, ShiftStatus) or (isinstance(v, str) and v in valid_statuses)
            ),
            None,
        )
        candidates = list(self.shifts)
        if status_val is not None:
            expected = ShiftStatus(status_val)
            candidates = [s for s in candidates if s.status == expected]
        return max(candidates, key=lambda s: s.shift_number) if candidates else None

    def _scalar_processed(self, params: dict[str, object]) -> int | None:
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

    async def scalar(self, statement: object) -> object:
        column_descs = getattr(statement, "column_descriptions", ())
        entity = column_descs[0].get("entity") if column_descs else None
        compile_fn = getattr(statement, "compile", None)
        if not callable(compile_fn):
            return None
        params = cast(dict[str, object], compile_fn().params)
        if entity is Shift:
            return self._scalar_shift(params)

        statement_str = str(statement)
        if "processed_events" in statement_str:
            return self._scalar_processed(params)
        if "orders.dining_session_id =" in statement_str.lower():
            session_id = next(
                (v for k, v in params.items() if "dining_session" in k and isinstance(v, UUID)),
                None,
            ) or next((v for v in params.values() if isinstance(v, UUID)), None)
            return next((o for o in self.db.values() if o.dining_session_id == session_id), None)
        order_id = next((v for v in params.values() if isinstance(v, UUID)), None)
        return self.db.get(order_id) if order_id is not None else None

    def _scalars_expired_candidates(self, params: dict[str, object]) -> list[Order]:
        threshold = next((v for v in params.values() if isinstance(v, datetime)), None)
        return [
            o
            for o in self.db.values()
            if o.status == OrderStatus.CREATED
            and o.confirmed_at is not None
            and (threshold is None or o.confirmed_at <= threshold)
        ]

    def _scalars_by_status(self, params: dict[str, object]) -> list[Order]:
        raw_status = params.get("status_1")
        target = OrderStatus(raw_status) if isinstance(raw_status, str) else raw_status
        return [o for o in self.db.values() if o.status == target]

    async def scalars(self, statement: object) -> object:
        statement_str = str(statement)
        compile_fn = getattr(statement, "compile", None)
        params: dict[str, object] = compile_fn().params if callable(compile_fn) else {}

        if "orders.confirmed_at <=" in statement_str:
            orders = self._scalars_expired_candidates(params)
        elif "coalesce(orders.in_preparation_at" in statement_str.lower():
            is_delivered = (
                "orders.status = :status_" in statement_str
                and "orders.status = :status_1 OR" not in statement_str
                and "DELIVERED" in repr(params)
                and "IN_PREPARATION" not in repr(params)
            )
            orders = filter_kds_orders(self.db, is_delivered)
        elif "orders.status = :status_1" in statement_str:
            orders = self._scalars_by_status(params)
        else:
            orders = list(self.db.values())

        class _Result:
            def unique(self) -> _Result:
                return self

            def all(self) -> list[Order]:
                return list(orders)

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
    """Transactional UnitOfWork double over BddSessionDouble."""

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


def build_uow(session: BddSessionDouble) -> BddUow:
    """Create a BddUow without exposing untyped lambdas."""

    def _session_factory() -> AsyncSession:
        return cast(AsyncSession, session)

    return BddUow(_session_factory, session)


@contextmanager
def bdd_client_override(
    session: BddSessionDouble,
    *,
    catalog_validator: DefaultCatalogValidator | None = None,
    notifier: OrderNotifier | None = None,
) -> Generator[TestClient, None, None]:
    """Mount FastAPI TestClient with in-memory session, order, and shift services."""

    async def _get_test_session() -> AsyncGenerator[BddSessionDouble, None]:
        yield session

    def _get_order_svc() -> OrderService:
        return OrderService(session=cast(AsyncSession, session), notifier=notifier)

    def _get_shift_svc() -> ShiftService:
        return ShiftService(session=cast(AsyncSession, session))

    app.dependency_overrides[get_session] = _get_test_session
    app.dependency_overrides[get_order_service] = _get_order_svc
    app.dependency_overrides[get_shift_service] = _get_shift_svc
    if catalog_validator is not None:

        def _get_catalog() -> DefaultCatalogValidator:
            return catalog_validator

        app.dependency_overrides[get_catalog_validator] = _get_catalog

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def make_item(
    dish_id: str,
    name: str,
    *,
    quantity: int = 1,
    unit_price: str = "45.00",
    status: ItemStatus = ItemStatus.PENDING,
    notes: str | None = None,
    offer_id: str | None = None,
) -> OrderItem:
    """Construct an OrderItem aggregate line for BDD scenarios."""
    return OrderItem(
        public_id=uuid4(),
        dish_id=dish_id,
        offer_id=offer_id or dish_id,
        name=name,
        quantity=quantity,
        unit_price=Decimal(unit_price),
        status=status,
        notes=notes,
    )


def make_order(
    *,
    table_number: int | None = 1,
    status: OrderStatus = OrderStatus.CREATED,
    items: Sequence[OrderItem] = (),
    confirmed_at: datetime | None = None,
    confirmed_by: UUID | None = None,
    stock_reserved_at: datetime | None = None,
    in_preparation_at: datetime | None = None,
    created_at: datetime | None = None,
    rejection_details: dict[str, object] | None = None,
) -> Order:
    """Construct an Order aggregate with totals computed from items."""
    order = Order(
        public_id=uuid4(),
        table_number=table_number,
        status=status,
        confirmed_at=confirmed_at,
        confirmed_by=confirmed_by,
        stock_reserved_at=stock_reserved_at,
        in_preparation_at=in_preparation_at,
        rejection_details=rejection_details,
        items=[],
    )
    if created_at is not None:
        order.created_at = created_at
    order.items.extend(items)
    order.recalculate_total()
    return order


def find_outbox_events(session: BddSessionDouble, event_type: str) -> list[Outbox]:
    """Return all staged Outbox rows matching event_type."""
    return [row for row in session.outbox_rows if row.event_type == event_type]


def resolve_bdd_path(template: str, order: Order, item: OrderItem | None = None) -> str:
    """Strip HTTP verb prefix and substitute order/item identifiers."""
    path = template.split(" ", 1)[-1].replace("{orderId}", str(order.public_id))
    if item is not None:
        path = path.replace("{itemId}", str(item.public_id))
    return path


def item_input_payload(
    dish_id: str,
    name: str,
    *,
    quantity: int = 1,
    unit_price: str = "45.00",
    offer_id: str | None = None,
) -> dict[str, object]:
    """Build a standard OrderItemInput payload dictionary for PATCH /items."""
    payload: dict[str, object] = {
        "dishId": dish_id,
        "sourceId": dish_id,
        "name": name,
        "quantity": quantity,
        "unitPrice": unit_price,
        "modifiers": [],
    }
    if offer_id is not None:
        payload["offerId"] = offer_id
    return payload
