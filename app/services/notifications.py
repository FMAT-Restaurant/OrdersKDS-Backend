"""Notification interfaces and hooks for order lifecycle updates.

Real-time WebSocket dispatch wired to the channel router (TASK-29 / OP-02, ADR-0002).
This module provides the decoupling interface and mockable hook.

Satisfies: RF-18, RF-33, RF-34, RNF-04, OP-02 (ADR-0002)
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.db.models import Order, OrderItem
from app.realtime.notifier import RealtimeNotifier

logger = logging.getLogger(__name__)


class OrderNotifier(Protocol):
    """Protocol for broadcasting order updates to external listeners."""

    async def notify_order_changed(self, order: Order) -> None:
        """Broadcast state transition to interested clients (WebSocket/SSE)."""
        ...

    async def notify_item_changed(self, order: Order, item: OrderItem) -> None:
        """Broadcast item state transition to interested clients (WebSocket/SSE)."""
        ...


class DefaultOrderNotifier:
    """Default implementation of OrderNotifier dispatching to real-time WebSocket channels."""

    def __init__(self) -> None:
        self._realtime_notifier = RealtimeNotifier()

    async def notify_order_changed(self, order: Order) -> None:
        """Log state transition and push to WebSocket channels."""
        logger.info(
            "notify_order_changed hook called for order %s (status=%s)",
            order.public_id,
            order.status,
            extra={"order_id": str(order.public_id), "status": str(order.status)},
        )
        await self._realtime_notifier.notify_order_changed(order)

    async def notify_item_changed(self, order: Order, item: OrderItem) -> None:
        """Log item state transition and push to WebSocket channels."""
        logger.info(
            "notify_item_changed hook called for item %s in order %s (status=%s)",
            item.public_id,
            order.public_id,
            item.status,
            extra={
                "order_id": str(order.public_id),
                "item_id": str(item.public_id),
                "status": str(item.status),
            },
        )
        await self._realtime_notifier.notify_item_changed(order, item)


default_notifier: OrderNotifier = DefaultOrderNotifier()


async def notify_order_changed(
    order: Order,
    notifier: OrderNotifier | None = None,
) -> None:
    """Invoke the order change notification hook.

    Args:
        order: The updated Order entity.
        notifier: Optional custom or mock notifier; defaults to global default.
    """
    target = notifier or default_notifier
    await target.notify_order_changed(order)


async def notify_item_changed(
    order: Order,
    item: OrderItem,
    notifier: OrderNotifier | None = None,
) -> None:
    """Invoke the item change notification hook.

    Args:
        order: The updated Order aggregate.
        item: The updated OrderItem entity.
        notifier: Optional custom or mock notifier; defaults to global default.
    """
    target = notifier or default_notifier
    await target.notify_item_changed(order, item)
