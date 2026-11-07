"""Event names and typed payloads emitted and consumed by Orders & KDS.

Spanish field names appear strictly inside Anti-Corruption Layer (ACL) aliases
on inbound Pydantic models. Domain and persistence code only use English names.

Satisfies: RF-10, RF-18, RF-21, RF-33, RF-34, RNF-02, RNF-13, OP-11
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar, Self, TypeGuard, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)


def _is_string_keyed_dict(value: object) -> TypeGuard[dict[str, object]]:
    if not isinstance(value, dict):
        return False
    keys = cast(dict[object, object], value)
    return all(isinstance(key, str) for key in keys)


class EventName(StrEnum):
    """Routing keys owned by publishing and subscribing contracts."""

    # Published events
    ORDER_CREATED = "ordenes.orden.creada"
    ORDER_IN_PREPARATION = "ordenes.orden.en_preparacion"
    KITCHEN_DISH_REJECTED = "ordenes.platillo.rechazado_cocina"
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
    MENU_OFFER_CREATED = "menu.oferta.creada"
    MENU_OFFER_UPDATED = "menu.oferta.actualizada"
    MENU_OFFER_CREATED_EN = "menu.offer.created"
    MENU_OFFER_UPDATED_EN = "menu.offer.updated"

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
    ) -> Self:
        """Build an event whose id is stable across retries and re-entrance."""
        event_type = getattr(cls, "event_name", EventName.ORDER_CREATED)
        event_id = (
            uuid5(NAMESPACE_URL, f"{event_type}:{order_id}") if cls.unique_per_order else uuid4()
        )
        return cls(eventId=event_id, orderId=order_id, correlationId=correlation_id)


class OrderCreatedItem(BaseModel):
    """Inventory-facing offer and quantity included in an order-created event."""

    model_config = ConfigDict(populate_by_name=True)

    offer_id: str = Field(
        alias="offerId",
        validation_alias=AliasChoices("offerId", "offer_id", "plateId", "plate_id"),
        serialization_alias="offerId",
        min_length=1,
    )
    quantity: int = Field(gt=0)

    @property
    def plate_id(self) -> str:
        """Backward-compatible property alias for offer_id."""
        return self.offer_id


class OrderCreatedEvent(OrderEvent):
    """Event emitted when an order enters the inventory reservation flow."""

    event_name: ClassVar[EventName] = EventName.ORDER_CREATED
    items: list[OrderCreatedItem] = Field(alias="items")

    @classmethod
    def for_order(
        cls,
        order_id: UUID,
        *,
        items: list[OrderCreatedItem] | None = None,
        correlation_id: UUID | None = None,
        attempt_key: str | int | None = None,
    ) -> OrderCreatedEvent:
        """Build the deterministic inventory-reservation event for an order."""
        seed = f"{cls.event_name}:{order_id}"
        if attempt_key is not None:
            seed = f"{seed}:{attempt_key}"
        event_id = uuid5(NAMESPACE_URL, seed)
        return cls(
            eventId=event_id,
            orderId=order_id,
            correlationId=correlation_id,
            items=items or [],
        )


class OrderInPreparationEvent(OrderEvent):
    """Event emitted when kitchen starts order preparation (moves to IN_PREPARATION)."""

    event_name: ClassVar[EventName] = EventName.ORDER_IN_PREPARATION
    items: list[OrderCreatedItem] = Field(default_factory=list, alias="items")

    @classmethod
    def for_order(
        cls,
        order_id: UUID,
        *,
        items: list[OrderCreatedItem] | None = None,
        correlation_id: UUID | None = None,
    ) -> OrderInPreparationEvent:
        """Build the deterministic order in preparation event."""
        event_id = uuid5(NAMESPACE_URL, f"{cls.event_name}:{order_id}")
        return cls(
            eventId=event_id,
            orderId=order_id,
            correlationId=correlation_id,
            items=items or [],
        )


class KitchenDishRejectedEvent(OrderEvent):
    """Event emitted when kitchen rejects an item in RESERVED due to physical lack of stock."""

    event_name: ClassVar[EventName] = EventName.KITCHEN_DISH_REJECTED
    unique_per_order: ClassVar[bool] = False

    item_id: UUID = Field(alias="itemId")
    offer_id: str = Field(alias="offerId")
    quantity: int = Field(alias="quantity", gt=0)
    reason: str = Field(alias="reason", min_length=1)

    @classmethod
    def for_item(
        cls,
        order_id: UUID,
        item_id: UUID,
        *,
        offer_id: str,
        quantity: int,
        reason: str,
        correlation_id: UUID | None = None,
    ) -> KitchenDishRejectedEvent:
        """Build a repeatable event for item rejected by kitchen."""
        return cls(
            eventId=uuid4(),
            orderId=order_id,
            itemId=item_id,
            offerId=offer_id,
            quantity=quantity,
            reason=reason,
            correlationId=correlation_id,
        )


class DishPreparedEvent(OrderEvent):
    """Repeatable event: each preparation emission gets a new identifier (RF-10)."""

    event_name: ClassVar[EventName] = EventName.DISH_PREPARED
    unique_per_order: ClassVar[bool] = False

    item_id: UUID | None = Field(default=None, alias="itemId")
    offer_id: str | None = Field(default=None, alias="offerId")
    quantity: int = Field(default=1, gt=0, alias="quantity")

    @classmethod
    def for_item(
        cls,
        order_id: UUID,
        item_id: UUID,
        *,
        offer_id: str | None = None,
        quantity: int = 1,
        correlation_id: UUID | None = None,
    ) -> DishPreparedEvent:
        """Build a repeatable event for an individual prepared dish/offer (RF-10)."""
        return cls(
            eventId=uuid4(),
            orderId=order_id,
            itemId=item_id,
            offerId=offer_id,
            quantity=quantity,
            correlationId=correlation_id,
        )


class OrderExpiredEvent(OrderEvent):
    """Event emitted when Inventory does not answer before the saga timeout.

    The identifier is deterministic per order, so retries or concurrent scans
    stage at most one outbox row for the same expiration (RNF-02, OP-01, OP-04).
    """

    event_name: ClassVar[EventName] = EventName.ORDER_EXPIRED
    elapsed_seconds: int | None = Field(default=None, alias="elapsedSeconds")
    reason: str = Field(default="timeout", alias="reason")
    motivo: str = Field(default="timeout", alias="motivo")
    items: list[OrderCreatedItem] = Field(default_factory=list, alias="items")

    @classmethod
    def for_order(
        cls,
        order_id: UUID,
        *,
        items: list[OrderCreatedItem] | None = None,
        elapsed_seconds: int | None = None,
        reason: str = "timeout",
        correlation_id: UUID | None = None,
    ) -> OrderExpiredEvent:
        """Build the deterministic expiration event for an order."""
        event_id = uuid5(NAMESPACE_URL, f"{cls.event_name}:{order_id}")
        return cls(
            eventId=event_id,
            orderId=order_id,
            correlationId=correlation_id,
            elapsedSeconds=elapsed_seconds,
            reason=reason,
            motivo=reason,
            items=items or [],
        )


class OrderCancelledEvent(OrderEvent):
    """Event emitted when an order in CREATED/RESERVED status is cancelled (RF-24, RF-25, OP-05).

    Stages compensation in outbox so Inventory releases any reservation.
    """

    event_name: ClassVar[EventName] = EventName.ORDER_CANCELLED
    items_cancelled: list[OrderCreatedItem] = Field(
        default_factory=list,
        alias="itemsCancelados",
        validation_alias=AliasChoices("itemsCancelados", "itemsCancelled", "items"),
        serialization_alias="itemsCancelados",
    )

    @classmethod
    def for_order(
        cls,
        order_id: UUID,
        *,
        items: list[OrderCreatedItem] | None = None,
        correlation_id: UUID | None = None,
    ) -> OrderCancelledEvent:
        """Build the deterministic cancellation event for an order."""
        event_id = uuid5(NAMESPACE_URL, f"{cls.event_name}:{order_id}")
        return cls(
            eventId=event_id,
            orderId=order_id,
            correlationId=correlation_id,
            itemsCancelados=items or [],
        )


class OrderWastedEvent(OrderEvent):
    """Event emitted when an in-preparation order is voided/wasted (RF-26, RF-27, RF-28)."""

    event_name: ClassVar[EventName] = EventName.ORDER_WASTED
    reason: str = Field(default="ADMINISTRATIVE", alias="reason")
    motivo: str = Field(default="administrativo", alias="motivo")
    items_affected: list[OrderCreatedItem] = Field(
        default_factory=list,
        alias="itemsAfectados",
        validation_alias=AliasChoices(
            "itemsAfectados", "items_affected", "items", "itemsMermados"
        ),
        serialization_alias="itemsAfectados",
    )
    items: list[OrderCreatedItem] = Field(default_factory=list, alias="items")

    @classmethod
    def for_order(
        cls,
        order_id: UUID,
        *,
        items: list[OrderCreatedItem] | None = None,
        reason: str = "ADMINISTRATIVE",
        motivo: str = "administrativo",
        correlation_id: UUID | None = None,
    ) -> OrderWastedEvent:
        """Build the deterministic order wasted event."""
        event_id = uuid5(NAMESPACE_URL, f"{cls.event_name}:{order_id}")
        items_list = items or []
        return cls(
            eventId=event_id,
            orderId=order_id,
            correlationId=correlation_id,
            reason=reason,
            motivo=motivo,
            itemsAfectados=items_list,
            items=items_list,
        )


class ChargeRequestedItem(BaseModel):
    """Single item entry included in a charge requested event."""

    model_config = ConfigDict(populate_by_name=True)

    item_id: UUID = Field(alias="itemId")
    plate_id: str = Field(
        alias="plateId",
        validation_alias=AliasChoices("plateId", "offerId", "dish_id"),
        serialization_alias="plateId",
    )
    name: str = Field(default="")
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(alias="unitPrice")
    subtotal: Decimal = Field(alias="subtotal")
    modifiers: list[dict[str, object] | str] = Field(default_factory=list)
    notes: str | None = Field(default=None)

    @field_serializer("unit_price", "subtotal", mode="plain")
    def _serialize_prices(self, value: Decimal) -> float:
        return float(value)


class ChargeRequestedEvent(OrderEvent):
    """Event emitted when payment is requested for an order (RF-20, RF-26, RF-27)."""

    event_name: ClassVar[EventName] = EventName.CHARGE_REQUESTED
    session_id: UUID | None = Field(default=None, alias="sessionId")
    table_id: str | None = Field(default=None, alias="tableId")
    waiter_id: UUID | None = Field(default=None, alias="waiterId")
    status: str = Field(default="WASTED", alias="status")
    total_amount: Decimal = Field(default=Decimal("0.00"), alias="totalAmount")
    items: list[ChargeRequestedItem] = Field(default_factory=list, alias="items")
    data: dict[str, Any] | None = Field(default=None, alias="data")

    @field_serializer("total_amount", mode="plain")
    def _serialize_total_amount(self, value: Decimal) -> float:
        return float(value)

    @classmethod
    def for_order(
        cls,
        order_id: UUID,
        *,
        total_amount: Decimal = Decimal("0.00"),
        items: list[ChargeRequestedItem] | None = None,
        session_id: UUID | None = None,
        table_id: str | None = None,
        waiter_id: UUID | None = None,
        status: str = "WASTED",
        correlation_id: UUID | None = None,
        data: dict[str, Any] | None = None,
    ) -> ChargeRequestedEvent:
        """Build the deterministic charge requested event for an order."""
        event_id = uuid5(NAMESPACE_URL, f"{cls.event_name}:{order_id}")
        items_list = items or []
        effective_total = total_amount if total_amount is not None else Decimal("0.00")
        payload_data = data or {
            "orderId": str(order_id),
            "sessionId": str(session_id) if session_id else None,
            "tableId": table_id,
            "waiterId": str(waiter_id) if waiter_id else None,
            "status": status,
            "totalAmount": float(effective_total),
            "items": [
                {
                    "itemId": str(it.item_id),
                    "plateId": it.plate_id,
                    "name": it.name,
                    "quantity": it.quantity,
                    "unitPrice": float(it.unit_price) if it.unit_price is not None else 0.0,
                    "subtotal": float(it.subtotal) if it.subtotal is not None else 0.0,
                    "modifiers": it.modifiers,
                    "notes": it.notes,
                }
                for it in items_list
            ],
        }
        return cls(
            eventId=event_id,
            orderId=order_id,
            correlationId=correlation_id,
            sessionId=session_id,
            tableId=table_id,
            waiterId=waiter_id,
            status=status,
            totalAmount=effective_total,
            items=items_list,
            data=payload_data,
        )


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
        default_factory=list[dict[str, object] | str],
        alias="reservas",
    )


class RejectedItemDetail(BaseModel):
    """Detail of a single item that could not be fully reserved by Inventory."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    offer_id: str = Field(
        validation_alias=AliasChoices("offerId", "offer_id", "plateId", "plate_id", "dishId"),
        serialization_alias="offerId",
    )
    requested_quantity: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "requestedQuantity", "requested_quantity", "requested", "solicitado"
        ),
        serialization_alias="requestedQuantity",
    )
    available_quantity: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "availableQuantity", "available_quantity", "available", "disponible"
        ),
        serialization_alias="availableQuantity",
    )


