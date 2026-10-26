"""Persistence for orders with optimistic concurrency.

The caller sends the ``version`` it read; if it is stale the update is
rejected with ``VersionConflictError`` (HTTP 409, RNF-09) instead of
silently overwriting another writer.

Satisfies: RF-24, RNF-09
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.db.models import Order, OrderItem
from app.domain.errors import OrderNotFoundError, VersionConflictError
from app.domain.order_status import ItemStatus, OrderStatus, ensure_transition_allowed


def build_kds_queue_query(view: str = "active") -> Select[Order]:
    """Build SQLAlchemy query for KDS preparation queue (TASK-12).

    Discards any order where in_preparation_at is null, and explicitly excludes PAID status.
    - view='active': filters IN_PREPARATION or (DELIVERED with at least one PENDING item).
    - view='delivered': filters pure DELIVERED orders, excluding PAID.
    Orders by in_preparation_at ASC (oldest in kitchen first),
    tie-breaking by created_at ASC, then order_id ASC.
    """
    query = (
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.in_preparation_at.isnot(None))
        .where(Order.status != OrderStatus.PAID)
    )
    if view == "active":
        query = query.where(
            or_(
                Order.status == OrderStatus.IN_PREPARATION,
                and_(
                    Order.status == OrderStatus.DELIVERED,
                    Order.items.any(OrderItem.status == ItemStatus.PENDING),
                ),
            )
        )
    elif view == "delivered":
        query = query.where(Order.status == OrderStatus.DELIVERED)

    return query.order_by(
        Order.in_preparation_at.asc(),
        Order.created_at.asc(),
        Order.public_id.asc(),
    )


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

    async def list_awaiting_stock_confirmed_before(
        self,
        threshold: datetime,
        *,
        limit: int = 100,
    ) -> list[Order]:
        """Return confirmed orders still awaiting Inventory whose timer elapsed.

        Drafts (``CREATED`` without ``confirmed_at``) are excluded: a waiter may
        take minutes to assemble an order without it expiring (RF-35, OP-01).

        Rows are locked with ``SKIP LOCKED`` so concurrent watcher instances do
        not expire the same order twice.

        Args:
            threshold: Latest ``confirmed_at`` considered stale.
            limit: Maximum number of rows to return in one scan.

        Returns:
            Oldest stale orders first.
        """
        result = await self._session.scalars(
            select(Order)
            .where(
                Order.status == OrderStatus.CREATED,
                Order.confirmed_at.is_not(None),
                Order.confirmed_at <= threshold,
            )
            .order_by(Order.confirmed_at, Order.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result)

    async def get_by_dining_session_id(self, session_id: UUID | str) -> Order | None:
        """Fetch the order associated with a dining session."""
        session_uuid = session_id if isinstance(session_id, UUID) else UUID(session_id)
        return await self._session.scalar(
            select(Order).where(Order.dining_session_id == session_uuid)
        )

    async def get_kds_queue(self, view: str = "active") -> list[Order]:
        """Fetch orders for KDS queue matching view and in_preparation_at.

        Orders by in_preparation_at ASC (oldest in kitchen first),
        then created_at ASC, then order_id (public_id) ASC.
        """
        query = build_kds_queue_query(view=view)
        result = await self._session.scalars(query)
        orders = list(result.unique().all())
        orders.sort(
            key=lambda o: (
                o.in_preparation_at or datetime.min.replace(tzinfo=UTC),
                o.created_at or datetime.min.replace(tzinfo=UTC),
                o.public_id,
            )
        )
        return orders


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
