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

_DEFAULT_DATABASE_URL = "postgresql+asyncpg://localhost:5432/orders"


def get_database_url() -> str:
    """Retrieve DATABASE_URL from environment or default to local Postgres."""
    url = os.getenv("DATABASE_URL")
    if url and url.strip():
        return url.strip()
    return _DEFAULT_DATABASE_URL


def _parse_positive_int_env(name: str) -> int | None:
    """Parse an optional strictly positive integer environment variable."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {name}: expected a positive integer, got {raw!r}") from exc
    if parsed <= 0:
        raise RuntimeError(f"Invalid {name}: expected a positive integer, got {parsed}")
    return parsed


def get_engine_kwargs() -> dict[str, int | bool]:
    """Return optional SQLAlchemy async engine pool settings from environment variables."""
    kwargs: dict[str, int | bool] = {}
    env_to_kwarg = (
        ("DB_POOL_SIZE", "pool_size"),
        ("DB_MAX_OVERFLOW", "max_overflow"),
        ("DB_POOL_TIMEOUT", "pool_timeout"),
    )
    for env_name, kwarg_name in env_to_kwarg:
        value = _parse_positive_int_env(env_name)
        if value is not None:
            kwargs[kwarg_name] = value

    pre_ping = os.getenv("DB_POOL_PRE_PING", "").strip().lower()
    if pre_ping in {"1", "true", "yes"}:
        kwargs["pool_pre_ping"] = True
    return kwargs


def get_engine() -> AsyncEngine:
    """Get or initialize the shared async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_database_url(), echo=False, **get_engine_kwargs())
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
