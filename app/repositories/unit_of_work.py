"""Transaction boundary shared by domain repositories and the Outbox."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.repositories.processed_events import ProcessedEventsRepository

SessionFactory = Callable[[], AsyncSession]


class UnitOfWork:
    """Own one database session and its commit/rollback boundary."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None
        self.outbox: OutboxRepository | None = None
        self.processed_events: ProcessedEventsRepository | None = None
        self.orders: OrderRepository | None = None

    async def __aenter__(self) -> Self:
        if self.session is None:
            self.session = self._session_factory()
        if self.outbox is None:
            self.outbox = OutboxRepository(self.session)
        if self.processed_events is None:
            self.processed_events = ProcessedEventsRepository(self.session)
        if self.orders is None:
            self.orders = OrderRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    async def commit(self) -> None:
        """Commit state changes and staged Outbox rows atomically."""
        if self.session is None:
            raise RuntimeError("UnitOfWork must be entered before commit")
        await self.session.commit()

    async def rollback(self) -> None:
        """Explicitly abandon the current transaction."""
        if self.session is None:
            raise RuntimeError("UnitOfWork must be entered before rollback")
        await self.session.rollback()
