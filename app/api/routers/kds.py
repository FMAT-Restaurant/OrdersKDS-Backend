"""KDS router: kitchen actions on orders and items.

Every route requires one of the ``KITCHEN_ROLES`` (Guidelines §5.7); a waiter gets
``403 FORBIDDEN_ROLE`` because the kitchen, not the waiter, marks dishes as ready (OP-08).

The role guard is the deliverable of TASK-20. The handlers are placeholders that answer
``501`` once the guard lets the caller through.

Satisfies: RF-01, RF-02, RF-03, RF-04, RF-08, RF-09, RF-10, RF-11, RNF-03, OP-09, OP-11 (TASK-12)
"""

import logging
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import require_roles
from app.api.schemas.errors import ErrorResponse
from app.api.schemas.order import MarkReadyRequest, OrderItemResponse, OrderResponse
from app.db.session import get_session
from app.domain.roles import KITCHEN_ROLES
from app.repositories.orders import OrderRepository
from app.services.catalog import get_catalog_info_mock, normalize_station_name

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
    },
)

OrderIdPath = Annotated[UUID, Path(alias="orderId", description="Public order identifier.")]
ItemIdPath = Annotated[UUID, Path(alias="itemId", description="Public order item identifier.")]

_NOT_IMPLEMENTED_DETAIL = "Not implemented yet."


@router.get(
    "/kds/queue",
    summary="Get KDS preparation queue",
    description="Returns orders ordered by kitchen elapsed time, filtered by station and view.",
    response_model=list[OrderResponse],
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
        valid_items: list[OrderItemResponse] = []
        for item in order.items:
            item_key = item.dish_id or str(item.public_id)
            catalog_info = get_catalog_info_mock(item_key)
            if (
                catalog_info.get("station") is None
                and item.dish_id
                and item.dish_id != str(item.public_id)
            ):
                fallback = get_catalog_info_mock(str(item.public_id))
                if fallback.get("station") is not None:
                    catalog_info = fallback

            raw_station = catalog_info.get("station")
            prep_time = catalog_info.get("preparation_time_text")
            norm_item_station = normalize_station_name(raw_station)

            if normalized_target_station is not None:
                if normalized_target_station == "general":
                    if raw_station is not None and norm_item_station != "general":
                        continue
                else:
                    if norm_item_station != normalized_target_station:
                        continue

            item_resp = OrderItemResponse.from_model(
                item,
                station=raw_station,
                preparation_time_text=prep_time,
            )
            valid_items.append(item_resp)

        if valid_items:
            order_resp = OrderResponse.from_model(order, items=valid_items)
            queue.append(order_resp)

    return queue


@router.patch(
    "/orders/{orderId}/items/{itemId}/ready",
    summary="Mark a dish as ready",
    description="Kitchen marks one item of an order as ready. Roles: kitchen, chef, admin.",
    response_model=None,
    responses={status.HTTP_501_NOT_IMPLEMENTED: {"description": "Placeholder until TASK-13."}},
)
def mark_item_ready(
    order_id: OrderIdPath,
    item_id: ItemIdPath,
    body: MarkReadyRequest | None = None,
) -> NoReturn:
    """Mark an item as ready.

    Satisfies: RF-09, RF-10

    Args:
        order_id: Public identifier of the order.
        item_id: Public identifier of the item.
        body: Optional empty body; any field in it is rejected (no mass assignment).

    Raises:
        HTTPException: Always ``501`` until TASK-13 implements the behaviour.
    """
    # TODO(RF-09, RF-10, TASK-13): delegate to the order service with the AuthContext of the
    # guard: item READY transition, DISH_PREPARED event in the outbox (same transaction) and
    # the role-dependent domain checks (defense in depth, Guidelines §5.7).
    logger.warning(
        "mark_item_ready is not implemented",
        extra={"order_id": str(order_id), "item_id": str(item_id), "has_body": body is not None},
    )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED_DETAIL
    )


@router.patch(
    "/orders/{orderId}/ready",
    summary="Mark an order as ready",
    description="Kitchen marks the whole order as ready. Roles: kitchen, chef, admin.",
    response_model=None,
    responses={status.HTTP_501_NOT_IMPLEMENTED: {"description": "Placeholder until TASK-13."}},
)
def mark_order_ready(
    order_id: OrderIdPath,
    body: MarkReadyRequest | None = None,
) -> NoReturn:
    """Mark a whole order as ready.

    Satisfies: RF-11

    Args:
        order_id: Public identifier of the order.
        body: Optional empty body; any field in it is rejected (no mass assignment).

    Raises:
        HTTPException: Always ``501`` until TASK-13 implements the behaviour.
    """
    # TODO(RF-11, RF-18, TASK-13): delegate to the order service (IN_PREPARATION -> DELIVERED
    # through the state machine) and push the change over WebSocket after commit.
    logger.warning(
        "mark_order_ready is not implemented",
        extra={"order_id": str(order_id), "has_body": body is not None},
    )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED_DETAIL
    )
