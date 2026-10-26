"""Notification interfaces and hooks for order lifecycle updates.

Real-time WebSocket dispatch arrives in TASK-29 (OP-02, ADR-0002).
This module provides the decoupling interface and mockable hook.

Satisfies: RF-18, RF-33, RF-34
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from app.db.models import Order

logger = logging.getLogger(__name__)


class OrderNotifier(Protocol):
    """Protocol for broadcasting order updates to external listeners."""

    async def notify_order_changed(self, order: Order) -> None:
        """Broadcast state transition to interested clients (WebSocket/SSE)."""
        ...


class DefaultOrderNotifier:
    """Default no-op implementation of OrderNotifier until TASK-29."""

    async def notify_order_changed(self, order: Order) -> None:
        """Log state transition hook execution without blocking."""
        await asyncio.sleep(0)
        logger.info(
            "notify_order_changed hook called for order %s (status=%s)",
            order.public_id,
            order.status,
            extra={"order_id": str(order.public_id), "status": str(order.status)},
        )


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
