"""Unit tests for realtime router, schemas, and station validation.

Satisfies: RNF-03, RNF-04, RF-18, SEC-09, OP-02 (ADR-0002)
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection

from app.api.dependencies.auth import (
    AuthContext,
    authorize_websocket,
    get_auth_context,
)
from app.db.models import Order, OrderItem
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role
from app.realtime.notifier import (
    RealtimeNotifier,
    notify_item_changed,
    notify_order_changed,
)
from app.realtime.router import (
    RealtimeChannelRouter,
    can_subscribe,
    get_item_station,
    is_valid_station,
)
from app.realtime.schemas import (
    ItemStatusChangedMessage,
    OrderStatusChangedMessage,
    SubscribeAction,
)
from app.services.catalog import register_catalog_mock
from tests.unit.conftest import TokenFactory

pytestmark = [pytest.mark.unit, pytest.mark.requirement("RNF-04", "RF-18", "SEC-09")]


def test_is_valid_station() -> None:
    assert is_valid_station("parrilla") is True
    assert is_valid_station("Estación Fría") is True
    assert is_valid_station("") is False
    assert is_valid_station("   ") is False
    assert is_valid_station("station:invalid") is False
    assert is_valid_station("station\ninvalid") is False
    assert is_valid_station("x" * 60) is False
    assert is_valid_station(cast(str, None)) is False


def test_can_subscribe_matrix() -> None:
    # KDS path
    assert can_subscribe("kds:all", Role.KITCHEN, "/ws/kds") is True
    assert can_subscribe("kds:all", Role.CHEF, "/ws/kds") is True
    assert can_subscribe("kds:all", Role.ADMIN, "/ws/kds") is True
    assert can_subscribe("kds:station:grill", Role.KITCHEN, "/ws/kds") is True
    assert can_subscribe("kds:station:", Role.KITCHEN, "/ws/kds") is False
    assert can_subscribe("floor", Role.KITCHEN, "/ws/kds") is False
    assert can_subscribe("user:123", Role.KITCHEN, "/ws/kds") is False

    # Waiter path
    assert can_subscribe("floor", Role.WAITER, "/ws/waiter") is True
    assert can_subscribe("floor", Role.ADMIN, "/ws/waiter") is True
    assert can_subscribe("user:waiter-1", Role.WAITER, "/ws/waiter") is False
    assert can_subscribe("kds:all", Role.WAITER, "/ws/waiter") is False
    assert can_subscribe("kds:station:grill", Role.WAITER, "/ws/waiter") is False

    # Invalid paths or roles
    assert can_subscribe("kds:all", Role.WAITER, "/ws/kds") is False
    assert can_subscribe("kds:all", Role.KITCHEN, "/ws/other") is False
    assert can_subscribe("", Role.KITCHEN, "/ws/kds") is False


def test_get_item_station_fallbacks() -> None:
    item_direct = OrderItem(
        public_id=uuid4(),
        dish_id="dish-mock-1",
        name="Plato 1",
    )
    # Direct station property if attached dynamically
    setattr(item_direct, "station", "Bar")  # noqa: B010
    assert get_item_station(item_direct) == "Bar"

    # Catalog mock lookup
    register_catalog_mock("dish-mock-lookup", station="Parrilla")
    item_catalog = OrderItem(
        public_id=uuid4(),
        dish_id="dish-mock-lookup",
        name="Plato 2",
    )
    assert get_item_station(item_catalog) == "Parrilla"

    # Unknown dish
    item_unknown = OrderItem(
        public_id=uuid4(),
        dish_id="dish-mock-none",
        name="Plato 3",
    )
    assert get_item_station(item_unknown) is None


@pytest.mark.asyncio
async def test_realtime_router_subscribe_and_broadcast() -> None:
    router = RealtimeChannelRouter()
    mock_ws = AsyncMock()

    context = AuthContext(user_id="user-42", role=Role.KITCHEN)
    await router.register(mock_ws, context, "/ws/kds")

    # Forbidden subscribe
    ok = await router.handle_subscribe(mock_ws, "floor")
    assert ok is False
    mock_ws.send_json.assert_called_with({"error": "FORBIDDEN_CHANNEL"})

    # Valid subscribe
    ok = await router.handle_subscribe(mock_ws, "kds:all")
    assert ok is True

    # Broadcast
    await router.broadcast_to_channels(["kds:all"], {"test": "data"})
    mock_ws.send_json.assert_called_with({"test": "data"})

    # Unregister
    await router.unregister(mock_ws)
    mock_ws.send_json.reset_mock()
    await router.broadcast_to_channels(["kds:all"], {"test": "data2"})
    mock_ws.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_realtime_router_handles_dead_websocket() -> None:
    router = RealtimeChannelRouter()
    mock_ws = AsyncMock()
    mock_ws.send_json.side_effect = RuntimeError("Connection closed")

    context = AuthContext(user_id="user-42", role=Role.KITCHEN)
    await router.register(mock_ws, context, "/ws/kds")
    await router.handle_subscribe(mock_ws, "kds:all")

    # Should safely catch error and remove connection
    await router.broadcast_to_channels(["kds:all"], {"event": "ping"})


@pytest.mark.asyncio
async def test_realtime_notifier_and_order_dispatch() -> None:
    router = RealtimeChannelRouter()
    mock_ws = AsyncMock()
    context = AuthContext(user_id="waiter-uuid", role=Role.WAITER)
    await router.register(mock_ws, context, "/ws/waiter")

    notifier = RealtimeNotifier(router)
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.IN_PREPARATION,
        responsible_waiter_id=uuid4(),
        version=1,
    )
    await notify_order_changed(order, notifier=notifier)
    assert mock_ws.send_json.called

    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-1",
        name="Plato",
        status=ItemStatus.READY,
        version=1,
    )
    mock_ws.send_json.reset_mock()
    await notify_item_changed(order, item, notifier=notifier)
    assert mock_ws.send_json.called


def test_realtime_message_to_envelope() -> None:
    order_id = uuid4()
    item_id = uuid4()
    msg = ItemStatusChangedMessage(
        orderId=order_id,
        itemId=item_id,
        tableId=5,
        status="READY",
        version=2,
    )
    env = msg.to_envelope()
    assert env["type"] == "item.status_changed"
    assert env["orderId"] == str(order_id)
    assert env["itemId"] == str(item_id)
    assert env["tableId"] == 5
    assert env["status"] == "READY"
    assert env["version"] == 2

    order_msg = OrderStatusChangedMessage(
        orderId=order_id,
        status="DELIVERED",
        version=3,
    )
    order_env = order_msg.to_envelope()
    assert order_env["type"] == "order.status_changed"
    assert "itemId" not in order_env
    assert "tableId" not in order_env

    # Schemas extra model
    action = SubscribeAction(action="subscribe", channel="kds:all")
    assert action.action == "subscribe"
    assert action.channel == "kds:all"


def test_sec_websocket_protocol_auth(make_token: TokenFactory) -> None:
    token = make_token(role="kitchen", user_id="chef-9")

    # 1. Subprotocol formatted as "bearer, <token>"
    mock_connection = AsyncMock(spec=HTTPConnection)
    mock_connection.headers = Headers({"sec-websocket-protocol": f"bearer, {token}"})
    context = get_auth_context(mock_connection)
    assert context.role == Role.KITCHEN
    assert context.user_id == "chef-9"

    # 2. Subprotocol formatted directly as "<token>"
    mock_connection2 = AsyncMock(spec=HTTPConnection)
    mock_connection2.headers = Headers({"sec-websocket-protocol": token})
    context2 = get_auth_context(mock_connection2)
    assert context2.role == Role.KITCHEN


@pytest.mark.asyncio
async def test_authorize_websocket_negotiates_subprotocol(make_token: TokenFactory) -> None:
    token = make_token(role="waiter", user_id="waiter-7")
    mock_ws = AsyncMock()
    mock_ws.headers = Headers({"sec-websocket-protocol": f"bearer, {token}"})

    context = await authorize_websocket(mock_ws, frozenset({Role.WAITER}))
    assert context is not None
    assert context.role == Role.WAITER
    mock_ws.accept.assert_called_with(subprotocol="bearer")


@pytest.mark.asyncio
async def test_order_and_item_notification_hooks() -> None:
    from app.services.notifications import (
        DefaultOrderNotifier,
    )
    from app.services.notifications import (
        notify_item_changed as svc_notify_item_changed,
    )
    from app.services.notifications import (
        notify_order_changed as svc_notify_order_changed,
    )

    order = Order(public_id=uuid4(), table_number=1, status=OrderStatus.IN_PREPARATION, version=1)
    item = OrderItem(
        public_id=uuid4(), dish_id="dish-1", name="Plato", status=ItemStatus.READY, version=1
    )

    # Test with custom spy
    spy = AsyncMock()
    await svc_notify_item_changed(order, item, notifier=spy)
    spy.notify_item_changed.assert_awaited_once_with(order, item)

    await svc_notify_order_changed(order, notifier=spy)
    spy.notify_order_changed.assert_awaited_once_with(order)

    # Test DefaultOrderNotifier directly
    default_impl = DefaultOrderNotifier()
    default_impl._realtime_notifier = AsyncMock()
    await default_impl.notify_item_changed(order, item)
    default_impl._realtime_notifier.notify_item_changed.assert_awaited_once_with(order, item)

    await default_impl.notify_order_changed(order)
    default_impl._realtime_notifier.notify_order_changed.assert_awaited_once_with(order)


@pytest.mark.asyncio
async def test_realtime_router_station_switching_and_reset() -> None:
    import json

    from app.realtime.router import (
        CHANNEL_KDS_STATION_GENERAL,
        _handle_incoming_text,
        _resolve_item_station_channels,
        _resolve_user_channels,
        realtime_router,
    )

    router = RealtimeChannelRouter()
    mock_ws = AsyncMock()
    context = AuthContext(user_id="cook-1", role=Role.KITCHEN)
    conn = await router.register(mock_ws, context, "/ws/kds")

    # Subscribe to station plancha
    assert await router.handle_subscribe(mock_ws, "kds:station:plancha")
    assert "kds:station:plancha" in conn.channels

    # Switch station to horno (replaces plancha)
    assert await router.handle_subscribe(mock_ws, "kds:station:horno")
    assert "kds:station:horno" in conn.channels
    assert "kds:station:plancha" not in conn.channels

    # Unregistered websocket subscribe returns False
    unregistered_ws = AsyncMock()
    assert await router.handle_subscribe(unregistered_ws, "kds:all") is False

    # Broadcast to empty channels does nothing
    await router.broadcast_to_channels([], {"msg": "noop"})

    # Reset clears all connections
    await router.reset()
    assert len(router._connections) == 0

    # Test helper resolvers
    order_with_creator = Order(
        public_id=uuid4(),
        table_number=1,
        status=OrderStatus.CREATED,
        created_by="waiter-uuid-42",
        responsible_waiter_id=None,
        version=1,
    )
    user_chans = _resolve_user_channels(order_with_creator)
    assert "user:waiter-uuid-42" in user_chans

    # Item with fallback/general station
    item_no_station = OrderItem(
        public_id=uuid4(), dish_id=None, name="X", status=ItemStatus.PENDING, version=1
    )
    st_chans = _resolve_item_station_channels(item_no_station)
    assert CHANNEL_KDS_STATION_GENERAL in st_chans

    # Incoming text handling edge cases
    await _handle_incoming_text(mock_ws, "not a json")
    await _handle_incoming_text(mock_ws, json.dumps([1, 2, 3]))

    # Register on global router to test _handle_incoming_text dispatch
    await realtime_router.reset()
    await realtime_router.register(mock_ws, context, "/ws/kds")

    # Subscribe via station key
    await _handle_incoming_text(mock_ws, json.dumps({"action": "subscribe", "station": "grill"}))
    mock_ws.send_json.assert_not_called()

    # Invalid channel type
    mock_ws.reset_mock()
    await _handle_incoming_text(mock_ws, json.dumps({"action": "subscribe", "channel": 123}))
    mock_ws.send_json.assert_called_with({"error": "FORBIDDEN_CHANNEL"})

    # Subscribe via raw channel key without action
    mock_ws.reset_mock()
    await _handle_incoming_text(mock_ws, json.dumps({"channel": "kds:all"}))

    await realtime_router.reset()
