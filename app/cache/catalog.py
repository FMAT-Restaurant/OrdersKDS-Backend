"""Redis-backed local view of the menu catalog."""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Mapping
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.domain.errors import DishUnavailableError

logger = logging.getLogger(__name__)

MAX_STATION_LENGTH = 50
MAX_PREPARATION_TIME_LENGTH = 100


class RedisHash(Protocol):
    async def hgetall(self, name: str) -> Mapping[object, object]: ...

    async def hset(self, name: str, mapping: Mapping[str, str]) -> int: ...


def normalize_station(value: str | None) -> str | None:
    """Normalize station text for stable comparisons and cache lookups."""
    if value is None:
        return None
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFD", value.strip().lower())
        if unicodedata.category(character) != "Mn"
    )
    if len(normalized) > MAX_STATION_LENGTH:
        logger.warning(
            "Catalog field station exceeded %d characters; truncating value",
            MAX_STATION_LENGTH,
        )
    return normalized[:MAX_STATION_LENGTH] or None


def _bounded_text(value: str | None, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) > maximum:
        logger.warning(
            "Catalog field %s exceeded %d characters; truncating value",
            field,
            maximum,
        )
        text = text[:maximum]
    return text or None


def _decode(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _is_stale(
    current: Mapping[str, str],
    *,
    updated_at: str | None,
    menu_version: int | str | None,
) -> bool:
    if updated_at is not None and current.get("updated_at", "") > updated_at:
        return True
    return (
        menu_version is not None
        and current.get("menu_version") is not None
        and int(menu_version) <= int(current["menu_version"])
    )


class CatalogRepository:
    """Stores one normalized menu item per Redis hash."""

    def __init__(self, redis: RedisHash, *, key_prefix: str = "catalog:platillo") -> None:
        self._redis = redis
        self._key_prefix = key_prefix

    def key(self, dish_id: UUID | str) -> str:
        return f"{self._key_prefix}:{dish_id}"

    async def get(self, dish_id: UUID | str) -> dict[str, str]:
        values = await self._redis.hgetall(self.key(dish_id))
        return {_decode(key): _decode(value) for key, value in values.items()}

    async def validate_dish_available(self, dish_id: str) -> None:
        """Raise the domain error when the projected item is unavailable."""
        values = await self.get(dish_id)
        if values.get("available", "true").lower() != "true":
            raise DishUnavailableError(
                dish_id=dish_id,
                reason="Dish marked unavailable in catalog",
            )

    async def set_availability(self, dish_id: UUID | str, available: bool) -> None:
        """Explicitly override the availability flag in cache (e.g. kitchen rejection)."""
        key = self.key(dish_id)
        await self._redis.hset(key, mapping={"available": str(available).lower()})

    async def upsert(
        self,
        dish_id: UUID | str,
        *,
        price: Decimal | str | int | float | None = None,
        available: bool | None = None,
        category: str | None = None,
        station: str | None = None,
        preparation_time_text: str | None = None,
        updated_at: str | None = None,
        menu_version: int | str | None = None,
    ) -> bool:
        """Update an item unless the event is older than the cached version."""
        key = self.key(dish_id)
        current = await self.get(dish_id)
        if _is_stale(current, updated_at=updated_at, menu_version=menu_version):
            return False

        mapping: dict[str, str] = {}
        if price is not None:
            mapping["price"] = str(price)
        if available is not None:
            mapping["available"] = str(available).lower()
        if category is not None:
            mapping["category"] = category
        if station is not None:
            mapping["station"] = normalize_station(station) or ""
        if preparation_time_text is not None:
            mapping["preparation_time_text"] = (
                _bounded_text(
                    preparation_time_text,
                    field="preparation_time_text",
                    maximum=MAX_PREPARATION_TIME_LENGTH,
                )
                or ""
            )
        if updated_at is not None:
            mapping["updated_at"] = updated_at
        if menu_version is not None:
            mapping["menu_version"] = str(menu_version)
        if not mapping:
            return False
        await self._redis.hset(key, mapping=mapping)
        return True
