"""Consumer for pagos.pago.completado events.

Satisfies: RF-21, RNF-02
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Final

from app.domain.order_status import OrderStatus
from app.messaging.events import EventName, PaymentCompletedMessage
from app.messaging.notifications import OrderNotifier, notify_order_changed
from app.repositories.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)

_ACCEPTED_SETTLEMENT_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "PAID",
        "COMPLETED",
        "APPROVED",
        "SUCCEEDED",
        "SETTLED",
        "PAGADO",
        "COMPLETADO",
        "APROBADO",
    }
)


def _is_settled_payment_status(raw_status: str | None) -> bool:
    """Return True when payment status is omitted (legacy) or explicitly settled."""
    if raw_status is None or not raw_status.strip():
        return True
    return raw_status.strip().upper() in _ACCEPTED_SETTLEMENT_STATUSES


def _is_sufficient_payment(
    message: PaymentCompletedMessage,
    expected_total: Decimal | None,
) -> bool:
    """Return True when reported settled amount covers the order total (OBS-02)."""
    if expected_total is None or expected_total <= Decimal("0.00"):
        return True
    reported = message.total_paid if message.total_paid is not None else message.total_amount
    if reported is None:
        return True
    return reported >= expected_total


async def handle_payment_completed(
    message: PaymentCompletedMessage,
    uow: UnitOfWork,
    notifier: OrderNotifier | None = None,
) -> bool:
    """React to Payments confirming successful settlement.

    Transitions order from DELIVERED or WASTED to terminal state PAID after
    verifying settlement status and amount coverage (RF-21, OBS-02).
    Orders in any other status or with non-settled/underpaid payloads are ignored.

    Args:
        message: Validated PaymentCompletedMessage from Payments.
        uow: Unit of work managing the database transaction boundary.
        notifier: Optional notification hook for state transition broadcasts.

    Returns:
        True if transition was applied, False if ignored (duplicate, unsettled, or ineligible).
    """
    if not _is_settled_payment_status(message.status):
        logger.warning(
            "Payment event %s ignored for order %s due to non-settled status %r",
            message.event_id,
            message.order_id,
            message.status,
            extra={"event_id": str(message.event_id), "order_id": str(message.order_id)},
        )
        return False

    async with uow:
        if uow.processed_events is None or uow.orders is None:
            raise RuntimeError("UnitOfWork repositories are not initialized")

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

        if not _is_sufficient_payment(message, order.total_amount):
            logger.warning(
                "Underpayment ignored for order %s: total_paid=%s, total_amount=%s, expected=%s",
                order.public_id,
                message.total_paid,
                message.total_amount,
                order.total_amount,
                extra={"event_id": str(message.event_id), "order_id": str(order.public_id)},
            )
            return False

        order.mark_paid()
        uow.processed_events.add(
            event_id=message.event_id,
            order_id=message.order_id,
            event_type=EventName.PAYMENT_COMPLETED,
        )
        await uow.commit()

    await notify_order_changed(order, notifier=notifier)
    return True
