"""Consumer that projects menu events into the local Redis catalog view."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.cache.catalog import CatalogRepository, normalize_station
from app.messaging.events import EventName

_STATION_KEYWORD_ALIASES: dict[str, str] = {
    "parrilla": "parrilla",
    "plancha": "parrilla",
    "asador": "parrilla",
    "grill": "parrilla",
    "barra": "barra",
    "bar": "barra",
    "bebida": "barra",
    "bebidas": "barra",
    "cocteleria": "barra",
    "fria": "fria",
    "estacion fria": "fria",
    "cocina fria": "fria",
    "entrada": "fria",
    "entradas": "fria",
    "ensalada": "fria",
    "ensaladas": "fria",
    "postre": "fria",
    "postres": "fria",
    "general": "general",
}


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
            "depleted",
            "agotado",
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
            if norm in {
                "UNAVAILABLE",
                "INACTIVE",
                "ARCHIVED",
                "NO_DISPONIBLE",
                "INACTIVO",
                "DISABLED",
                "AGOTADO",
            }:
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


def _collect_slot_station_hints(composition: object) -> tuple[str | None, list[str]]:
    """Return (explicit_slot_station, candidate_keywords) from an offer composition."""
    if not isinstance(composition, Mapping):
        return None, []
    slots = composition.get("slots")
    if not isinstance(slots, list):
        return None, []
    candidates: list[str] = []
    for slot in slots:
        if not isinstance(slot, Mapping):
            continue
        slot_station = _first(slot, "estacion", "station")
        if isinstance(slot_station, str) and slot_station.strip():
            return slot_station, []
        for key in ("course", "name"):
            val = slot.get(key)
            if isinstance(val, str) and val.strip():
                candidates.append(val)
    return None, candidates


def infer_station_from_menu_item(item: Mapping[str, Any]) -> str | None:
    """Resolve explicit station or infer a known KDS station from Menu slot/category metadata."""
    explicit = _first(item, "estacion", "station")
    if isinstance(explicit, str) and explicit.strip():
        return explicit

    slot_explicit, candidates = _collect_slot_station_hints(item.get("composition"))
    if slot_explicit is not None:
        return slot_explicit

    category_hint = _first(item, "categoryName", "categoriaNombre")
    if isinstance(category_hint, str) and category_hint.strip():
        candidates.insert(0, category_hint)

    for candidate in candidates:
        norm = normalize_station(candidate)
        if not norm:
            continue
        if norm in _STATION_KEYWORD_ALIASES:
            return _STATION_KEYWORD_ALIASES[norm]
        for token in norm.split():
            if token in _STATION_KEYWORD_ALIASES:
                return _STATION_KEYWORD_ALIASES[token]
    return None


_SOURCE_SINGLE_ITEM_EVENTS: frozenset[str] = frozenset(
    {
        EventName.MENU_SOURCE_CREATED,
        EventName.MENU_SOURCE_UPDATED,
        EventName.MENU_SOURCE_DELETED,
    }
)


def _catalog_item_payload(event_name: str, data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Extract catalog item entries, keeping ``PlateDtoContract`` ingredient lists intact."""
    if event_name in _SOURCE_SINGLE_ITEM_EVENTS or "sourceId" in data:
        return [data]
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
    for secondary_key in ("offerId", "ofertaId", "oferta", "plateId", "dishId"):
        secondary = item.get(secondary_key)
        if secondary is not None and str(secondary) not in ids:
            ids.append(str(secondary))
    return ids


_FULL_CATALOG_UPSERT_EVENTS: frozenset[str] = frozenset(
    {
        EventName.MENU_CATALOG_UPDATED,
        EventName.MENU_OFFER_CREATED,
        EventName.MENU_OFFER_UPDATED,
        EventName.MENU_OFFER_CREATED_EN,
        EventName.MENU_OFFER_UPDATED_EN,
    }
)

