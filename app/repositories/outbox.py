"""Persistence operations for the transactional outbox."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Outbox
from app.messaging.events import OrderEvent


class OutboxRepository:
    """Stage and update outbox rows using the caller's transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: OrderEvent) -> Outbox:
        """Stage an event without committing the surrounding unit of work."""
        row = Outbox(
            event_id=event.event_id,
            event_type=str(event.event_name),
            payload=event.model_dump(mode="json", by_alias=True),
            correlation_id=event.correlation_id,
        )
        self._session.add(row)
        return row

    async def pending(self, limit: int = 100) -> list[Outbox]:
        """Return unpublished rows oldest first for the outbox worker."""
        result = await self._session.scalars(
            select(Outbox)
            .where(Outbox.published_at.is_(None))
            .order_by(Outbox.created_at, Outbox.id)
            .limit(limit)
        )
        return list(result)

    async def mark_published(self, event_id: UUID) -> None:
        """Mark a row only after the broker publisher confirms delivery."""
        await self._session.execute(
            update(Outbox)
            .where(Outbox.event_id == event_id, Outbox.published_at.is_(None))
            .values(published_at=datetime.now(UTC))
        )