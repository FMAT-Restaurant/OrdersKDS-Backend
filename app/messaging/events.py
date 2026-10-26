"""Event names and typed payloads emitted and consumed by Orders & KDS.

Spanish field names appear strictly inside Anti-Corruption Layer (ACL) aliases
on inbound Pydantic models. Domain and persistence code only use English names.

Satisfies: RF-21, RF-33, RF-34, RNF-02
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventName(StrEnum):
    """Routing keys owned by publishing and subscribing contracts."""

    # Published events
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

    # Consumed events
    STOCK_RESERVED = "inventario.stock.reservado"
    STOCK_INSUFFICIENT = "inventario.stock.insuficiente"
    PAYMENT_COMPLETED = "pagos.pago.completado"
    DINING_SESSION_STARTED = "sala.dining_session.iniciada"


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


# ---------------------------------------------------------------------------
# Inbound event models (Anti-Corruption Layer)
# Spanish external names exist exclusively as Field aliases here.
# ---------------------------------------------------------------------------


class StockReservedMessage(BaseModel):
    """Inbound message from Inventory confirming ingredient reservation."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    event_id: UUID = Field(alias="eventId")
    order_id: UUID = Field(alias="orderId")
    correlation_id: UUID | None = Field(default=None, alias="correlationId")
    reservations: list[dict[str, object] | str] = Field(
        default_factory=list,
        alias="reservas",
    )


class StockInsufficientMessage(BaseModel):
    """Inbound message from Inventory rejecting order due to lack of stock."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    event_id: UUID = Field(alias="eventId")
    order_id: UUID = Field(alias="orderId")
    correlation_id: UUID | None = Field(default=None, alias="correlationId")
    missing_ingredients: list[str] = Field(
        default_factory=list,
        alias="faltantes",
    )


class PaymentCompletedMessage(BaseModel):
    """Inbound message from Payments confirming monetary settlement."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    event_id: UUID = Field(alias="eventId")
    order_id: UUID = Field(alias="orderId")
    correlation_id: UUID | None = Field(default=None, alias="correlationId")
    session_id: UUID | None = Field(default=None, alias="sessionId")
    payment_id: str | None = Field(default=None, alias="paymentId")
    payment_method: str | None = Field(default=None, alias="paymentMethod")
    total_amount: Decimal | None = Field(default=None, alias="montoTotal")
    total_paid: Decimal | None = Field(default=None, alias="totalPaid")
    status: str | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _extract_nested_data(cls, data: Any) -> Any:
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            nested = dict(data["data"])
            merged = {**nested, **data}
            if "eventId" in data:
                merged["eventId"] = data["eventId"]
            return merged
        return data


class DiningSessionCreatedMessage(BaseModel):
    """Inbound Floor event that starts one order for a dining session."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    event_id: UUID = Field(alias="eventId")
    session_id: UUID = Field(alias="sessionId")
    waiter_id: UUID | None = Field(default=None, alias="waiterId")
    diners: int | None = Field(default=None, alias="dinersCount")

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        nested = data.get("data")
        payload = {**nested, **data} if isinstance(nested, dict) else dict(data)
        if "meseroId" in payload and "waiterId" not in payload:
            payload["waiterId"] = payload["meseroId"]
        if "comensales" in payload and "dinersCount" not in payload:
            payload["dinersCount"] = payload["comensales"]
        if "numComensales" in payload and "dinersCount" not in payload:
            payload["dinersCount"] = payload["numComensales"]
        return payload
