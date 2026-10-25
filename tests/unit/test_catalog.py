"""Tests for the local Redis catalog projection."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.cache.catalog import CatalogRepository, normalize_station
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.events import EventName


def test_station_is_normalized_and_empty_values_are_missing() -> None:
    assert normalize_station("  Estación Ártica  ") == "estacion artica"
    assert normalize_station("   ") is None


def test_station_is_truncated_with_warning() -> None:
    with patch("app.cache.catalog.logger.warning") as warning:
        assert normalize_station("a" * 51) == "a" * 50
    warning.assert_called_once()


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
async def test_price_event_and_updated_at_are_projected() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {}

    stored = await CatalogConsumer(CatalogRepository(redis)).consume(
        EventName.MENU_DISH_PRICE_UPDATED,
        {
            "data": {
                "dishId": "dish-price",
                "price": "8.75",
                "updatedAt": "2026-10-25T10:00:00Z",
            }
        },
    )

    assert stored is True
    assert redis.hset.await_args.kwargs["mapping"] == {
        "price": "8.75",
        "updated_at": "2026-10-25T10:00:00Z",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        (
            EventName.MENU_DISH_AVAILABILITY_UPDATED,
            {"dishId": "dish-available", "available": "false"},
            "false",
        ),
        (
            EventName.MENU_DISH_STATE_UPDATED,
            {"dishId": "dish-state", "state": "UNAVAILABLE"},
            "false",
        ),
        (
            EventName.MENU_DISH_STATE_UPDATED,
            {"dishId": "dish-state-available", "state": "AVAILABLE"},
            "true",
        ),
    ],
)
async def test_availability_events_are_projected(
    event_type: EventName,
    payload: dict[str, str],
    expected: str,
) -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {}

    assert await CatalogConsumer(CatalogRepository(redis)).consume(event_type, payload) is True
    assert redis.hset.await_args.kwargs["mapping"]["available"] == expected


@pytest.mark.asyncio
async def test_invalid_or_unknown_events_are_ignored() -> None:
    redis = AsyncMock()
    consumer = CatalogConsumer(CatalogRepository(redis))

    assert await consumer.consume("unknown.event", {"dishId": "dish"}) is False
    assert await consumer.consume(EventName.MENU_CATALOG_UPDATED, {"data": []}) is False
    assert await consumer.consume(EventName.MENU_CATALOG_UPDATED, {}) is False
    redis.hset.assert_not_awaited()


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
async def test_preparation_time_is_truncated_with_warning() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {}

    with patch("app.cache.catalog.logger.warning") as warning:
        stored = await CatalogRepository(redis).upsert(
            "dish-time", preparation_time_text="x" * 101
        )

    assert stored is True
    assert redis.hset.await_args.kwargs["mapping"]["preparation_time_text"] == "x" * 100
    warning.assert_called_once()


@pytest.mark.asyncio
async def test_older_menu_version_is_ignored() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {"menu_version": "5"}
    stored = await CatalogRepository(redis).upsert("dish-3", available=False, menu_version=4)
    assert stored is False
    redis.hset.assert_not_awaited()


@pytest.mark.asyncio
async def test_older_updated_at_is_ignored() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {"updated_at": "2026-10-25T12:00:00Z"}

    stored = await CatalogRepository(redis).upsert(
        "dish-old", available=False, updated_at="2026-10-25T11:00:00Z"
    )

    assert stored is False
    redis.hset.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_decodes_redis_bytes() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {b"station": b"plancha", "available": b"true"}

    result = await CatalogRepository(redis).get("dish-bytes")

    assert result == {"station": "plancha", "available": "true"}


@pytest.mark.asyncio
async def test_empty_update_is_not_written() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {}

    stored = await CatalogRepository(redis).upsert("dish-empty")

    assert stored is False
    redis.hset.assert_not_awaited()
