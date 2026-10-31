"""Background worker that expires orders abandoned by the Inventory saga.

Every scan looks for orders that were *confirmed* (``confirmed_at`` is set) and
are still ``CREATED``. When ``now - confirmed_at >= SAGA_TIMEOUT_SECONDS`` the
order moves to ``EXPIRED`` and an ``ordenes.orden.expirada`` row is staged in
the transactional outbox, so the event is published exactly once regardless of
how many times the watcher runs (RNF-02).

Drafts (``CREATED`` without ``confirmed_at``) never expire: the waiter may take
minutes to assemble the order (RF-35, OP-01 / ADR-0001).

Satisfies: RF-35, RNF-01, RNF-02, OP-01, UT-BE-50, UT-BE-51
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import SagaTimeoutSettings, get_saga_timeout_settings
from app.db.models import Order
from app.domain.order_status import OrderStatus
from app.messaging.events import OrderExpiredEvent
from app.messaging.notifications import notify_order_changed
from app.messaging.validator import validate_event_payload
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.services.orders import _consolidate_order_items

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
Sleeper = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]

_BASE_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0
_DEFAULT_BATCH_SIZE = 100


def _utcnow() -> datetime:
    """Default clock returning the current timezone-aware UTC datetime."""
    return datetime.now(UTC)


class SagaTimeoutWatcher:
    """Expire confirmed orders that Inventory did not answer in time.

    Args:
        session_factory: Callable that creates a new ``AsyncSession``.
        settings: Optional pre-loaded settings. When omitted they are read from
            the environment, which fails fast if ``SAGA_TIMEOUT_SECONDS`` is
            missing (OP-01 / ADR-0001).
        batch_size: Maximum orders to expire per scan.
        sleeper: ``asyncio.sleep`` or a test double.
        clock: Injectable clock, defaults to ``datetime.now(UTC)``.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        settings: SagaTimeoutSettings | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        sleeper: Sleeper = asyncio.sleep,
        clock: Clock = _utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or get_saga_timeout_settings()
        self._batch_size = batch_size
        self._sleeper = sleeper
        self._clock = clock

    def _is_expired(self, order: Order, now: datetime) -> bool:
        """Return whether an order crossed the saga timeout at ``now``."""
        confirmed_at = order.confirmed_at
        if order.status != OrderStatus.CREATED or confirmed_at is None:
            return False
        return now - confirmed_at >= timedelta(seconds=self._settings.timeout_seconds)

    async def expire_stale_orders(self) -> int:
        """Expire every stale confirmed order in one transaction.

        The status change and its outbox row are committed together. Because
        the event identifier is derived from the order id, a second scan (or a
        concurrent watcher) cannot stage a duplicate event.

        Returns:
            Number of orders expired in this scan.
        """
        now = self._clock()
        threshold = now - timedelta(seconds=self._settings.timeout_seconds)

        async with self._session_factory() as session:
            candidates = await OrderRepository(session).list_awaiting_stock_confirmed_before(
                threshold,
                limit=self._batch_size,
            )
            stale_orders = [order for order in candidates if self._is_expired(order, now)]
            if not stale_orders:
                return 0

            outbox_repo = OutboxRepository(session)
            for order in stale_orders:
                confirmed_at = order.confirmed_at
                elapsed_seconds = (
                    0 if confirmed_at is None else int((now - confirmed_at).total_seconds())
                )
                order.mark_expired()
                event_items = _consolidate_order_items(order.items)
                event = OrderExpiredEvent.for_order(
                    order.public_id,
                    elapsed_seconds=elapsed_seconds,
                    items=event_items,
                    reason="timeout",
                )
                payload = event.model_dump(mode="json", by_alias=True)
                validate_event_payload(event.event_name, payload)
                outbox_repo.add(event)
                logger.info(
                    "Order %s expired after %d seconds in CREATED (confirmed_at=%s)",
                    order.public_id,
                    elapsed_seconds,
                    confirmed_at.isoformat() if confirmed_at else None,
                    extra={"order_id": str(order.public_id)},
                )

            await session.commit()

        for order in stale_orders:
            await notify_order_changed(order)

        return len(stale_orders)

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Scan periodically until ``stop_event`` is set or the task is cancelled.

        Args:
            stop_event: Optional event signalling a graceful shutdown.
        """
        consecutive_failures = 0
        while stop_event is None or not stop_event.is_set():
            try:
                expired = await self.expire_stale_orders()
                consecutive_failures = 0
                if expired:
                    logger.info("Saga watcher expired %d order(s) in this scan", expired)
                await self._sleeper(self._settings.watcher_interval_seconds)
            except asyncio.CancelledError:
                logger.info("Saga Timeout Watcher received cancellation, shutting down")
                raise
            except Exception as exc:
                consecutive_failures += 1
                backoff = min(
                    _MAX_BACKOFF_SECONDS,
                    _BASE_BACKOFF_SECONDS * (2 ** (consecutive_failures - 1)),
                )
                logger.warning(
                    "Error scanning stale orders: %s. Backing off for %.2f seconds (attempt %d)",
                    exc,
                    backoff,
                    consecutive_failures,
                )
                await self._sleeper(backoff)
