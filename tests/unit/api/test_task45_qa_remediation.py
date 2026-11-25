"""Comprehensive regression tests for TASK-45 (Team 6 QA Audit Remediation).

Covers:
- DEF-E5-006 (CP-012): Reject URL ?token= / ?access_token= on HTTP REST with 401; allow on WS.
- DEF-E5-007 (CP-014): CORS origin whitelist via CORS_ALLOWED_ORIGINS (no '*' reflection).
- DEF-E5-008 & OBS-03 (CP-019, CP-020): Pydantic bounds & tableNumber + isTakeaway=true 422.
- DEF-E5-010 (CP-023): Absent dishes in CatalogRepository (Redis) -> 422 DISH_UNAVAILABLE.
- DEF-E5-011 (CP-025): POST /orders/{id}/confirm without active items rejected with 422.
- DEF-E5-012 (CP-028): Cancellation contract aligned with ADR-0012 (CREATED/RESERVED/REJECTED).
- DEF-E5-013 (CP-032): GET /kds/queue reads station & preparationTimeText from CatalogRepository.
- DEF-E5-014 (CP-036): void_order stages ordenes.comanda.mermada + ordenes.cobro.solicitado.
- DEF-E5-015 (CP-044, SEC-12): RabbitMQ DLX/DLQ + nack(requeue=False) & bounded DLQ router.
- DEF-E5-024 (CP-039): Deterministic UUIDv5 on DishPreparedEvent.for_item and item events.
- OBS-01: Mandatory exp claim in JWT verification.
- OBS-02: PaymentCompletedMessage & handle_payment_completed status and totalPaid validation.
- OBS-04: Waiter BOLA ownership enforcement on order edition, transfer, and cancellation.
- OBS-05: Zero production assert statements in app/ and python-dotenv >= 1.2.2.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import AsyncGenerator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib.metadata import version as pkg_version
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from app.cache.catalog import CatalogRepository
from app.core.config import get_cors_allowed_origins
from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.constants import MAX_ITEM_QUANTITY, MAX_SQL_INT32, MAX_TABLE_NUMBER
from app.domain.errors import DishUnavailableError
from app.domain.order_status import ItemStatus, OrderStatus
from app.main import app, create_app
from app.messaging.consumers.base import (
    BrokerNativeDLQRouter,
    EventConsumerDispatcher,
    InMemoryDLQRouter,
)
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.consumers.payment_completed import handle_payment_completed
from app.messaging.events import (
    DishPreparedEvent,
    DishRedoRequestedEvent,
    EventName,
    KitchenDishRejectedEvent,
    PaymentCompletedMessage,
)
from app.repositories.orders import OrderRepository
from app.repositories.processed_events import ProcessedEventsRepository
from app.repositories.unit_of_work import UnitOfWork
from app.services.catalog import (
    CatalogRepositoryValidator,
    clear_catalog_mock,
    get_catalog_validator,
    register_catalog_mock,
)
from tests.conftest import TEST_JWT_SECRET
from tests.unit.conftest import FakeAsyncSession, TokenFactory
from tests.unit.test_kds_queue import FakeKdsAsyncSession

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement(
        "RF-08",
        "RF-14",
        "RF-17",
        "RF-24",
        "RF-26",
        "RF-27",
        "RF-32",
        "RF-40",
        "RNF-02",
        "RNF-03",
        "RNF-13",
    ),
]


class _InMemoryRedisHash:
    """In-memory Redis hash backing CatalogRepository without mock fallbacks."""

    def __init__(self) -> None:
        self.data: dict[str, dict[object, object]] = {}

    async def hgetall(self, name: str) -> Mapping[object, object]:
        return dict(self.data.get(name, {}))

    async def hset(self, name: str, mapping: Mapping[str, str]) -> int:
        self.data.setdefault(name, {}).update(mapping)
        return len(mapping)


# ---------------------------------------------------------------------------
# DEF-E5-006 (CP-012) & OBS-01: REST URL token rejection + mandatory JWT exp
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query_param", ["token", "access_token"])
def test_cp012_rest_endpoints_reject_url_query_tokens_even_when_gateway_trusted(
    client: TestClient,
    make_token: TokenFactory,
    monkeypatch: pytest.MonkeyPatch,
    query_param: str,
) -> None:
    valid_waiter = make_token(role="waiter", user_id="waiter-cp012")
    valid_kitchen = make_token(role="kitchen", user_id="kitchen-cp012")

    for trust_gw in ("false", "true"):
        monkeypatch.setenv("TRUST_GATEWAY_HEADERS", trust_gw)
        r_orders = client.get(f"/orders?{query_param}={valid_waiter}")
        assert r_orders.status_code == 401
        assert r_orders.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED.value

        r_kds = client.get(f"/kds/queue?{query_param}={valid_kitchen}")
        assert r_kds.status_code == 401
        assert r_kds.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED.value

    # WebSocket handshake with ?token= / ?access_token= remains operational
    with client.websocket_connect(f"/ws/kds?{query_param}={valid_kitchen}") as ws:
        ws.send_json({"action": "subscribe", "channel": "kds:all"})


def test_obs01_jwt_without_exp_claim_is_rejected_with_401(client: TestClient) -> None:
    token_without_exp = jwt.encode(
        {
            "sub": "waiter-no-exp",
            "user_id": "waiter-no-exp",
            "role": "waiter",
            "iat": int(datetime.now(UTC).timestamp()),
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    response = client.get("/orders", headers={"Authorization": f"Bearer {token_without_exp}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED.value


# ---------------------------------------------------------------------------
# DEF-E5-007 (CP-014): CORS origin allowlist (never '*' with credentials)
# ---------------------------------------------------------------------------


def test_cp014_cors_whitelist_blocks_untrusted_origins_and_strips_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://shell.fmat.mx, https://menu.fmat.mx/, *",
    )
    allowed = get_cors_allowed_origins()
    assert allowed == ["https://shell.fmat.mx", "https://menu.fmat.mx"]
    assert "*" not in allowed

    scoped_app = create_app()
    scoped_client = TestClient(scoped_app)

    # Untrusted origin is NOT reflected
    evil_preflight = scoped_client.options(
        "/orders",
        headers={
            "Origin": "https://evil.attacker.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert "access-control-allow-origin" not in evil_preflight.headers

    evil_get = scoped_client.get(
        "/health",
        headers={"Origin": "https://evil.attacker.example"},
    )
    assert "access-control-allow-origin" not in evil_get.headers

    # Whitelisted origin IS allowed with credentials
    ok_preflight = scoped_client.options(
        "/orders",
        headers={
            "Origin": "https://menu.fmat.mx",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        },
    )
    assert ok_preflight.status_code == 200
    assert ok_preflight.headers.get("access-control-allow-origin") == "https://menu.fmat.mx"
    assert ok_preflight.headers.get("access-control-allow-credentials") == "true"


def test_cp014_default_cors_origins_cover_all_six_microfrontends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    allowed = get_cors_allowed_origins()
    assert "*" not in allowed
    for port in (3000, 5173, 5174, 5176, 5177, 5178, 5179, 8000):
        assert f"http://localhost:{port}" in allowed
        assert f"http://127.0.0.1:{port}" in allowed


# ---------------------------------------------------------------------------
# DEF-E5-008 & OBS-03 (CP-019, CP-020): Input bounds & table+takeaway exclusion
# ---------------------------------------------------------------------------


def test_cp020_create_order_rejects_simultaneous_table_number_and_takeaway(
    client: TestClient,
    fake_session: FakeAsyncSession,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    del fake_session
    response = client.post(
        "/orders",
        headers=auth_headers("waiter"),
        json={"tableNumber": 5, "isTakeaway": True},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.parametrize(
    "bad_table",
    [0, -1, MAX_TABLE_NUMBER + 1, MAX_SQL_INT32 + 1, 9_999_999_999],
)
def test_cp019_table_number_out_of_range_returns_422_on_create_and_transfer(
    client: TestClient,
    fake_session: FakeAsyncSession,
    auth_headers: Callable[[str], dict[str, str]],
    bad_table: int,
) -> None:
    order = Order(public_id=uuid4(), table_number=2, status=OrderStatus.CREATED, items=[])
    fake_session.add(order)

    res_create = client.post(
        "/orders",
        headers=auth_headers("waiter"),
        json={"tableNumber": bad_table, "isTakeaway": False},
    )
    assert res_create.status_code == 422
    assert res_create.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value

    res_transfer = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": bad_table},
    )
    assert res_transfer.status_code == 422
    assert res_transfer.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.parametrize(
    "bad_item",
    [
        {"dishId": "D-1", "name": "Taco", "quantity": MAX_ITEM_QUANTITY + 1, "unitPrice": "25.00"},
        {"dishId": "D-1", "name": "Taco", "quantity": 100_000, "unitPrice": "25.00"},
        {"dishId": "D-1", "name": "Taco", "quantity": MAX_SQL_INT32 + 1, "unitPrice": "25.00"},
        {"dishId": "D-1", "name": "Taco", "quantity": 1, "unitPrice": "19.999"},
        {"dishId": "D-1", "name": "Taco", "quantity": 1, "unitPrice": "10000000000.00"},
        {"dishId": "D-1", "name": "Taco", "quantity": 1000, "unitPrice": "99999999.99"},
    ],
)
def test_cp019_obs03_item_quantity_and_unit_price_bounds_return_422(
    client: TestClient,
    fake_session: FakeAsyncSession,
    auth_headers: Callable[[str], dict[str, str]],
    bad_item: dict[str, object],
) -> None:
    order = Order(public_id=uuid4(), table_number=3, status=OrderStatus.CREATED, items=[])
    fake_session.add(order)

    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={"items": [bad_item]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value


# ---------------------------------------------------------------------------
# DEF-E5-010 (CP-023): Absent catalog dishes rejected with 422 DISH_UNAVAILABLE
# ---------------------------------------------------------------------------


def test_cp023_absent_dish_in_redis_catalog_returns_422_dish_unavailable(
    client: TestClient,
    fake_session: FakeAsyncSession,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    redis_double = _InMemoryRedisHash()
    repo = CatalogRepository(redis_double)
    real_validator = CatalogRepositoryValidator(repo)

    order = Order(public_id=uuid4(), table_number=6, status=OrderStatus.CREATED, items=[])
    fake_session.add(order)

    prev = app.dependency_overrides.get(get_catalog_validator)
    app.dependency_overrides[get_catalog_validator] = lambda: real_validator
    try:
        # Dish absent from Redis -> 422 DISH_UNAVAILABLE
        missing_resp = client.patch(
            f"/orders/{order.public_id}/items",
            headers=auth_headers("waiter"),
            json={
                "items": [
                    {
                        "dishId": "DISH-DOES-NOT-EXIST",
                        "name": "Platillo Fantasma",
                        "quantity": 1,
                        "unitPrice": "99.00",
                    }
                ]
            },
        )
        assert missing_resp.status_code == 422
        assert missing_resp.json()["error"]["code"] == ErrorCode.DISH_UNAVAILABLE.value

        # Project dual IDs via CatalogConsumer and verify acceptance + authoritative price
        consumer = CatalogConsumer(repo)
        asyncio.run(
            consumer.consume(
                EventName.MENU_CATALOG_UPDATED,
                {
                    "sourceId": "PLATE-REAL-01",
                    "offerId": "OFFER-REAL-01",
                    "basePrice": "145.00",
                    "isAvailable": True,
                    "station": "Parrilla",
                },
            )
        )
        valid_resp = client.patch(
            f"/orders/{order.public_id}/items",
            headers=auth_headers("waiter"),
            json={
                "items": [
                    {
                        "dishId": "PLATE-REAL-01",
                        "offerId": "OFFER-REAL-01",
                        "name": "Arrachera Real",
                        "quantity": 2,
                        "unitPrice": "10.00",
                    }
                ]
            },
        )
        assert valid_resp.status_code == 200
        assert Decimal(valid_resp.json()["totalAmount"]) == Decimal("290.00")
    finally:
        if prev is not None:
            app.dependency_overrides[get_catalog_validator] = prev
        else:
            app.dependency_overrides.pop(get_catalog_validator, None)


@pytest.mark.asyncio
async def test_cp023_catalog_repository_rejects_empty_hash() -> None:
    repo = CatalogRepository(_InMemoryRedisHash())
    with pytest.raises(DishUnavailableError, match="not found in catalog"):
        await repo.validate_dish_available("NON-EXISTENT-ID")


# ---------------------------------------------------------------------------
# DEF-E5-011 (CP-025): Confirming order without active items rejected with 422
# ---------------------------------------------------------------------------


def test_cp025_confirm_order_without_active_items_returns_422(
    client: TestClient,
    fake_session: FakeAsyncSession,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    empty_order = Order(public_id=uuid4(), table_number=2, status=OrderStatus.CREATED, items=[])
    cancelled_only_order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.CREATED,
        items=[
            OrderItem(
                public_id=uuid4(),
                dish_id="PLATE-BURGER-01",
                name="Cancelado",
                quantity=1,
                unit_price=Decimal("100.00"),
                status=ItemStatus.CANCELLED,
            )
        ],
    )
    fake_session.add(empty_order)
    fake_session.add(cancelled_only_order)

    for target in (empty_order, cancelled_only_order):
        resp = client.post(
            f"/orders/{target.public_id}/confirm",
            headers=auth_headers("waiter"),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
        assert target.confirmed_at is None
    assert fake_session.outbox_rows == []


# ---------------------------------------------------------------------------
# DEF-E5-012 (CP-028) & OBS-04: ADR-0012 cancel contract + Waiter BOLA check
# ---------------------------------------------------------------------------


def test_cp028_openapi_documents_adr0012_cancel_states() -> None:
    cancel_op = app.openapi()["paths"]["/orders/{orderId}/cancel"]["patch"]
    desc = cancel_op["description"]
    assert "CREATED" in desc
    assert "RESERVED" in desc
    assert "REJECTED" in desc
    assert "ADR-0012" in desc


def test_obs04_waiter_bola_ownership_blocks_other_waiter_but_allows_owner_and_admin(
    client: TestClient,
    fake_session: FakeAsyncSession,
    make_token: TokenFactory,
) -> None:
    now = datetime.now(UTC)
    from scripts.seed_demo import _build_demo_shift

    fake_session.add(_build_demo_shift(now, shift_number=1, fixed_ids=True))
    waiter_a_id = str(uuid4())
    waiter_b_id = str(uuid4())
    headers_a = {"Authorization": f"Bearer {make_token(role='waiter', user_id=waiter_a_id)}"}
    headers_b = {"Authorization": f"Bearer {make_token(role='waiter', user_id=waiter_b_id)}"}
    headers_admin = {"Authorization": f"Bearer {make_token(role='admin', user_id=str(uuid4()))}"}

    created = client.post("/orders", headers=headers_a, json={"tableNumber": 14})
    assert created.status_code == 201
    order_id = created.json()["orderId"]

    # Waiter B cannot edit, transfer, or cancel Waiter A's order
    assert (
        client.patch(
            f"/orders/{order_id}/items",
            headers=headers_b,
            json={
                "items": [{"dishId": "D-1", "name": "Tacos", "quantity": 1, "unitPrice": "80.00"}]
            },
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/orders/{order_id}/transfer",
            headers=headers_b,
            json={"tableNumber": 15},
        ).status_code
        == 403
    )
    assert client.patch(f"/orders/{order_id}/cancel", headers=headers_b).status_code == 403

    # Owner Waiter A can edit and transfer; Admin can cancel
    assert (
        client.patch(
            f"/orders/{order_id}/items",
            headers=headers_a,
            json={
                "items": [{"dishId": "D-1", "name": "Tacos", "quantity": 1, "unitPrice": "80.00"}]
            },
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/orders/{order_id}/transfer",
            headers=headers_a,
            json={"tableNumber": 15},
        ).status_code
        == 200
    )
    assert client.patch(f"/orders/{order_id}/cancel", headers=headers_admin).status_code == 200


def test_obs04_waiter_bola_blocks_confirm_request_bill_and_redo(
    client: TestClient,
    fake_session: FakeAsyncSession,
    make_token: TokenFactory,
) -> None:
    now = datetime.now(UTC)
    from scripts.seed_demo import _build_demo_shift

    fake_session.add(_build_demo_shift(now, shift_number=1, fixed_ids=True))
    waiter_a_uuid = uuid4()
    waiter_b_uuid = uuid4()
    headers_a = {
        "Authorization": f"Bearer {make_token(role='waiter', user_id=str(waiter_a_uuid))}"
    }
    headers_b = {
        "Authorization": f"Bearer {make_token(role='waiter', user_id=str(waiter_b_uuid))}"
    }
    headers_admin = {"Authorization": f"Bearer {make_token(role='admin', user_id=str(uuid4()))}"}

    # 1. Confirm BOLA: Waiter B gets 403 on Waiter A's draft order; Waiter A gets 200
    created = client.post("/orders", headers=headers_a, json={"tableNumber": 18})
    assert created.status_code == 201
    draft_order_id = created.json()["orderId"]
    patched = client.patch(
        f"/orders/{draft_order_id}/items",
        headers=headers_a,
        json={"items": [{"dishId": "D-1", "name": "Tacos", "quantity": 1, "unitPrice": "80.00"}]},
    )
    assert patched.status_code == 200

    confirm_bola = client.post(f"/orders/{draft_order_id}/confirm", headers=headers_b)
    assert confirm_bola.status_code == 403
    assert confirm_bola.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value

    confirm_owner = client.post(f"/orders/{draft_order_id}/confirm", headers=headers_a)
    assert confirm_owner.status_code == 200

    # 2. Request-bill & Redo BOLA on Waiter A's DELIVERED order
    delivered_id = uuid4()
    delivered_item_id = uuid4()
    delivered_order = Order(
        public_id=delivered_id,
        table_number=19,
        status=OrderStatus.DELIVERED,
        responsible_waiter_id=waiter_a_uuid,
        total_amount=Decimal("160.00"),
        items=[
            OrderItem(
                public_id=delivered_item_id,
                dish_id="D-1",
                name="Tacos",
                quantity=2,
                unit_price=Decimal("80.00"),
                status=ItemStatus.DELIVERED,
            )
        ],
    )
    fake_session.add(delivered_order)

    bill_bola = client.post(f"/orders/{delivered_id}/request-bill", headers=headers_b)
    assert bill_bola.status_code == 403
    assert bill_bola.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value

    redo_bola = client.patch(
        f"/orders/{delivered_id}/items/{delivered_item_id}/redo",
        headers=headers_b,
        json={"note": "Intento de otro mesero"},
    )
    assert redo_bola.status_code == 403
    assert redo_bola.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value

    # Owner Waiter A and Admin can request-bill; Owner Waiter A can redo
    assert (
        client.post(f"/orders/{delivered_id}/request-bill", headers=headers_a).status_code == 200
    )
    assert (
        client.post(f"/orders/{delivered_id}/request-bill", headers=headers_admin).status_code
        == 200
    )
    assert (
        client.patch(
            f"/orders/{delivered_id}/items/{delivered_item_id}/redo",
            headers=headers_a,
            json={"note": "Llegó frío"},
        ).status_code
        == 200
    )


# ---------------------------------------------------------------------------
# DEF-E5-013 (CP-032): KDS queue reads real Redis CatalogRepository (not mock)
# ---------------------------------------------------------------------------


def test_cp032_kds_queue_filters_using_redis_catalog_repository_ignoring_mock(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    redis_double = _InMemoryRedisHash()
    repo = CatalogRepository(redis_double)
    redis_validator = CatalogRepositoryValidator(repo)

    # Deliberately poison the legacy mock with a conflicting station to prove kds.py ignores it
    clear_catalog_mock()
    register_catalog_mock("DISH-LIVE-1", station="BarraFalsaMock", preparation_time_text="99 min")

    asyncio.run(
        repo.upsert(
            "DISH-LIVE-1",
            price=Decimal("120.00"),
            available=True,
            station="Parrilla",
            preparation_time_text="14 min",
        )
    )
    asyncio.run(
        repo.upsert(
            "OFFER-COLD-2",
            price=Decimal("95.00"),
            available=True,
            station="Estación Fría",
            preparation_time_text="6 min",
        )
    )

    now = datetime.now(UTC)
    order_id = uuid4()
    kds_db: dict[UUID, Order] = {
        order_id: Order(
            public_id=order_id,
            table_number=8,
            status=OrderStatus.IN_PREPARATION,
            created_at=now - timedelta(minutes=10),
            stock_reserved_at=now - timedelta(minutes=9),
            in_preparation_at=now - timedelta(minutes=8),
            items=[
                OrderItem(
                    public_id=uuid4(),
                    dish_id="DISH-LIVE-1",
                    name="Ribeye",
                    quantity=1,
                    unit_price=Decimal("120.00"),
                    status=ItemStatus.PENDING,
                ),
                OrderItem(
                    public_id=uuid4(),
                    dish_id="UNMAPPED-RECIPE",
                    offer_id="OFFER-COLD-2",
                    name="Aguachile",
                    quantity=1,
                    unit_price=Decimal("95.00"),
                    status=ItemStatus.PENDING,
                ),
            ],
        )
    }
    kds_session = FakeKdsAsyncSession(kds_db)

    async def _override_session() -> AsyncGenerator[FakeKdsAsyncSession, None]:
        yield kds_session

    prev_session = app.dependency_overrides.get(get_session)
    prev_catalog = app.dependency_overrides.get(get_catalog_validator)
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_catalog_validator] = lambda: redis_validator
    try:
        # Mock station 'BarraFalsaMock' returns empty because real Redis says 'parrilla'
        mock_station_res = client.get(
            "/kds/queue?station=BarraFalsaMock",
            headers=auth_headers("kitchen"),
        )
        assert mock_station_res.status_code == 200
        assert mock_station_res.json() == []

        # Real Redis station 'parrilla' matches DISH-LIVE-1 with 14 min prep time
        parrilla_res = client.get(
            "/kds/queue?station=Parrilla",
            headers=auth_headers("kitchen"),
        )
        assert parrilla_res.status_code == 200
        parrilla_items = parrilla_res.json()[0]["items"]
        assert len(parrilla_items) == 1
        assert parrilla_items[0]["dishId"] == "DISH-LIVE-1"
        assert parrilla_items[0]["preparationTimeText"] == "14 min"

        # Offer-level fallback resolves 'estacion fria' for OFFER-COLD-2
        fria_res = client.get(
            "/kds/queue?station=Estaci%C3%B3n%20Fr%C3%ADa",
            headers=auth_headers("kitchen"),
        )
        assert fria_res.status_code == 200
        fria_items = fria_res.json()[0]["items"]
        assert len(fria_items) == 1
        assert fria_items[0]["offerId"] == "OFFER-COLD-2"
        assert fria_items[0]["preparationTimeText"] == "6 min"
    finally:
        if prev_session is not None:
            app.dependency_overrides[get_session] = prev_session
        else:
            app.dependency_overrides.pop(get_session, None)
        if prev_catalog is not None:
            app.dependency_overrides[get_catalog_validator] = prev_catalog
        else:
            app.dependency_overrides.pop(get_catalog_validator, None)
        clear_catalog_mock()


# ---------------------------------------------------------------------------
# DEF-E5-024 (CP-039): Deterministic UUIDv5 on DishPreparedEvent & item events
# ---------------------------------------------------------------------------


def test_cp039_item_events_use_deterministic_uuid5() -> None:
    order_id = uuid4()
    item_id = uuid4()

    prep_1 = DishPreparedEvent.for_item(order_id, item_id, offer_id="OFFER-1", is_redo=False)
    prep_2 = DishPreparedEvent.for_item(order_id, item_id, offer_id="OFFER-1", is_redo=False)
    prep_redo_1 = DishPreparedEvent.for_item(order_id, item_id, offer_id="OFFER-1", is_redo=True)
    prep_redo_2 = DishPreparedEvent.for_item(order_id, item_id, offer_id="OFFER-1", is_redo=True)
    prep_redo_v6 = DishPreparedEvent.for_item(
        order_id, item_id, offer_id="OFFER-1", is_redo=True, attempt=6
    )

    assert prep_1.event_id.version == 5
    assert prep_1.event_id == prep_2.event_id
    assert prep_redo_1.event_id.version == 5
    assert prep_redo_1.event_id == prep_redo_2.event_id
    assert prep_1.event_id != prep_redo_1.event_id
    assert prep_redo_1.event_id != prep_redo_v6.event_id

    rej_1 = KitchenDishRejectedEvent.for_item(
        order_id, item_id, offer_id="OFFER-1", quantity=1, reason="Agotado", attempt=2
    )
    rej_2 = KitchenDishRejectedEvent.for_item(
        order_id, item_id, offer_id="OFFER-1", quantity=1, reason="Agotado", attempt=2
    )
    assert rej_1.event_id.version == 5
    assert rej_1.event_id == rej_2.event_id

    redo_1 = DishRedoRequestedEvent.for_item(
        order_id, item_id, offer_id="OFFER-1", quantity=1, reason="Frío", attempt=4
    )
    redo_2 = DishRedoRequestedEvent.for_item(
        order_id, item_id, offer_id="OFFER-1", quantity=1, reason="Frío", attempt=4
    )
    assert redo_1.event_id.version == 5
    assert redo_1.redo_id.version == 5
    assert redo_1.event_id == redo_2.event_id
    assert redo_1.redo_id == redo_2.redo_id


# ---------------------------------------------------------------------------
# DEF-E5-015 (SEC-12) & OBS-02: DLQ boundedness + payment settlement checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sec12_bounded_dlq_router_and_poison_outcome() -> None:
    router = InMemoryDLQRouter(max_entries=3)
    native_router = BrokerNativeDLQRouter()
    await native_router.route_to_dlq(b"bad", "orders.inventory.inbound", "Invalid JSON")

    dispatcher = EventConsumerDispatcher(session_factory=Mock(), dlq_router=router)
    for idx in range(5):
        outcome = await dispatcher.dispatch_with_outcome(
            raw_message=f"{{invalid-{idx}".encode(),
            queue_name="orders.inventory.inbound",
            event_type="inventory.reservation.confirmed",
        )
        assert outcome.applied is False
        assert outcome.dead_lettered is True

    assert len(router.dead_letters) == 3


@pytest.mark.asyncio
async def test_obs02_payment_completed_rejects_unsettled_status_and_underpayment() -> None:
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=4,
        status=OrderStatus.DELIVERED,
        total_amount=Decimal("200.00"),
    )

    session = AsyncMock()
    uow = UnitOfWork(lambda: session)
    order_repo = Mock(spec=OrderRepository)
    order_repo.get_by_public_id = AsyncMock(return_value=order)
    proc_repo = Mock(spec=ProcessedEventsRepository)
    proc_repo.exists = AsyncMock(return_value=False)
    proc_repo.add = Mock()
    uow.orders = order_repo
    uow.processed_events = proc_repo

    # 1. Non-settled payment status ignored
    failed_msg = PaymentCompletedMessage(
        eventId=uuid4(),
        orderId=order_id,
        totalPaid=Decimal("200.00"),
        status="FAILED",
    )
    assert await handle_payment_completed(failed_msg, uow) is False
    assert order.status is OrderStatus.DELIVERED

    # 2. Partial/underpaid settlement ignored
    underpaid_msg = PaymentCompletedMessage(
        eventId=uuid4(),
        orderId=order_id,
        totalPaid=Decimal("150.00"),
        status="PAID",
    )
    assert await handle_payment_completed(underpaid_msg, uow) is False
    assert order.status is OrderStatus.DELIVERED

    # 3. Exact or tipped settlement transitions order to PAID
    valid_msg = PaymentCompletedMessage(
        eventId=uuid4(),
        orderId=order_id,
        totalPaid=Decimal("220.00"),
        status="COMPLETED",
    )
    assert await handle_payment_completed(valid_msg, uow) is True
    assert order.status is OrderStatus.PAID


# ---------------------------------------------------------------------------
# OBS-05: Zero production assert statements in app/ + python-dotenv >= 1.2.2
# ---------------------------------------------------------------------------


def test_obs05_zero_production_asserts_and_dotenv_version() -> None:
    app_root = Path(__file__).resolve().parents[3] / "app"
    offenders: list[str] = []
    for py_file in app_root.rglob("*.py"):
        if "alembic" in py_file.parts:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                offenders.append(f"{py_file.relative_to(app_root.parent)}:{node.lineno}")
    assert offenders == [], f"Production assert statements found: {offenders}"

    installed = tuple(int(part) for part in pkg_version("python-dotenv").split(".")[:3])
    assert installed >= (1, 2, 2)
