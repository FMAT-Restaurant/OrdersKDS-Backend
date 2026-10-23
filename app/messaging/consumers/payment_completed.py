"""Consumer for pagos.pago.completado events.

Satisfies: RF-21, RNF-02
"""

from __future__ import annotations

import logging

from app.domain.order_status import OrderStatus
from app.messaging.events import EventName, PaymentCompletedMessage
from app.messaging.notifications import OrderNotifier, notify_order_changed
from app.repositories.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


async def handle_payment_completed(
    message: PaymentCompletedMessage,
    uow: UnitOfWork,
    notifier: OrderNotifier | None = None,
) -> bool:
    """React to Payments confirming successful settlement.

    Transitions order from DELIVERED or WASTED to terminal state PAID.
    Orders in any other status are ignored with a warning.

    Args:
        message: Validated PaymentCompletedMessage from Payments.
        uow: Unit of work managing the database transaction boundary.
        notifier: Optional notification hook for state transition broadcasts.

    Returns:
        True if transition was applied, False if ignored (duplicate or ineligible).
    """
    async with uow:
        assert uow.processed_events is not None
        assert uow.orders is not None

        if await uow.processed_events.exists(message.event_id, message.order_id):
            logger.info(
                "Duplicate payment_completed event %s ignored for order %s",
                message.event_id,
                message.order_id,
                extra={"event_id": str(message.event_id), "order_id": str(message.order_id)},
            )
            return False

        order = await uow.orders.get_by_public_id(message.order_id, for_update=True)
        if order is None:
            logger.warning(
                "Order %s not found for payment_completed event %s",
                message.order_id,
                message.event_id,
            )
            return False

        if order.status not in (OrderStatus.DELIVERED, OrderStatus.WASTED):
            logger.warning(
                "Payment completed ignored for order %s in ineligible status %s",
                order.public_id,
                order.status,
                extra={"event_id": str(message.event_id), "order_id": str(order.public_id)},
            )
            return False

        order.mark_paid()
        await uow.processed_events.add(
            event_id=message.event_id,
            order_id=message.order_id,
            event_type=EventName.PAYMENT_COMPLETED,
        )
        await uow.commit()

    await notify_order_changed(order, notifier=notifier)
    return True
