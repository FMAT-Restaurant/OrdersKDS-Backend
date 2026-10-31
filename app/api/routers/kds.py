"""KDS router: kitchen actions on orders and items.

Every route requires one of the ``KITCHEN_ROLES`` (Guidelines §5.7); a waiter gets
``403 FORBIDDEN_ROLE`` because the kitchen, not the waiter, marks dishes as ready (OP-08).

Satisfies: RF-01, RF-02, RF-03, RF-04, RF-08, RF-09, RF-10, RF-11, RNF-03,
OP-08, OP-09, OP-11 (TASK-12, TASK-13)
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import AuthContext, get_auth_context, require_roles
from app.api.schemas.errors import ErrorResponse
from app.api.schemas.order import (
    KitchenRejectRequest,
    MarkReadyRequest,
    OrderItemResponse,
    OrderResponse,
)
from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.roles import KITCHEN_ROLES
from app.repositories.orders import OrderRepository
from app.services.catalog import get_catalog_info_mock, normalize_station_name
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


def _resolve_catalog_info(item: OrderItem) -> dict[str, str | None]:
    item_key = item.dish_id or str(item.public_id)
    info = get_catalog_info_mock(item_key)
    if info.get("station") is not None:
        return info
    if item.dish_id and item.dish_id != str(item.public_id):
        fallback = get_catalog_info_mock(str(item.public_id))
        if fallback.get("station") is not None:
            return fallback
    return info


def _matches_station(
    norm_target: str | None,
    raw_station: str | None,
    norm_item_station: str | None,
) -> bool:
    if norm_target is None:
        return True
    if norm_target == "general":
        return raw_station is None or norm_item_station == "general"
    return norm_item_station == norm_target


def _filter_order_items(
    order: Order,
    normalized_target_station: str | None,
) -> list[OrderItemResponse]:
    valid_items: list[OrderItemResponse] = []
    for item in order.items:
        catalog_info = _resolve_catalog_info(item)
        raw_station = catalog_info.get("station")
        norm_item_station = normalize_station_name(raw_station)
        if not _matches_station(normalized_target_station, raw_station, norm_item_station):
            continue
        valid_items.append(
            OrderItemResponse.from_model(
                item,
                station=raw_station,
                preparation_time_text=catalog_info.get("preparation_time_text"),
            )
        )
    return valid_items


@router.get(
    "/kds/queue",
    summary="Get KDS preparation queue",
    description="Returns orders ordered by kitchen elapsed time, filtered by station and view.",
)
async def get_kds_queue(
    station: Annotated[str | None, Query(description="Optional station to filter items.")] = None,
    view: Annotated[
        str,
        Query(pattern="^(active|delivered)$", description="active or delivered view."),
    ] = "active",
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> list[OrderResponse]:
    """Retrieve orders for the KDS display.

    Satisfies: RF-01, RF-02, RF-03, RF-04, RF-08, OP-09, OP-11 (TASK-12)

    Args:
        station: Optional query param to filter items by station.
        view: 'active' (default) or 'delivered'.
        session: Active database session.

    Returns:
        List of OrderResponse objects with normalized station filtering applied in memory.
    """
    repo = OrderRepository(session)
    orders = await repo.get_kds_queue(view=view)

    normalized_target_station = (
        normalize_station_name(station) if station and station.strip() else None
    )

    queue: list[OrderResponse] = []
    for order in orders:
        valid_items = _filter_order_items(order, normalized_target_station)
        if valid_items:
            queue.append(OrderResponse.from_model(order, items=valid_items))

    return queue


@router.patch(
    "/orders/{orderId}/start-preparation",
    status_code=status.HTTP_200_OK,
    summary="Start preparation of a reserved order",
    description="Kitchen/Chef starts preparation of an order in RESERVED status (OP-11).",
)
@router.post(
    "/orders/{orderId}/start-preparation",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def start_order_preparation(
    order_id: OrderIdPath,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    body: MarkReadyRequest | None = None,
) -> OrderResponse:
    """Transition order from RESERVED to IN_PREPARATION and consume inventory stock."""
    order = await order_service.start_order_preparation(order_id, auth)
    return OrderResponse.from_model(order)


@router.patch(
    "/orders/{orderId}/items/{itemId}/kitchen-reject",
    status_code=status.HTTP_200_OK,
    summary="Kitchen rejects item due to physical lack of stock",
    description=(
        "Kitchen/Chef rejects item in RESERVED status; updates Redis and returns order "
        "to REJECTED for waiter."
    ),
)
async def kitchen_reject_item(
    order_id: OrderIdPath,
    item_id: ItemIdPath,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    body: KitchenRejectRequest | None = None,
) -> OrderResponse:
    """Kitchen rejects an individual dish item because of insufficient physical stock."""
    reason = body.reason if body and body.reason else "Insumo físico insuficiente en cocina"
    order = await order_service.kitchen_reject_item(order_id, item_id, auth, reason=reason)
    return OrderResponse.from_model(order)


@router.patch(
    "/orders/{orderId}/items/{itemId}/cancel",
    status_code=status.HTTP_200_OK,
    summary="Cancel a single item in an order",
    description="Cancels an item in CREATED or RESERVED status.",
)
async def cancel_order_item(
    order_id: OrderIdPath,
    item_id: ItemIdPath,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    body: MarkReadyRequest | None = None,
) -> OrderResponse:
    """Kitchen or waiter cancels a single item from an unstarted order."""
    order = await order_service.cancel_item(order_id, item_id, auth)
    return OrderResponse.from_model(order)


@router.patch(
    "/orders/{orderId}/items/{itemId}/ready",
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
