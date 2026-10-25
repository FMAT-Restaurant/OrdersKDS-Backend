"""Tests for the local Redis catalog projection."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.cache.catalog import CatalogRepository, normalize_station
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.events import EventName


def test_station_is_normalized_and_empty_values_are_missing() -> None:
    assert normalize_station("  Estación Ártica  ") == "estacion artica"
    assert normalize_station("   ") is None


@pytest.mark.asyncio
async def test_catalog_event_maps_anticorruption_aliases() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {}
    consumer = CatalogConsumer(CatalogRepository(redis))

    stored = await consumer.consume(
        EventName.MENU_CATALOG_UPDATED,
        {
            "platilloId": "dish-1",
            "precioActualizado": Decimal("12.50"),
            "disponibilidad": True,
            "categoria": "Hamburguesas",
            "estacion": "  Plancha Á  ",
            "tiempoPreparacion": "10 minutos",
            "menuVersion": 4,
        },
    )

    assert stored is True
    mapping = redis.hset.await_args.kwargs["mapping"]
    assert mapping["price"] == "12.50"
    assert mapping["station"] == "plancha a"
    assert mapping["preparation_time_text"] == "10 minutos"
    assert mapping["menu_version"] == "4"


@pytest.mark.asyncio
async def test_missing_station_is_stored_as_null_text() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {}
    await CatalogConsumer(CatalogRepository(redis)).consume(
        EventName.MENU_CATALOG_UPDATED,
        {"platilloId": "dish-2", "precioActualizado": "5.00"},
    )

    mapping = redis.hset.await_args.kwargs["mapping"]
    assert "station" not in mapping


@pytest.mark.asyncio
async def test_older_menu_version_is_ignored() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {"menu_version": "5"}
    stored = await CatalogRepository(redis).upsert("dish-3", available=False, menu_version=4)
    assert stored is False
    redis.hset.assert_not_awaited()
