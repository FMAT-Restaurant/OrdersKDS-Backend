"""Transaction boundary shared by domain repositories and the Outbox."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.outbox import OutboxRepository

SessionFactory = Callable[[], AsyncSession]


class UnitOfWork:
    """Own one database session and its commit/rollback boundary."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None
        self.outbox: OutboxRepository | None = None

    async def __aenter__(self) -> Self:
        self.session = self._session_factory()
        self.outbox = OutboxRepository(self.session)
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