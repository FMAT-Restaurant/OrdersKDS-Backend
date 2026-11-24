"""Consumer template and Dead Letter Queue (DLQ) dispatcher.

Ensures malformed messages are routed to a per-queue DLQ without
altering any order state or terminating the consumer worker.

Satisfies: RNF-02, RNF-13
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.consumers.dining_session import handle_dining_session_created
from app.messaging.consumers.payment_completed import handle_payment_completed
from app.messaging.consumers.redo_rejected import handle_redo_rejected
from app.messaging.consumers.reservation_expired import handle_reservation_expired
from app.messaging.consumers.stock_insufficient import handle_stock_insufficient
from app.messaging.consumers.stock_reserved import handle_stock_reserved
from app.messaging.events import (
    DiningSessionCreatedMessage,
    EventName,
    PaymentCompletedMessage,
    RedoConfirmedMessage,
    RedoRejectedMessage,
    ReservationExpiredMessage,
    ReservationLifecycleAckMessage,
    StockInsufficientMessage,
    StockReservedMessage,
)
from app.messaging.notifications import OrderNotifier
from app.repositories.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]


class DLQRouter(Protocol):
    """Protocol for routing unprocessable or poison messages to a DLQ."""

    async def route_to_dlq(
        self,
        raw_message: object,
        queue_name: str,
        reason: str,
    ) -> None:
        """Route raw poison payload to the designated queue's DLQ."""
        ...


class InMemoryDLQRouter:
    """In-memory DLQ tracker for testing and standalone operations."""

    def __init__(self) -> None:
        self.dead_letters: list[dict[str, object]] = []

    async def route_to_dlq(
        self,
        raw_message: object,
        queue_name: str,
        reason: str,
    ) -> None:
        """Record dead-letter payload with timestamp and reason."""
        await asyncio.sleep(0)
        entry = {
            "queue_name": queue_name,
            "dlq_queue": f"{queue_name}.dlq",
            "reason": reason,
            "raw_message": raw_message,
        }
        self.dead_letters.append(entry)
        logger.error(
            "Message sent to DLQ [%s]: %s (payload: %s)",
            entry["dlq_queue"],
            reason,
            raw_message,
        )


def _parse_payload(raw_message: object) -> tuple[dict[str, Any] | None, str | None]:
    """Parse raw message into a dictionary or return failure reason."""
    if isinstance(raw_message, (bytes, str)):
        try:
            parsed = json.loads(raw_message)
            if isinstance(parsed, dict):
                return parsed, None
            return None, "Decoded JSON payload is not an object"
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            return None, f"Invalid JSON: {err}"
    if isinstance(raw_message, dict):
        return raw_message, None
    return None, f"Unsupported payload type: {type(raw_message)}"


_STATUS_TO_EVENT: dict[str, EventName] = {
    "confirmed": EventName.STOCK_RESERVED,
    "rejected": EventName.STOCK_INSUFFICIENT,
    "expired": EventName.INVENTORY_RESERVATION_EXPIRED,
    "consumed": EventName.INVENTORY_RESERVATION_CONSUMED,
    "released": EventName.INVENTORY_RESERVATION_RELEASED,
    "cancelled": EventName.INVENTORY_RESERVATION_RELEASED,
}

_RESERVATION_ACK_KEYS: tuple[str, ...] = (
    EventName.INVENTORY_RESERVATION_CONSUMED,
    EventName.INVENTORY_RESERVATION_RELEASED,
    "reservation.consumed",
    "reservation_consumed",
    "reservation.consumida",
    "reservation.released",
    "reservation_released",
    "reservation.liberada",
    "reservation.cancelled",
    "reservation_cancelled",
    "reservation.cancelada",
)


