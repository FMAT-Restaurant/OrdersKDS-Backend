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

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.consumers.dining_session import handle_dining_session_created
from app.messaging.consumers.payment_completed import handle_payment_completed
from app.messaging.consumers.stock_insufficient import handle_stock_insufficient
from app.messaging.consumers.stock_reserved import handle_stock_reserved
from app.messaging.events import (
    DiningSessionCreatedMessage,
    EventName,
    PaymentCompletedMessage,
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
    if "faltantes" in parsed_data or "missing_ingredients" in parsed_data:
        return str(EventName.STOCK_INSUFFICIENT)
    if "reservas" in parsed_data or "reservations" in parsed_data:
        return str(EventName.STOCK_RESERVED)
    return queue_name


async def _execute_typed_handler(
    target_event: str,
    parsed_data: dict[str, Any],
    uow: UnitOfWork,
    notifier: OrderNotifier | None,
    catalog_consumer: CatalogConsumer | None,
) -> bool:
    """Validate data against specific Pydantic model and execute domain handler."""
    target_lower = target_event.lower()
    stock_res_keys = (EventName.STOCK_RESERVED, "stock_reserved", "stock.reservado")
    if any(k in target_lower for k in stock_res_keys):
        msg_res = StockReservedMessage.model_validate(parsed_data)
        return await handle_stock_reserved(msg_res, uow, notifier=notifier)

    stock_ins_keys = (EventName.STOCK_INSUFFICIENT, "stock_insufficient", "stock.insuficiente")
    if any(k in target_lower for k in stock_ins_keys):
        msg_ins = StockInsufficientMessage.model_validate(parsed_data)
        return await handle_stock_insufficient(msg_ins, uow, notifier=notifier)

    payment_keys = (EventName.PAYMENT_COMPLETED, "payment_completed", "pago.completado")
    if any(k in target_lower for k in payment_keys):
        msg_pay = PaymentCompletedMessage.model_validate(parsed_data)
        return await handle_payment_completed(msg_pay, uow, notifier=notifier)

    dining_session_keys = (
        EventName.DINING_SESSION_STARTED,
        "dining_session_started",
        "dining.session.started",
        "dining.session.iniciada",
    )
    if any(k in target_lower for k in dining_session_keys):
        msg_dining_session = DiningSessionCreatedMessage.model_validate(parsed_data)
        return await handle_dining_session_created(msg_dining_session, uow)

    menu_events = tuple(event.value for event in EventName if event.value.startswith("menu."))
    if target_event in menu_events:
        if catalog_consumer is None:
            raise RuntimeError("Catalog consumer is required for menu events")
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
