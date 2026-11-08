"""Orders router: creation, modification, query, and transfer of orders.

Every route is guarded by JWT role claims (ADR-0011). Waiters and admins can create,
modify, query, transfer, and confirm draft orders. Modification is strictly rejected
once the order has been confirmed or has moved past CREATED status (OP-05 / ADR-0004).
Administrative voiding of in-preparation orders is strictly restricted to admin (RF-26).

Satisfies: RF-12, RF-13, RF-14, RF-15, RF-16, RF-17, RF-18, RF-23, RF-24, RF-25,
           RF-26, RF-27, RNF-03, RNF-09, RNF-13, ADR-0012
"""

from __future__ import annotations

import contextlib
import logging
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import AuthContext, require_roles
from app.api.schemas.errors import ErrorResponse
from app.api.schemas.order import (
    CancelOrderRequest,
    CreateOrderRequest,
    OrderResponse,
    RedoItemRequest,
    TransferOrderRequest,
    UpdateOrderItemsRequest,
    VoidKitchenErrorRequest,
    VoidOrderRequest,
)
from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.errors import (
    InvalidStateTransitionError,
    NoteRequiredError,
    OrderNotFoundError,
)
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import VOID_KITCHEN_ERROR_ROLES, WAITER_ROLES, Role
from app.repositories.orders import OrderRepository
from app.services.catalog import CatalogValidatorProtocol, get_catalog_validator
from app.services.orders import OrderService, get_order_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/orders",
    tags=["Orders"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing, expired or malformed JWT (`UNAUTHENTICATED`).",
        },
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Caller lacks required role (`FORBIDDEN_ROLE`).",
        },
    },
)

OrderIdPath = Annotated[UUID, Path(alias="orderId", description="Public order UUID identifier.")]


