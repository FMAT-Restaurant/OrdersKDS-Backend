"""Consumers for domain events consumed by Orders & KDS."""

from __future__ import annotations

from app.messaging.consumers.base import (
    DLQRouter,
    EventConsumerDispatcher,
    InMemoryDLQRouter,
)
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.consumers.payment_completed import handle_payment_completed
from app.messaging.consumers.stock_insufficient import handle_stock_insufficient
from app.messaging.consumers.stock_reserved import handle_stock_reserved

__all__ = [
    "DLQRouter",
    "EventConsumerDispatcher",
    "InMemoryDLQRouter",
    "CatalogConsumer",
    "handle_payment_completed",
    "handle_stock_insufficient",
    "handle_stock_reserved",
]
