"""Real-time channel router and WebSocket connection manager.

Role matrix and channel taxonomy (OP-02 / ADR-0002):
- Channels:
  - ``kds:station:{station}``: requested by kitchen, chef, admin on ``/ws/kds``.
  - ``kds:all``: requested by kitchen, chef, admin on ``/ws/kds``.
  - ``floor``: automatically subscribed on ``/ws/waiter`` (waiter, admin).
  - ``user:{userId}``: automatically subscribed from JWT on ``/ws/waiter``.
- Validation:
  - Disconnect with 4401 on missing or expired token.
  - Disconnect with 4403 on role not allowed for the endpoint.
  - Response ``{ "error": "FORBIDDEN_CHANNEL" }`` on forbidden channel.
  - Monotonic ``version`` propagation for client-side ordering.

Satisfies: RNF-03, RNF-04, RF-18, SEC-09, OP-02 (ADR-0002)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.api.dependencies.auth import (
    WS_CLOSE_UNAUTHENTICATED,
    AuthContext,
    authorize_websocket,
)
from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.domain.roles import Role
from app.realtime.schemas import (
    FORBIDDEN_CHANNEL_ERROR,
    ItemStatusChangedMessage,
    OrderStatusChangedMessage,
)
from app.services.catalog import get_catalog_info_mock, normalize_station_name

logger = logging.getLogger(__name__)

KDS_WS_PATH: Final = "/ws/kds"
WAITER_WS_PATH: Final = "/ws/waiter"
MAX_STATION_LENGTH: Final = 50

# Channel constants and prefixes (Sonar maintainability & taxonomy)
CHANNEL_KDS_ALL: Final = "kds:all"
CHANNEL_KDS_STATION_PREFIX: Final = "kds:station:"
CHANNEL_FLOOR: Final = "floor"
CHANNEL_USER_PREFIX: Final = "user:"
CHANNEL_KDS_PREFIX: Final = "kds:"
CHANNEL_KDS_STATION_GENERAL: Final = f"{CHANNEL_KDS_STATION_PREFIX}general"


def _parse_actor_uuid(user_id_str: str) -> UUID:
    """Parse string actor identifier into a valid UUID deterministically."""
    try:
        return UUID(user_id_str)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"{CHANNEL_USER_PREFIX}{user_id_str}")


def is_valid_station(station: str) -> bool:
    """Validate that station name is non-empty, within bounds and without forbidden chars."""
    if not isinstance(station, str):
        return False
    cleaned = station.strip()
    if not cleaned:
        return False
    norm = normalize_station_name(cleaned)
    if not norm or norm.strip() == "":
        return False
    if len(norm) > MAX_STATION_LENGTH:
        return False
    return not any(c in station for c in (":", "\n", "\r", "\t", "\0"))


def _can_subscribe_kds(channel: str, role: Role) -> bool:
    if role not in {Role.KITCHEN, Role.CHEF, Role.ADMIN}:
        return False
    if channel == CHANNEL_KDS_ALL:
        return True
    if channel.startswith(CHANNEL_KDS_STATION_PREFIX):
        station = channel.removeprefix(CHANNEL_KDS_STATION_PREFIX)
        return is_valid_station(station)
    return False


def _can_subscribe_waiter(channel: str, role: Role) -> bool:
    if role not in {Role.WAITER, Role.ADMIN}:
        return False
    # user:{userId} is automatic from JWT, client cannot request it (ADR-0002)
    if channel.startswith((CHANNEL_USER_PREFIX, CHANNEL_KDS_PREFIX)):
        return False
    return channel == CHANNEL_FLOOR


def can_subscribe(channel: str, role: Role, path: str) -> bool:
    """Evaluate whether caller role and endpoint are permitted to subscribe to channel."""
    if not isinstance(channel, str) or not channel.strip():
        return False
    if path == KDS_WS_PATH:
        return _can_subscribe_kds(channel, role)
    if path == WAITER_WS_PATH:
        return _can_subscribe_waiter(channel, role)
    return False


def get_item_station(item: OrderItem) -> str | None:
    """Resolve station for an order item via direct attribute or catalog mock."""
    item_station = getattr(item, "station", None)
    if item_station:
        return str(item_station)
    item_key = item.dish_id or str(item.public_id)
    info = get_catalog_info_mock(item_key)
    if info.get("station") is not None:
        return info["station"]
    if item.dish_id and item.dish_id != str(item.public_id):
        fallback = get_catalog_info_mock(str(item.public_id))
        if fallback.get("station") is not None:
            return fallback["station"]
    return None


def _resolve_user_channels(order: Order) -> set[str]:
    """Resolve user channels for an order based on responsible waiter or creator."""
    channels: set[str] = set()
    resp = getattr(order, "responsible_waiter_id", None) or getattr(order, "created_by", None)
    if resp is not None:
        channels.add(f"{CHANNEL_USER_PREFIX}{resp}")
        channels.add(f"{CHANNEL_USER_PREFIX}{str(resp)}")
    return channels


def _resolve_item_station_channels(item: OrderItem) -> set[str]:
    """Resolve station channels for a single order item."""
    channels: set[str] = set()
    raw_station = get_item_station(item)
    norm = normalize_station_name(raw_station)
    if norm:
        channels.add(f"{CHANNEL_KDS_STATION_PREFIX}{norm}")
    if norm == "general" or norm is None:
        channels.add(CHANNEL_KDS_STATION_GENERAL)
    return channels


@dataclass
class RealtimeConnection:
    """Active client session and its subscribed channels."""

    websocket: WebSocket
    auth_context: AuthContext
    path: str
    channels: set[str] = field(default_factory=set)


class RealtimeChannelRouter:
    """Manages WebSocket connections and routes messages to subscribed channels."""

    def __init__(self) -> None:
        self._connections: dict[WebSocket, RealtimeConnection] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, websocket: WebSocket, auth_context: AuthContext, path: str
    ) -> RealtimeConnection:
        """Register active connection and configure automatic subscriptions."""
        async with self._lock:
            conn = RealtimeConnection(
                websocket=websocket,
                auth_context=auth_context,
                path=path,
            )
            if path == WAITER_WS_PATH:
                conn.channels.add(CHANNEL_FLOOR)
                conn.channels.add(f"{CHANNEL_USER_PREFIX}{auth_context.user_id}")
                actor_uuid = _parse_actor_uuid(auth_context.user_id)
                conn.channels.add(f"{CHANNEL_USER_PREFIX}{actor_uuid}")

            self._connections[websocket] = conn
            logger.debug(
                "realtime connection registered",
                extra={
                    "path": path,
                    "user_id": auth_context.user_id,
                    "role": auth_context.role.value,
                    "channels": list(conn.channels),
                },
            )
            return conn

    async def unregister(self, websocket: WebSocket) -> None:
        """Unregister connection and clean up subscriptions."""
        async with self._lock:
            self._connections.pop(websocket, None)

    async def reset(self) -> None:
        """Clear all active connections (for test cleanup)."""
        async with self._lock:
            self._connections.clear()

    async def handle_subscribe(self, websocket: WebSocket, channel: str) -> bool:
        """Process a client subscribe request with channel validation."""
        async with self._lock:
            conn = self._connections.get(websocket)
            if conn is None:
                return False

            if not can_subscribe(channel, conn.auth_context.role, conn.path):
                logger.info(
                    "forbidden channel subscription attempt",
                    extra={
                        "path": conn.path,
                        "role": conn.auth_context.role.value,
                        "channel": channel,
                    },
                )
                with contextlib.suppress(Exception):
                    await websocket.send_json(FORBIDDEN_CHANNEL_ERROR)
                return False

            if channel.startswith(CHANNEL_KDS_STATION_PREFIX):
                # Station switch without reconnecting: replace previous station
                conn.channels = {
                    c for c in conn.channels if not c.startswith(CHANNEL_KDS_STATION_PREFIX)
                }
                norm_station = normalize_station_name(
                    channel.removeprefix(CHANNEL_KDS_STATION_PREFIX)
                )
                conn.channels.add(f"{CHANNEL_KDS_STATION_PREFIX}{norm_station}")
            else:
                conn.channels.add(channel)

            logger.debug(
                "subscribed to channel",
                extra={"user_id": conn.auth_context.user_id, "channel": channel},
            )
            return True

    async def broadcast_to_channels(
        self, channels: Iterable[str], message: dict[str, Any]
    ) -> None:
        """Dispatch a message to all connections subscribed to any of the target channels."""
        target_channels = set(channels)
        if not target_channels:
            return

        to_remove: list[WebSocket] = []
        async with self._lock:
            for ws, conn in self._connections.items():
                if conn.channels.intersection(target_channels):
                    try:
                        await ws.send_json(message)
                    except Exception:
                        to_remove.append(ws)

            for ws in to_remove:
                self._connections.pop(ws, None)

    async def publish_order_changed(self, order: Order) -> None:
        """Build order.status_changed envelope and broadcast to target channels."""
        msg = OrderStatusChangedMessage(
            orderId=order.public_id,
            tableId=order.table_number,
            status=order.status.value,
            version=getattr(order, "version", 1) or 1,
            rejectionDetails=getattr(order, "rejection_details", None),
        )
        envelope = msg.to_envelope()

        target_channels: set[str] = {CHANNEL_FLOOR, CHANNEL_KDS_ALL}
        items = getattr(order, "items", []) or []
        for item in items:
            target_channels.update(_resolve_item_station_channels(item))
        target_channels.update(_resolve_user_channels(order))

        await self.broadcast_to_channels(target_channels, envelope)

    async def publish_item_changed(self, order: Order, item: OrderItem) -> None:
        """Build item.status_changed envelope and broadcast to target channels."""
        msg = ItemStatusChangedMessage(
            orderId=order.public_id,
            itemId=item.public_id,
            tableId=order.table_number,
            status=item.status.value,
            version=getattr(order, "version", 1) or 1,
        )
        envelope = msg.to_envelope()

        target_channels: set[str] = {CHANNEL_FLOOR, CHANNEL_KDS_ALL}
        target_channels.update(_resolve_item_station_channels(item))
        target_channels.update(_resolve_user_channels(order))

        await self.broadcast_to_channels(target_channels, envelope)


realtime_router = RealtimeChannelRouter()


async def _handle_incoming_text(websocket: WebSocket, raw_text: str) -> None:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return

    if not isinstance(data, dict):
        return

    action = data.get("action") or data.get("type")
    if action == "subscribe":
        channel = data.get("channel")
        if not channel and data.get("station"):
            channel = f"{CHANNEL_KDS_STATION_PREFIX}{data['station']}"

        if not channel or not isinstance(channel, str):
            await websocket.send_json(FORBIDDEN_CHANNEL_ERROR)
            return

        await realtime_router.handle_subscribe(websocket, channel)
    elif "channel" in data:
        channel = data["channel"]
        if isinstance(channel, str):
            await realtime_router.handle_subscribe(websocket, channel)


async def serve_websocket_connection(
    websocket: WebSocket,
    path: str,
    allowed_roles: frozenset[Role],
) -> None:
    """Serve a WebSocket connection lifecycle including JWT auth, token expiry, and dispatch."""
    context = await authorize_websocket(websocket, allowed_roles)
    if context is None:
        return

    await realtime_router.register(websocket, context, path)
    try:
        while True:
            if context.exp is not None and time.time() >= context.exp:
                await websocket.close(
                    code=WS_CLOSE_UNAUTHENTICATED,
                    reason=ErrorCode.UNAUTHENTICATED.value,
                )
                break

            time_to_expire: float | None = None
            if context.exp is not None:
                remaining = context.exp - time.time()
                time_to_expire = max(0.01, remaining)

            try:
                if time_to_expire is not None:
                    raw_text = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=time_to_expire,
                    )
                else:
                    raw_text = await websocket.receive_text()
            except TimeoutError:
                logger.info(
                    "token expired during websocket session",
                    extra={"path": path, "user_id": context.user_id},
                )
                await websocket.close(
                    code=WS_CLOSE_UNAUTHENTICATED,
                    reason=ErrorCode.UNAUTHENTICATED.value,
                )
                break

            await _handle_incoming_text(websocket, raw_text)

    except WebSocketDisconnect:
        logger.debug("websocket disconnected", extra={"path": path, "user_id": context.user_id})
    except Exception as exc:
        logger.warning(
            "websocket error",
            extra={"path": path, "user_id": context.user_id, "error": str(exc)},
        )
    finally:
        await realtime_router.unregister(websocket)
