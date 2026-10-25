"""Persistence operations for consumer idempotency tracking.

Satisfies: RNF-02
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProcessedEvent


class ProcessedEventsRepository:
    """Stage and query processed event records within the caller's transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists(self, event_id: UUID | str, order_id: UUID | str) -> bool:
        """Check if an event emission was already applied to the specified order.

        Args:
            event_id: Unique event identifier.
            order_id: Public order identifier.

        Returns:
            True if (order_id, event_id) has already been processed, False otherwise.
        """
        event_uuid = event_id if isinstance(event_id, UUID) else UUID(event_id)
        order_uuid = order_id if isinstance(order_id, UUID) else UUID(order_id)

        result = await self._session.scalar(
            select(ProcessedEvent.id).where(
                ProcessedEvent.order_id == order_uuid,
                ProcessedEvent.event_id == event_uuid,
            )
        )
        return result is not None

    def add(
        self,
        event_id: UUID | str,
        order_id: UUID | str,
        event_type: str | None = None,
    ) -> ProcessedEvent:
        """Stage a processed event row without committing the surrounding unit of work.

        Args:
            event_id: Unique event identifier.
            order_id: Public order identifier.
            event_type: Optional routing key or event contract name.

        Returns:
            The created ProcessedEvent entity.
        """
        event_uuid = event_id if isinstance(event_id, UUID) else UUID(event_id)
        order_uuid = order_id if isinstance(order_id, UUID) else UUID(order_id)

        row = ProcessedEvent(
            order_id=order_uuid,
            event_id=event_uuid,
            event_type=event_type,
        )
        self._session.add(row)
        return row
