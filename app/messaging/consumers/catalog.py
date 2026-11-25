"""Consumer that projects menu events into the local Redis catalog view."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.cache.catalog import CatalogRepository
from app.messaging.events import EventName


def _first(payload: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return None


def _as_bool(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "available", "disponible", "active", "activo"}:
            return True
        if normalized in {
            "false",
            "0",
            "no",
            "unavailable",
            "no_disponible",
            "inactive",
            "inactivo",
        }:
            return False
    return bool(value)


def _extract_available(item: Mapping[str, Any]) -> bool | None:
    explicit = _first(item, "disponibilidad", "available", "isAvailable", "disponible")
    if explicit is not None:
        return _as_bool(explicit)
    state_val = _first(item, "status", "estado", "state")
    if state_val is not None:
        if isinstance(state_val, str):
            norm = state_val.strip().upper()
            if norm in {"UNAVAILABLE", "INACTIVE", "NO_DISPONIBLE", "INACTIVO", "DISABLED"}:
                return False
            if norm in {"ACTIVE", "AVAILABLE", "ACTIVO", "DISPONIBLE", "ENABLED"}:
                return True
        return _as_bool(state_val)
    return None


def _extract_category(item: Mapping[str, Any]) -> str | None:
    direct = _first(item, "categoria", "category")
    if isinstance(direct, str) and direct.strip():
        return direct
    composition = item.get("composition")
    if isinstance(composition, Mapping):
        slots = composition.get("slots")
        if isinstance(slots, list) and slots and isinstance(slots[0], Mapping):
            course = slots[0].get("course")
            if isinstance(course, str) and course.strip():
                return course
    tag = item.get("presentationTag")
    if isinstance(tag, str) and tag.strip():
        return tag
    return None


def _catalog_item_payload(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = data.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, Mapping)]
    return [data]


def _resolve_projection_ids(item: Mapping[str, Any]) -> list[str]:
    """Collect distinct source/recipe and commercial offer IDs from a Menu item payload."""
    primary = _first(
        item,
        "sourceId",
        "oferta",
        "ofertaId",
        "offerId",
        "platilloId",
        "dishId",
        "plateId",
        "itemId",
        "id",
    )
    if primary is None:
        return []
    ids = [str(primary)]
    secondary = _first(item, "offerId", "ofertaId", "oferta")
    if secondary is not None and str(secondary) not in ids:
        ids.append(str(secondary))
    return ids


class CatalogConsumer:
    """Translate menu events while keeping the anti-corruption boundary explicit."""

    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository

    async def _project_single_id(
        self,
        dish_id: str,
        event_name: str,
        item: Mapping[str, Any],
        common: dict[str, Any],
    ) -> bool:
        if event_name == EventName.MENU_DISH_PRICE_UPDATED:
            return await self._repository.upsert(
                dish_id,
                price=_first(
                    item, "precio_actualizado", "precioActualizado", "basePrice", "price"
                ),
                **common,
            )
        if event_name in (
            EventName.MENU_CATALOG_UPDATED,
            EventName.MENU_OFFER_CREATED,
            EventName.MENU_OFFER_UPDATED,
            EventName.MENU_OFFER_CREATED_EN,
            EventName.MENU_OFFER_UPDATED_EN,
        ):
            return await self._repository.upsert(
                dish_id,
                price=_first(
                    item, "precio_actualizado", "precioActualizado", "basePrice", "price"
                ),
                available=_extract_available(item),
                category=_extract_category(item),
                station=_first(item, "estacion", "station"),
                preparation_time_text=_first(
                    item,
                    "tiempoPreparacion",
                    "preparation_time_text",
                    "preparationTime",
                ),
                **common,
            )
        if event_name == EventName.MENU_SOURCE_CREATED:
            available = _extract_available(item)
            return await self._repository.upsert(
                dish_id,
                available=True if available is None else available,
                **common,
            )
        if event_name == EventName.MENU_SOURCE_UPDATED:
            available = _extract_available(item)
            return await self._repository.upsert(
                dish_id,
                available=available,
                **common,
            )
        if event_name in (
            EventName.MENU_SOURCE_DELETED,
            EventName.INVENTORY_RECIPE_NOT_AVAILABLE,
            EventName.INVENTORY_RECIPE_INCOMPLETE,
        ):
            return await self._repository.upsert(dish_id, available=False, **common)
        if event_name in (
            EventName.MENU_DISH_AVAILABILITY_UPDATED,
            EventName.MENU_DISH_STATE_UPDATED,
            EventName.INVENTORY_RECIPE_AVAILABILITY_CHANGED,
            EventName.INVENTORY_SOURCE_AVAILABILITY_UPDATED,
        ):
            available = _extract_available(item)
            return await self._repository.upsert(dish_id, available=available, **common)
        return False

    async def _project_item(
        self,
        event_name: str,
        item: Mapping[str, Any],
        data: Mapping[str, Any],
    ) -> bool:
        target_ids = _resolve_projection_ids(item)
        if not target_ids:
            return False

        common = {
            "updated_at": _first(item, "updated_at", "updatedAt")
            or _first(data, "updated_at", "updatedAt"),
            "menu_version": _first(item, "menuVersion", "menu_version")
            or _first(data, "menuVersion", "menu_version"),
        }
        stored = False
        for dish_id in target_ids:
            projected = await self._project_single_id(dish_id, event_name, item, common)
            stored = projected or stored
        return stored

    async def consume(self, event_type: str | EventName, payload: Mapping[str, Any]) -> bool:
        event_name = str(event_type)
        data = payload.get("data", payload)
        if not isinstance(data, Mapping):
            return False
        stored = False
        for item in _catalog_item_payload(data):
            projected = await self._project_item(event_name, item, data)
            stored = projected or stored
        return stored
