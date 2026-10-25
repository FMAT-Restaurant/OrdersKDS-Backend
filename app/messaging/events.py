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
    MENU_DISH_PRICE_UPDATED = "menu.platillo.precio_actualizado"
    MENU_CATALOG_UPDATED = "menu.catalogo.actualizado"
    MENU_DISH_AVAILABILITY_UPDATED = "menu.platillo.disponibilidad_actualizada"
    MENU_DISH_STATE_UPDATED = "menu.platillo.estado_actualizado"


class OrderEvent(BaseModel):
    """Base event with a stable id for one order and event type."""

    model_config = ConfigDict(populate_by_name=True)
    event_name: ClassVar[EventName]
    unique_per_order: ClassVar[bool] = True

    event_id: UUID = Field(alias="eventId")
    order_id: UUID = Field(alias="orderId")
    correlation_id: UUID | None = Field(default=None, alias="correlationId")

    @classmethod
    def for_order(
        cls,
        order_id: UUID,
        *,
        correlation_id: UUID | None = None,
    ) -> OrderEvent:
        """Build an event whose id is stable across retries and re-entrance."""
        event_type = getattr(cls, "event_name", EventName.ORDER_CREATED)
        event_id = (
            uuid5(NAMESPACE_URL, f"{event_type}:{order_id}") if cls.unique_per_order else uuid4()
        )
        return cls(eventId=event_id, orderId=order_id, correlationId=correlation_id)


class OrderCreatedEvent(OrderEvent):
    """Event emitted when an order enters the inventory reservation flow."""

    event_name: ClassVar[EventName] = EventName.ORDER_CREATED


class DishPreparedEvent(OrderEvent):
    """Repeatable event: each preparation emission gets a new identifier."""

    event_name: ClassVar[EventName] = EventName.DISH_PREPARED
    unique_per_order: ClassVar[bool] = False
