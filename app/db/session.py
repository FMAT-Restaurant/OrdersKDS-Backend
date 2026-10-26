"""Database engine and request-scoped async session dependency.

Satisfies: RNF-01, RNF-09, RNF-10
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_database_url() -> str:
    """Retrieve DATABASE_URL from environment or default to local Postgres."""
    return os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/orders"
    )


def get_engine() -> AsyncEngine:
    """Get or initialize the shared async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_database_url(), echo=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or initialize the shared session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an application-lifespan session."""
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise RuntimeError("Database session factory is not configured.")
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
