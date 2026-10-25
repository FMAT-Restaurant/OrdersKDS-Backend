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
        if value.strip().lower() in {"true", "1", "yes", "available", "disponible"}:
            return True
        if value.strip().lower() in {"false", "0", "no", "unavailable", "no_disponible"}:
            return False
    return bool(value)


class CatalogConsumer:
    """Translate menu events while keeping the anti-corruption boundary explicit."""

    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository

    async def consume(self, event_type: str | EventName, payload: Mapping[str, Any]) -> bool:
        event_name = str(event_type)
        data = payload.get("data", payload)
        if not isinstance(data, Mapping):
            return False
        dish_id = _first(data, "platilloId", "dishId", "id")
        if dish_id is None:
            return False

        common = {
            "updated_at": _first(data, "updated_at", "updatedAt"),
            "menu_version": _first(data, "menuVersion", "menu_version"),
        }
        if event_name == EventName.MENU_DISH_PRICE_UPDATED:
            return await self._repository.upsert(
                dish_id,
                price=_first(data, "precio_actualizado", "precioActualizado", "price"),
                **common,
            )
        if event_name == EventName.MENU_CATALOG_UPDATED:
            return await self._repository.upsert(
                dish_id,
                price=_first(data, "precio_actualizado", "precioActualizado", "price"),
                available=_as_bool(_first(data, "disponibilidad", "available")),
                category=_first(data, "categoria", "category"),
                station=_first(data, "estacion", "station"),
                preparation_time_text=_first(
                    data, "tiempoPreparacion", "preparation_time_text", "preparationTime"
                ),
                **common,
            )
        if event_name in (
            EventName.MENU_DISH_AVAILABILITY_UPDATED,
            EventName.MENU_DISH_STATE_UPDATED,
        ):
            available = _first(data, "disponibilidad", "available", "disponible")
            if available is None:
                available = _first(data, "estado", "state") not in ("UNAVAILABLE", "unavailable")
            return await self._repository.upsert(dish_id, available=_as_bool(available), **common)
        return False
