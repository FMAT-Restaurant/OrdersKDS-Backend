"""Local catalog validation service for dish availability.

Satisfies: RF-17 (Guidelines §3.4, §5.5)
"""

from __future__ import annotations

import asyncio
import unicodedata
from typing import Protocol

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
    """Protocol for checking catalog dish availability."""

    async def validate_dish_available(self, dish_id: str) -> None:
        """Raise DishUnavailableError if dish_id is not available or inactive."""
        ...


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


_default_catalog_validator = DefaultCatalogValidator()


def get_catalog_validator() -> CatalogValidatorProtocol:
    """FastAPI dependency yielding the active catalog validator."""
    return _default_catalog_validator
