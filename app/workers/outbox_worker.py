"""Background worker that publishes pending transactional outbox events.

Satisfies: RNF-01, RNF-06, RNF-12, UT-BE-48, UT-BE-49
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Outbox
from app.repositories.outbox import OutboxRepository

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
Sleeper = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    """Default clock returning current timezone-aware UTC datetime."""
    return datetime.now(UTC)


class EventPublisherProtocol(Protocol):
    """Protocol for event publishing implementations."""

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, object],
        event_id: UUID,
        correlation_id: UUID | None = None,
    ) -> None:
        """Publish an event to the message broker."""
        ...


class OutboxWorker:
    """Worker that continuously polls unpublished outbox rows and publishes them."""

    def __init__(
        self,
        session_factory: SessionFactory,
        publisher: EventPublisherProtocol,
        *,
        poll_interval_seconds: float = 1.0,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        sleeper: Sleeper = asyncio.sleep,
        clock: Clock = _utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._poll_interval = poll_interval_seconds
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._sleeper = sleeper
        self._clock = clock

    async def _publish_and_mark(self, session: AsyncSession, row: Outbox) -> None:
        """Publish a single outbox row and mark it published upon confirmation.

        Args:
            session: Active database session for updating published_at.
            row: The Outbox row to publish.
        """
        await self._publisher.publish(
            event_type=row.event_type,
            payload=row.payload,
            event_id=row.event_id,
            correlation_id=row.correlation_id,
        )

        published_at = self._clock()
        outbox_repo = OutboxRepository(session)
        await outbox_repo.mark_published(row.event_id, published_at=published_at)
        await session.commit()

        lag_seconds = (published_at - row.created_at).total_seconds()
        lag_ms = max(0, int(lag_seconds * 1000))
        order_id = row.payload.get("orderId") if isinstance(row.payload, dict) else None
        logger.info(
            "Published outbox event %s of type %s with lag %d ms (correlation_id=%s)",
            row.event_id,
            row.event_type,
            lag_ms,
            row.correlation_id,
            extra={
                "event_id": str(row.event_id),
                "event_type": row.event_type,
                "order_id": str(order_id) if order_id else None,
                "correlation_id": str(row.correlation_id) if row.correlation_id else None,
                "lag_ms": lag_ms,
            },
        )

    async def process_batch(self, limit: int = 100) -> int:
        """Fetch and publish one batch of pending outbox events.

        Args:
            limit: Maximum number of rows to process.

        Returns:
            Number of successfully published events.

        Raises:
            Exception: If any event fails to publish or confirm.
        """
        async with self._session_factory() as read_session:
            outbox_repo = OutboxRepository(read_session)
            pending_rows = await outbox_repo.pending(limit=limit)

        if not pending_rows:
            return 0

        published_count = 0
        for row in pending_rows:
            async with self._session_factory() as write_session:
                await self._publish_and_mark(write_session, row)
                published_count += 1

        return published_count

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Run the worker loop until cancelled or stop_event is signaled.

        Args:
            stop_event: Optional event signaling the worker to stop gracefully.
        """
        consecutive_failures = 0
        while stop_event is None or not stop_event.is_set():
            try:
                count = await self.process_batch()
                consecutive_failures = 0
                if count == 0:
                    await self._sleeper(self._poll_interval)
            except asyncio.CancelledError:
                logger.info("Outbox worker received cancellation, shutting down")
                raise
            except Exception as exc:
                consecutive_failures += 1
                backoff = min(
                    self._max_backoff,
                    self._base_backoff * (2 ** (consecutive_failures - 1)),
                )
                logger.warning(
                    "Error publishing outbox batch: %s. Backing off for %.2f seconds (attempt %d)",
                    exc,
                    backoff,
                    consecutive_failures,
                )
                await self._sleeper(backoff)
