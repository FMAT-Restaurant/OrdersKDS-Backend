"""Real-time module: WebSocket channels and notification hooks.

Satisfies: RNF-03, RNF-04, RF-18, SEC-09, OP-02 (ADR-0002)
"""

from __future__ import annotations

from app.realtime.notifier import (
    RealtimeNotifier,
    default_realtime_notifier,
    notify_item_changed,
    notify_order_changed,
)
from app.realtime.router import (
    CHANNEL_FLOOR,
    CHANNEL_KDS_ALL,
    CHANNEL_KDS_PREFIX,
    CHANNEL_KDS_STATION_GENERAL,
    CHANNEL_KDS_STATION_PREFIX,
    CHANNEL_USER_PREFIX,
    KDS_WS_PATH,
    WAITER_WS_PATH,
    RealtimeChannelRouter,
    RealtimeConnection,
    can_subscribe,
    is_valid_station,
    realtime_router,
    serve_websocket_connection,
)
from app.realtime.schemas import (
    FORBIDDEN_CHANNEL,
    FORBIDDEN_CHANNEL_ERROR,
    ItemStatusChangedMessage,
    OrderStatusChangedMessage,
    RealtimeMessage,
    SubscribeAction,
)

__all__ = [
    "CHANNEL_FLOOR",
    "CHANNEL_KDS_ALL",
    "CHANNEL_KDS_PREFIX",
    "CHANNEL_KDS_STATION_GENERAL",
    "CHANNEL_KDS_STATION_PREFIX",
    "CHANNEL_USER_PREFIX",
    "FORBIDDEN_CHANNEL",
    "FORBIDDEN_CHANNEL_ERROR",
    "ItemStatusChangedMessage",
    "KDS_WS_PATH",
    "OrderStatusChangedMessage",
    "RealtimeChannelRouter",
    "RealtimeConnection",
    "RealtimeMessage",
    "RealtimeNotifier",
    "SubscribeAction",
    "WAITER_WS_PATH",
    "can_subscribe",
    "default_realtime_notifier",
    "is_valid_station",
    "notify_item_changed",
    "notify_order_changed",
    "realtime_router",
    "serve_websocket_connection",
]