_FORCE_UNAVAILABLE_EVENTS: frozenset[str] = frozenset(
    {
        EventName.MENU_SOURCE_DELETED,
        EventName.MENU_ITEM_DEPLETED,
        EventName.MENU_ITEM_UNAVAILABLE_EN,
        EventName.MENU_OFFER_DELETED,
        EventName.MENU_OFFER_DELETED_EN,
        EventName.INVENTORY_RECIPE_NOT_AVAILABLE,
        EventName.INVENTORY_RECIPE_INCOMPLETE,
    }
)

_FORCE_AVAILABLE_EVENTS: frozenset[str] = frozenset(
    {
        EventName.MENU_ITEM_AVAILABLE,
        EventName.MENU_ITEM_AVAILABLE_EN,
    }
)

_EXPLICIT_AVAILABILITY_EVENTS: frozenset[str] = frozenset(
    {
        EventName.MENU_DISH_AVAILABILITY_UPDATED,
        EventName.MENU_DISH_STATE_UPDATED,
        EventName.INVENTORY_RECIPE_AVAILABILITY_CHANGED,
        EventName.INVENTORY_SOURCE_AVAILABILITY_UPDATED,
    }
)


class CatalogConsumer:
    """Translate menu events while keeping the anti-corruption boundary explicit."""

    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository

    async def _upsert_and_propagate_availability(
        self,
        dish_id: str,
        available: bool | None,
        common: dict[str, Any],
    ) -> bool:
        stored = await self._repository.upsert(dish_id, available=available, **common)
        if stored and available is not None:
            await self._repository.propagate_source_availability(dish_id, available)
        return stored

    async def _project_full_catalog_item(
        self,
        dish_id: str,
        item: Mapping[str, Any],
        common: dict[str, Any],
    ) -> bool:
        source_ids = item.get("_source_ids")
        linked_offers = item.get("_linked_offers")
        return await self._repository.upsert(
            dish_id,
            price=_first(item, "precio_actualizado", "precioActualizado", "basePrice", "price"),
            available=_extract_available(item),
            category=_extract_category(item),
            station=infer_station_from_menu_item(item),
            preparation_time_text=_first(
                item,
                "tiempoPreparacion",
                "preparation_time_text",
                "preparationTime",
            ),
            source_ids=str(source_ids) if source_ids else None,
            linked_offers=str(linked_offers) if linked_offers else None,
            **common,
        )

    async def _project_source_updated(
        self,
        dish_id: str,
        item: Mapping[str, Any],
        common: dict[str, Any],
    ) -> bool:
        available = _extract_available(item)
        if available is None and common.get("updated_at") is None:
            current = await self._repository.get(dish_id)
            preserved = current.get("available", "true").lower() == "true"
            return await self._repository.upsert(dish_id, available=preserved, **common)
        return await self._repository.upsert(dish_id, available=available, **common)

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
        if event_name in _FULL_CATALOG_UPSERT_EVENTS:
            return await self._project_full_catalog_item(dish_id, item, common)
        if event_name == EventName.MENU_SOURCE_CREATED or event_name in _FORCE_AVAILABLE_EVENTS:
            extracted = _extract_available(item)
            return await self._upsert_and_propagate_availability(
                dish_id,
                True if extracted is None else extracted,
                common,
            )
        if event_name == EventName.MENU_SOURCE_UPDATED:
            return await self._project_source_updated(dish_id, item, common)
        if event_name in _FORCE_UNAVAILABLE_EVENTS:
            return await self._upsert_and_propagate_availability(dish_id, False, common)
        if event_name in _EXPLICIT_AVAILABILITY_EVENTS:
            return await self._upsert_and_propagate_availability(
                dish_id,
                _extract_available(item),
                common,
            )
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
            "updated_at": _first(item, "updated_at", "updatedAt", "occurredOn")
            or _first(data, "updated_at", "updatedAt", "occurredOn"),
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

        items = _catalog_item_payload(event_name, data)
        stored = False
        for item in items:
            projected = await self._project_item(event_name, item, data)
            stored = projected or stored
        return stored
