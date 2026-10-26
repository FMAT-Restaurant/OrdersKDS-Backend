"""Order confirmation business logic.

Confirmation and its Inventory event are staged in the same database transaction.

Satisfies: RF-32, RF-33, RNF-02, RNF-09
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.db.models import Order
from app.domain.errors import (
    InvalidStateTransitionError,
    OrderNotFoundError,
    VersionConflictError,
)
from app.domain.order_status import OrderStatus
from app.messaging.events import OrderCreatedEvent, OrderCreatedItem
from app.repositories.outbox import OutboxRepository

_IDEMPOTENT_STATUSES = frozenset(
    {
        OrderStatus.IN_PREPARATION,
        OrderStatus.DELIVERED,
        OrderStatus.WASTED,
        OrderStatus.PAID,
    }
)
_TERMINAL_STATUSES = frozenset(
    {
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.VOIDED,
    }
)


async def confirm_order(
    session: AsyncSession,
    public_id: UUID,
    confirmed_by: UUID,
    *,
    correlation_id: UUID | None = None,
) -> Order:
    """Confirm an order once and stage its inventory event atomically.

    Args:
        session: Active async session; the caller owns the transaction.
        public_id: Public UUID of the order to confirm.
        confirmed_by: Authenticated actor UUID.
        correlation_id: Optional request correlation UUID.

    Returns:
        The order in its current status.

    Raises:
        OrderNotFoundError: If no order matches ``public_id``.
        InvalidStateTransitionError: If the order is in a terminal state.
        VersionConflictError: If an optimistic update loses a concurrent race.
    """
    statement = select(Order).where(Order.public_id == public_id).with_for_update()
    order = await session.scalar(statement)
    if order is None:
        raise OrderNotFoundError(f"No order with public_id={public_id}.")

    if order.status in _TERMINAL_STATUSES:
        raise InvalidStateTransitionError(current=order.status, target=OrderStatus.CREATED)
    if order.status in _IDEMPOTENT_STATUSES:
        return order

    if order.confirmed_at is not None:
        if order.confirmed_by is None:
            order.confirmed_by = confirmed_by
            await _flush_or_raise_conflict(session, public_id)
        return order

    order.confirmed_at = datetime.now(UTC)
    if order.confirmed_by is None:
        order.confirmed_by = confirmed_by

    event = OrderCreatedEvent.for_order(
        order.public_id,
        [
            OrderCreatedItem(itemId=item.public_id, name=item.name, quantity=item.quantity)
            for item in order.items
        ],
        correlation_id=correlation_id,
    )
    OutboxRepository(session).add(event)
    await _flush_or_raise_conflict(session, public_id)
    return order


async def _flush_or_raise_conflict(session: AsyncSession, public_id: UUID) -> None:
    try:
        await session.flush()
    except StaleDataError as exc:
        raise VersionConflictError(f"Concurrent update lost for order {public_id}.") from exc
