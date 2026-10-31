"""Local catalog validation service for dish availability.

Satisfies: RF-17 (Guidelines §3.4, §5.5)
"""

from __future__ import annotations

import asyncio
import unicodedata
from collections.abc import Mapping
from typing import Any, Protocol, cast

from redis.asyncio import Redis

from app.cache.catalog import CatalogRepository
from app.core.config import get_redis_url
from app.domain.errors import DishUnavailableError


def normalize_station_name(name: str | None) -> str | None:
    """Normalize station name: strip, lowercase, and remove accents (NFD).

    Satisfies: RF-08, OP-09
    """
    if name is None:
        return None
    cleaned = name.strip().lower()
    normalized = unicodedata.normalize("NFD", cleaned)
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


_CATALOG_MOCK_REGISTRY: dict[str, dict[str, str | None]] = {}


def register_catalog_mock(
    item_id: str,
    station: str | None = None,
    preparation_time_text: str | None = None,
) -> None:
    """Register station and preparation time for a dish/item ID in the mock."""
    _CATALOG_MOCK_REGISTRY[item_id] = {
        "station": station,
        "preparation_time_text": preparation_time_text,
    }


def clear_catalog_mock() -> None:
    """Clear all entries in the catalog mock registry."""
    _CATALOG_MOCK_REGISTRY.clear()


def get_catalog_info_mock(item_id: str) -> dict[str, str | None]:
    """Temporary mock for catalog information until TASK-21a (Redis local view).

    Rule of TASK-21a: If a dish has no station, returns station: None.
    """
    if item_id in _CATALOG_MOCK_REGISTRY:
        return dict(_CATALOG_MOCK_REGISTRY[item_id])
    return {
        "station": None,
        "preparation_time_text": None,
    }


class CatalogValidatorProtocol(Protocol):
    """Protocol for checking and updating catalog dish availability."""

    async def validate_dish_available(self, dish_id: str) -> None:
        """Raise DishUnavailableError if dish_id is not available or inactive."""
        ...

    async def set_availability(self, dish_id: str, available: bool) -> None:
        """Explicitly set dish availability flag in the catalog view."""
        ...


class CatalogRepositoryValidator:
    """Adapt the Redis catalog projection to the order API validator contract."""

    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository

    async def validate_dish_available(self, dish_id: str) -> None:
        await self._repository.validate_dish_available(dish_id)

    async def set_availability(self, dish_id: str, available: bool) -> None:
        await self._repository.set_availability(dish_id, available)


class DefaultCatalogValidator:
    """In-memory and stub-friendly catalog validator.

    Allows registering unavailable dish IDs for negative testing (UT-BE-25),
    while defaulting all other items to available until TASK-21a (Redis local view).
    """

    def __init__(self, unavailable_dish_ids: set[str] | None = None) -> None:
        self.unavailable_dish_ids: set[str] = set(unavailable_dish_ids or ())

    def set_unavailable(self, dish_id: str) -> None:
        """Mark a dish as unavailable in the catalog view."""
        self.unavailable_dish_ids.add(dish_id)

    def set_available(self, dish_id: str) -> None:
        """Mark a dish as available in the catalog view."""
        self.unavailable_dish_ids.discard(dish_id)

    async def validate_dish_available(self, dish_id: str) -> None:
        """Verify dish availability, raising DishUnavailableError if inactive (RF-17)."""
        await asyncio.sleep(0)
        if dish_id in self.unavailable_dish_ids:
            raise DishUnavailableError(
                dish_id=dish_id,
                reason="Dish marked unavailable in catalog",
            )

    async def set_availability(self, dish_id: str, available: bool) -> None:
        await asyncio.sleep(0)
        if available:
            self.set_available(dish_id)
        else:
            self.set_unavailable(dish_id)


_default_catalog_validator = DefaultCatalogValidator()
_redis_validator: CatalogRepositoryValidator | None = None


class _RedisHashAdapter:
    """Adapt redis-py's broad command signatures to the catalog port."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hgetall(self, name: str) -> dict[object, object]:
        values = await self._redis.hgetall(name)
        return dict(values.items())

    async def hset(self, name: str, mapping: Mapping[str, str]) -> int:
        return await self._redis.hset(name, mapping=cast(Mapping[Any, Any], mapping))


def _get_redis_validator() -> CatalogRepositoryValidator:
    global _redis_validator
    if _redis_validator is None:
        _redis_validator = CatalogRepositoryValidator(
            CatalogRepository(
                _RedisHashAdapter(Redis.from_url(get_redis_url(), decode_responses=True))
            )
        )
    return _redis_validator


def get_catalog_validator() -> CatalogValidatorProtocol:
    """FastAPI dependency yielding the Redis-backed catalog validator."""
    return _get_redis_validator()
