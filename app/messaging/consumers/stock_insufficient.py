"""Consumer for inventario.stock.insuficiente and inventory.reservation.rejected events.

Satisfies: RF-34, RNF-02
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from app.domain.order_status import OrderStatus
from app.messaging.events import EventName, StockInsufficientMessage
from app.messaging.notifications import OrderNotifier, notify_order_changed
from app.repositories.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)

_IGNORED_PROTOCOL_ERROR_CODES = frozenset(
    {
        "ALREADY_SERVED",
        "RESERVATION_NOT_FOUND",
        "RESERVATION_NOT_ACTIVE",
    }
)

_NON_REJECTABLE_STATUSES = frozenset(
    {
        OrderStatus.EXPIRED,
        OrderStatus.CANCELLED,
        OrderStatus.IN_PREPARATION,
        OrderStatus.DELIVERED,
        OrderStatus.PAID,
        OrderStatus.VOIDED,
        OrderStatus.WASTED,
    }
)


def _build_rejection_details(message: StockInsufficientMessage) -> dict[str, object]:
    return {
        "reason": message.reason,
        "missingIngredients": message.missing_ingredients,
        "rejectedItems": [
            i.model_dump(mode="json", by_alias=True) for i in message.rejected_items
        ],
        "reservedItems": [
            i.model_dump(mode="json", by_alias=True) for i in message.reserved_items
        ],
    }


def _has_nonempty_list(details: Mapping[str, object], key: str) -> bool:
    value = details.get(key)
    return isinstance(value, list) and len(value) > 0


def _has_named_rejected_items(details: Mapping[str, object]) -> bool:
    raw = details.get("rejectedItems")
    if not isinstance(raw, list):
        return False
    return any(isinstance(entry, Mapping) and bool(entry.get("itemName")) for entry in raw)


def _is_more_informative_rejection(
    incoming: Mapping[str, object],
    existing: Mapping[str, object] | None,
) -> bool:
    """Return True when incoming rejection details carry richer UI context than existing."""
    if not isinstance(existing, Mapping) or not existing:
        return True
    gains_missing = _has_nonempty_list(incoming, "missingIngredients") and not _has_nonempty_list(
        existing, "missingIngredients"
    )
    gains_reserved = _has_nonempty_list(incoming, "reservedItems") and not _has_nonempty_list(
        existing, "reservedItems"
    )
    gains_named = _has_named_rejected_items(incoming) and not _has_named_rejected_items(existing)
    return gains_missing or gains_reserved or gains_named


async def handle_stock_insufficient(
    message: StockInsufficientMessage,
    uow: UnitOfWork,
    notifier: OrderNotifier | None = None,
) -> bool:
    """React to Inventory signaling insufficient stock.

    Transitions eligible orders to REJECTED, enriches already-REJECTED orders when
    a richer breakdown arrives after a raw reservation.rejected event, and ignores
    protocol errorCodes or late notifications on non-rejectable orders.
    """
    normalized_code = (message.error_code or "").strip().upper()
    if normalized_code in _IGNORED_PROTOCOL_ERROR_CODES:
        logger.warning(
            "Protocol rejection with errorCode=%s ignored for order %s (event=%s)",
            normalized_code,
            message.order_id,
            message.event_id,
        )
        return False

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

        if order.status in _NON_REJECTABLE_STATUSES:
            logger.warning(
                "Late stock_insufficient ignored for order %s in status %s",
                order.public_id,
                order.status,
                extra={"event_id": str(message.event_id), "order_id": str(order.public_id)},
            )
            return False

        details = _build_rejection_details(message)
        if order.status == OrderStatus.REJECTED:
            if not _is_more_informative_rejection(details, order.rejection_details):
                logger.info(
                    "Stock insufficient ignored for REJECTED order %s without extra detail",
                    order.public_id,
                    extra={"event_id": str(message.event_id), "order_id": str(order.public_id)},
                )
                return False
            order.rejection_details = details
        else:
            order.mark_rejected(details=details)

        uow.processed_events.add(
            event_id=message.event_id,
            order_id=message.order_id,
            event_type=EventName.STOCK_INSUFFICIENT,
        )
        await uow.commit()

    await notify_order_changed(order, notifier=notifier)
    return True

