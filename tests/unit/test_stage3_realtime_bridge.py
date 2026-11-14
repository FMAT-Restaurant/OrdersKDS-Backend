"""Unit tests for Stage 3 cross-process Redis Pub/Sub real-time WebSocket bridge."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models import Order, OrderItem
from app.domain.order_status import ItemStatus, OrderStatus
from app.main import app, lifespan
from app.realtime.redis_bridge import (
    REALTIME_PUBSUB_CHANNEL,
    RedisRealtimeBridge,
    _wait_for_stop_or_timeout,
)
from app.realtime.router import RealtimeChannelRouter

pytestmark = [pytest.mark.unit]


class _FakePubSub:
    """In-memory async PubSub double simulating Redis packets and errors."""

    def __init__(self, behaviors: list[dict[str, Any] | Exception | None]) -> None:
        self._behaviors = behaviors
        self.subscribe = AsyncMock()
        self.unsubscribe = AsyncMock()
        self.aclose = AsyncMock()

    async def get_message(
        self,
        ignore_subscribe_messages: bool = False,
        **kwargs: float | None,
    ) -> dict[str, Any] | None:
        del ignore_subscribe_messages, kwargs
        if not self._behaviors:
            return None
        next_item = self._behaviors.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


def _sample_order() -> Order:
    order = Order(public_id=uuid4(), table_number=7, status=OrderStatus.RESERVED, version=3)
    order.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="recipe-chilaquiles",
            offer_id="offer-chilaquiles",
            name="Chilaquiles Verdes",
            quantity=1,
            status=ItemStatus.PENDING,
        )
    )
    return order


@pytest.mark.asyncio
async def test_redis_bridge_publish_serializes_payload_and_skips_empty() -> None:
    """Verify publish sends deduplicated channels + envelope and ignores empty channel sets."""
    fake_redis = MagicMock()
    fake_redis.publish = AsyncMock(return_value=1)
    bridge = RedisRealtimeBridge(fake_redis, instance_id="worker-1")

    await bridge.publish(set(), {"type": "order.status_changed"})
    fake_redis.publish.assert_not_awaited()

    envelope: dict[str, Any] = {"type": "order.status_changed", "status": "RESERVED"}
    await bridge.publish(["kds:all", "floor", "floor", ""], envelope)

    fake_redis.publish.assert_awaited_once()
    call_args = fake_redis.publish.await_args
    assert call_args is not None
    assert call_args.args[0] == REALTIME_PUBSUB_CHANNEL
    sent = json.loads(call_args.args[1])
    assert sent == {
        "instanceId": "worker-1",
        "channels": ["floor", "kds:all"],
        "message": envelope,
    }


@pytest.mark.asyncio
async def test_redis_bridge_publish_swallows_redis_errors() -> None:
    """Verify transient Redis failures during publish never abort domain flows."""
    fake_redis = MagicMock()
    fake_redis.publish = AsyncMock(side_effect=OSError("connection refused"))
    bridge = RedisRealtimeBridge(fake_redis, instance_id="worker-1")

    await bridge.publish(["floor"], {"type": "order.status_changed"})
    fake_redis.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_bridge_handle_raw_message_filtering_and_relay() -> None:
    """Verify valid external payloads broadcast to router and invalid/self ones are skipped."""
    router = MagicMock(spec=RealtimeChannelRouter)
    router.broadcast_to_channels = AsyncMock()
    bridge = RedisRealtimeBridge(MagicMock(), router=router, instance_id="api-1")

    message: dict[str, Any] = {"type": "order.status_changed", "status": "RESERVED"}
    valid_str = json.dumps(
        {
            "instanceId": "worker-1",
            "channels": ["floor", "kds:all", ""],
            "message": message,
        }
    )
    assert await bridge.handle_raw_message(valid_str) is True
    router.broadcast_to_channels.assert_awaited_once_with(["floor", "kds:all"], message)

    router.broadcast_to_channels.reset_mock()
    valid_bytes = valid_str.encode("utf-8")
    assert await bridge.handle_raw_message(valid_bytes) is True
    router.broadcast_to_channels.assert_awaited_once_with(["floor", "kds:all"], message)

    router.broadcast_to_channels.reset_mock()
    self_payload = json.dumps({"instanceId": "api-1", "channels": ["floor"], "message": message})
    assert await bridge.handle_raw_message(self_payload) is False
    assert await bridge.handle_raw_message(cast(str, 123)) is False
    assert await bridge.handle_raw_message("{bad-json") is False
    assert await bridge.handle_raw_message("[]") is False
    assert (
        await bridge.handle_raw_message(
            json.dumps({"instanceId": "w2", "channels": "not-list", "message": {}})
        )
        is False
    )
    assert (
        await bridge.handle_raw_message(
            json.dumps({"instanceId": "w2", "channels": ["", 5], "message": {}})
        )
        is False
    )
    router.broadcast_to_channels.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_bridge_run_subscriber_relays_retries_and_cancels() -> None:
    """Verify subscriber retries after transient Redis errors, delivers packets, and shuts down."""
    stop_event = asyncio.Event()
    router = MagicMock(spec=RealtimeChannelRouter)
    delivered = asyncio.Event()

    async def _on_broadcast(channels: list[str], msg: dict[str, Any]) -> None:
        del channels, msg
        delivered.set()
        stop_event.set()

    router.broadcast_to_channels = AsyncMock(side_effect=_on_broadcast)

    packet_body = json.dumps(
        {
            "instanceId": "worker-1",
            "channels": ["floor"],
            "message": {"type": "order.status_changed", "status": "RESERVED"},
        }
    ).encode("utf-8")
    pubsub_error = _FakePubSub([OSError("redis dropped")])
    pubsub_ok = _FakePubSub([None, {"type": "message", "data": packet_body}])

    fake_redis = MagicMock()
    fake_redis.pubsub = MagicMock(side_effect=[pubsub_error, pubsub_ok])

    bridge = RedisRealtimeBridge(fake_redis, router=router, instance_id="api-1")
    await bridge.run_subscriber(stop_event=stop_event, poll_timeout=0.01, retry_delay=0.001)

    assert delivered.is_set()
    pubsub_error.aclose.assert_awaited_once()
    pubsub_ok.subscribe.assert_awaited_once_with(REALTIME_PUBSUB_CHANNEL)
    pubsub_ok.unsubscribe.assert_awaited_once_with(REALTIME_PUBSUB_CHANNEL)
    pubsub_ok.aclose.assert_awaited_once()

    # Cancellation when stop_event=None must propagate cleanly
    pubsub_idle = _FakePubSub([])
    fake_redis_idle = MagicMock()
    fake_redis_idle.pubsub = MagicMock(return_value=pubsub_idle)
    bridge_idle = RedisRealtimeBridge(fake_redis_idle, router=router, instance_id="api-2")
    task = asyncio.create_task(bridge_idle.run_subscriber(stop_event=None, poll_timeout=0.01))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Helper checks with stop_event=None and pre-set stop_event
    assert await _wait_for_stop_or_timeout(None, 0.001) is False
    preset_event = asyncio.Event()
    preset_event.set()
    assert await _wait_for_stop_or_timeout(preset_event, 0.05) is True


@pytest.mark.asyncio
async def test_realtime_router_invokes_and_isolates_external_publisher() -> None:
    """Verify RealtimeChannelRouter forwards order/item events to external publisher safely."""
    router = RealtimeChannelRouter()
    external_spy = AsyncMock()
    router.set_external_publisher(external_spy)

    order = _sample_order()
    item = order.items[0]
    await router.publish_order_changed(order)
    await router.publish_item_changed(order, item)

    assert external_spy.await_count == 2
    first_channels, first_envelope = external_spy.await_args_list[0].args
    assert "floor" in first_channels
    assert "kds:all" in first_channels
    assert first_envelope["type"] == "order.status_changed"
    assert first_envelope["status"] == "RESERVED"

    second_channels, second_envelope = external_spy.await_args_list[1].args
    assert "floor" in second_channels
    assert second_envelope["type"] == "item.status_changed"

    # External failures must be swallowed so local broadcasts/mutations do not fail
    failing_spy = AsyncMock(side_effect=RuntimeError("redis offline"))
    router.set_external_publisher(failing_spy)
    await router.publish_order_changed(order)
    failing_spy.assert_awaited_once()

    router.set_external_publisher(None)
    external_spy.reset_mock()
    await router.publish_order_changed(order)
    external_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_lifespan_handles_optional_and_configured_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify FastAPI lifespan starts/stops Redis subscriber only when REDIS_URL is configured."""
    monkeypatch.setenv("REDIS_URL", "")
    async with lifespan(app):
        assert app.title == "Orders KDS Backend"

    fake_pubsub = _FakePubSub([])
    fake_redis = MagicMock()
    fake_redis.pubsub = MagicMock(return_value=fake_pubsub)
    fake_redis.aclose = AsyncMock()

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with patch("app.main.Redis.from_url", return_value=fake_redis) as mock_from_url:
        async with lifespan(app):
            await asyncio.sleep(0.02)

    mock_from_url.assert_called_once()
    fake_pubsub.subscribe.assert_awaited_once_with(REALTIME_PUBSUB_CHANNEL)
    fake_redis.aclose.assert_awaited_once()
    with contextlib.suppress(Exception):
        fake_pubsub.aclose.assert_awaited()
