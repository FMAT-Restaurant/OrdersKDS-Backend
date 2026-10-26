"""Order lifecycle HTTP endpoints.

Satisfies: RF-32, RF-33, RNF-02, RNF-09
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import AuthContext, get_auth_context, require_roles
from app.api.schemas.errors import ErrorResponse
from app.api.schemas.order import ConfirmOrderResponse
from app.db.session import get_session
from app.domain.errors import UnauthenticatedError
from app.domain.order_status import OrderStatus
from app.domain.roles import WAITER_ROLES
from app.services.orders import confirm_order

router = APIRouter(
    tags=["orders"],
    dependencies=[Depends(require_roles(*WAITER_ROLES))],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing, expired or malformed JWT (`UNAUTHENTICATED`).",
        },
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Role not allowed (`FORBIDDEN_ROLE`).",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Order not found (`ORDER_NOT_FOUND`).",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Order cannot be confirmed in its current state.",
        },
    },
)

OrderIdPath = Annotated[UUID, Path(alias="orderId", description="Public order identifier.")]


@router.post(
    "/orders/{orderId}/confirm",
    summary="Confirm an order",
    description=(
        "Records confirmation and queues `ordenes.orden.creada` transactionally. "
        "Returns immediately without waiting for Inventory."
    ),
    response_model=ConfirmOrderResponse,
)
async def confirm_order_endpoint(
    order_id: OrderIdPath,
    actor: Annotated[AuthContext, Depends(get_auth_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfirmOrderResponse:
    """Confirm an order once and return its current state.

    Satisfies: RF-32, RF-33

    Args:
        order_id: Public order UUID.
        actor: Authenticated caller resolved from the JWT.
        session: Request-scoped database session.

    Returns:
        The order's current ID, state, and confirmation timestamp.

    Raises:
        UnauthenticatedError: If the caller's user ID is not a UUID.
    """
    try:
        actor_id = UUID(actor.user_id)
    except ValueError as exc:
        raise UnauthenticatedError("Authentication required.") from exc

    async with session.begin():
        order = await confirm_order(session, order_id, actor_id)
    return ConfirmOrderResponse(
        orderId=order.public_id,
        status=OrderStatus(order.status),
        confirmedAt=order.confirmed_at,
    )
