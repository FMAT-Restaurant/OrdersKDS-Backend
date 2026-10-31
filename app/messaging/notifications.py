"""Re-export order notification hook for messaging consumers."""

from __future__ import annotations

from app.services.notifications import (
    DefaultOrderNotifier,
    OrderNotifier,
    default_notifier,
    notify_item_changed,
    notify_order_changed,
)

__all__ = [
    "DefaultOrderNotifier",
    "OrderNotifier",
    "default_notifier",
    "notify_item_changed",
    "notify_order_changed",
]
