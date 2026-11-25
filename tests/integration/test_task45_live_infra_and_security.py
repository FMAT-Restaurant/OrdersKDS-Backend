"""Live infrastructure integration & container smoke tests (TASK-45).

Verifies real PostgreSQL, Redis, RabbitMQ DLQ, and deployed container WebSocket + SEC-03/04:
1. DEF-E5-001 / OBS-06: Real PostgreSQL POST /orders (table + takeaway) with AsyncSession
   and expire_on_commit=False (zero MissingGreenlet errors) + DB persistence check.
2. DEF-E5-010 / DEF-E5-013: Real Redis CatalogRepository rejecting absent dishes with 422
   DISH_UNAVAILABLE and driving GET /kds/queue?station= filtering.
3. DEF-E5-015 / CP-044 / SEC-12: Real RabbitMQ x-dead-letter-exchange + *.dlq routing when
   InboundConsumerWorker rejects malformed/poison messages with nack(requeue=False), while
   continuing to process valid messages.
4. DEF-E5-017 / OBS-06 / OBS-08: Real WebSocket handshake & channel subscription smoke test
    plus SEC-03 / SEC-04 authentication and role-matrix security verification against the
   deployed Docker container.

When ``LIVE_INFRA_REQUIRED=1`` (set in GitHub Actions CI), missing infrastructure fails hard;
in local unit-only environments without running services, tests skip cleanly with reason.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import aio_pika
import httpx
import jwt
import pytest
import websockets
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from websockets.exceptions import ConnectionClosed

from app.cache.catalog import CatalogRepository, RedisHashAdapter
from app.db.models import Order
from app.domain.order_status import OrderStatus
from app.main import create_app
from app.messaging.consumers.base import BrokerNativeDLQRouter, EventConsumerDispatcher
from app.services.shifts import ShiftService
from app.workers.inbound_consumer import InboundConsumerWorker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requirement("RF-08", "RF-12", "RF-13", "RF-17", "RNF-02", "RNF-03", "RNF-04"),
]


def _require_live_infra() -> bool:
    return os.getenv("LIVE_INFRA_REQUIRED", "").strip().lower() in {"1", "true", "yes"}


def _tcp_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _endpoint_or_skip(env_name: str, default_url: str, default_port: int) -> str:
    url = os.getenv(env_name, "").strip() or default_url
    parsed = urlsplit(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or default_port
    if not _tcp_reachable(host, port):
        if _require_live_infra():
            pytest.fail(f"Required live infrastructure unreachable at {host}:{port} ({env_name})")
        pytest.skip(f"Live service {env_name} not reachable at {host}:{port}")
    return url


def _sign_live_jwt(
    *,
    secret: str,
    role: str = "waiter",
    user_id: str | None = None,
    ttl_seconds: int | None = 900,
    algorithm: str = "HS256",
) -> str:
    uid = user_id or str(uuid4())
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": uid,
        "user_id": uid,
        "role": role,
        "roles": [role],
        "iat": int(now.timestamp()),
    }
    if ttl_seconds is not None:
        claims["exp"] = int((now + timedelta(seconds=ttl_seconds)).timestamp())
    if algorithm == "none":
        hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode("ascii")
        body = (
            base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8"))
            .rstrip(b"=")
            .decode("ascii")
        )
        return f"{hdr}.{body}."
    return jwt.encode(claims, secret, algorithm=algorithm)


@pytest.mark.asyncio
async def test_live_postgres_and_redis_post_orders_catalog_and_kds_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEF-E5-001, DEF-E5-010, DEF-E5-013 against real PostgreSQL and Redis."""
    db_url = _endpoint_or_skip(
        "LIVE_DATABASE_URL",
        os.getenv("DATABASE_URL", "postgresql+asyncpg://ordenes:ordenes@127.0.0.1:5432/ordenes"),
        5432,
    )
    redis_url = _endpoint_or_skip(
        "LIVE_REDIS_URL",
        os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        6379,
    )
    jwt_secret = os.getenv("LIVE_JWT_SECRET") or os.getenv("JWT_SECRET") or uuid4().hex * 2

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("JWT_SECRET", jwt_secret)
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "false")

    import app.db.session as db_session
    import app.services.catalog as catalog_svc

    monkeypatch.setattr(db_session, "_engine", None)
    monkeypatch.setattr(db_session, "_session_factory", None)
    monkeypatch.setattr(catalog_svc, "_redis_validator", None)

    engine = create_async_engine(db_url, echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    redis_client = Redis.from_url(redis_url, decode_responses=True)
    catalog_repo = CatalogRepository(RedisHashAdapter(redis_client))

    live_dish = f"PLATE-IT-LIVE-{uuid4().hex[:8]}"
    await catalog_repo.upsert(
        live_dish,
        price=Decimal("185.00"),
        available=True,
        station="Barra Especial",
        preparation_time_text="7 min",
    )

    try:
        live_app = create_app()
        waiter_uid = str(uuid4())
        waiter_token = _sign_live_jwt(secret=jwt_secret, role="waiter", user_id=waiter_uid)
        kitchen_token = _sign_live_jwt(secret=jwt_secret, role="kitchen")
        admin_token = _sign_live_jwt(secret=jwt_secret, role="admin")

        transport = httpx.ASGITransport(app=live_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://live.local") as client:
            async with session_maker() as session:
                snapshot = await ShiftService(session).get_current_status()
            if not snapshot.can_accept_orders:
                open_res = await client.post(
                    "/shifts/open",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={"notes": "IT live shift"},
                )
                assert open_res.status_code == 201, open_res.text
            # 1. Real PostgreSQL POST /orders (table + takeaway) without MissingGreenlet
            r_table = await client.post(
                "/orders",
                headers={"Authorization": f"Bearer {waiter_token}"},
                json={"tableNumber": 33, "isTakeaway": False},
            )
            assert r_table.status_code == 201, r_table.text
            order_id = UUID(r_table.json()["orderId"])

            r_takeaway = await client.post(
                "/orders",
                headers={"Authorization": f"Bearer {waiter_token}"},
                json={"isTakeaway": True},
            )
            assert r_takeaway.status_code == 201, r_takeaway.text

            async with session_maker() as verify_session:
                persisted = await verify_session.scalar(
                    select(Order).where(Order.public_id == order_id)
                )
                assert persisted is not None
                assert persisted.table_number == 33
                assert persisted.status is OrderStatus.CREATED

            # 2. Absent dish in real Redis rejected with 422 DISH_UNAVAILABLE (CP-023)
            r_missing = await client.patch(
                f"/orders/{order_id}/items",
                headers={"Authorization": f"Bearer {waiter_token}"},
                json={
                    "items": [
                        {
                            "dishId": f"MISSING-{uuid4().hex[:8]}",
                            "name": "No Existe",
                            "quantity": 1,
                            "unitPrice": "50.00",
                        }
                    ]
                },
            )
            assert r_missing.status_code == 422
            assert r_missing.json()["error"]["code"] == "DISH_UNAVAILABLE"

            # 3. Valid dish in real Redis sets authoritative price & filters in KDS (CP-032)
            r_items = await client.patch(
                f"/orders/{order_id}/items",
                headers={"Authorization": f"Bearer {waiter_token}"},
                json={
                    "items": [
                        {
                            "dishId": live_dish,
                            "name": "Coctel Autor",
                            "quantity": 2,
                            "unitPrice": "1.00",
                        }
                    ]
                },
            )
            assert r_items.status_code == 200
            assert Decimal(r_items.json()["totalAmount"]) == Decimal("370.00")

            async with session_maker() as prep_session:
                ord_row = await prep_session.scalar(
                    select(Order).where(Order.public_id == order_id)
                )
                assert ord_row is not None
                ord_row.mark_reserved()
                ord_row.mark_in_preparation()
                await prep_session.commit()

            r_kds = await client.get(
                "/kds/queue?station=Barra%20Especial",
                headers={"Authorization": f"Bearer {kitchen_token}"},
            )
            assert r_kds.status_code == 200
            matched_orders = [o for o in r_kds.json() if o["orderId"] == str(order_id)]
            assert len(matched_orders) == 1
            assert matched_orders[0]["items"][0]["preparationTimeText"] == "7 min"
    finally:
        if catalog_svc._redis_validator is not None:
            await catalog_svc._redis_validator._repository._redis._redis.aclose()  # type: ignore[attr-defined]
            monkeypatch.setattr(catalog_svc, "_redis_validator", None)
        if db_session._engine is not None:
            await db_session._engine.dispose()
            monkeypatch.setattr(db_session, "_engine", None)
            monkeypatch.setattr(db_session, "_session_factory", None)
        await redis_client.delete(catalog_repo.key(live_dish))
        await redis_client.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_rabbitmq_dlq_routes_poison_messages_and_keeps_worker_healthy() -> None:
    """DEF-E5-015 / CP-044 / SEC-12 against a real RabbitMQ broker."""
    amqp_url = _endpoint_or_skip(
        "LIVE_RABBITMQ_URL",
        os.getenv("RABBITMQ_URL", "amqp://ordenes:ordenes@127.0.0.1:5672/"),
        5672,
    )
    db_url = _endpoint_or_skip(
        "LIVE_DATABASE_URL",
        os.getenv("DATABASE_URL", "postgresql+asyncpg://ordenes:ordenes@127.0.0.1:5432/ordenes"),
        5432,
    )

    engine = create_async_engine(db_url, echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    unique_suffix = uuid4().hex[:8]
    queue_name = f"orders.it.dlq.{unique_suffix}"
    dlx_name = f"orders.dlx.{unique_suffix}"
    inv_exchange_name = f"inventory.topic.{unique_suffix}"

    order_id = uuid4()
    async with session_maker() as session:
        order = Order(
            public_id=order_id,
            table_number=19,
            status=OrderStatus.CREATED,
            confirmed_at=datetime.now(UTC),
            items=[],
        )
        session.add(order)
        await session.commit()

    dispatcher = EventConsumerDispatcher(
        session_factory=session_maker,
        dlq_router=BrokerNativeDLQRouter(),
    )
    worker = InboundConsumerWorker(
        dispatcher=dispatcher,
        url=amqp_url,
        vhost="/",
        queue_name=queue_name,
        dlx_exchange=dlx_name,
        inventory_exchange=inv_exchange_name,
    )
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(worker.run(stop_event))

    conn = await aio_pika.connect_robust(amqp_url, virtualhost="/")
    try:
        channel = await conn.channel()
        # Wait until worker declares the DLQ queue
        dlq = None
        for _ in range(30):
            try:
                dlq = await channel.declare_queue(f"{queue_name}.dlq", passive=True)
                break
            except Exception:
                await asyncio.sleep(0.2)
        assert dlq is not None, "Worker did not declare durable *.dlq queue"

        inv_ex = await channel.declare_exchange(
            inv_exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
        )

        # Publish 3 poison messages (SEC-12) followed by 1 valid reservation confirmation
        poison_payloads = [
            (b"{corrupted-json", "inventory.reservation.confirmed"),
            (b'["array-not-object"]', "inventory.reservation.confirmed"),
            (b'{"orderId": "not-a-uuid"}', "inventory.reservation.confirmed"),
        ]
        for body, rk in poison_payloads:
            await inv_ex.publish(aio_pika.Message(body=body), routing_key=rk)

        valid_payload = json.dumps(
            {
                "eventId": str(uuid4()),
                "orderId": str(order_id),
                "status": "confirmed",
                "items": [],
            }
        ).encode("utf-8")
        await inv_ex.publish(
            aio_pika.Message(body=valid_payload),
            routing_key="inventory.reservation.confirmed",
        )

        # Verify all 3 poison messages arrived in the real RabbitMQ *.dlq with x-death header
        dead_bodies: list[bytes] = []
        for _ in range(3):
            dlq_msg = None
            for _attempt in range(25):
                dlq_msg = await dlq.get(fail=False, timeout=1.0)
                if dlq_msg is not None:
                    break
                await asyncio.sleep(0.2)
            assert dlq_msg is not None, "Expected poison message in *.dlq"
            assert dlq_msg.headers.get("x-death") is not None
            dead_bodies.append(dlq_msg.body)
            await dlq_msg.ack()
        assert len(dead_bodies) == 3

        # Verify valid message was processed and transitioned order to RESERVED
        for _ in range(25):
            async with session_maker() as check_session:
                refreshed = await check_session.scalar(
                    select(Order).where(Order.public_id == order_id)
                )
                if refreshed is not None and refreshed.status is OrderStatus.RESERVED:
                    break
            await asyncio.sleep(0.2)
        assert refreshed is not None
        assert refreshed.status is OrderStatus.RESERVED
    finally:
        stop_event.set()
        await asyncio.wait_for(worker_task, timeout=10.0)
        cleanup_ch = await conn.channel()
        await cleanup_ch.queue_delete(queue_name)
        await cleanup_ch.queue_delete(f"{queue_name}.dlq")
        await cleanup_ch.exchange_delete(dlx_name)
        await cleanup_ch.exchange_delete(inv_exchange_name)
        await conn.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_deployed_container_websocket_smoke_and_sec03_sec04() -> None:
    """DEF-E5-017 / OBS-06 / OBS-08 against a live deployed FastAPI/Docker HTTP+WS server."""
    base_url = os.getenv("DEPLOYED_API_BASE_URL", "").strip()
    if not base_url:
        if _require_live_infra():
            pytest.fail("DEPLOYED_API_BASE_URL is required when LIVE_INFRA_REQUIRED=1")
        pytest.skip("DEPLOYED_API_BASE_URL not set; skipping deployed container smoke")

    parsed = urlsplit(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not _tcp_reachable(host, port):
        pytest.fail(f"Deployed container unreachable at {base_url}")

    jwt_secret = os.getenv("LIVE_JWT_SECRET") or os.getenv("JWT_SECRET") or ""
    assert jwt_secret, "JWT_SECRET / LIVE_JWT_SECRET required for deployed container smoke"

    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_base = f"{ws_scheme}://{parsed.netloc}"

    kitchen_token = _sign_live_jwt(secret=jwt_secret, role="kitchen")
    waiter_token = _sign_live_jwt(secret=jwt_secret, role="waiter")
    chef_token = _sign_live_jwt(secret=jwt_secret, role="chef")
    forged_token = _sign_live_jwt(secret=f"{jwt_secret}-forged", role="admin")
    expired_token = _sign_live_jwt(secret=jwt_secret, role="admin", ttl_seconds=-60)
    no_exp_token = _sign_live_jwt(secret=jwt_secret, role="admin", ttl_seconds=None)
    none_token = _sign_live_jwt(secret=jwt_secret, role="admin", algorithm="none")

    # 1. Real WebSocket smoke (DEF-E5-017): valid handshake + forbidden channel + 4401 / 4403 close
    async with websockets.connect(f"{ws_base}/ws/kds?token={kitchen_token}") as ws_kds:
        await ws_kds.send(json.dumps({"action": "subscribe", "channel": "kds:all"}))
        await ws_kds.send(json.dumps({"action": "subscribe", "channel": "floor"}))
        forbidden_reply = json.loads(await asyncio.wait_for(ws_kds.recv(), timeout=5.0))
        assert forbidden_reply["error"] == "FORBIDDEN_CHANNEL"

    async with websockets.connect(f"{ws_base}/ws/waiter?token={waiter_token}") as ws_waiter:
        await ws_waiter.send(json.dumps({"action": "subscribe", "channel": "floor"}))

    async def _connect_and_expect_close(ws_uri: str) -> None:
        async with websockets.connect(ws_uri) as ws_conn:
            await asyncio.wait_for(ws_conn.recv(), timeout=5.0)

    with pytest.raises(ConnectionClosed) as unauth_ws:
        await _connect_and_expect_close(f"{ws_base}/ws/kds")
    assert unauth_ws.value.rcvd is not None
    assert unauth_ws.value.rcvd.code == 4401

    with pytest.raises(ConnectionClosed) as forbidden_ws:
        await _connect_and_expect_close(f"{ws_base}/ws/kds?token={waiter_token}")
    assert forbidden_ws.value.rcvd is not None
    assert forbidden_ws.value.rcvd.code == 4403

    # 2. SEC-03 & SEC-04 logical security checks over live HTTP (OBS-08)
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        assert (await client.get("/orders")).status_code == 401
        assert (await client.get(f"/orders?token={waiter_token}")).status_code == 401
        for bad_tok in (forged_token, expired_token, no_exp_token, none_token):
            res = await client.get("/orders", headers={"Authorization": f"Bearer {bad_tok}"})
            assert res.status_code == 401

        # SEC-04: Spoofed gateway headers without token -> 401; with waiter token -> 403 on /void
        spoof_no_jwt = await client.get(
            "/orders",
            headers={
                "X-User-Id": "spoofed-admin",
                "X-User-Roles": "ADMINISTRADOR",
                "X-User-Permissions": "*",
            },
        )
        assert spoof_no_jwt.status_code == 401

        dummy_order_id = str(uuid4())
        spoof_escalate = await client.patch(
            f"/orders/{dummy_order_id}/void",
            headers={
                "Authorization": f"Bearer {waiter_token}",
                "X-User-Id": "spoofed-admin",
                "X-User-Roles": "ADMINISTRADOR",
                "X-User-Permissions": "*",
            },
            json={"reason": "Attempted escalation"},
        )
        assert spoof_escalate.status_code == 403

        # Role matrix on /void and /void-kitchen-error and /kds/queue
        assert (
            await client.get("/kds/queue", headers={"Authorization": f"Bearer {waiter_token}"})
        ).status_code == 403
        assert (
            await client.patch(
                f"/orders/{dummy_order_id}/void",
                headers={"Authorization": f"Bearer {chef_token}"},
            )
        ).status_code == 403
        assert (
            await client.patch(
                f"/orders/{dummy_order_id}/void-kitchen-error",
                headers={"Authorization": f"Bearer {kitchen_token}"},
                json={"note": "Not allowed"},
            )
        ).status_code == 403
