"""INT-04 (IT-13 & IT-14): Dual-contract Menu reconciliation, async sync, and live Menu-Backend.

Covers:
1. Contract-accurate reconciliation against Menu-Backend v8.0.1 ``GET /api/v1/menu/catalog``
   (pagination, categories, ``GET /api/v1/menu/offers/{offerId}`` enrichment, Bearer JWT with
   ``menu:catalog:read`` / ``menu:offers:read``, station mapping + ``general`` fallback).
2. Source-to-offer live availability propagation (``inventory.source.availability.updated``,
   ``menu.item.agotado``, ``menu.item.disponible``, ``menu.source.deleted``).
3. Dual-ID persistence & propagation end-to-end (``offerId`` vs ``sourceId``/``plateId`` +
   authoritative ``unitPrice`` override + KDS station filtering).
4. Live end-to-end execution against the real ``Menu-Backend`` Spring Boot service + real
   PostgreSQL + Redis + RabbitMQ when ``LIVE_MENU_BASE_URL`` (or ``:8080``) is reachable.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
import zlib
from collections.abc import AsyncGenerator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import aio_pika
import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cache.catalog import CatalogRepository, RedisHashAdapter
from app.db.models import Order
from app.db.session import get_session
from app.domain.errors import DishUnavailableError
from app.domain.order_status import ItemStatus
from app.main import app, create_app
from app.messaging.consumers.base import BrokerNativeDLQRouter, EventConsumerDispatcher
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.events import EventName
from app.services.catalog import CatalogRepositoryValidator, get_catalog_validator
from app.services.shifts import ShiftService
from app.workers.catalog_reconciliation import CatalogReconciliationWorker
from app.workers.inbound_consumer import InboundConsumerWorker
from scripts.seed_demo import _build_demo_shift
from tests.conftest import TEST_JWT_SECRET
from tests.unit.conftest import FakeAsyncSession
from tests.unit.test_kds_queue import FakeKdsAsyncSession

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requirement("RF-02", "RF-08", "RF-17", "RF-40", "RF-41"),
]

_CAT_PARRILLA = "10000000-0000-0000-0000-000000000001"
_CAT_BEBIDAS = "10000000-0000-0000-0000-000000000002"
_CAT_OTROS = "10000000-0000-0000-0000-000000000003"

_OFFER_BURGER = "20000000-0000-0000-0000-000000000001"
_RECIPE_BURGER = "30000000-0000-0000-0000-000000000001"
_OPT_BURGER = "40000000-0000-0000-0000-000000000001"

_OFFER_LEMONADE = "20000000-0000-0000-0000-000000000002"
_RECIPE_LEMONADE = "30000000-0000-0000-0000-000000000002"
_OPT_LEMONADE = "40000000-0000-0000-0000-000000000002"

_OFFER_SPECIAL = "20000000-0000-0000-0000-000000000003"
_RECIPE_SPECIAL = "30000000-0000-0000-0000-000000000003"
_OPT_SPECIAL = "40000000-0000-0000-0000-000000000003"


class _MemRedisHash:
    def __init__(self) -> None:
        self.store: dict[str, dict[object, object]] = {}

    async def hgetall(self, name: str) -> Mapping[object, object]:
        return dict(self.store.get(name, {}))

    async def hset(self, name: str, mapping: Mapping[str, str]) -> int:
        self.store.setdefault(name, {}).update(mapping)
        return len(mapping)


def _mint_menu_admin_jwt(secret: str = TEST_JWT_SECRET) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000004",
            "user_id": "00000000-0000-0000-0000-000000000004",
            "role": "admin",
            "roles": ["admin", "waiter", "kitchen", "chef"],
            "permissions": ["*"],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=30)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )


def _catalog_entry_json(
    entry_id: str,
    brand: str,
    cat_id: str,
    offer_id: str,
    price: float,
    course: str,
    opt_id: str,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "brandName": brand,
        "description": brand,
        "categoryIds": [cat_id],
        "isAvailable": True,
        "offers": [
            {
                "id": offer_id,
                "presentationTag": "Individual",
                "basePrice": price,
                "isAvailable": True,
                "composition": {
                    "slots": [
                        {
                            "slotId": str(uuid4()),
                            "name": course,
                            "quantity": 1,
                            "course": course,
                            "isAvailable": True,
                            "options": [
                                {"optionId": opt_id, "displayName": brand, "isAvailable": True}
                            ],
                        }
                    ]
                },
            }
        ],
    }


def _build_menu_v1_mock_handler(
    seen_auth_headers: list[str],
) -> Callable[[httpx.Request], httpx.Response]:
    offer_details = {
        f"/api/v1/menu/offers/{_OFFER_BURGER}": (
            _OFFER_BURGER,
            165.00,
            "parrilla",
            _OPT_BURGER,
            _RECIPE_BURGER,
        ),
        f"/api/v1/menu/offers/{_OFFER_LEMONADE}": (
            _OFFER_LEMONADE,
            45.00,
            "bebida",
            _OPT_LEMONADE,
            _RECIPE_LEMONADE,
        ),
        f"/api/v1/menu/offers/{_OFFER_SPECIAL}": (
            _OFFER_SPECIAL,
            210.00,
            "plato fuerte",
            _OPT_SPECIAL,
            _RECIPE_SPECIAL,
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth_headers.append(request.headers.get("Authorization", ""))
        path = request.url.path
        if path == "/api/v1/menu/catalog/categories":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": _CAT_PARRILLA, "name": "Parrilla", "entryCount": 1},
                        {"id": _CAT_BEBIDAS, "name": "Bebidas", "entryCount": 1},
                        {"id": _CAT_OTROS, "name": "Especialidades de Casa", "entryCount": 1},
                    ],
                    "uncategorized": {"entryCount": 0},
                },
            )
        if path == "/api/v1/menu/catalog":
            page = int(dict(request.url.params).get("page", "1"))
            entries = (
                [
                    _catalog_entry_json(
                        "50000000-0000-0000-0000-000000000001",
                        "Hamburguesa Clásica",
                        _CAT_PARRILLA,
                        _OFFER_BURGER,
                        165.00,
                        "parrilla",
                        _OPT_BURGER,
                    )
                ]
                if page == 1
                else [
                    _catalog_entry_json(
                        "50000000-0000-0000-0000-000000000002",
                        "Limonada Natural",
                        _CAT_BEBIDAS,
                        _OFFER_LEMONADE,
                        45.00,
                        "bebida",
                        _OPT_LEMONADE,
                    ),
                    _catalog_entry_json(
                        "50000000-0000-0000-0000-000000000003",
                        "Especial del Chef",
                        _CAT_OTROS,
                        _OFFER_SPECIAL,
                        210.00,
                        "plato fuerte",
                        _OPT_SPECIAL,
                    ),
                ]
            )
            return httpx.Response(
                200,
                json={
                    "data": entries,
                    "meta": {"page": page, "pageSize": 1, "totalItems": 3, "totalPages": 2},
                },
            )
        if path in offer_details:
            oid, price, course, opt_id, rec_id = offer_details[path]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": oid,
                        "entryId": str(uuid4()),
                        "presentationTag": "Individual",
                        "basePrice": price,
                        "status": "ACTIVE",
                        "isAvailable": True,
                        "composition": {
                            "slots": [
                                {
                                    "slotId": str(uuid4()),
                                    "name": "Slot",
                                    "quantity": 1,
                                    "course": course,
                                    "isAvailable": True,
                                    "options": [
                                        {
                                            "optionId": opt_id,
                                            "sourceType": "RECIPE",
                                            "recipeId": rec_id,
                                            "status": "ACTIVE",
                                            "displayName": "Opción",
                                            "isAvailable": True,
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                },
            )
        return httpx.Response(404, json={"error": {"message": "Not found"}})

    return handler


def _assert_dual_ids_and_kds_stations(validator: CatalogRepositoryValidator) -> None:
    fake_db: dict[UUID, Order] = {}
    fake_sess = FakeAsyncSession(fake_db)
    fake_sess.add(_build_demo_shift(datetime.now(UTC), shift_number=1, fixed_ids=True))

    async def _get_sess() -> AsyncGenerator[FakeAsyncSession, None]:
        yield fake_sess

    prev_sess = app.dependency_overrides.get(get_session)
    prev_cat = app.dependency_overrides.get(get_catalog_validator)
    app.dependency_overrides[get_session] = _get_sess
    app.dependency_overrides[get_catalog_validator] = lambda: validator
    try:
        token_waiter = _mint_menu_admin_jwt()
        headers = {"Authorization": f"Bearer {token_waiter}"}
        with TestClient(app) as client:
            r_order = client.post(
                "/orders",
                headers=headers,
                json={"tableNumber": 7, "isTakeaway": False},
            )
            assert r_order.status_code == 201
            oid = UUID(r_order.json()["orderId"])

            r_items = client.patch(
                f"/orders/{oid}/items",
                headers=headers,
                json={
                    "items": [
                        {
                            "dishId": _RECIPE_BURGER,
                            "sourceId": _RECIPE_BURGER,
                            "offerId": _OFFER_BURGER,
                            "name": "Hamburguesa Clásica",
                            "quantity": 1,
                            "unitPrice": "1.00",
                        },
                        {
                            "dishId": _RECIPE_LEMONADE,
                            "sourceId": _RECIPE_LEMONADE,
                            "offerId": _OFFER_LEMONADE,
                            "name": "Limonada Natural",
                            "quantity": 2,
                            "unitPrice": "1.00",
                        },
                        {
                            "dishId": _RECIPE_SPECIAL,
                            "sourceId": _RECIPE_SPECIAL,
                            "offerId": _OFFER_SPECIAL,
                            "name": "Especial del Chef",
                            "quantity": 1,
                            "unitPrice": "1.00",
                        },
                    ]
                },
            )
            assert r_items.status_code == 200
            assert Decimal(r_items.json()["totalAmount"]) == Decimal("465.00")

            r_confirm = client.post(f"/orders/{oid}/confirm", headers=headers)
            assert r_confirm.status_code == 200
            outbox_items = fake_sess.outbox_rows[0].payload["items"]
            assert isinstance(outbox_items, list)
            assert outbox_items[0] == {
                "offerId": _OFFER_BURGER,
                "plateId": _RECIPE_BURGER,
                "sourceId": _RECIPE_BURGER,
                "quantity": 1,
            }

            persisted_order = fake_db[oid]
            persisted_order.mark_reserved()
            persisted_order.mark_in_preparation()
            for it in persisted_order.items:
                it.status = ItemStatus.PENDING

            kds_sess = FakeKdsAsyncSession({oid: persisted_order})

            async def _get_kds_sess() -> AsyncGenerator[FakeKdsAsyncSession, None]:
                yield kds_sess

            app.dependency_overrides[get_session] = _get_kds_sess
            for station_name, expected_offer in (
                ("parrilla", _OFFER_BURGER),
                ("barra", _OFFER_LEMONADE),
                ("general", _OFFER_SPECIAL),
            ):
                res = client.get(f"/kds/queue?station={station_name}", headers=headers)
                assert res.status_code == 200
                assert [i["offerId"] for i in res.json()[0]["items"]] == [expected_offer]
    finally:
        if prev_sess is not None:
            app.dependency_overrides[get_session] = prev_sess
        else:
            app.dependency_overrides.pop(get_session, None)
        if prev_cat is not None:
            app.dependency_overrides[get_catalog_validator] = prev_cat
        else:
            app.dependency_overrides.pop(get_catalog_validator, None)


@pytest.mark.asyncio
async def test_int04_menu_v1_catalog_reconciliation_dual_ids_station_and_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconcile Menu-Backend v8.0.1 paginated catalog + offers and verify dual IDs + KDS."""
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    redis_mem = _MemRedisHash()
    catalog_repo = CatalogRepository(redis_mem)
    validator = CatalogRepositoryValidator(catalog_repo)
    seen_auth_headers: list[str] = []

    transport = httpx.MockTransport(_build_menu_v1_mock_handler(seen_auth_headers))
    async with httpx.AsyncClient(transport=transport) as http_client:
        worker = CatalogReconciliationWorker(
            catalog_repo,
            sync_url="http://menu.local:8080/api/v1/menu/catalog",
            interval_seconds=None,
            http_client=http_client,
        )
        updated, discrepancies = await worker.reconcile_once(http_client)

    assert discrepancies == 0
    assert updated >= 6
    assert all(h.startswith("Bearer ") for h in seen_auth_headers)
    assert await catalog_repo.get_price(_OFFER_BURGER) == Decimal("165.00")
    assert await catalog_repo.get_price(_RECIPE_BURGER) == Decimal("165.00")
    assert (await catalog_repo.get_catalog_info(_OFFER_BURGER))["station"] == "parrilla"
    assert (await catalog_repo.get_catalog_info(_OFFER_LEMONADE))["station"] == "barra"
    assert (await catalog_repo.get_catalog_info(_OFFER_SPECIAL))["station"] is None

    # Live availability propagation from sourceId to commercial offerId
    consumer = CatalogConsumer(catalog_repo)
    await consumer.consume(
        EventName.INVENTORY_SOURCE_AVAILABILITY_UPDATED,
        {"sourceId": _RECIPE_BURGER, "isAvailable": False},
    )
    with pytest.raises(DishUnavailableError):
        await validator.validate_dish_available(_RECIPE_BURGER)
    with pytest.raises(DishUnavailableError):
        await validator.validate_dish_available(_OFFER_BURGER)

    await consumer.consume(EventName.MENU_ITEM_AVAILABLE, {"sourceId": _RECIPE_BURGER})
    await validator.validate_dish_available(_RECIPE_BURGER)
    await validator.validate_dish_available(_OFFER_BURGER)

    _assert_dual_ids_and_kds_stations(validator)


