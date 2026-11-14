"""Tests for the local Redis catalog projection."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.cache.catalog import CatalogRepository, normalize_station
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.events import EventName

EMPTY_CACHE: dict[str, str] = {}


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
    redis.hgetall.return_value = EMPTY_CACHE
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
async def test_catalog_event_projects_contract_items_batch_and_aliases() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = EMPTY_CACHE

    stored = await CatalogConsumer(CatalogRepository(redis)).consume(
        EventName.MENU_CATALOG_UPDATED,
        {
            "items": [
                {
                    "plateId": "plate-1",
                    "basePrice": 14.5,
                    "available": True,
                    "station": "  Grill Á  ",
                    "preparationTime": "15 min",
                },
                {
                    "itemId": "item-2",
                    "basePrice": "8.00",
                    "disponible": False,
                    "estacion": "Bar",
                    "tiempoPreparacion": "5 min",
                },
            ],
        },
    )

    assert stored is True
    assert redis.hset.await_count == 2
    first_mapping = redis.hset.await_args_list[0].kwargs["mapping"]
    second_mapping = redis.hset.await_args_list[1].kwargs["mapping"]
    assert first_mapping["price"] == "14.5"
    assert first_mapping["station"] == "grill a"
    assert second_mapping["price"] == "8.00"
    assert second_mapping["available"] == "false"


@pytest.mark.asyncio
async def test_price_event_and_updated_at_are_projected() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = EMPTY_CACHE

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
    redis.hgetall.return_value = EMPTY_CACHE

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
    redis.hgetall.return_value = EMPTY_CACHE
    await CatalogConsumer(CatalogRepository(redis)).consume(
        EventName.MENU_CATALOG_UPDATED,
        {"platilloId": "dish-2", "precioActualizado": "5.00"},
    )

    mapping = redis.hset.await_args.kwargs["mapping"]
    assert "station" not in mapping


@pytest.mark.asyncio
async def test_preparation_time_is_truncated_with_warning() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = EMPTY_CACHE

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
async def test_updated_at_comparison_handles_timezone_offsets() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {"updated_at": "2026-10-25T12:00:00+02:00"}

    stored = await CatalogRepository(redis).upsert(
        "dish-offset",
        station="Kitchen",
        updated_at="2026-10-25T09:30:00Z",
    )

    assert stored is False
    redis.hset.assert_not_awaited()


@pytest.mark.asyncio
async def test_unparseable_updated_at_does_not_overwrite_cached_item() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {"updated_at": "unknown-current-version"}

    stored = await CatalogRepository(redis).upsert(
        "dish-invalid-time",
        station="Kitchen",
        updated_at="2026-10-25T09:30:00Z",
    )

    assert stored is False
    redis.hset.assert_not_awaited()


@pytest.mark.asyncio
async def test_naive_timestamp_updated_at_parses_to_utc() -> None:
    redis = AsyncMock()
    # Cache has a timestamp without timezone suffix (naive ISO format)
    redis.hgetall.return_value = {"updated_at": "2026-10-25T12:00:00"}

    # Incoming timestamp is older
    stored = await CatalogRepository(redis).upsert(
        "dish-naive",
        station="Kitchen",
        updated_at="2026-10-25T11:00:00Z",
    )

    assert stored is False
    redis.hset.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_hash_adapter_methods() -> None:
    from app.cache.catalog import RedisHashAdapter

    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {"key": "val"}
    mock_redis.hset.return_value = 1

    adapter = RedisHashAdapter(mock_redis)

    res_get = await adapter.hgetall("cat:1")
    assert res_get == {"key": "val"}
    mock_redis.hgetall.assert_awaited_once_with("cat:1")

    res_set = await adapter.hset("cat:1", {"key": "val2"})
    assert res_set == 1
    mock_redis.hset.assert_awaited_once_with("cat:1", mapping={"key": "val2"})


@pytest.mark.asyncio
async def test_get_decodes_redis_bytes() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = {b"station": b"plancha", "available": b"true"}

    result = await CatalogRepository(redis).get("dish-bytes")

    assert result == {"station": "plancha", "available": "true"}


@pytest.mark.asyncio
async def test_empty_update_is_not_written() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = EMPTY_CACHE

    stored = await CatalogRepository(redis).upsert("dish-empty")

    assert stored is False
    redis.hset.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_consumer_projects_menu_offer_payload() -> None:
    redis = AsyncMock()
    redis.hgetall.return_value = EMPTY_CACHE
    consumer = CatalogConsumer(CatalogRepository(redis))

    menu_offer_payload = {
        "data": {
            "id": "b57f7e2a-02a4-4fb7-bb41-55f2d974b101",
            "entryId": "7d19a8ce-402c-4bab-b383-9969b69fe101",
            "presentationTag": "Individual",
            "basePrice": 180,
            "status": "ACTIVE",
            "image": {
                "id": "8c3f4365-1d1d-4a85-969a-c6c80757b201",
                "url": "https://cdn.example.com/menu/hamburguesa-individual.jpg",
                "thumbnailUrl": "https://cdn.example.com/menu/hamburguesa-individual-thumb.jpg",
            },
            "composition": {
                "slots": [
                    {
                        "slotId": "b37c1bd7-17b2-47a2-a3a4-802559445301",
                        "name": "Plato principal",
                        "quantity": 1,
                        "course": "plato fuerte",
                        "options": [
                            {
                                "optionId": "89cce4da-2f19-4d27-942d-b2ee82fb5401",
                                "sourceType": "RECIPE",
                                "recipeId": "c4813f65-00b4-4921-874e-03c555796401",
                                "displayName": "Hamburguesa Hawaiana",
                            }
                        ],
                    }
                ]
            },
        }
    }

    stored = await consumer.consume(EventName.MENU_OFFER_UPDATED, menu_offer_payload)
    assert stored is True
    redis.hset.assert_awaited_once_with(
        "catalog:platillo:b57f7e2a-02a4-4fb7-bb41-55f2d974b101",
        mapping={
            "price": "180",
            "available": "true",
            "category": "plato fuerte",
        },
    )
