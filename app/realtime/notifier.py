"""Real-time notifier implementation connecting domain events to WebSockets.

Satisfies: RNF-03, RNF-04, RF-18, SEC-09, OP-02 (ADR-0002)
"""

from __future__ import annotations

import logging

from app.db.models import Order, OrderItem
from app.realtime.router import RealtimeChannelRouter, realtime_router

logger = logging.getLogger(__name__)


class RealtimeNotifier:
    """Dispatches order and item lifecycle updates to real-time WebSocket channels."""

    def __init__(self, router: RealtimeChannelRouter | None = None) -> None:
        self._router = router or realtime_router

    async def notify_order_changed(self, order: Order) -> None:
        """Broadcast order status changed event to channels."""
        await self._router.publish_order_changed(order)

    async def notify_item_changed(self, order: Order, item: OrderItem) -> None:
        """Broadcast item status changed event to channels."""
        await self._router.publish_item_changed(order, item)


default_realtime_notifier = RealtimeNotifier()


async def notify_order_changed(
    order: Order,
    notifier: RealtimeNotifier | None = None,
) -> None:
    """Helper function to broadcast order status change to real-time channels."""
    target = notifier or default_realtime_notifier
    await target.notify_order_changed(order)


async def notify_item_changed(
    order: Order,
    item: OrderItem,
    notifier: RealtimeNotifier | None = None,
) -> None:
    """Helper function to broadcast item status change to real-time channels."""
    target = notifier or default_realtime_notifier
    await target.notify_item_changed(order, item)
