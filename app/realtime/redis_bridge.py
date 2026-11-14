"""Redis Pub/Sub bridge broadcasting worker real-time updates to API WebSockets.

When background workers (``python -m app.workers.run_all``) process asynchronous
events such as ``inventory.reservation.confirmed``, ``inventory.reservation.rejected``,
``pagos.pago.completado``, ``sala.dining_session.iniciada``, or Saga timeouts, they
run in a separate OS process from FastAPI/uvicorn. This bridge publishes serialized
channel broadcasts over Redis Pub/Sub so the API process relays them to active
WebSocket clients on ``/ws/kds`` and ``/ws/waiter``.

Satisfies: RF-18, RF-33, RF-34, RNF-04, OP-02 (ADR-0002)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Iterable
from typing import Any, Final, Protocol
from uuid import uuid4

from app.realtime.router import RealtimeChannelRouter, realtime_router

logger = logging.getLogger(__name__)

REALTIME_PUBSUB_CHANNEL: Final = "orders:realtime:events"
_POLL_IDLE_SLEEP_SECONDS: Final = 0.05
_DEFAULT_POLL_TIMEOUT_SECONDS: Final = 0.5
_DEFAULT_RETRY_DELAY_SECONDS: Final = 1.0


class RedisPubSubConnection(Protocol):
    """Minimal async Redis PubSub interface needed for subscription loops."""

    def subscribe(self, *args: Any, **kwargs: Any) -> Awaitable[Any]:
        """Subscribe to one or more Pub/Sub channels."""
        ...

    def unsubscribe(self, *args: Any) -> Awaitable[Any]:
        """Unsubscribe from one or more Pub/Sub channels."""
        ...

    def get_message(self, *args: Any, **kwargs: Any) -> Awaitable[dict[str, Any] | None]:
        """Read the next pending Pub/Sub message packet."""
        ...

    def aclose(self) -> Awaitable[Any]:
        """Close the Pub/Sub connection."""
        ...


class RedisPubSubClient(Protocol):
    """Minimal async Redis client interface needed by ``RedisRealtimeBridge``."""

    def publish(self, channel: str, message: str) -> Awaitable[Any]:
        """Publish a serialized payload to a Redis Pub/Sub channel."""
        ...

    def pubsub(self, **kwargs: Any) -> RedisPubSubConnection:
        """Create an async Pub/Sub subscriber handle."""
        ...


async def _wait_for_stop_or_timeout(stop_event: asyncio.Event | None, delay: float) -> bool:
    """Wait up to ``delay`` seconds returning True when ``stop_event`` is signalled."""
    if stop_event is None:
        await asyncio.sleep(delay)
        return False
    if stop_event.is_set():
        return True
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
        return True
    except TimeoutError:
        return stop_event.is_set()


def _decode_raw_payload(raw_data: str | bytes) -> dict[str, Any] | None:
    """Decode raw Redis Pub/Sub data into a JSON dictionary or return None."""
    if isinstance(raw_data, bytes):
        raw_text = raw_data.decode("utf-8", errors="replace")
    elif isinstance(raw_data, str):
        raw_text = raw_data
    else:
        return None

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class RedisRealtimeBridge:
    """Bridges ``RealtimeChannelRouter`` across processes via Redis Pub/Sub."""

    def __init__(
        self,
        redis_client: RedisPubSubClient,
        router: RealtimeChannelRouter = realtime_router,
        *,
        instance_id: str | None = None,
    ) -> None:
        self._redis = redis_client
        self._router = router
        self._instance_id = instance_id or str(uuid4())

    @property
    def instance_id(self) -> str:
        """Return the unique identifier of this bridge instance."""
        return self._instance_id

    async def publish(self, channels: Iterable[str], message: dict[str, Any]) -> None:
        """Publish a channel broadcast envelope to Redis Pub/Sub."""
        unique_channels = sorted({ch for ch in channels if ch})
        if not unique_channels:
            return
        payload = json.dumps(
            {
                "instanceId": self._instance_id,
                "channels": unique_channels,
                "message": message,
            }
        )
        try:
            await self._redis.publish(REALTIME_PUBSUB_CHANNEL, payload)
        except Exception as exc:
            logger.warning("Failed publishing realtime event to Redis Pub/Sub: %s", exc)

    async def handle_raw_message(self, raw_data: str | bytes) -> bool:
        """Parse a Redis Pub/Sub payload and relay it to local WebSocket connections."""
        data = _decode_raw_payload(raw_data)
        if data is None or data.get("instanceId") == self._instance_id:
            return False

        channels = data.get("channels")
        message = data.get("message")
        if not isinstance(channels, list) or not isinstance(message, dict):
            return False

        valid_channels = [ch for ch in channels if isinstance(ch, str) and ch]
        if not valid_channels:
            return False

        await self._router.broadcast_to_channels(valid_channels, message)
        return True

    async def _consume_once(
        self,
        stop_event: asyncio.Event | None,
        *,
        poll_timeout: float,
    ) -> None:
        """Open a Pub/Sub subscription and process incoming messages until stop or error."""
        pubsub = self._redis.pubsub()
        try:
            await pubsub.subscribe(REALTIME_PUBSUB_CHANNEL)
            while stop_event is None or not stop_event.is_set():
                packet = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=poll_timeout,
                )
                if packet is not None and packet.get("data") is not None:
                    await self.handle_raw_message(packet["data"])
                elif await _wait_for_stop_or_timeout(stop_event, _POLL_IDLE_SLEEP_SECONDS):
                    break
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(REALTIME_PUBSUB_CHANNEL)
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    async def run_subscriber(
        self,
        stop_event: asyncio.Event | None = None,
        *,
        poll_timeout: float = _DEFAULT_POLL_TIMEOUT_SECONDS,
        retry_delay: float = _DEFAULT_RETRY_DELAY_SECONDS,
    ) -> None:
        """Subscribe to Redis Pub/Sub and forward broadcasts until ``stop_event`` is set."""
        while stop_event is None or not stop_event.is_set():
            try:
                await self._consume_once(stop_event, poll_timeout=poll_timeout)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Redis realtime subscriber disconnected (%s); retrying", exc)
                if await _wait_for_stop_or_timeout(stop_event, retry_delay):
                    break