def _tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _make_valid_png_bytes() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    raw_rows = b"\x00" + (b"\xd9\x77\x06" * 2) + b"\x00" + (b"\xd9\x77\x06" * 2)
    idat = zlib.compress(raw_rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


_TINY_PNG_BYTES = _make_valid_png_bytes()


async def _seed_live_menu_offer(
    menu_client: httpx.AsyncClient,
    menu_headers: dict[str, str],
    amqp_ch: aio_pika.abc.AbstractChannel,
    catalog_repo: CatalogRepository,
) -> tuple[str, str, str]:
    """Create image, category, recipe, entry, and active offer in live Menu-Backend; return IDs."""
    inv_res = await menu_client.get("/api/v1/items")
    assert inv_res.status_code == 200, inv_res.text
    inv_item_id = inv_res.json()[0]["id"]

    img_res = await menu_client.post(
        "/api/v1/menu/media/images",
        headers={**menu_headers, "Idempotency-Key": str(uuid4())},
        files={"file": ("arrachera.png", _TINY_PNG_BYTES, "image/png")},
    )
    assert img_res.status_code == 201, img_res.text
    image_id = str(img_res.json()["data"]["id"])

    suffix = uuid4().hex[:6]
    cat_res = await menu_client.post(
        "/api/v1/menu/categories",
        headers={**menu_headers, "Idempotency-Key": str(uuid4())},
        json={"name": f"Parrilla {suffix}", "description": "Cortes y hamburguesas"},
    )
    assert cat_res.status_code == 201, cat_res.text
    category_id = cat_res.json()["data"]["id"]

    rec_res = await menu_client.post(
        "/api/v1/menu/recipes",
        headers={**menu_headers, "Idempotency-Key": str(uuid4())},
        json={
            "name": f"Receta Arrachera {suffix}",
            "description": "Arrachera marinada",
            "instructions": "Asar en parrilla 8 minutos",
            "ingredients": [{"inventoryItemId": inv_item_id, "quantity": 1}],
        },
    )
    assert rec_res.status_code == 201, rec_res.text
    recipe_id = str(rec_res.json()["data"]["id"])

    projected_source: dict[str, str] = {}
    for _ in range(30):
        projected_source = await catalog_repo.get(recipe_id)
        if projected_source:
            break
        await asyncio.sleep(0.2)
    assert projected_source.get("available") == "true"

    inv_ex = await amqp_ch.declare_exchange(
        "inventory.topic", aio_pika.ExchangeType.TOPIC, durable=True
    )
    await inv_ex.publish(
        aio_pika.Message(
            body=json.dumps({"sourceId": recipe_id, "isAvailable": True}).encode(),
            message_id=str(uuid4()),
        ),
        routing_key="inventory.source.availability.updated",
    )

    entry_res = await menu_client.post(
        "/api/v1/menu/entries",
        headers={**menu_headers, "Idempotency-Key": str(uuid4())},
        json={
            "brandName": f"Arrachera Norteña {suffix}",
            "description": "Corte jugoso",
            "categoryIds": [category_id],
            "imageId": image_id,
        },
    )
    assert entry_res.status_code == 201, entry_res.text
    entry_id = entry_res.json()["data"]["id"]
    act_entry = await menu_client.patch(
        f"/api/v1/menu/entries/{entry_id}",
        headers={**menu_headers, "If-Match": entry_res.headers["ETag"]},
        json={"status": "ACTIVE"},
    )
    assert act_entry.status_code == 200, act_entry.text

    offer_res = await menu_client.post(
        f"/api/v1/menu/entries/{entry_id}/offers",
        headers={**menu_headers, "Idempotency-Key": str(uuid4())},
        json={
            "presentationTag": "300g Individual",
            "basePrice": 189.50,
            "imageId": image_id,
            "composition": {
                "slots": [
                    {
                        "name": "Parrilla",
                        "quantity": 1,
                        "course": "plato fuerte",
                        "options": [
                            {
                                "sourceType": "RECIPE",
                                "recipeId": recipe_id,
                                "displayName": "Arrachera al carbón",
                                "status": "ACTIVE",
                            }
                        ],
                    }
                ]
            },
        },
    )
    assert offer_res.status_code == 201, offer_res.text
    offer_id = str(offer_res.json()["data"]["id"])
    act_offer = await menu_client.patch(
        f"/api/v1/menu/offers/{offer_id}",
        headers={
            **menu_headers,
            "If-Match": offer_res.headers["ETag"],
            "Idempotency-Key": str(uuid4()),
        },
        json={"status": "ACTIVE"},
    )
    assert act_offer.status_code == 200, act_offer.text

    for _ in range(25):
        chk = await menu_client.get(f"/api/v1/menu/offers/{offer_id}", headers=menu_headers)
        if chk.status_code == 200 and chk.json()["data"]["isAvailable"] is True:
            break
        await asyncio.sleep(0.2)

    return suffix, recipe_id, offer_id


async def _verify_live_orders_kds_and_depletion(
    session_maker: async_sessionmaker[AsyncSession],
    amqp_ch: aio_pika.abc.AbstractChannel,
    catalog_repo: CatalogRepository,
    admin_jwt: str,
    suffix: str,
    recipe_id: str,
    offer_id: str,
) -> None:
    live_app = create_app()
    auth_hdr = {"Authorization": f"Bearer {admin_jwt}"}
    transport = httpx.ASGITransport(app=live_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://orders.local") as client:
        async with session_maker() as s_chk:
            snap = await ShiftService(s_chk).get_current_status()
        if not snap.can_accept_orders:
            await client.post("/shifts/open", headers=auth_hdr, json={"notes": "INT-04 live"})

        r_create = await client.post(
            "/orders",
            headers=auth_hdr,
            json={"tableNumber": 18, "isTakeaway": False},
        )
        assert r_create.status_code == 201, r_create.text
        order_id = UUID(r_create.json()["orderId"])

        r_patch = await client.patch(
            f"/orders/{order_id}/items",
            headers=auth_hdr,
            json={
                "items": [
                    {
                        "dishId": recipe_id,
                        "sourceId": recipe_id,
                        "offerId": offer_id,
                        "name": f"Arrachera Norteña {suffix}",
                        "quantity": 2,
                        "unitPrice": "5.00",
                    }
                ]
            },
        )
        assert r_patch.status_code == 200, r_patch.text
        assert Decimal(r_patch.json()["totalAmount"]) == Decimal("379.00")

        assert (
            await client.post(f"/orders/{order_id}/confirm", headers=auth_hdr)
        ).status_code == 200

        async with session_maker() as prep_s:
            db_order = await prep_s.scalar(select(Order).where(Order.public_id == order_id))
            assert db_order is not None
            assert db_order.items[0].dish_id == recipe_id
            assert db_order.items[0].offer_id == offer_id
            db_order.mark_reserved()
            db_order.mark_in_preparation()
            await prep_s.commit()

        r_kds = await client.get("/kds/queue?station=parrilla", headers=auth_hdr)
        assert r_kds.status_code == 200
        matched = [o for o in r_kds.json() if o["orderId"] == str(order_id)]
        assert len(matched) == 1
        assert matched[0]["items"][0]["offerId"] == offer_id
        assert matched[0]["items"][0]["dishId"] == recipe_id

        menu_ex = await amqp_ch.declare_exchange(
            "menu.topic", aio_pika.ExchangeType.TOPIC, durable=True
        )
        await menu_ex.publish(
            aio_pika.Message(
                body=json.dumps({"sourceId": recipe_id}).encode(),
                message_id=str(uuid4()),
            ),
            routing_key=EventName.MENU_ITEM_DEPLETED,
        )
        for _ in range(25):
            if (await catalog_repo.get(offer_id)).get("available") == "false":
                break
            await asyncio.sleep(0.2)

        r_draft2 = await client.post("/orders", headers=auth_hdr, json={"isTakeaway": True})
        oid2 = r_draft2.json()["orderId"]
        r_blocked = await client.patch(
            f"/orders/{oid2}/items",
            headers=auth_hdr,
            json={
                "items": [
                    {
                        "dishId": recipe_id,
                        "offerId": offer_id,
                        "name": "Arrachera Agotada",
                        "quantity": 1,
                        "unitPrice": "189.50",
                    }
                ]
            },
        )
        assert r_blocked.status_code == 422
        assert r_blocked.json()["error"]["code"] == "DISH_UNAVAILABLE"


@pytest.mark.asyncio
async def test_int04_live_menu_backend_it13_and_it14_e2e(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute IT-13 & IT-14 against real Menu-Backend + Postgres + Redis + RabbitMQ."""
    menu_base = os.getenv("LIVE_MENU_BASE_URL", "http://127.0.0.1:8080").strip().rstrip("/")
    parsed_menu = urlsplit(menu_base)
    require_menu = os.getenv("LIVE_MENU_REQUIRED", "").strip().lower() in {"1", "true", "yes"}
    if not _tcp_open(parsed_menu.hostname or "127.0.0.1", parsed_menu.port or 8080):
        if require_menu:
            pytest.fail(f"LIVE_MENU_REQUIRED=1 but Menu-Backend unreachable at {menu_base}")
        pytest.skip(f"Real Menu-Backend not running at {menu_base}; skipping live Menu E2E")

    db_url = (
        os.getenv("LIVE_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
        or "postgresql+asyncpg://ordenes@127.0.0.1:5432/ordenes"
    )
    redis_url = os.getenv("LIVE_REDIS_URL", os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
    amqp_url = os.getenv(
        "LIVE_RABBITMQ_URL",
        os.getenv("RABBITMQ_URL", "amqp://ordenes:ordenes@127.0.0.1:5672/"),
    )
    jwt_secret = os.getenv("LIVE_JWT_SECRET") or os.getenv("JWT_SECRET") or TEST_JWT_SECRET

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("JWT_SECRET", jwt_secret)
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "false")

    import app.db.session as db_session
    import app.services.catalog as catalog_svc

    monkeypatch.setattr(db_session, "_engine", None)
    monkeypatch.setattr(db_session, "_session_factory", None)
    monkeypatch.setattr(catalog_svc, "_redis_validator", None)

    admin_jwt = _mint_menu_admin_jwt(jwt_secret)
    menu_headers = {"Authorization": f"Bearer {admin_jwt}"}
    engine = create_async_engine(db_url, echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    redis_client = Redis.from_url(redis_url, decode_responses=True)
    catalog_repo = CatalogRepository(RedisHashAdapter(redis_client))
    dispatcher = EventConsumerDispatcher(
        session_factory=session_maker,
        dlq_router=BrokerNativeDLQRouter(),
        catalog_consumer=CatalogConsumer(catalog_repo),
    )

    queue_name = f"orders.int04.live.{uuid4().hex[:8]}"
    dlx_name = f"orders.int04.dlx.{uuid4().hex[:8]}"
    worker = InboundConsumerWorker(
        dispatcher=dispatcher,
        url=amqp_url,
        vhost="/",
        queue_name=queue_name,
        dlx_exchange=dlx_name,
    )
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(worker.run(stop_event))
    amqp_conn = await aio_pika.connect_robust(amqp_url, virtualhost="/")

    try:
        amqp_ch = await amqp_conn.channel()
        for _ in range(30):
            try:
                await amqp_ch.declare_queue(queue_name, passive=True)
                break
            except Exception:
                await asyncio.sleep(0.2)

        async with httpx.AsyncClient(base_url=menu_base, timeout=15.0) as menu_client:
            suffix, recipe_id, offer_id = await _seed_live_menu_offer(
                menu_client, menu_headers, amqp_ch, catalog_repo
            )
            reconciler = CatalogReconciliationWorker(
                catalog_repo,
                sync_url=f"{menu_base}/api/v1/menu/catalog",
                interval_seconds=None,
                sync_token=admin_jwt,
                http_client=menu_client,
            )
            updated_count, discrepancy_count = await reconciler.reconcile_once(menu_client)
            assert updated_count >= 2
            assert discrepancy_count == 0
            assert await catalog_repo.get_price(offer_id) == Decimal("189.50")
            assert (await catalog_repo.get_catalog_info(offer_id))["station"] == "parrilla"

        await _verify_live_orders_kds_and_depletion(
            session_maker, amqp_ch, catalog_repo, admin_jwt, suffix, recipe_id, offer_id
        )
    finally:
        stop_event.set()
        await asyncio.wait_for(worker_task, timeout=10.0)
        clean_ch = await amqp_conn.channel()
        await clean_ch.queue_delete(queue_name)
        await clean_ch.queue_delete(f"{queue_name}.dlq")
        await clean_ch.exchange_delete(dlx_name)
        await amqp_conn.close()
        if catalog_svc._redis_validator is not None:
            await catalog_svc._redis_validator._repository._redis._redis.aclose()  # type: ignore[attr-defined]
            monkeypatch.setattr(catalog_svc, "_redis_validator", None)
        if db_session._engine is not None:
            await db_session._engine.dispose()
            monkeypatch.setattr(db_session, "_engine", None)
            monkeypatch.setattr(db_session, "_session_factory", None)
        await redis_client.aclose()
        await engine.dispose()
