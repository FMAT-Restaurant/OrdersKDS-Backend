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
from dataclasses import dataclass
from typing import Any, Final, Protocol

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


MAX_IN_MEMORY_DEAD_LETTERS: Final[int] = 500


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """Outcome of dispatching one raw broker message."""

    applied: bool
    dead_lettered: bool = False
    reason: str | None = None


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
    """Bounded in-memory DLQ tracker for testing and standalone operations."""

    def __init__(self, *, max_entries: int = MAX_IN_MEMORY_DEAD_LETTERS) -> None:
        self._max_entries = max(1, max_entries)
        self.dead_letters: list[dict[str, object]] = []

    async def route_to_dlq(
        self,
        raw_message: object,
        queue_name: str,
        reason: str,
    ) -> None:
        """Record dead-letter payload with bounded retention to prevent OOM."""
        await asyncio.sleep(0)
        entry = {
            "queue_name": queue_name,
            "dlq_queue": f"{queue_name}.dlq",
            "reason": reason,
            "raw_message": raw_message,
        }
        if len(self.dead_letters) >= self._max_entries:
            self.dead_letters.pop(0)
        self.dead_letters.append(entry)
        logger.error(
            "Message sent to DLQ [%s]: %s (payload: %s)",
            entry["dlq_queue"],
            reason,
            raw_message,
        )


class BrokerNativeDLQRouter:
    """Stateless DLQ logger used when RabbitMQ dead-letters rejected messages natively."""

    async def route_to_dlq(
        self,
        raw_message: object,
        queue_name: str,
        reason: str,
    ) -> None:
        """Log structured poison-message rejection without retaining payloads in memory."""
        del raw_message
        await asyncio.sleep(0)
        logger.error(
            "Poison message rejected to broker DLQ [%s.dlq]: %s",
            queue_name,
            reason,
            extra={"queue_name": queue_name, "dlq_queue": f"{queue_name}.dlq"},
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


_REDO_STATUS_TO_EVENT: dict[str, EventName] = {
    "confirmed": EventName.INVENTORY_REDO_CONFIRMED,
    "rejected": EventName.INVENTORY_REDO_REJECTED,
}

_PAYLOAD_FIELD_TO_EVENT: tuple[tuple[str, EventName], ...] = (
    ("faltantes", EventName.STOCK_INSUFFICIENT),
    ("missing_ingredients", EventName.STOCK_INSUFFICIENT),
    ("reservas", EventName.STOCK_RESERVED),
    ("reservations", EventName.STOCK_RESERVED),
)

_CATALOG_EVENT_NAMES: frozenset[str] = frozenset(
    event.value
    for event in EventName
    if event.value.startswith(("menu.", "inventory.recipe.", "inventory.source."))
)


def _infer_event_from_order_status(parsed_data: dict[str, Any]) -> str | None:
    """Infer order or redo event name from order identifier and status fields."""
    if "orderId" not in parsed_data and "order_id" not in parsed_data:
        return None
    status = str(parsed_data.get("status", "")).lower()
    has_redo = "redoId" in parsed_data or "redo_id" in parsed_data
    if has_redo and status in _REDO_STATUS_TO_EVENT:
        return str(_REDO_STATUS_TO_EVENT[status])
    mapped = _STATUS_TO_EVENT.get(status)
    return str(mapped) if mapped is not None else None


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
    inferred = _infer_event_from_order_status(parsed_data)
    if inferred is not None:
        return inferred
    for field_name, mapped_event in _PAYLOAD_FIELD_TO_EVENT:
        if field_name in parsed_data:
            return str(mapped_event)
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

    if target_event in _CATALOG_EVENT_NAMES:
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

    async def dispatch_with_outcome(
        self,
        raw_message: object,
        queue_name: str,
        event_type: str | None = None,
    ) -> DispatchOutcome:
        """Parse, validate, and execute handler returning explicit DLQ status (SEC-12)."""
        parsed_data, parse_err = _parse_payload(raw_message)
        if parse_err is not None or parsed_data is None:
            reason = parse_err or "Empty payload"
            await self._dlq_router.route_to_dlq(
                raw_message=raw_message,
                queue_name=queue_name,
                reason=reason,
            )
            return DispatchOutcome(applied=False, dead_lettered=True, reason=reason)

        target_event = _resolve_target_event(parsed_data, queue_name, event_type)
        uow = self._uow_factory(self._session_factory)
        try:
            applied = await _execute_typed_handler(
                target_event=target_event,
                parsed_data=parsed_data,
                uow=uow,
                notifier=self._notifier,
                catalog_consumer=self._catalog_consumer,
            )
            return DispatchOutcome(applied=applied, dead_lettered=False)
        except (ValidationError, ValueError, TypeError) as err:
            reason = f"Message validation error: {err}"
            await self._dlq_router.route_to_dlq(
                raw_message=raw_message,
                queue_name=queue_name,
                reason=reason,
            )
            return DispatchOutcome(applied=False, dead_lettered=True, reason=reason)
        except Exception as exc:
            logger.exception("Unexpected error processing message: %s", exc)
            reason = f"Unexpected processing error: {exc}"
            await self._dlq_router.route_to_dlq(
                raw_message=raw_message,
                queue_name=queue_name,
                reason=reason,
            )
            return DispatchOutcome(applied=False, dead_lettered=True, reason=reason)

    async def dispatch_raw(
        self,
        raw_message: object,
        queue_name: str,
        event_type: str | None = None,
    ) -> bool:
        """Parse, validate, and execute idempotent handler with DLQ fallback."""
        outcome = await self.dispatch_with_outcome(
            raw_message=raw_message,
            queue_name=queue_name,
            event_type=event_type,
        )
        return outcome.applied
