"""Request and response schemas for order lifecycle endpoints.

Satisfies: RNF-03, RNF-13, RF-12, RF-13, RF-14, RF-15, RF-17, RF-23 (Guidelines §5.7, §9.1)
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.order_status import ItemStatus, OrderStatus

if TYPE_CHECKING:
    from app.db.models import Order, OrderItem


class MarkReadyRequest(BaseModel):
    """Optional body of the 'mark as ready' endpoints.

    The endpoints take no input from the body: ``user_id`` and ``role`` come from the JWT
    and the target status is decided by the backend. Any field sent (``userId``,
    ``status``, ...) is rejected with ``422`` instead of silently ignored.
    """

    model_config = ConfigDict(extra="forbid")


class KitchenRejectRequest(BaseModel):
    """Payload for kitchen rejecting an item due to physical lack of stock."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    reason: str = Field(
        default="Insumo físico insuficiente en cocina",
        min_length=1,
        max_length=500,
        description="Reason why the kitchen cannot prepare this item.",
    )


class CreateOrderRequest(BaseModel):
    """Payload for creating a new order at a table or for takeaway (RF-12, RF-13).

    Forbidden fields (userId, status, confirmedAt, etc.) are rejected with 422 (RF-15, RNF-03).
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    table_number: int | None = Field(
        default=None,
        alias="tableNumber",
        ge=1,
        description="Assigned table number in dining room (required if isTakeaway is False).",
    )
    is_takeaway: bool = Field(
        default=False,
        alias="isTakeaway",
        description="Whether this order is for takeaway/pickup without an assigned table.",
    )
    dining_session_id: UUID | None = Field(
        default=None,
        alias="diningSessionId",
        description="Optional dining session correlation ID.",
    )

    @model_validator(mode="after")
    def validate_table_or_takeaway(self) -> CreateOrderRequest:
        if not self.is_takeaway and self.table_number is None:
            raise ValueError("tableNumber is required when isTakeaway is false")
        return self


class OrderItemInput(BaseModel):
    """Single item entry for creation or batch item replacement in an order (RF-14)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    dish_id: str = Field(
        alias="dishId",
        min_length=1,
        max_length=64,
        description="Unique catalog plate/item identifier.",
    )
    name: str = Field(
        min_length=1,
        max_length=200,
        description="Name of the dish at time of ordering.",
    )
    quantity: int = Field(
        default=1,
        gt=0,
        description="Quantity of dishes requested.",
    )
    unit_price: Decimal = Field(
        alias="unitPrice",
        ge=Decimal("0.00"),
        description="Unit price frozen at ordering time.",
    )
    modifiers: list[str] = Field(
        default_factory=list,
        description="Custom preparation modifier tags (e.g. 'Sin cebolla').",
    )
    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Special preparation instructions for kitchen.",
    )


class UpdateOrderItemsRequest(BaseModel):
    """Payload for adding or replacing items in a draft order (RF-14, RF-23)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    items: list[OrderItemInput] = Field(
        min_length=1,
        description="Non-empty list of items that replace the draft order items.",
    )


class TransferOrderRequest(BaseModel):
    """Payload for transferring an order to a different table (RF-16, RNF-09)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    table_number: int = Field(
        alias="tableNumber",
        ge=1,
        description="Target table number in the dining room.",
    )
    version: int | None = Field(
        default=None,
        ge=1,
        description="Expected order version for optimistic concurrency (RNF-09).",
    )


class CancelOrderRequest(BaseModel):
    """Optional payload for cancelling an order (RF-24)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    reason: str | None = Field(default=None, description="Optional cancellation reason.")


class OrderItemResponse(BaseModel):
    """Public representation of an order item."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    public_id: UUID = Field(alias="publicId")
    item_id: UUID = Field(alias="itemId")
    dish_id: str | None = Field(default=None, alias="dishId")
    name: str
    quantity: int
    unit_price: Decimal = Field(alias="unitPrice")
    status: ItemStatus
    modifiers: list[str] = Field(default_factory=list)
    notes: str | None = None
    station: str | None = None
    preparation_time_text: str | None = Field(default=None, alias="preparationTimeText")
    version: int
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    @classmethod
    def from_model(
        cls,
        item: OrderItem,
        *,
        station: str | None = None,
        preparation_time_text: str | None = None,
    ) -> OrderItemResponse:
        now = datetime.now(UTC)
        item_id = item.public_id or uuid4()
        return cls(
            id=str(item.id) if item.id is not None else str(item_id),
            publicId=item_id,
            itemId=item_id,
            dishId=item.dish_id,
            name=item.name,
            quantity=item.quantity if item.quantity is not None else 1,
            unitPrice=item.unit_price if item.unit_price is not None else Decimal("0.00"),
            status=item.status or ItemStatus.PENDING,
            modifiers=list(item.modifiers or []),
            notes=item.notes,
            station=station,
            preparationTimeText=preparation_time_text,
            version=item.version if item.version is not None else 1,
            createdAt=item.created_at or now,
            updatedAt=item.updated_at or now,
        )


class OrderResponse(BaseModel):
    """Public representation of an order matching frontend boundary contract."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    public_id: UUID = Field(alias="publicId")
    order_id: UUID = Field(alias="orderId")
    table_number: int | None = Field(default=None, alias="tableNumber")
    is_takeaway: bool = Field(default=False, alias="isTakeaway")
    status: OrderStatus
    version: int
    total_amount: Decimal = Field(alias="totalAmount")
    items: list[OrderItemResponse] = Field(default_factory=list)
    confirmed_at: datetime | None = Field(default=None, alias="confirmedAt")
    confirmed_by: UUID | None = Field(default=None, alias="confirmedBy")
    stock_reserved_at: datetime | None = Field(default=None, alias="stockReservedAt")
    in_preparation_at: datetime | None = Field(default=None, alias="inPreparationAt")
    rejection_details: dict[str, object] | None = Field(default=None, alias="rejectionDetails")
    dining_session_id: UUID | None = Field(default=None, alias="diningSessionId")
    responsible_waiter_id: UUID | None = Field(default=None, alias="responsibleWaiterId")
    awaiting_stock_confirmation: bool = Field(alias="awaitingStockConfirmation")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    @classmethod
    def from_model(
        cls,
        order: Order,
        *,
        items: list[OrderItemResponse] | None = None,
    ) -> OrderResponse:
        now = datetime.now(UTC)
        order_public_id = order.public_id or uuid4()
        return cls(
            id=str(order.id) if order.id is not None else str(order_public_id),
            publicId=order_public_id,
            orderId=order_public_id,
            tableNumber=order.table_number,
            isTakeaway=order.is_takeaway or False,
            status=order.status or OrderStatus.CREATED,
            version=order.version if order.version is not None else 1,
            totalAmount=order.total_amount if order.total_amount is not None else Decimal("0.00"),
            items=(
                items
                if items is not None
                else [OrderItemResponse.from_model(item) for item in order.items]
            ),
            confirmedAt=order.confirmed_at,
            confirmedBy=order.confirmed_by,
            stockReservedAt=order.stock_reserved_at,
            inPreparationAt=order.in_preparation_at,
            rejectionDetails=order.rejection_details,
            diningSessionId=order.dining_session_id,
            responsibleWaiterId=order.responsible_waiter_id,
            awaitingStockConfirmation=order.awaiting_stock_confirmation,
            createdAt=order.created_at or now,
            updatedAt=order.updated_at or now,
        )
