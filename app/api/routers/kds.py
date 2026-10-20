"""KDS router: kitchen actions on orders and items.

Every route requires one of the ``KITCHEN_ROLES`` (Guidelines §5.7); a waiter gets
``403 FORBIDDEN_ROLE`` because the kitchen, not the waiter, marks dishes as ready (OP-08).

The role guard is the deliverable of TASK-20. The handlers are placeholders that answer
``501`` once the guard lets the caller through.

Satisfies: RF-09, RF-10, RF-11, RNF-03

TODO(RF-01, RF-02, RF-03, RF-04, RF-08, TASK-12): ``GET /kds/queue?station=&view=`` belongs
to this router and must share the same ``KITCHEN_ROLES`` guard.
"""

import logging
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.dependencies.auth import require_roles
from app.api.schemas.errors import ErrorResponse
from app.api.schemas.order import MarkReadyRequest
from app.domain.roles import KITCHEN_ROLES

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
