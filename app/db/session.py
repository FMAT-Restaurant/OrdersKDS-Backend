"""Database engine and async session dependency for FastAPI.

Satisfies: RNF-01, RNF-09, RNF-10
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_database_url() -> str:
    """Retrieve DATABASE_URL from environment or fail fast."""
    url = os.getenv("DATABASE_URL")
    if not url or not url.strip():
        try:
            from dotenv import load_dotenv

            load_dotenv()
            url = os.getenv("DATABASE_URL")
        except ImportError:
            pass
    if not url or not url.strip():
        raise RuntimeError("DATABASE_URL environment variable is required")
    return url.strip()


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


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an active AsyncSession."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