def _parse_actor_uuid(user_id_str: str) -> UUID:
    """Parse string actor identifier into a valid UUID deterministically."""
    try:
        return UUID(user_id_str)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"user:{user_id_str}")


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary="List orders",
    description="List orders optionally filtered by status enum or dining session (RF-18).",
)
async def list_orders(
    auth: Annotated[AuthContext, Depends(require_roles(Role.WAITER, Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[
        OrderStatus | None, Query(description="Filter by order status enum.")
    ] = None,
    dining_session_id: Annotated[
        UUID | None, Query(alias="diningSessionId", description="Filter by dining session UUID.")
    ] = None,
) -> list[OrderResponse]:
    """List orders optionally filtered by OrderStatus or dining session (RF-18).

    Args:
        auth: Authenticated caller context from JWT.
        session: Active database session.
        status: Optional OrderStatus filter.
        dining_session_id: Optional dining session filter.

    Returns:
        List of matching Order representations.
    """
    repo = OrderRepository(session)
    orders = await repo.list_orders(status=status, dining_session_id=dining_session_id)
    return [OrderResponse.from_model(order) for order in orders]


@router.patch(
    "/{orderId}/transfer",
    status_code=status.HTTP_200_OK,
    summary="Transfer order to another table",
    description=(
        "Transfers an active order to a different table with optimistic version "
        "control (RF-16, RNF-09)."
    ),
)
async def transfer_order(
    order_id: OrderIdPath,
    payload: TransferOrderRequest,
    auth: Annotated[AuthContext, Depends(require_roles(Role.WAITER, Role.ADMIN))],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> OrderResponse:
    """Transfer an active order to another table (RF-16, RNF-09).

    Args:
        order_id: Public UUID of target order.
        payload: Transfer request payload with target table number and optional version.
        auth: Authenticated caller context from JWT.
        order_service: Order domain service instance.
        if_match: Optional If-Match HTTP header specifying expected version.

    Returns:
        Updated Order representation with the new table number.

    Raises:
        OrderNotFoundError: If order does not exist (404).
        VersionConflictError: If expected version is stale or concurrent update lost (409).
        InvalidStateTransitionError: If order is in a terminal status (409).
    """
    expected_version = payload.version
    if expected_version is None and if_match:
        cleaned = if_match.strip().strip('"').removeprefix("W/")
        with contextlib.suppress(ValueError):
            expected_version = int(cleaned)

    order = await order_service.transfer_order(
        order_id=order_id,
        new_table_number=payload.table_number,
        expected_version=expected_version,
    )
    return OrderResponse.from_model(order)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create order (table or takeaway)",
    description="Creates a new order in CREATED status linked to a table or marked as takeaway.",
)
async def create_order(
    payload: CreateOrderRequest,
    auth: Annotated[AuthContext, Depends(require_roles(Role.WAITER, Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrderResponse:
    """Create a new order draft for dining room or takeaway (RF-12, RF-13, RF-15).

    Args:
        payload: Table or takeaway configuration.
        auth: Authenticated caller context from JWT.
        session: Active database session.

    Returns:
        The newly created Order aggregate representation.
    """
    actor_uuid = _parse_actor_uuid(auth.user_id)
    order = Order(
        public_id=uuid4(),
        table_number=payload.table_number,
        is_takeaway=payload.is_takeaway,
        dining_session_id=payload.dining_session_id,
        status=OrderStatus.CREATED,
        created_by=actor_uuid,
        responsible_waiter_id=actor_uuid,
    )

    repo = OrderRepository(session)
    repo.save(order)
    await session.commit()

    logger.info(
        "Created order %s (table=%s, takeaway=%s) by waiter %s",
        order.public_id,
        order.table_number,
        order.is_takeaway,
        auth.user_id,
    )
    return OrderResponse.from_model(order)


@router.patch(
    "/{orderId}/items",
    status_code=status.HTTP_200_OK,
    summary="Add or replace order items",
    description="Replaces the item lines of a draft order before it is confirmed.",
)
async def update_order_items(
    order_id: OrderIdPath,
    payload: UpdateOrderItemsRequest,
    auth: Annotated[AuthContext, Depends(require_roles(Role.WAITER, Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_session)],
    catalog_validator: Annotated[CatalogValidatorProtocol, Depends(get_catalog_validator)],
) -> OrderResponse:
    """Update dish items, quantities, modifiers and notes in a draft order (RF-14, RF-17, RF-23).

    Freezes prices at modification time. Rejects modification if the order has been
    confirmed or has moved past CREATED status (OP-05 / ADR-0004).

    Args:
        order_id: Public UUID of target order.
        payload: List of replacement items.
        auth: Authenticated caller context.
        session: Active database session.
        catalog_validator: Catalog availability checker.

    Returns:
        Updated Order representation with recalculated total amount.

    Raises:
        OrderNotFoundError: If order does not exist (404).
        InvalidStateTransitionError: If order is not CREATED or is confirmed (409).
        DishUnavailableError: If a requested dish is inactive in catalog (422).
    """
    repo = OrderRepository(session)
    order = await repo.get_by_public_id(order_id)
    if order is None:
        raise OrderNotFoundError(f"Order with id={order_id} not found.")

    if order.status == OrderStatus.REJECTED:
        order.reopen_as_draft()
    elif order.status != OrderStatus.CREATED:
        raise InvalidStateTransitionError(order.status, "ITEMS_MODIFIED")

    if order.confirmed_at is not None:
        raise InvalidStateTransitionError(
            order.status,
            "Cannot modify items on confirmed order awaiting stock",
        )

    # Validate catalog availability for every item (RF-17)
    for item_input in payload.items:
        await catalog_validator.validate_dish_available(item_input.dish_id)

    # Replace items with new entities with frozen prices (RF-14)
    order.items.clear()
    new_items = [
        OrderItem(
            public_id=uuid4(),
            dish_id=item_input.dish_id,
            name=item_input.name,
            quantity=item_input.quantity,
            unit_price=item_input.unit_price,
            modifiers=list(item_input.modifiers),
            notes=item_input.notes,
            status=ItemStatus.PENDING,
        )
        for item_input in payload.items
    ]
    order.items.extend(new_items)
    order.recalculate_total()

    repo.save(order)
    await session.commit()

    logger.info(
        "Updated %d items on order %s by user %s (total=%s)",
        len(new_items),
        order.public_id,
        auth.user_id,
        order.total_amount,
    )
    return OrderResponse.from_model(order)


@router.patch(
    "/{orderId}/cancel",
    status_code=status.HTTP_200_OK,
    summary="Cancel order",
    description=(
        "Cancels an order while in CREATED status and publishes compensation event (RF-24, RF-25)."
    ),
)
@router.post(
    "/{orderId}/cancel",
    status_code=status.HTTP_200_OK,
    summary="Cancel order (POST alias)",
    description=(
        "Cancels an order while in CREATED status and publishes compensation event (RF-24, RF-25)."
    ),
    include_in_schema=False,
)
async def cancel_order(
    order_id: OrderIdPath,
    auth: Annotated[AuthContext, Depends(require_roles(Role.WAITER, Role.ADMIN))],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    payload: CancelOrderRequest | None = None,
) -> OrderResponse:
    """Cancel an order draft in CREATED status (RF-24, RF-25).

    Args:
        order_id: Public UUID of target order.
        auth: Authenticated caller context from JWT.
        order_service: Order domain service instance.
        payload: Optional cancellation payload.

    Returns:
        The cancelled Order aggregate representation.

    Raises:
        OrderNotFoundError: If order does not exist (404).
        InvalidStateTransitionError: If order is not in CREATED status (409).
    """
    order = await order_service.cancel_order(
        order_id=order_id,
        auth=auth,
        reason=payload.reason if payload else None,
    )
    return OrderResponse.from_model(order)


@router.patch(
    "/{orderId}/void",
    status_code=status.HTTP_200_OK,
    summary="Administratively void order (with charge)",
    description=(
        "Administratively voids an order in IN_PREPARATION status (RF-26, RF-27, ADR-0012). "
        "Transitions the order to WASTED, and publishes both ordenes.comanda.mermada "
        "and ordenes.cobro.solicitado with frozen prices in the same transaction. "
        "Strictly restricted to admin role."
    ),
    responses={
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Caller lacks admin role (`FORBIDDEN_ROLE`).",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Order not found (`ORDER_NOT_FOUND`).",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Order is not in IN_PREPARATION status (`INVALID_STATE_TRANSITION`).",
        },
    },
)
@router.post(
    "/{orderId}/void",
    status_code=status.HTTP_200_OK,
    summary="Administratively void order (POST alias)",
    description="POST alias for administrative void.",
    include_in_schema=False,
)
async def void_order(
    order_id: OrderIdPath,
    auth: Annotated[AuthContext, Depends(require_roles(Role.ADMIN))],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    payload: VoidOrderRequest | None = None,
) -> OrderResponse:
    """Administratively void an order in IN_PREPARATION status (RF-26, RF-27, ADR-0012).

    Args:
        order_id: Public UUID of target order.
        auth: Authenticated caller context from JWT (strictly admin).
        order_service: Order domain service instance.
        payload: Optional void request payload.

    Returns:
        The order aggregate in WASTED status.

    Raises:
        OrderNotFoundError: If order does not exist (404).
        InvalidStateTransitionError: If order is not in IN_PREPARATION status (409).
        ForbiddenRoleError: If caller is not admin (403).
    """
    order = await order_service.void_order(
        order_id=order_id,
        auth=auth,
        reason=payload.reason if payload else None,
    )
    return OrderResponse.from_model(order)


@router.patch(
    "/{orderId}/void-kitchen-error",
    status_code=status.HTTP_200_OK,
    summary="Void order for kitchen error (without charge)",
    description=(
        "Voids an order in IN_PREPARATION status due to a kitchen incident (RF-28..31, ADR-0012). "
        "Transitions the order to VOIDED and publishes ordenes.comanda.mermada with "
        "motivo='error_cocina' (reason='KITCHEN_ERROR') without publishing any charge event. "
        "Requires a non-empty explanation note. Restricted to admin and chef roles."
    ),
    responses={
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Caller lacks admin or chef role (`FORBIDDEN_ROLE`).",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Order not found (`ORDER_NOT_FOUND`).",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Order is not in IN_PREPARATION status (`INVALID_STATE_TRANSITION`).",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": (
                "Missing, empty, or whitespace-only explanation note (`NOTE_REQUIRED`)."
            ),
        },
    },
)
@router.post(
    "/{orderId}/void-kitchen-error",
    status_code=status.HTTP_200_OK,
    summary="Void order for kitchen error (POST alias)",
    description="POST alias for kitchen error void.",
    include_in_schema=False,
)
async def void_kitchen_error(
    order_id: OrderIdPath,
    auth: Annotated[AuthContext, Depends(require_roles(*VOID_KITCHEN_ERROR_ROLES))],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    payload: VoidKitchenErrorRequest | None = None,
) -> OrderResponse:
    """Void an order in IN_PREPARATION status due to kitchen error (RF-28..31, ADR-0012).

    Args:
        order_id: Public UUID of target order.
        auth: Authenticated caller context from JWT (admin or chef).
        order_service: Order domain service instance.
        payload: Mandatory kitchen error explanation payload.

    Returns:
        The order aggregate in VOIDED status.

    Raises:
        NoteRequiredError: If note is missing, empty, or whitespace-only (422).
        OrderNotFoundError: If order does not exist (404).
        InvalidStateTransitionError: If order is not in IN_PREPARATION status (409).
        ForbiddenRoleError: If caller lacks admin or chef role (403).
    """
    note = payload.note if payload is not None else None
    if note is None or not note.strip():
        raise NoteRequiredError("A non-empty note is required for a kitchen error void.")

    order = await order_service.void_kitchen_error(
        order_id=order_id,
        auth=auth,
        note=note.strip(),
    )
    return OrderResponse.from_model(order)


@router.post(
    "/{orderId}/request-bill",
    status_code=status.HTTP_200_OK,
    summary="Request bill for delivered order",
    description=(
        "Requests the bill for an order in DELIVERED status (RF-20, RF-21). "
        "Stages 'ordenes.cuenta.solicitada' outbox event with frozen dish prices, "
        "modifiers, and dual identifiers (orderId, sessionId). "
        "The order remains in DELIVERED status until payment settlement. "
        "Restricted to waiter and admin roles."
    ),
    responses={
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Caller lacks waiter or admin role (`FORBIDDEN_ROLE`).",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Order not found (`ORDER_NOT_FOUND`).",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Order is not in DELIVERED status (`INVALID_STATE_TRANSITION`).",
        },
    },
)
@router.patch(
    "/{orderId}/request-bill",
    status_code=status.HTTP_200_OK,
    summary="Request bill for delivered order (PATCH alias)",
    description="PATCH alias for request bill.",
    include_in_schema=False,
)
async def request_bill(
    order_id: OrderIdPath,
    auth: Annotated[AuthContext, Depends(require_roles(*WAITER_ROLES))],
    order_service: Annotated[OrderService, Depends(get_order_service)],
) -> OrderResponse:
    """Request bill for an order in DELIVERED status (RF-20, RF-21).

    Args:
        order_id: Public UUID of target order.
        auth: Authenticated caller context from JWT (waiter or admin).
        order_service: Order domain service instance.

    Returns:
        The order aggregate representation (remains in DELIVERED status).

    Raises:
        OrderNotFoundError: If order does not exist (404).
        InvalidStateTransitionError: If order is not in DELIVERED status (409).
        ForbiddenRoleError: If caller lacks waiter or admin role (403).
    """
    order = await order_service.request_bill(
        order_id=order_id,
        auth=auth,
    )
    return OrderResponse.from_model(order)


@router.post(
    "/{orderId}/confirm",
    status_code=status.HTTP_200_OK,
    summary="Confirm order",
    description=(
        "Confirms a draft order and queues inventory reservation without waiting for Inventory."
    ),
)
async def confirm_order(
    order_id: OrderIdPath,
    auth: Annotated[AuthContext, Depends(require_roles(Role.WAITER, Role.ADMIN))],
    order_service: Annotated[OrderService, Depends(get_order_service)],
) -> OrderResponse:
    """Confirm an order once, atomically recording its timestamp and outbox event (TASK-11).

    Args:
        order_id: Public UUID of target order.
        auth: Authenticated caller context from JWT.
        order_service: Order domain service instance.

    Returns:
        The confirmed Order aggregate representation.

    Raises:
        OrderNotFoundError: If order does not exist (404).
        InvalidStateTransitionError: If order is not in CREATED status or is terminal (409).
    """
    order = await order_service.confirm_order(
        order_id,
        confirmed_by=_parse_actor_uuid(auth.user_id),
    )
    return OrderResponse.from_model(order)


@router.get(
    "/{orderId}",
    status_code=status.HTTP_200_OK,
    summary="Get order details",
    description="Retrieve an order aggregate by public UUID.",
    dependencies=[
        Depends(require_roles(Role.WAITER, Role.ADMIN, Role.KITCHEN, Role.CHEF)),
    ],
)
async def get_order_by_id(
    order_id: OrderIdPath,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrderResponse:
    """Fetch order by public UUID (RF-18).

    Args:
        order_id: Public UUID of target order.
        session: Active database session.

    Returns:
        Order representation.

    Raises:
        OrderNotFoundError: If order does not exist (404).
    """
    repo = OrderRepository(session)
    order = await repo.get_by_public_id(order_id)
    if order is None:
        raise OrderNotFoundError(f"Order with id={order_id} not found.")

    return OrderResponse.from_model(order)


@router.patch(
    "/{orderId}/items/{itemId}/redo",
    status_code=status.HTTP_200_OK,
    summary="Return a delivered dish to kitchen",
    description=(
        "Returns one delivered item to PENDING while preserving the order DELIVERED status."
    ),
)
async def redo_order_item(
    order_id: OrderIdPath,
    item_id: Annotated[UUID, Path(alias="itemId", description="Public order item identifier.")],
    auth: Annotated[AuthContext, Depends(require_roles(*WAITER_ROLES))],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    payload: RedoItemRequest | None = None,
) -> OrderResponse:
    """Return a delivered item to kitchen with a mandatory note (RF-22, OP-12)."""
    order = await order_service.redo_order_item(
        order_id,
        item_id,
        auth,
        note=payload.note if payload else None,
    )
    return OrderResponse.from_model(order)
