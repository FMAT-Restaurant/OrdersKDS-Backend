"""Unit tests for DEF-E5-016 (correlation ID & JSON logging) and DEF-E5-021 (pagination & indexes).

Satisfies: RNF-01, RNF-06, RNF-12
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table

from app.api.middleware import CorrelationIdMiddleware, _extract_header
from app.core.logging import (
    CORRELATION_ID_HEADER,
    JsonLogFormatter,
    configure_structured_logging,
    get_correlation_id,
    get_log_correlation_id,
    parse_or_generate_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from app.db.models import Order, OrderItem, Outbox
from app.db.session import get_session
from app.domain.order_status import ItemStatus, OrderStatus
from app.main import app
from app.messaging.events import OrderConfirmedEvent
from app.repositories.orders import OrderRepository, build_kds_queue_query
from app.repositories.outbox import OutboxRepository
from app.services.catalog import clear_catalog_mock, register_catalog_mock
from tests.unit.api.conftest import FakeAsyncSession
from tests.unit.test_kds_queue import FakeKdsAsyncSession

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RNF-01", "RNF-06", "RNF-12"),
]


def test_parse_or_generate_correlation_id_variants() -> None:
    """Valid UUIDs stay intact; opaque tokens map to UUIDv5; blank values yield UUIDv4."""
    explicit = UUID("11111111-2222-4333-8444-555555555555")
    assert parse_or_generate_correlation_id(f"  {explicit}  ") == explicit

    derived_a = parse_or_generate_correlation_id("req-trace-abc")
    derived_b = parse_or_generate_correlation_id("req-trace-abc")
    assert derived_a == derived_b
    assert derived_a.version == 5

    generated_a = parse_or_generate_correlation_id(None)
    generated_b = parse_or_generate_correlation_id("   ")
    assert generated_a != generated_b
    assert generated_a.version == 4


def test_json_log_formatter_renders_correlation_extra_and_exception() -> None:
    """Formatter emits single-line JSON with timestamp, correlation_id, extras, and exc."""
    formatter = JsonLogFormatter(service="orders-test")
    cid = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    token = set_correlation_id(cid)
    try:
        try:
            raise ValueError("boom")
        except ValueError as err:
            exc_info = (type(err), err, err.__traceback__)

        created_at = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
        record = logging.LogRecord(
            name="app.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=10,
            msg="Failed item %s",
            args=("dish-1",),
            exc_info=exc_info,
        )
        record.order_uuid = UUID("99999999-8888-4777-8666-555555555555")
        record.happened_at = created_at
        record.meta = {"tags": ["a", "b"], "custom": object()}

        parsed = json.loads(formatter.format(record))
        assert parsed["service"] == "orders-test"
        assert parsed["level"] == "ERROR"
        assert parsed["message"] == "Failed item dish-1"
        assert parsed["correlation_id"] == str(cid)
        assert parsed["order_uuid"] == "99999999-8888-4777-8666-555555555555"
        assert parsed["happened_at"] == created_at.isoformat()
        assert parsed["meta"]["tags"] == ["a", "b"]
        assert "ValueError: boom" in parsed["exception"]
    finally:
        reset_correlation_id(token)


def test_configure_structured_logging_sets_json_handler() -> None:
    """configure_structured_logging installs JSON handlers and suppresses duplicate access logs."""
    root = logging.getLogger()
    uvicorn_names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved_state = {
        name: (
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
            logging.getLogger(name).disabled,
        )
        for name in uvicorn_names
    }
    previous_handlers = list(root.handlers)
    previous_level = root.level
    try:
        configure_structured_logging(service="orders-worker", level="WARNING")
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonLogFormatter)
        assert root.level == logging.WARNING
        uvicorn_err = logging.getLogger("uvicorn.error")
        assert isinstance(uvicorn_err.handlers[0].formatter, JsonLogFormatter)

        configure_structured_logging(service="orders-backend", level="INFO")
        access_logger = logging.getLogger("uvicorn.access")
        assert access_logger.handlers == []
        assert access_logger.propagate is False
        assert access_logger.disabled is True
    finally:
        root.handlers.clear()
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)
        for name, (handlers, level, propagate, disabled) in saved_state.items():
            lgr = logging.getLogger(name)
            lgr.handlers.clear()
            for handler in handlers:
                lgr.addHandler(handler)
            lgr.setLevel(level)
            lgr.propagate = propagate
            lgr.disabled = disabled


def test_correlation_middleware_echoes_header_and_propagates_to_outbox(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """Incoming X-Correlation-ID propagates through Async context into Outbox row and payload."""
    order = Order(
        public_id=uuid4(),
        table_number=5,
        status=OrderStatus.CREATED,
        items=[
            OrderItem(
                public_id=uuid4(),
                dish_id="dish-1",
                name="Tacos",
                quantity=1,
                unit_price=Decimal("45.00"),
            )
        ],
    )
    test_db[order.public_id] = order

    incoming_cid = "22222222-3333-4444-8555-666666666666"
    headers = {**auth_headers("waiter"), CORRELATION_ID_HEADER: incoming_cid}

    response = client.post(f"/orders/{order.public_id}/confirm", headers=headers)

    assert response.status_code == 200
    assert response.headers[CORRELATION_ID_HEADER.lower()] == incoming_cid
    assert len(fake_session.outbox_rows) == 1
    outbox: Outbox = fake_session.outbox_rows[0]
    assert outbox.correlation_id == UUID(incoming_cid)
    assert outbox.payload["correlationId"] == incoming_cid
    assert get_correlation_id() is None


def test_correlation_middleware_generates_uuid_when_header_missing(
    client: TestClient,
) -> None:
    """Requests without X-Correlation-ID receive a generated UUIDv4 header."""
    response = client.get("/health")
    assert response.status_code == 200
    generated = UUID(response.headers[CORRELATION_ID_HEADER.lower()])
    assert generated.version == 4


def test_outbox_repository_uses_active_context_correlation_id() -> None:
    """OutboxRepository.add uses bound ContextVar when event had no explicit correlation_id."""
    session = FakeAsyncSession()
    repo = OutboxRepository(session)  # type: ignore[arg-type]
    order_id = uuid4()
    active_cid = UUID("33333333-4444-4555-8666-777777777777")
    token = set_correlation_id(active_cid)
    try:
        event = OrderConfirmedEvent.for_order(order_id)
        row = repo.add(event)
        assert row.correlation_id == active_cid
        assert row.payload["correlationId"] == str(active_cid)
    finally:
        reset_correlation_id(token)


@pytest.mark.asyncio
async def test_correlation_middleware_websocket_and_lifespan_scopes() -> None:
    """WebSocket scope binds trace UUID when omitted and preserves explicit header when present."""
    observed: list[tuple[UUID | None, UUID | None]] = []

    async def dummy_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        observed.append((get_correlation_id(), get_log_correlation_id()))
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    middleware = CorrelationIdMiddleware(dummy_app)  # type: ignore[arg-type]
    ws_cid = UUID("44444444-5555-4666-8777-888888888888")

    async def noop_receive() -> dict[str, Any]:
        return {}

    async def noop_send(_msg: MutableMapping[str, Any]) -> None:
        return None

    await middleware({"type": "lifespan"}, noop_receive, noop_send)
    await middleware(
        {"type": "websocket", "headers": [(b"x-correlation-id", str(ws_cid).encode("latin-1"))]},
        noop_receive,
        noop_send,
    )
    await middleware({"type": "websocket", "headers": []}, noop_receive, noop_send)

    assert observed[0] == (None, None)
    assert observed[1] == (ws_cid, ws_cid)
    assert observed[2][0] is None
    trace_ws_cid = observed[2][1]
    assert trace_ws_cid is not None
    assert trace_ws_cid.version == 4
    assert get_correlation_id() is None
    assert get_log_correlation_id() is None
    assert _extract_header({"headers": [(b"other", b"1")]}, b"x-correlation-id") is None


def test_list_orders_pagination_and_validation_bounds(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """GET /orders paginates using limit and offset and validates query bounds."""
    base_time = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
    ordered_ids: list[UUID] = []
    for idx in range(5):
        oid = uuid4()
        ordered_ids.append(oid)
        test_db[oid] = Order(
            public_id=oid,
            table_number=idx + 1,
            status=OrderStatus.CREATED,
            created_at=base_time + timedelta(minutes=idx),
            items=[],
        )

    page = client.get("/orders?limit=2&offset=1", headers=auth_headers("waiter"))
    assert page.status_code == 200
    payload = page.json()
    assert len(payload) == 2
    # Descending by created_at -> index 3 then index 2
    assert [UUID(item["orderId"]) for item in payload] == [ordered_ids[3], ordered_ids[2]]

    assert client.get("/orders?limit=0", headers=auth_headers("waiter")).status_code == 422
    assert client.get("/orders?limit=501", headers=auth_headers("waiter")).status_code == 422
    assert client.get("/orders?offset=-1", headers=auth_headers("waiter")).status_code == 422


def test_kds_queue_pagination_with_and_without_station_filter(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """GET /kds/queue paginates both SQL-level unfiltered queries and station-filtered slices."""
    clear_catalog_mock()
    register_catalog_mock("dish-grill", station="Parrilla", preparation_time_text="10 min")
    register_catalog_mock("dish-cold", station="Fria", preparation_time_text="5 min")
    kds_session = FakeKdsAsyncSession(test_db)
    previous_override = app.dependency_overrides.get(get_session)

    async def _kds_session_override() -> Any:
        yield kds_session

    app.dependency_overrides[get_session] = _kds_session_override
    try:
        base_time = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
        grill_ids: list[UUID] = []
        for idx in range(4):
            oid = uuid4()
            dish_id = "dish-grill" if idx != 1 else "dish-cold"
            if dish_id == "dish-grill":
                grill_ids.append(oid)
            test_db[oid] = Order(
                public_id=oid,
                table_number=idx + 10,
                status=OrderStatus.IN_PREPARATION,
                stock_reserved_at=base_time + timedelta(minutes=idx),
                in_preparation_at=base_time + timedelta(minutes=idx),
                created_at=base_time + timedelta(minutes=idx),
                items=[
                    OrderItem(
                        public_id=uuid4(),
                        dish_id=dish_id,
                        name=f"Plato {idx}",
                        quantity=1,
                        unit_price=Decimal("50.00"),
                        status=ItemStatus.PENDING,
                    )
                ],
            )

        res_unfiltered = client.get("/kds/queue?limit=2&offset=1", headers=auth_headers("kitchen"))
        assert res_unfiltered.status_code == 200
        assert len(res_unfiltered.json()) == 2

        res_station = client.get(
            "/kds/queue?station=parrilla&limit=1&offset=1",
            headers=auth_headers("kitchen"),
        )
        assert res_station.status_code == 200
        station_data = res_station.json()
        assert len(station_data) == 1
        assert UUID(station_data[0]["orderId"]) == grill_ids[1]

        assert client.get("/kds/queue?limit=0", headers=auth_headers("kitchen")).status_code == 422
    finally:
        if previous_override is not None:
            app.dependency_overrides[get_session] = previous_override
        else:
            app.dependency_overrides.pop(get_session, None)
        clear_catalog_mock()


def test_build_kds_queue_query_and_orm_composite_indexes() -> None:
    """SQL query compiles LIMIT/OFFSET and ORM tables expose the composite performance indexes."""
    stmt = build_kds_queue_query(view="active", limit=25, offset=10)
    sql = str(stmt).lower()
    assert "limit" in sql
    assert "offset" in sql

    order_table = Order.__table__
    assert isinstance(order_table, Table)
    order_indexes = {
        str(idx.name): list(idx.columns.keys()) for idx in order_table.indexes if idx.name
    }
    assert order_indexes["ix_orders_status_in_prep_created"] == [
        "status",
        "in_preparation_at",
        "created_at",
    ]
    assert order_indexes["ix_orders_created_at_public_id"] == ["created_at", "public_id"]

    item_table = OrderItem.__table__
    assert isinstance(item_table, Table)
    item_indexes = {
        str(idx.name): list(idx.columns.keys()) for idx in item_table.indexes if idx.name
    }
    assert item_indexes["ix_order_items_order_id_status"] == ["order_id", "status"]


@pytest.mark.asyncio
async def test_repository_pagination_arguments_apply_offset_and_limit() -> None:
    """OrderRepository forwards limit and offset to SQLAlchemy query execution."""
    session = FakeAsyncSession()
    repo = OrderRepository(session)  # type: ignore[arg-type]
    for idx in range(3):
        oid = uuid4()
        session.db[oid] = Order(
            public_id=oid,
            table_number=idx + 1,
            status=OrderStatus.CREATED,
            created_at=datetime(2026, 11, 24, 8, idx, tzinfo=UTC),
            items=[],
        )

    rows = await repo.list_orders(limit=2, offset=1)
    assert len(rows) == 2
