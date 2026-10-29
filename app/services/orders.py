"""Order domain service orchestrating lifecycle operations and transitions.

Satisfies: RF-09, RF-10, RF-11, RF-18, RNF-01, RNF-03, OP-05, OP-08
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import AuthContext
from app.db.models import Order
from app.db.session import get_session
from app.domain.errors import (
    ForbiddenRoleError,
    InvalidOrderItemError,
    InvalidStateTransitionError,
    OrderNotFoundError,
)
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import KITCHEN_ROLES
from app.messaging.events import DishPreparedEvent, OrderCreatedEvent, OrderCreatedItem
from app.messaging.validator import validate_event_payload
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.services.notifications import (
    OrderNotifier,
    default_notifier,
    notify_item_changed,
    notify_order_changed,
)

logger = logging.getLogger(__name__)


class OrderService:
    """Service encapsulating order operations, outbox staging, and lifecycle events."""

    def __init__(
        self,
        session: AsyncSession,
        notifier: OrderNotifier | None = None,
    ) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._outbox = OutboxRepository(session)
        self._notifier = notifier or default_notifier

    def _verify_kitchen_role(self, auth: AuthContext) -> None:
        if auth.role not in KITCHEN_ROLES:
            raise ForbiddenRoleError("Role not allowed for kitchen action.")

    def _sync_delivery_if_all_ready(self, order: Order) -> bool:
        """Move order and all items to DELIVERED if every item is ready/delivered."""
        if not order.items:
            return False
        all_ready = all(i.status in (ItemStatus.READY, ItemStatus.DELIVERED) for i in order.items)
        if all_ready and order.status == OrderStatus.IN_PREPARATION:
            order.mark_delivered()
            for item in order.items:
                if item.status == ItemStatus.READY:
                    item.mark_delivered()
            return True
        return False

    async def mark_item_ready(
        self,
        order_id: UUID,
        item_id: UUID,
        auth: AuthContext,
    ) -> Order:
        """Mark an individual order item as READY (RF-09, RF-10, OP-08)."""
        self._verify_kitchen_role(auth)
        order = await self._orders.get_by_public_id(order_id)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        target_item = next((i for i in order.items if i.public_id == item_id), None)
        if target_item is None:
            raise OrderNotFoundError(f"Item with id={item_id} not found in order {order_id}.")

        if target_item.status in (ItemStatus.READY, ItemStatus.DELIVERED):
            if order.status == OrderStatus.DELIVERED:
                return order
            if order.status != OrderStatus.IN_PREPARATION:
                raise InvalidStateTransitionError(order.status, OrderStatus.DELIVERED)
        elif target_item.status == ItemStatus.PENDING:
            if order.status != OrderStatus.IN_PREPARATION:
                raise InvalidStateTransitionError(order.status, OrderStatus.DELIVERED)
            target_item.mark_ready()
            self._outbox.add(
                DishPreparedEvent.for_item(
                    order.public_id,
                    target_item.public_id,
                    offer_id=target_item.dish_id,
                )
            )

        delivered_now = self._sync_delivery_if_all_ready(order)
        self._orders.save(order)
        await self._session.commit()

        await notify_item_changed(order, target_item, notifier=self._notifier)
        if delivered_now:
            await notify_order_changed(order, notifier=self._notifier)
        return order

    async def mark_order_ready(
        self,
        order_id: UUID,
        auth: AuthContext,
    ) -> Order:
        """Mark all pending items of an order as READY and deliver order (RF-11, OP-08)."""
        self._verify_kitchen_role(auth)
        order = await self._orders.get_by_public_id(order_id)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")
        if order.status == OrderStatus.DELIVERED:
            return order
        if order.status != OrderStatus.IN_PREPARATION:
            raise InvalidStateTransitionError(order.status, OrderStatus.DELIVERED)

        for item in order.items:
            if item.status == ItemStatus.PENDING:
                item.mark_ready()
                self._outbox.add(
                    DishPreparedEvent.for_item(
                        order.public_id,
                        item.public_id,
                        offer_id=item.dish_id,
                    )
                )

        order.mark_delivered()
        for item in order.items:
            if item.status == ItemStatus.READY:
                item.mark_delivered()

        self._orders.save(order)
        await self._session.commit()
        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def confirm_order(self, order_id: UUID, confirmed_by: UUID) -> Order:
        """Confirm an order and atomically stage its inventory event (OP-05, TASK-11)."""
        order = await self._orders.get_for_update(order_id)

        if order.status == OrderStatus.CREATED:
            if order.confirmed_at is None:
                event_items: list[OrderCreatedItem] = []
                for item in order.items:
                    dish_id = item.dish_id
                    if dish_id is None or item.quantity < 1:
                        raise InvalidOrderItemError()
                    event_items.append(OrderCreatedItem(offerId=dish_id, quantity=item.quantity))
                event = OrderCreatedEvent.for_order(
                    order.public_id,
                    items=event_items,
                )
                payload = event.model_dump(mode="json", by_alias=True)
                validate_event_payload(event.event_name, payload)
                order.confirmed_at = datetime.now(UTC)
                order.confirmed_by = confirmed_by
                self._outbox.add(event)
                self._orders.save(order)
                await self._session.commit()
            elif order.confirmed_by is None:
                order.confirmed_by = confirmed_by
                self._orders.save(order)
                await self._session.commit()
            return order

        if order.status in {
            OrderStatus.IN_PREPARATION,
            OrderStatus.DELIVERED,
            OrderStatus.WASTED,
            OrderStatus.PAID,
        }:
            return order

        raise InvalidStateTransitionError(order.status, "CONFIRM")


def get_order_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrderService:
    """FastAPI dependency yielding an OrderService bound to active session."""
    return OrderService(session)
