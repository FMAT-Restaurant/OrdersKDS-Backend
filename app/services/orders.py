"""Order domain service orchestrating lifecycle operations and transitions.

Satisfies: RF-09, RF-10, RF-11, RF-16, RF-18, RF-24, RF-25, RNF-01, RNF-03,
           RNF-09, OP-05, OP-08, OP-11
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.api.dependencies.auth import AuthContext
from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.errors import (
    ForbiddenRoleError,
    InvalidOrderItemError,
    InvalidStateTransitionError,
    OrderNotFoundError,
    VersionConflictError,
)
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import KITCHEN_ROLES
from app.messaging.events import (
    DishPreparedEvent,
    KitchenDishRejectedEvent,
    OrderCancelledEvent,
    OrderCreatedEvent,
    OrderCreatedItem,
    OrderInPreparationEvent,
)
from app.messaging.validator import validate_event_payload
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.services.catalog import CatalogValidatorProtocol, get_catalog_validator
from app.services.notifications import (
    OrderNotifier,
    default_notifier,
    notify_item_changed,
    notify_order_changed,
)

logger = logging.getLogger(__name__)


def _consolidate_order_items(items: list[OrderItem]) -> list[OrderCreatedItem]:
    """Group active non-cancelled items by dish_id (offerId) and sum quantities."""
    quantities: dict[str, int] = {}
    for item in items:
        if item.status == ItemStatus.CANCELLED or not item.dish_id:
            continue
        quantities[item.dish_id] = quantities.get(item.dish_id, 0) + item.quantity
    return [OrderCreatedItem(offerId=dish_id, quantity=qty) for dish_id, qty in quantities.items()]


class OrderService:
    """Service encapsulating order operations, outbox staging, and lifecycle events."""

    def __init__(
        self,
        session: AsyncSession,
        notifier: OrderNotifier | None = None,
        catalog_repo: CatalogValidatorProtocol | None = None,
    ) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._outbox = OutboxRepository(session)
        self._notifier = notifier or default_notifier
        self._catalog_repo = catalog_repo

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

    async def start_order_preparation(
        self,
        order_id: UUID,
        auth: AuthContext,
    ) -> Order:
        """Transition order from RESERVED to IN_PREPARATION and publish OrderInPreparationEvent.

        Roles: kitchen, chef, admin.
        Inventory consumes reserved stock and closes reservation TTL upon receiving this event.
        """
        self._verify_kitchen_role(auth)
        order = await self._orders.get_by_public_id(order_id)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        if order.status == OrderStatus.IN_PREPARATION:
            return order

        if order.status != OrderStatus.RESERVED:
            raise InvalidStateTransitionError(order.status, OrderStatus.IN_PREPARATION)

        order.mark_in_preparation()
        active_items = _consolidate_order_items(order.items)
        event = OrderInPreparationEvent.for_order(order.public_id, items=active_items)
        payload = event.model_dump(mode="json", by_alias=True)
        validate_event_payload(event.event_name, payload)
        self._outbox.add(event)

        self._orders.save(order)
        await self._session.commit()
        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def kitchen_reject_item(
        self,
        order_id: UUID,
        item_id: UUID,
        auth: AuthContext,
        reason: str = "Insumo físico insuficiente en cocina",
    ) -> Order:
        """Kitchen rejects an item from RESERVED order due to lack of physical stock.

        Updates local Redis catalog availability (available: false), publishes
        KitchenDishRejectedEvent to Inventory/Menu, and transitions order to REJECTED
        so the waiter can edit and re-confirm the order.
        """
        self._verify_kitchen_role(auth)
        order = await self._orders.get_by_public_id(order_id)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        if order.status != OrderStatus.RESERVED:
            raise InvalidStateTransitionError(order.status, OrderStatus.REJECTED)

        target_item = next((i for i in order.items if i.public_id == item_id), None)
        if target_item is None:
            raise OrderNotFoundError(f"Item with id={item_id} not found in order {order_id}.")

        dish_id = target_item.dish_id or str(target_item.public_id)

        # 1. Update local Redis catalog to immediately block new additions
        if self._catalog_repo is not None:
            await self._catalog_repo.set_availability(dish_id, False)

        # 2. Stage KitchenDishRejectedEvent into Outbox for Inventory and Menu
        event = KitchenDishRejectedEvent.for_item(
            order_id=order.public_id,
            item_id=target_item.public_id,
            offer_id=dish_id,
            quantity=target_item.quantity,
            reason=reason,
        )
        payload = event.model_dump(mode="json", by_alias=True)
        validate_event_payload(event.event_name, payload)
        self._outbox.add(event)

        # 3. Transition order to REJECTED (recording details for waiter)
        rejection_details: dict[str, object] = {
            "source": "kitchen",
            "rejectedItems": [
                {
                    "itemId": str(target_item.public_id),
                    "offerId": dish_id,
                    "name": target_item.name,
                    "requestedQuantity": target_item.quantity,
                    "availableQuantity": 0,
                    "reason": reason,
                }
            ],
        }
        order.mark_rejected(details=rejection_details)
        self._orders.save(order)
        await self._session.commit()

        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def cancel_item(
        self,
        order_id: UUID,
        item_id: UUID,
        auth: AuthContext,
        reason: str | None = None,
    ) -> Order:
        """Cancel a single item in an order while still in CREATED or RESERVED status."""
        order = await self._orders.get_by_public_id(order_id)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        if order.status not in (OrderStatus.CREATED, OrderStatus.RESERVED):
            raise InvalidStateTransitionError(order.status, ItemStatus.CANCELLED)

        target_item = next((i for i in order.items if i.public_id == item_id), None)
        if target_item is None:
            raise OrderNotFoundError(f"Item with id={item_id} not found in order {order_id}.")

        target_item.mark_cancelled()
        order.recalculate_total()
        self._orders.save(order)
        await self._session.commit()

        await notify_item_changed(order, target_item, notifier=self._notifier)
        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def transfer_order(
        self,
        order_id: UUID,
        new_table_number: int,
        expected_version: int | None = None,
    ) -> Order:
        """Transfer an active order to a different table (RF-16, RNF-09)."""
        order = await self._orders.get_by_public_id(order_id)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        terminal_statuses = {
            OrderStatus.PAID,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.VOIDED,
        }
        if order.status in terminal_statuses:
            raise InvalidStateTransitionError(order.status, "TRANSFER")

        if expected_version is not None and order.version != expected_version:
            raise VersionConflictError(
                f"Stale version for order {order_id}: "
                f"expected {expected_version}, found {order.version}."
            )

        order.table_number = new_table_number
        order.is_takeaway = False
        if hasattr(self._session, "db"):
            order.version = (order.version or 1) + 1
        self._orders.save(order)

        try:
            await self._session.commit()
        except StaleDataError as exc:
            raise VersionConflictError(f"Concurrent update lost for order {order_id}.") from exc

        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def cancel_order(
        self,
        order_id: UUID,
        auth: AuthContext,
        reason: str | None = None,
    ) -> Order:
        """Cancel an order while in CREATED, RESERVED, or REJECTED status (RF-24, RF-25, OP-05)."""
        order = await self._orders.get_by_public_id(order_id)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        allowed_cancel_statuses = {
            OrderStatus.CREATED,
            OrderStatus.RESERVED,
            OrderStatus.REJECTED,
        }
        if order.status not in allowed_cancel_statuses:
            raise InvalidStateTransitionError(order.status, OrderStatus.CANCELLED)

        logger.info(
            "Order %s cancelled by actor %s (role=%s). Reason: %s",
            order.public_id,
            auth.user_id,
            auth.role,
            reason or "No reason provided",
        )

        order.mark_cancelled()

        cancelled_items = _consolidate_order_items(order.items)
        event = OrderCancelledEvent.for_order(
            order.public_id,
            items=cancelled_items,
        )
        payload = event.model_dump(mode="json", by_alias=True)
        validate_event_payload(event.event_name, payload)
        self._outbox.add(event)

        self._orders.save(order)
        await self._session.commit()

        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def confirm_order(self, order_id: UUID, confirmed_by: UUID) -> Order:
        """Confirm an order and atomically stage its inventory event (OP-05, TASK-11)."""
        order = await self._orders.get_for_update(order_id)

        if order.status == OrderStatus.CREATED:
            if order.confirmed_at is None:
                for item in order.items:
                    if item.dish_id is None or item.quantity < 1:
                        raise InvalidOrderItemError()
                event_items = _consolidate_order_items(order.items)
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
            OrderStatus.RESERVED,
            OrderStatus.IN_PREPARATION,
            OrderStatus.DELIVERED,
            OrderStatus.WASTED,
            OrderStatus.PAID,
        }:
            return order

        raise InvalidStateTransitionError(order.status, "CONFIRM")


def get_order_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    catalog_repo: Annotated[CatalogValidatorProtocol, Depends(get_catalog_validator)],
) -> OrderService:
    """FastAPI dependency yielding an OrderService bound to active session."""
    return OrderService(session, catalog_repo=catalog_repo)
