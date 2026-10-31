"""Integration tests for WebSocket channels and authorization matrix.

Traceability:
- IT-17: test_websocket_channels_and_auth_matrix
- SEC-09: Authentication and WebSocket channel matrix
- Requirements: RNF-03, RNF-04, RF-18
- Decision: OP-02 / ADR-0002
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.websockets.routes import KDS_WS_PATH, WAITER_WS_PATH
from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.order_status import ItemStatus, OrderStatus
from app.main import app
from app.realtime.notifier import notify_item_changed, notify_order_changed
from app.realtime.router import realtime_router
from app.services.catalog import register_catalog_mock
from tests.unit.conftest import FakeAsyncSession

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requirement("IT-17", "SEC-09", "RNF-03", "RNF-04", "RF-18"),
]

_TEST_KEY = "test-key-not-a-secret-0123456789-32bytes"


def _create_token(
    user_id: str = "waiter-01",
    role: str = "waiter",
    expires_in: int = 3600,
) -> str:
    payload: dict[str, Any] = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.now(UTC) + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, _TEST_KEY, algorithm="HS256")


def _headers_for(
    user_id: str = "user-1", role: str = "waiter", expires_in: int = 3600
) -> dict[str, str]:
    token = _create_token(user_id=user_id, role=role, expires_in=expires_in)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def clear_realtime_state() -> Any:
    await realtime_router.reset()
    yield
    await realtime_router.reset()


def test_websocket_channels_and_auth_matrix() -> None:
    """IT-17 / SEC-09: Canonical test function verifying channel matrix and auth rules."""
    client = TestClient(app)

    # 1. Handshake without token closes with 4401 (UNAUTHENTICATED)
    with (
        client.websocket_connect(KDS_WS_PATH, headers={}) as ws_no_token,
        pytest.raises(WebSocketDisconnect) as closed_no_token,
    ):
        ws_no_token.receive_text()
    assert closed_no_token.value.code == 4401

    # 2. Handshake with wrong role closes with 4403 (FORBIDDEN_ROLE)
    with (
        client.websocket_connect(
            KDS_WS_PATH, headers=_headers_for(role="waiter")
        ) as ws_wrong_role,
        pytest.raises(WebSocketDisconnect) as closed_wrong_role,
    ):
        ws_wrong_role.receive_text()
    assert closed_wrong_role.value.code == 4403

    with (
        client.websocket_connect(
            WAITER_WS_PATH, headers=_headers_for(role="kitchen")
        ) as ws_kitchen_waiter,
        pytest.raises(WebSocketDisconnect) as closed_kitchen_waiter,
    ):
        ws_kitchen_waiter.receive_text()
    assert closed_kitchen_waiter.value.code == 4403

    # 3. Handshake with expired token closes with 4401
    with (
        client.websocket_connect(
            KDS_WS_PATH, headers=_headers_for(role="kitchen", expires_in=-30)
        ) as ws_expired,
        pytest.raises(WebSocketDisconnect) as closed_expired,
    ):
        ws_expired.receive_text()
    assert closed_expired.value.code == 4401

    # 4. Allowed connections stay open
    with (
        client.websocket_connect(KDS_WS_PATH, headers=_headers_for(role="kitchen")) as ws_kds,
        client.websocket_connect(WAITER_WS_PATH, headers=_headers_for(role="waiter")) as ws_waiter,
    ):
        # 5. Forbidden channel subscription returns { "error": "FORBIDDEN_CHANNEL" }
        ws_kds.send_json({"action": "subscribe", "channel": "floor"})
        assert ws_kds.receive_json() == {"error": "FORBIDDEN_CHANNEL"}

        ws_kds.send_json({"action": "subscribe", "channel": "user:waiter-01"})
        assert ws_kds.receive_json() == {"error": "FORBIDDEN_CHANNEL"}

        ws_kds.send_json({"action": "subscribe", "channel": "kds:station:"})
        assert ws_kds.receive_json() == {"error": "FORBIDDEN_CHANNEL"}

        ws_waiter.send_json({"action": "subscribe", "channel": "kds:all"})
        assert ws_waiter.receive_json() == {"error": "FORBIDDEN_CHANNEL"}

        ws_waiter.send_json({"action": "subscribe", "channel": "user:another"})
        assert ws_waiter.receive_json() == {"error": "FORBIDDEN_CHANNEL"}


def test_token_expired_during_connection_closes_with_4401() -> None:
    """Token expiration occurring after handshake closes connection with 4401."""
    client = TestClient(app)
    # Token expires in 1 second
    headers = _headers_for(role="kitchen", expires_in=1)

    with (
        client.websocket_connect(KDS_WS_PATH, headers=headers) as ws,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        # Waiting longer than token TTL will trigger server-side timeout
        # and close connection with 4401.
        ws.receive_text()

    assert closed.value.code == 4401


def test_waiter_automatic_channels_receive_dispatched_events() -> None:
    """Waiter endpoint automatically joins floor and user:{userId} without client request."""
    client = TestClient(app)
    waiter_id = "00000000-0000-4000-8000-000000000099"
    headers = _headers_for(user_id=waiter_id, role="waiter")

    with client.websocket_connect(WAITER_WS_PATH, headers=headers) as ws_waiter:
        order = Order(
            public_id=uuid4(),
            table_number=4,
            status=OrderStatus.IN_PREPARATION,
            responsible_waiter_id=UUID(waiter_id),
            version=3,
        )

        asyncio.run(notify_order_changed(order))

        msg = ws_waiter.receive_json()
        assert msg["type"] == "order.status_changed"
        assert msg["orderId"] == str(order.public_id)
        assert msg["tableId"] == 4
        assert msg["status"] == "IN_PREPARATION"
        assert msg["version"] == 3
        assert "eventId" in msg
        assert "occurredAt" in msg


def test_kds_station_filtering_and_switching() -> None:
    """KDS receives updates for subscribed station and switches without reconnect."""
    client = TestClient(app)
    headers = _headers_for(role="kitchen")

    # Register dish stations in catalog mock
    register_catalog_mock("dish-grill", station="Parrilla")
    register_catalog_mock("dish-cold", station="Estación Fría")

    with client.websocket_connect(KDS_WS_PATH, headers=headers) as ws:
        # Subscribe to Parrilla station
        ws.send_json({"action": "subscribe", "channel": "kds:station:Parrilla"})

        # Order with a cold dish should NOT be received
        order_cold = Order(
            public_id=uuid4(),
            table_number=1,
            status=OrderStatus.IN_PREPARATION,
            version=1,
            items=[
                OrderItem(
                    public_id=uuid4(),
                    dish_id="dish-cold",
                    name="Ensalada",
                    status=ItemStatus.PENDING,
                )
            ],
        )
        asyncio.run(notify_order_changed(order_cold))

        # Order with a grill dish SHOULD be received
        order_grill = Order(
            public_id=uuid4(),
            table_number=2,
            status=OrderStatus.IN_PREPARATION,
            version=2,
            items=[
                OrderItem(
                    public_id=uuid4(),
                    dish_id="dish-grill",
                    name="Corte New York",
                    status=ItemStatus.PENDING,
                )
            ],
        )
        asyncio.run(notify_order_changed(order_grill))

        received = ws.receive_json()
        assert received["type"] == "order.status_changed"
        assert received["orderId"] == str(order_grill.public_id)
        assert received["version"] == 2

        # Switch station without reconnecting: switch to Estación Fría
        ws.send_json({"action": "subscribe", "channel": "kds:station:Estación Fría"})

        # Now update cold order, it should be received
        order_cold.status = OrderStatus.DELIVERED
        order_cold.version = 3
        asyncio.run(notify_order_changed(order_cold))

        received_cold = ws.receive_json()
        assert received_cold["type"] == "order.status_changed"
        assert received_cold["orderId"] == str(order_cold.public_id)
        assert received_cold["status"] == "DELIVERED"
        assert received_cold["version"] == 3


def test_item_status_changed_dispatches_typed_envelope() -> None:
    """item.status_changed carries itemId, orderId, status and version in camelCase envelope."""
    client = TestClient(app)
    headers = _headers_for(role="chef")

    with client.websocket_connect(KDS_WS_PATH, headers=headers) as ws:
        ws.send_json({"action": "subscribe", "channel": "kds:all"})

        order = Order(
            public_id=uuid4(),
            table_number=7,
            status=OrderStatus.IN_PREPARATION,
            version=5,
        )
        item = OrderItem(
            public_id=uuid4(),
            dish_id="dish-grill",
            name="Corte New York",
            status=ItemStatus.READY,
            version=5,
        )

        asyncio.run(notify_item_changed(order, item))

        msg = ws.receive_json()
        assert msg["type"] == "item.status_changed"
        assert msg["orderId"] == str(order.public_id)
        assert msg["itemId"] == str(item.public_id)
        assert msg["tableId"] == 7
        assert msg["status"] == "READY"
        assert msg["version"] == 5
        assert "eventId" in msg
        assert "occurredAt" in msg


def test_mark_item_ready_endpoint_dispatches_realtime_events_to_channels() -> None:
    """End-to-end integration: dish ready dispatches item.status_changed.

    Verifies:
    1. KDS station subscription receives item change only for matching dishes.
    2. Non-matching station KDS subscriber does not receive the item event.
    3. Floor/waiter channel receives the item event.
    4. Transition of the final item triggers both item and order DELIVERED events.
    """
    client = TestClient(app)
    register_catalog_mock("dish-grill-ribeye", station="Parrilla")
    register_catalog_mock("dish-cold-salad", station="Estación Fría")

    grill_item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-grill-ribeye",
        name="Corte Rib Eye",
        quantity=1,
        unit_price=Decimal("250.00"),
        status=ItemStatus.PENDING,
        version=1,
    )
    cold_item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-cold-salad",
        name="Ensalada Mixta",
        quantity=1,
        unit_price=Decimal("80.00"),
        status=ItemStatus.PENDING,
        version=1,
    )
    order = Order(
        public_id=uuid4(),
        table_number=12,
        status=OrderStatus.IN_PREPARATION,
        version=1,
        items=[grill_item, cold_item],
    )

    fake_session = FakeAsyncSession({order.public_id: order})

    async def _test_session() -> Any:
        yield fake_session

    app.dependency_overrides[get_session] = _test_session

    try:
        with (
            client.websocket_connect(
                KDS_WS_PATH, headers=_headers_for(role="kitchen")
            ) as ws_grill,
            client.websocket_connect(KDS_WS_PATH, headers=_headers_for(role="kitchen")) as ws_cold,
            client.websocket_connect(
                WAITER_WS_PATH, headers=_headers_for(role="waiter")
            ) as ws_waiter,
        ):
            ws_grill.send_json({"action": "subscribe", "channel": "kds:station:Parrilla"})
            ws_cold.send_json({"action": "subscribe", "channel": "kds:station:Estación Fría"})

            # 1. Mark first item (grill) ready via REST PATCH endpoint
            patch_url = f"/orders/{order.public_id}/items/{grill_item.public_id}/ready"
            resp1 = client.patch(patch_url, headers=_headers_for(role="kitchen"))
            assert resp1.status_code == 200

            # ws_grill receives item.status_changed
            msg_grill = ws_grill.receive_json()
            assert msg_grill["type"] == "item.status_changed"
            assert msg_grill["orderId"] == str(order.public_id)
            assert msg_grill["itemId"] == str(grill_item.public_id)
            assert msg_grill["status"] == "READY"
            assert msg_grill["tableId"] == 12
            assert "eventId" in msg_grill
            assert "occurredAt" in msg_grill

            # ws_waiter receives item.status_changed on floor channel
            msg_waiter = ws_waiter.receive_json()
            assert msg_waiter["type"] == "item.status_changed"
            assert msg_waiter["itemId"] == str(grill_item.public_id)
            assert msg_waiter["status"] == "READY"

            # 2. Mark second item (cold) ready via REST PATCH endpoint (completing all dishes)
            patch_url_cold = f"/orders/{order.public_id}/items/{cold_item.public_id}/ready"
            resp2 = client.patch(patch_url_cold, headers=_headers_for(role="kitchen"))
            assert resp2.status_code == 200

            # ws_cold receives item.status_changed for the cold item
            msg_cold = ws_cold.receive_json()
            assert msg_cold["type"] == "item.status_changed"
            assert msg_cold["itemId"] == str(cold_item.public_id)
            assert msg_cold["status"] == "DELIVERED"

            # Waiter receives item.status_changed then order.status_changed
            msg_waiter_item = ws_waiter.receive_json()
            assert msg_waiter_item["type"] == "item.status_changed"
            assert msg_waiter_item["itemId"] == str(cold_item.public_id)

            msg_waiter_order = ws_waiter.receive_json()
            assert msg_waiter_order["type"] == "order.status_changed"
            assert msg_waiter_order["orderId"] == str(order.public_id)
            assert msg_waiter_order["status"] == "DELIVERED"
    finally:
        app.dependency_overrides.clear()
