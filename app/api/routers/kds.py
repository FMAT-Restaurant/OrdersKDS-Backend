"""KDS router: kitchen actions on orders and items.

Every route requires one of the ``KITCHEN_ROLES`` (Guidelines §5.7); a waiter gets
``403 FORBIDDEN_ROLE`` because the kitchen, not the waiter, marks dishes as ready (OP-08).

Satisfies: RF-09, RF-10, RF-11, RNF-03, OP-08
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.api.dependencies.auth import AuthContext, get_auth_context, require_roles
from app.api.schemas.errors import ErrorResponse
from app.api.schemas.order import MarkReadyRequest, OrderResponse
from app.domain.roles import KITCHEN_ROLES
from app.services.orders import OrderService, get_order_service

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["kds"],
    dependencies=[Depends(require_roles(*KITCHEN_ROLES))],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing, expired or malformed JWT (`UNAUTHENTICATED`).",
        },
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Role not allowed, e.g. a waiter (`FORBIDDEN_ROLE`).",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Order or item not found (`ORDER_NOT_FOUND`).",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Invalid state transition (`INVALID_STATE_TRANSITION`).",
        },
    },
)

OrderIdPath = Annotated[UUID, Path(alias="orderId", description="Public order identifier.")]
ItemIdPath = Annotated[UUID, Path(alias="itemId", description="Public order item identifier.")]


@router.patch(
    "/orders/{orderId}/items/{itemId}/ready",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark a dish as ready",
    description="Kitchen marks one item of an order as ready. Roles: kitchen, chef, admin.",
)
async def mark_item_ready(
    order_id: OrderIdPath,
    item_id: ItemIdPath,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    body: MarkReadyRequest | None = None,
) -> OrderResponse:
    """Mark an item as ready (RF-09, RF-10, OP-08).

    Args:
        order_id: Public identifier of the order.
        item_id: Public identifier of the item.
        auth: Authenticated caller context.
        order_service: Order lifecycle service.
        body: Optional empty body; any field in it is rejected (no mass assignment).

    Returns:
        Updated Order representation.
    """
    order = await order_service.mark_item_ready(order_id, item_id, auth)
    return OrderResponse.from_model(order)


@router.patch(
    "/orders/{orderId}/ready",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark an order as ready",
    description="Kitchen marks the whole order as ready. Roles: kitchen, chef, admin.",
)
async def mark_order_ready(
    order_id: OrderIdPath,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    body: MarkReadyRequest | None = None,
) -> OrderResponse:
    """Mark a whole order as ready (RF-11, OP-08).

    Args:
        order_id: Public identifier of the order.
        auth: Authenticated caller context.
        order_service: Order lifecycle service.
        body: Optional empty body; any field in it is rejected (no mass assignment).

    Returns:
        Updated Order representation.
    """
    order = await order_service.mark_order_ready(order_id, auth)
    return OrderResponse.from_model(order)
