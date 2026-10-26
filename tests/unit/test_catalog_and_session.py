"""Unit tests for catalog validation service and database session dependency.

Satisfies: RF-17, RNF-01, RNF-09
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.db import session as db_session
from app.domain.errors import DishUnavailableError
from app.services.catalog import (
    DefaultCatalogValidator,
    get_catalog_validator,
)

pytestmark = [pytest.mark.unit, pytest.mark.requirement("RF-17", "RNF-01", "RNF-09")]


async def test_default_catalog_validator_lifecycle() -> None:
    validator = DefaultCatalogValidator()

    # Normal dish passes without error
    await validator.validate_dish_available("dish-123")

    # Marking dish unavailable raises DishUnavailableError
    validator.set_unavailable("dish-123")
    with pytest.raises(DishUnavailableError) as exc_info:
        await validator.validate_dish_available("dish-123")
    assert "dish-123" in str(exc_info.value)

    # Re-enabling dish restores availability
    validator.set_available("dish-123")
    await validator.validate_dish_available("dish-123")


def test_get_catalog_validator_returns_singleton() -> None:
    validator = get_catalog_validator()
    assert isinstance(validator, DefaultCatalogValidator)


def test_get_database_url_prefers_env_var() -> None:
    with patch.dict(
        os.environ, {"DATABASE_URL": "postgresql+asyncpg://user:pass@host:5432/testdb"}
    ):
        assert db_session.get_database_url() == "postgresql+asyncpg://user:pass@host:5432/testdb"


def test_get_engine_and_session_factory() -> None:
    with (
        patch("app.db.session.create_async_engine") as mock_create_engine,
        patch("app.db.session.async_sessionmaker") as mock_sessionmaker,
    ):
        mock_engine = AsyncMock(spec=AsyncEngine)
        mock_factory = AsyncMock(spec=async_sessionmaker)
        mock_create_engine.return_value = mock_engine
        mock_sessionmaker.return_value = mock_factory

        # Reset module globals for testing
        db_session._engine = None
        db_session._session_factory = None

        engine = db_session.get_engine()
        assert engine is mock_engine
        assert db_session.get_engine() is mock_engine

        factory = db_session.get_session_factory()
        assert factory is mock_factory
        assert db_session.get_session_factory() is mock_factory


async def test_get_session_yields_session_happy_path() -> None:
    mock_session = AsyncMock()
    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_session
    mock_context.__aexit__.return_value = False
    mock_factory = MagicMock(return_value=mock_context)

    with patch("app.db.session.get_session_factory", return_value=mock_factory):
        generator = db_session.get_session()
        session = await anext(generator)
        assert session is mock_session
        with pytest.raises(StopAsyncIteration):
            await anext(generator)


async def test_get_session_handles_exception_and_rolls_back() -> None:
    mock_session = AsyncMock()
    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_session
    mock_context.__aexit__.return_value = False
    mock_factory = MagicMock(return_value=mock_context)

    with patch("app.db.session.get_session_factory", return_value=mock_factory):
        generator = db_session.get_session()
        session = await anext(generator)
        assert session is mock_session
        simulated_error = RuntimeError("Simulated error in route")
        with pytest.raises(RuntimeError):
            await generator.athrow(simulated_error)
        mock_session.rollback.assert_awaited()
