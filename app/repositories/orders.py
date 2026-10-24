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


class OrderRepository:
    """Repository managing Order aggregate root persistence and retrieval."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_public_id(
        self,
        public_id: UUID | str,
        *,
        for_update: bool = False,
    ) -> Order | None:
        """Fetch an order by its public UUID.

        Args:
            public_id: Public UUID of the order.
            for_update: If True, acquire row-level lock (SELECT FOR UPDATE).

        Returns:
            The Order if found, or None.
        """
        order_uuid = public_id if isinstance(public_id, UUID) else UUID(public_id)
        query = select(Order).where(Order.public_id == order_uuid)
        if for_update:
            query = query.with_for_update()
        return await self._session.scalar(query)

    async def get_for_update(self, public_id: UUID | str) -> Order:
        """Fetch an order with a row lock, raising OrderNotFoundError if absent.

        Args:
            public_id: Public UUID of the order.

        Returns:
            The locked Order aggregate root.

        Raises:
            OrderNotFoundError: If no order with public_id exists.
        """
        order = await self.get_by_public_id(public_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"No order with public_id={public_id}.")
        return order

    def save(self, order: Order) -> None:
        """Stage an order in the active session."""
        self._session.add(order)


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
