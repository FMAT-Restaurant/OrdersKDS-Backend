"""IT-13: project menu events and reject unavailable dishes."""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import Mock

import pytest

from app.cache.catalog import CatalogRepository
from app.domain.errors import DishUnavailableError
from app.messaging.consumers.base import EventConsumerDispatcher
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.events import EventName
from app.services.catalog import CatalogRepositoryValidator


class InMemoryRedisHash:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {}

    async def hgetall(self, name: str) -> Mapping[object, object]:
        return self.values.get(name, {})

    async def hset(self, name: str, mapping: Mapping[str, str]) -> int:
        self.values.setdefault(name, {}).update(mapping)
        return len(mapping)


@pytest.mark.asyncio
async def test_it13_menu_event_projects_to_catalog_and_blocks_unavailable_dish() -> None:
    redis = InMemoryRedisHash()
    repository = CatalogRepository(redis)
    consumer = CatalogConsumer(repository)
    dispatcher = EventConsumerDispatcher(
        session_factory=Mock(),
        uow_factory=lambda _factory: Mock(),
        catalog_consumer=consumer,
    )

    projected = await dispatcher.dispatch_raw(
        {
            "eventId": "catalog-event-1",
            "eventType": EventName.MENU_CATALOG_UPDATED,
            "data": {
                "platilloId": "dish-1",
                "precioActualizado": "12.50",
                "disponibilidad": True,
                "estacion": "  Plancha Á  ",
                "tiempoPreparacion": "10 minutos",
                "menuVersion": 4,
            },
        },
        queue_name="menu.catalog.updated",
    )

    assert projected is True
    assert await repository.get("dish-1") == {
        "price": "12.50",
        "available": "true",
        "station": "plancha a",
        "preparation_time_text": "10 minutos",
        "menu_version": "4",
    }

    unavailable = await dispatcher.dispatch_raw(
        {
            "eventId": "catalog-event-2",
            "eventType": EventName.MENU_DISH_AVAILABILITY_UPDATED,
            "data": {"dishId": "dish-1", "available": False, "menuVersion": 5},
        },
        queue_name="menu.dish.availability.updated",
    )

    assert unavailable is True
    validator = CatalogRepositoryValidator(repository)
    with pytest.raises(DishUnavailableError, match="dish-1"):
        await validator.validate_dish_available("dish-1")


@pytest.mark.asyncio
async def test_it13_missing_station_remains_absent_from_projection() -> None:
    redis = InMemoryRedisHash()
    repository = CatalogRepository(redis)

    assert await CatalogConsumer(repository).consume(
        EventName.MENU_CATALOG_UPDATED,
        {"platilloId": "dish-2", "precioActualizado": "5.00"},
    )

    assert await repository.get("dish-2") == {"price": "5.00"}