def _acknowledge_reservation_lifecycle(
    target_event: str,
    parsed_data: dict[str, Any],
) -> bool:
    """Validate and log informational Inventory reservation lifecycle confirmations."""
    msg_ack = ReservationLifecycleAckMessage.model_validate(parsed_data)
    logger.info(
        "Acknowledged informational inventory event %s for order %s (status=%s, items=%d)",
        target_event,
        msg_ack.order_id,
        msg_ack.status,
        len(msg_ack.items),
        extra={
            "event_id": str(msg_ack.event_id),
            "order_id": str(msg_ack.order_id),
            "event_type": target_event,
        },
    )
    return True


def _acknowledge_redo_confirmed(
    target_event: str,
    parsed_data: dict[str, Any],
) -> bool:
    """Validate and log informational Inventory FEFO confirmation for a dish redo."""
    msg_confirmed = RedoConfirmedMessage.model_validate(parsed_data)
    logger.info(
        "Acknowledged %s for order %s (redo_id=%s, item_id=%s, items=%d)",
        target_event,
        msg_confirmed.order_id,
        msg_confirmed.redo_id,
        msg_confirmed.item_id,
        len(msg_confirmed.items),
        extra={
            "event_id": str(msg_confirmed.event_id),
            "order_id": str(msg_confirmed.order_id),
            "event_type": target_event,
        },
    )
    return True


def _resolve_target_event(
    parsed_data: dict[str, Any],
    queue_name: str,
    event_type: str | None,
) -> str:
    """Infer target event name from routing key, envelope, or payload fields."""
    if event_type:
        return event_type
    for key in ("eventType", "event_type"):
        if key in parsed_data:
            return str(parsed_data[key])
    status = str(parsed_data.get("status", "")).lower()
    has_order = "orderId" in parsed_data or "order_id" in parsed_data
    has_redo = "redoId" in parsed_data or "redo_id" in parsed_data
    if has_order and has_redo:
        if status == "confirmed":
            return str(EventName.INVENTORY_REDO_CONFIRMED)
        if status == "rejected":
            return str(EventName.INVENTORY_REDO_REJECTED)
    if has_order and status in _STATUS_TO_EVENT:
        return str(_STATUS_TO_EVENT[status])
    if "faltantes" in parsed_data or "missing_ingredients" in parsed_data:
        return str(EventName.STOCK_INSUFFICIENT)
    if "reservas" in parsed_data or "reservations" in parsed_data:
        return str(EventName.STOCK_RESERVED)
    return queue_name


_REDO_CONFIRMED_KEYS: tuple[str, ...] = (
    EventName.INVENTORY_REDO_CONFIRMED,
    "redo.confirmed",
    "redo_confirmed",
    "redo.confirmada",
)

_DINING_SESSION_KEYS: tuple[str, ...] = (
    EventName.DINING_SESSION_STARTED,
    "dining_session_started",
    "dining.session.started",
    "dining.session.iniciada",
)


class _NotifiedConsumerHandler(Protocol):
    async def __call__(
        self,
        message: Any,
        uow: UnitOfWork,
        notifier: OrderNotifier | None = None,
    ) -> bool: ...


_NOTIFIED_ROUTES: tuple[tuple[tuple[str, ...], type[BaseModel], _NotifiedConsumerHandler], ...] = (
    (
        (
            EventName.INVENTORY_REDO_REJECTED,
            "redo.rejected",
            "redo_rejected",
            "redo.rechazada",
        ),
        RedoRejectedMessage,
        handle_redo_rejected,
    ),
    (
        (
            EventName.STOCK_RESERVED,
            EventName.STOCK_RESERVED_EN,
            "stock_reserved",
            "stock.reservado",
            "reservation.confirmed",
            "reservation_confirmed",
            "reservation.confirmada",
        ),
        StockReservedMessage,
        handle_stock_reserved,
    ),
    (
        (
            EventName.STOCK_INSUFFICIENT,
            EventName.STOCK_INSUFFICIENT_EN,
            "stock_insufficient",
            "stock.insuficiente",
            "reservation.rejected",
            "reservation_rejected",
            "reservation.rechazada",
        ),
        StockInsufficientMessage,
        handle_stock_insufficient,
    ),
    (
        (
            EventName.INVENTORY_RESERVATION_EXPIRED,
            "reservation.expired",
            "reservation_expired",
            "reservation.expirada",
            "reserva.expirada",
            "stock.expirado",
        ),
        ReservationExpiredMessage,
        handle_reservation_expired,
    ),
    (
        (EventName.PAYMENT_COMPLETED, "payment_completed", "pago.completado"),
        PaymentCompletedMessage,
        handle_payment_completed,
    ),
)


