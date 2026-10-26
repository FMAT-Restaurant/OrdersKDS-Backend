"""Event names and typed payloads emitted by Orders & KDS."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field


class EventName(StrEnum):
    """Routing keys owned by the Orders & KDS publishing contract."""

    ORDER_CREATED = "ordenes.orden.creada"
    ORDER_CANCELLED = "ordenes.orden.cancelada"
    DISH_PREPARED = "ordenes.platillo.preparado"
    ORDER_WASTED = "ordenes.comanda.mermada"
    CHARGE_REQUESTED = "ordenes.cobro.solicitado"
    BILL_REQUESTED = "ordenes.cuenta.solicitada"
    ORDER_EXPIRED = "ordenes.orden.expirada"


class OrderEvent(BaseModel):
    """Base event with a stable id for one order and event type."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    event_name: ClassVar[EventName]
    unique_per_order: ClassVar[bool] = True

    event_id: UUID = Field(alias="eventId")
    order_id: UUID = Field(alias="orderId")
    correlation_id: UUID | None = Field(default=None, alias="correlationId")

    @classmethod
    def _event_id_for_order(cls, order_id: UUID) -> UUID:
        event_type = cls.event_name
        if cls.unique_per_order:
            return uuid5(NAMESPACE_URL, f"{event_type}:{order_id}")
        return uuid4()

    @classmethod
    def for_order(
        cls,
        order_id: UUID,
        *,
        correlation_id: UUID | None = None,
    ) -> OrderEvent:
        """Build an event whose id is stable across retries and re-entrance."""
        event_id = cls._event_id_for_order(order_id)
        return cls(eventId=event_id, orderId=order_id, correlationId=correlation_id)


class OrderCreatedItem(BaseModel):
    """Inventory reservation data for one order line."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    item_id: UUID = Field(alias="itemId")
    name: str = Field(min_length=1, max_length=200, strict=True)
    quantity: int = Field(gt=0, strict=True)


class OrderCreatedEvent(OrderEvent):
    """Event emitted when an order enters the inventory reservation flow."""

    event_name: ClassVar[EventName] = EventName.ORDER_CREATED
    items: list[OrderCreatedItem]

    @classmethod
    def for_order(
        cls,
        order_id: UUID,
        items: list[OrderCreatedItem] | None = None,
        *,
        correlation_id: UUID | None = None,
    ) -> OrderCreatedEvent:
        """Build an order-created event with a deterministic id and validated items."""
        return cls(
            eventId=cls._event_id_for_order(order_id),
            orderId=order_id,
            correlationId=correlation_id,
            items=items or [],
        )


class DishPreparedEvent(OrderEvent):
    """Repeatable event: each preparation emission gets a new identifier."""

    event_name: ClassVar[EventName] = EventName.DISH_PREPARED
    unique_per_order: ClassVar[bool] = False
