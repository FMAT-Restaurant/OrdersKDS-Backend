"""Consumer for inventory.redo.rejected compensation events.

Satisfies: RF-22, RNF-02, RNF-13
"""

from __future__ import annotations

import logging
from uuid import NAMESPACE_URL, UUID, uuid5

from app.db.models import Order, OrderItem
from app.domain.order_status import ItemStatus, OrderStatus
from app.messaging.events import EventName, RedoRejectedMessage
from app.messaging.notifications import (
    OrderNotifier,
    notify_item_changed,
    notify_order_changed,
)
from app.repositories.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


def _dedup_event_id(message: RedoRejectedMessage) -> UUID:
    discriminator = message.redo_id or message.item_id or message.order_id
    return uuid5(
        NAMESPACE_URL,
        f"{EventName.INVENTORY_REDO_REJECTED}:{message.event_id}:{discriminator}",
    )


def _resolve_target_item(order: Order, item_id: UUID | None) -> OrderItem | None:
    if item_id is not None:
        return next((item for item in order.items if item.public_id == item_id), None)
    pending_items = [item for item in order.items if item.status == ItemStatus.PENDING]
    return pending_items[0] if len(pending_items) == 1 else None


async def handle_redo_rejected(
    message: RedoRejectedMessage,
    uow: UnitOfWork,
    notifier: OrderNotifier | None = None,
) -> bool:
    """Compensate a rejected dish redo by returning its PENDING item to DELIVERED."""
    dedup_event_id = _dedup_event_id(message)
    target_item: OrderItem | None = None

    async with uow:
        if uow.processed_events is None or uow.orders is None:
            raise RuntimeError("UnitOfWork repositories are not initialized")

        if await uow.processed_events.exists(dedup_event_id, message.order_id):
            logger.info(
                "Duplicate inventory.redo.rejected event %s ignored for order %s",
                message.event_id,
                message.order_id,
                extra={"event_id": str(message.event_id), "order_id": str(message.order_id)},
            )
            return False

        order = await uow.orders.get_by_public_id(message.order_id, for_update=True)
        if order is None:
            logger.warning(
                "Order %s not found for inventory.redo.rejected event %s",
                message.order_id,
                message.event_id,
            )
            return False

        if order.status != OrderStatus.DELIVERED:
            logger.info(
                "Redo rejection ignored for order %s in non-DELIVERED status %s",
                order.public_id,
                order.status,
            )
            return False

        target_item = _resolve_target_item(order, message.item_id)
        if target_item is None or target_item.status != ItemStatus.PENDING:
            logger.info(
                "Redo rejection ignored for order %s (item=%s not in PENDING status)",
                order.public_id,
                message.item_id,
            )
            return False

        target_item.revert_redo_to_delivered()
        order.restore_pre_redo_note(target_item)
        order.record_redo_rejection(
            item=target_item,
            reason=message.reason,
            missing_ingredients=message.missing_ingredients,
            redo_id=message.redo_id,
        )
        uow.processed_events.add(
            event_id=dedup_event_id,
            order_id=message.order_id,
            event_type=EventName.INVENTORY_REDO_REJECTED,
        )
        await uow.commit()

    await notify_item_changed(order, target_item, notifier=notifier)
    await notify_order_changed(order, notifier=notifier)
    return True
