"""Consumer for inventario.stock.insuficiente events.

Satisfies: RF-34, RNF-02
"""

from __future__ import annotations

import logging

from app.domain.order_status import OrderStatus
from app.messaging.events import EventName, StockInsufficientMessage
from app.messaging.notifications import OrderNotifier, notify_order_changed
from app.repositories.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


async def handle_stock_insufficient(
    message: StockInsufficientMessage,
    uow: UnitOfWork,
    notifier: OrderNotifier | None = None,
) -> bool:
    """React to Inventory signaling insufficient stock.

    Transitions order from CREATED to REJECTED.
    Late notifications on EXPIRED and CANCELLED orders are ignored.

    Args:
        message: Validated StockInsufficientMessage from Inventory.
        uow: Unit of work managing the database transaction boundary.
        notifier: Optional notification hook for state transition alerts.

    Returns:
        True if transition was applied, False if ignored (duplicate or ineligible).
    """
    async with uow:
        assert uow.processed_events is not None
        assert uow.orders is not None

        if await uow.processed_events.exists(message.event_id, message.order_id):
            logger.info(
                "Duplicate stock_insufficient event %s ignored for order %s",
                message.event_id,
                message.order_id,
                extra={"event_id": str(message.event_id), "order_id": str(message.order_id)},
            )
            return False

        order = await uow.orders.get_by_public_id(message.order_id, for_update=True)
        if order is None:
            logger.warning(
                "Order %s not found for stock_insufficient event %s",
                message.order_id,
                message.event_id,
            )
            return False

        if order.status in (OrderStatus.EXPIRED, OrderStatus.CANCELLED):
            logger.warning(
                "Late stock_insufficient ignored for order %s in status %s",
                order.public_id,
                order.status,
                extra={"event_id": str(message.event_id), "order_id": str(order.public_id)},
            )
            return False

        order.mark_rejected()
        uow.processed_events.add(
            event_id=message.event_id,
            order_id=message.order_id,
            event_type=EventName.STOCK_INSUFFICIENT,
        )
        await uow.commit()

    await notify_order_changed(order, notifier=notifier)
    return True
