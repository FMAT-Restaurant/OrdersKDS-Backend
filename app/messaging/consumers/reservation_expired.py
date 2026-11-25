"""Consumer for inventory.reservation.expired events.

Satisfies: RF-12, RF-13, RF-19, RNF-02, RNF-13
"""

from __future__ import annotations

import logging
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domain.order_status import OrderStatus
from app.messaging.events import EventName, ReservationExpiredMessage
from app.messaging.notifications import OrderNotifier, notify_order_changed
from app.repositories.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


def _dedup_event_id(message: ReservationExpiredMessage) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"{EventName.INVENTORY_RESERVATION_EXPIRED}:{message.event_id}:{message.order_id}",
    )


async def handle_reservation_expired(
    message: ReservationExpiredMessage,
    uow: UnitOfWork,
    notifier: OrderNotifier | None = None,
) -> bool:
    """Expire a CREATED or RESERVED order when Inventory reports reservation expiration."""
    dedup_event_id = _dedup_event_id(message)
    async with uow:
        if uow.processed_events is None or uow.orders is None:
            raise RuntimeError("UnitOfWork repositories are not initialized")

        if await uow.processed_events.exists(dedup_event_id, message.order_id):
            logger.info(
                "Duplicate reservation_expired event %s ignored for order %s",
                message.event_id,
                message.order_id,
                extra={"event_id": str(message.event_id), "order_id": str(message.order_id)},
            )
            return False

        order = await uow.orders.get_by_public_id(message.order_id, for_update=True)
        if order is None:
            logger.warning(
                "Order %s not found for reservation_expired event %s",
                message.order_id,
                message.event_id,
            )
            return False

        if order.status not in (OrderStatus.CREATED, OrderStatus.RESERVED):
            logger.info(
                "Late reservation_expired ignored for order %s in status %s",
                order.public_id,
                order.status,
                extra={"event_id": str(message.event_id), "order_id": str(order.public_id)},
            )
            return False

        order.mark_expired()
        uow.processed_events.add(
            event_id=dedup_event_id,
            order_id=message.order_id,
            event_type=EventName.INVENTORY_RESERVATION_EXPIRED,
        )
        await uow.commit()

    await notify_order_changed(order, notifier=notifier)
    return True
