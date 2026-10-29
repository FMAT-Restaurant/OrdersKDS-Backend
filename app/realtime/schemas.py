"""Typed real-time event schemas and envelope definitions.

Role matrix (OP-02 / ADR-0002):
- Channels: ``kds:station:{station}``, ``kds:all``, ``floor``, ``user:{userId}``.
- Typed messages: ``order.status_changed`` and ``item.status_changed``.
- CamelCase envelope: ``{ type, eventId, occurredAt, orderId, tableId?, ... }``.

Satisfies: RNF-03, RNF-04, RF-18, SEC-09, OP-02 (ADR-0002)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

FORBIDDEN_CHANNEL: Final = "FORBIDDEN_CHANNEL"
FORBIDDEN_CHANNEL_ERROR: Final[dict[str, str]] = {"error": FORBIDDEN_CHANNEL}


class RealtimeMessage(BaseModel):
    """Envelope base class for real-time WebSocket notifications."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    type: str
    event_id: UUID = Field(default_factory=uuid4, alias="eventId")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="occurredAt")
    order_id: UUID = Field(alias="orderId")
    table_id: int | str | None = Field(default=None, alias="tableId")
    item_id: UUID | None = Field(default=None, alias="itemId")
    status: str
    version: int

    def to_envelope(self) -> dict[str, Any]:
        """Convert message to camelCase dictionary envelope matching Zod/Pydantic."""
        payload: dict[str, Any] = {
            "type": self.type,
            "eventId": str(self.event_id),
            "occurredAt": self.occurred_at.isoformat(),
            "orderId": str(self.order_id),
            "status": self.status,
            "version": self.version,
        }
        if self.table_id is not None:
            payload["tableId"] = self.table_id
        if self.item_id is not None:
            payload["itemId"] = str(self.item_id)
        return payload


class OrderStatusChangedMessage(RealtimeMessage):
    """Notification emitted when an order status changes."""

    type: Literal["order.status_changed"] = "order.status_changed"
    item_id: None = Field(default=None, alias="itemId")


class ItemStatusChangedMessage(RealtimeMessage):
    """Notification emitted when an individual order item status changes."""

    type: Literal["item.status_changed"] = "item.status_changed"
    item_id: UUID = Field(alias="itemId")


class SubscribeAction(BaseModel):
    """Incoming subscription request message from client."""

    model_config = ConfigDict(extra="ignore")

    action: str | None = None
    type: str | None = None
    channel: str | None = None
    station: str | None = None