async def _execute_typed_handler(
    target_event: str,
    parsed_data: dict[str, Any],
    uow: UnitOfWork,
    notifier: OrderNotifier | None,
    catalog_consumer: CatalogConsumer | None,
) -> bool:
    """Validate data against specific Pydantic model and execute domain handler."""
    target_lower = target_event.lower()
    if any(k in target_lower for k in _REDO_CONFIRMED_KEYS):
        return _acknowledge_redo_confirmed(target_event, parsed_data)

    for keys, model_cls, handler in _NOTIFIED_ROUTES:
        if any(k in target_lower for k in keys):
            return await handler(model_cls.model_validate(parsed_data), uow, notifier=notifier)

    if any(k in target_lower for k in _RESERVATION_ACK_KEYS):
        return _acknowledge_reservation_lifecycle(target_event, parsed_data)

    if any(k in target_lower for k in _DINING_SESSION_KEYS):
        msg_dining = DiningSessionCreatedMessage.model_validate(parsed_data)
        return await handle_dining_session_created(msg_dining, uow)

    catalog_events = tuple(
        event.value
        for event in EventName
        if event.value.startswith(("menu.", "inventory.recipe.", "inventory.source."))
    )
    if target_event in catalog_events:
        if catalog_consumer is None:
            raise RuntimeError("Catalog consumer is required for menu and catalog events")
        return await catalog_consumer.consume(target_event, parsed_data)

    raise ValueError(f"Unknown or unmapped event type: {target_event}")


class EventConsumerDispatcher:
    """Dispatches raw queue payloads to typed handlers with DLQ safety."""

    def __init__(
        self,
        session_factory: SessionFactory,
        dlq_router: DLQRouter | None = None,
        notifier: OrderNotifier | None = None,
        uow_factory: Callable[[SessionFactory], UnitOfWork] | None = None,
        catalog_consumer: CatalogConsumer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._dlq_router: DLQRouter = dlq_router or InMemoryDLQRouter()
        self._notifier = notifier
        self._uow_factory = uow_factory or UnitOfWork
        self._catalog_consumer = catalog_consumer

    @property
    def dlq_router(self) -> DLQRouter:
        """Return the active DLQ router instance."""
        return self._dlq_router

    async def dispatch_raw(
        self,
        raw_message: object,
        queue_name: str,
        event_type: str | None = None,
    ) -> bool:
        """Parse, validate, and execute idempotent handler with DLQ fallback."""
        parsed_data, parse_err = _parse_payload(raw_message)
        if parse_err is not None or parsed_data is None:
            await self._dlq_router.route_to_dlq(
                raw_message=raw_message,
                queue_name=queue_name,
                reason=parse_err or "Empty payload",
            )
            return False

        target_event = _resolve_target_event(parsed_data, queue_name, event_type)
        uow = self._uow_factory(self._session_factory)
        try:
            return await _execute_typed_handler(
                target_event=target_event,
                parsed_data=parsed_data,
                uow=uow,
                notifier=self._notifier,
                catalog_consumer=self._catalog_consumer,
            )
        except (ValidationError, ValueError, TypeError) as err:
            await self._dlq_router.route_to_dlq(
                raw_message=raw_message,
                queue_name=queue_name,
                reason=f"Message validation error: {err}",
            )
            return False
        except Exception as exc:
            logger.exception("Unexpected error processing message: %s", exc)
            await self._dlq_router.route_to_dlq(
                raw_message=raw_message,
                queue_name=queue_name,
                reason=f"Unexpected processing error: {exc}",
            )
            return False
