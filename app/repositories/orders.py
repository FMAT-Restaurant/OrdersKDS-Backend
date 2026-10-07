"""Persistence for orders with optimistic concurrency.

The caller sends the ``version`` it read; if it is stale the update is
rejected with ``VersionConflictError`` (HTTP 409, RNF-09) instead of
silently overwriting another writer.

Satisfies: RF-24, RNF-09
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.db.models import Order
from app.domain.errors import OrderNotFoundError, VersionConflictError
from app.domain.order_status import OrderStatus, ensure_transition_allowed


async def transition_order(
    session: AsyncSession,
    public_id: UUID,
    target: OrderStatus,
    expected_version: int,
) -> Order:
    """Move an order to ``target`` if ``expected_version`` is current.

    Args:
        session: Active async session (caller owns the transaction).
        public_id: Public UUID of the order.
        target: Requested status.
        expected_version: Version the caller read beforehand.

    Returns:
        The order with the new status (caller must commit).

    Raises:
        OrderNotFoundError: If no order matches ``public_id``.
        VersionConflictError: If ``expected_version`` is stale.
        InvalidStateTransitionError: If ``current -> target`` is not allowed.
    """
    order = await session.scalar(select(Order).where(Order.public_id == public_id))
    if order is None:
        raise OrderNotFoundError(f"No order with public_id={public_id}.")
    if order.version != expected_version:
        raise VersionConflictError(
            f"Stale version for order {public_id}: "
            f"expected {expected_version}, found {order.version}."
        )
    ensure_transition_allowed(order.status, target)
    order.status = target
    try:
        await session.flush()
    except StaleDataError as exc:
        raise VersionConflictError(f"Concurrent update lost for order {public_id}.") from exc
    return order
