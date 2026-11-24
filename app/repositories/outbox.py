"""Persistence operations for the transactional outbox."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_correlation_id
from app.db.models import Outbox
from app.messaging.events import OrderEvent


class OutboxRepository:
    """Stage and update outbox rows using the caller's transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: OrderEvent) -> Outbox:
        """Stage an event without committing the surrounding unit of work."""
        resolved_correlation_id = event.correlation_id or get_correlation_id() or event.order_id
        payload = event.model_dump(mode="json", by_alias=True)
        payload["correlationId"] = str(resolved_correlation_id)
        row = Outbox(
            event_id=event.event_id,
            event_type=str(event.event_name),
            payload=payload,
            correlation_id=resolved_correlation_id,
        )
        self._session.add(row)
        return row

    async def exists(self, event_id: UUID) -> bool:
        """Return True if an outbox row with event_id has already been staged."""
        outbox_rows = getattr(self._session, "outbox_rows", None)
        if not isinstance(outbox_rows, list):
            outbox_rows = getattr(getattr(self._session, "db", None), "outbox", None)
        if isinstance(outbox_rows, list):
            return any(getattr(row, "event_id", None) == event_id for row in outbox_rows)
        row = await self._session.scalar(select(Outbox.id).where(Outbox.event_id == event_id))
        return row is not None

    async def pending(self, limit: int = 100) -> list[Outbox]:
        """Return unpublished rows oldest first for the outbox worker."""
        result = await self._session.scalars(
            select(Outbox)
            .where(Outbox.published_at.is_(None))
            .order_by(Outbox.created_at, Outbox.id)
            .limit(limit)
        )
        return list(result)

    async def mark_published(
        self,
        event_id: UUID,
        published_at: datetime | None = None,
    ) -> None:
        """Mark a row only after the broker publisher confirms delivery."""
        timestamp = published_at or datetime.now(UTC)
        await self._session.execute(
            update(Outbox)
            .where(Outbox.event_id == event_id, Outbox.published_at.is_(None))
            .values(published_at=timestamp)
        )