class StockInsufficientMessage(BaseModel):
    """Inbound message from Inventory rejecting order due to lack of stock."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    event_id: UUID = Field(alias="eventId")
    order_id: UUID = Field(alias="orderId")
    correlation_id: UUID | None = Field(default=None, alias="correlationId")
    missing_ingredients: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("faltantes", "missingIngredients", "missing_ingredients"),
    )
    rejected_items: list[RejectedItemDetail] = Field(
        default_factory=list,
        validation_alias=AliasChoices("rejectedItems", "rejected_items", "itemsRechazados"),
    )
    reserved_items: list[OrderCreatedItem] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "reservedItems", "reserved_items", "itemsReservados", "reservations", "reservas"
        ),
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
        if not _is_string_keyed_dict(data):
            return data
        nested = data.get("data")
        if _is_string_keyed_dict(nested):
            merged: dict[str, object] = {**nested, **data}
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
        if not _is_string_keyed_dict(data):
            return data
        nested = data.get("data")
        payload: dict[str, object] = (
            {**nested, **data} if _is_string_keyed_dict(nested) else dict(data)
        )
        if "meseroId" in payload and "waiterId" not in payload:
            payload["waiterId"] = payload["meseroId"]
        if "comensales" in payload and "dinersCount" not in payload:
            payload["dinersCount"] = payload["comensales"]
        if "numComensales" in payload and "dinersCount" not in payload:
            payload["dinersCount"] = payload["numComensales"]
        return payload
