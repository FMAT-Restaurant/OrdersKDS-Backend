"""Deterministic unit tests for INT-04 Menu v1 catalog sync, propagation, and adapters."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import uuid4

import httpx
import jwt
import pytest

from app.cache.catalog import CatalogRepository
from app.messaging.consumers.catalog import CatalogConsumer, infer_station_from_menu_item
from app.messaging.events import EventName
from app.workers.catalog_reconciliation import (
    CatalogReconciliationWorker,
    _build_reconciliation_headers,
    _derive_menu_api_root,
    _extract_sources_from_composition,
    _is_menu_offer_summaries_list,
    _with_query_params,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-02", "RF-08", "RF-17", "RF-40", "RF-41"),
]


class _MemRedisHash:
    def __init__(self) -> None:
        self.store: dict[str, dict[object, object]] = {}

    async def hgetall(self, name: str) -> Mapping[object, object]:
        return dict(self.store.get(name, {}))

    async def hset(self, name: str, mapping: Mapping[str, str]) -> int:
        self.store.setdefault(name, {}).update(mapping)
        return len(mapping)


def test_reconciliation_auth_headers_and_url_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _build_reconciliation_headers("raw-token-123") == {
        "Authorization": "Bearer raw-token-123"
    }
    assert _build_reconciliation_headers("Bearer explicit-456") == {
        "Authorization": "Bearer explicit-456"
    }

    monkeypatch.setenv("JWT_SECRET", "unit-test-secret-0123456789abcdef")
    auto_headers = _build_reconciliation_headers(None)
    assert auto_headers["Authorization"].startswith("Bearer ")
    decoded = jwt.decode(
        auto_headers["Authorization"].split(" ", 1)[1],
        "unit-test-secret-0123456789abcdef",
        algorithms=["HS256"],
    )
    assert "menu:catalog:read" in decoded["permissions"]
    assert "menu:offers:read" in decoded["permissions"]

    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert _build_reconciliation_headers(None) == {}

    assert (
        _with_query_params("http://menu.local/api/v1/menu/catalog", page=2, pageSize=50)
        == "http://menu.local/api/v1/menu/catalog?page=2&pageSize=50"
    )
    assert (
        _with_query_params("http://menu.local/api/v1/menu/catalog?q=taco", page=2)
        == "http://menu.local/api/v1/menu/catalog?q=taco&page=2"
    )
    assert (
        _derive_menu_api_root("http://menu.local:8080/api/v1/menu/catalog")
        == "http://menu.local:8080/api/v1/menu"
    )
    assert _derive_menu_api_root("http://menu.local/legacy/catalogo") is None


def test_extract_sources_from_composition_and_summary_detection() -> None:
    assert _extract_sources_from_composition("invalid") == ([], [])
    assert _extract_sources_from_composition({"slots": "invalid"}) == ([], [])

    composition = {
        "slots": [
            "not-a-dict",
            {"options": "not-a-list"},
            {
                "options": [
                    "not-a-dict",
                    {
                        "optionId": "opt-inactive",
                        "sourceType": "RECIPE",
                        "recipeId": "rec-inactive",
                        "status": "INACTIVE",
                    },
                    {
                        "optionId": "opt-rec",
                        "sourceType": "RECIPE",
                        "recipeId": "rec-1",
                        "status": "ACTIVE",
                        "isAvailable": True,
                    },
                    {
                        "optionId": "opt-inv",
                        "sourceType": "INVENTORY_ITEM",
                        "inventoryItemId": "inv-water-1",
                        "status": "ACTIVE",
                        "isAvailable": False,
                    },
                ]
            },
        ]
    }
    pairs, primary = _extract_sources_from_composition(composition)
    assert primary == ["rec-1", "opt-inv"]
    assert pairs == [
        ("rec-1", True),
        ("opt-rec", True),
        ("inv-water-1", False),
        ("opt-inv", False),
    ]

    assert (
        _is_menu_offer_summaries_list(
            "http://menu.local/api/v1/menu/catalog",
            [{"id": "1", "slotCount": 1}],
        )
        is False
    )
    assert (
        _is_menu_offer_summaries_list(
            "http://menu.local/api/v1/menu/offers",
            [{"id": "1", "slotCount": 1}],
        )
        is True
    )


@pytest.mark.asyncio
async def test_reconciler_paginated_catalog_entries_and_offer_enrichment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret-0123456789abcdef")
    redis_mem = _MemRedisHash()
    repo = CatalogRepository(redis_mem)
    cat_id = str(uuid4())
    offer_1 = str(uuid4())
    recipe_1 = str(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/menu/catalog/categories":
            return httpx.Response(200, json={"data": [{"id": cat_id, "name": "Bebidas"}]})
        if path == "/api/v1/menu/catalog":
            page = int(dict(request.url.params).get("page", "1"))
            if page == 1:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": "skip-no-offers"},
                            {
                                "id": "entry-1",
                                "categoryIds": [cat_id],
                                "isAvailable": True,
                                "offers": [
                                    "bad-offer-shape",
                                    {
                                        "id": offer_1,
                                        "presentationTag": "Vaso",
                                        "basePrice": 45.0,
                                        "isAvailable": True,
                                    },
                                ],
                            },
                        ],
                        "meta": {"page": 1, "pageSize": 1, "totalPages": 2},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "entry-2",
                            "isAvailable": True,
                            "offers": [
                                {
                                    "id": "offer-fallback",
                                    "presentationTag": "Sin detalle",
                                    "basePrice": 99.0,
                                    "isAvailable": True,
                                }
                            ],
                        }
                    ],
                    "meta": {"page": 2, "pageSize": 1, "totalPages": 2},
                },
            )
        if path == f"/api/v1/menu/offers/{offer_1}":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": offer_1,
                        "basePrice": 45.0,
                        "isAvailable": True,
                        "composition": {
                            "slots": [
                                {
                                    "name": "Bebida",
                                    "course": "bebida",
                                    "options": [
                                        {
                                            "optionId": "opt-1",
                                            "sourceType": "RECIPE",
                                            "recipeId": recipe_1,
                                            "status": "ACTIVE",
                                            "isAvailable": True,
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                },
            )
        return httpx.Response(500, json={"error": {"message": "fail"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        worker = CatalogReconciliationWorker(
            repo,
            sync_url="http://menu.local:8080/api/v1/menu/catalog",
            interval_seconds=None,
            http_client=client,
        )
        updated, discrepancies = await worker.reconcile_once(client)

    assert discrepancies == 0
    assert updated >= 3
    assert await repo.get_price(offer_1) == Decimal("45.0")
    assert (await repo.get_catalog_info(offer_1))["station"] == "barra"
    assert await repo.get_price("offer-fallback") == Decimal("99.0")
    assert (await repo.get_catalog_info("offer-fallback"))["station"] is None


@pytest.mark.asyncio
async def test_reconciler_offers_summary_endpoint_and_error_fallbacks() -> None:
    redis_mem = _MemRedisHash()
    repo = CatalogRepository(redis_mem)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/menu/offers":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"slotCount": 1},  # missing id -> skipped
                        {
                            "id": "offer-summary-1",
                            "entryId": "entry-1",
                            "entryName": "Parrilla",
                            "basePrice": 150.0,
                            "status": "ACTIVE",
                            "isAvailable": True,
                            "slotCount": 1,
                        },
                    ],
                    "meta": {"page": "invalid", "totalPages": "invalid"},
                },
            )
        if path == "/api/v1/menu/offers/offer-summary-1":
            return httpx.Response(200, text="not-json")
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        worker = CatalogReconciliationWorker(
            repo,
            sync_url="http://menu.local:8080/api/v1/menu/offers",
            interval_seconds=None,
            sync_token="dev-token",
            http_client=client,
        )
        updated, discrepancies = await worker.reconcile_once(client)
        assert await worker._fetch_category_names(client, None, {}) == {}
        assert await worker._fetch_offer_detail(client, None, "x", {}) is None
        assert (
            await worker._fetch_category_names(client, "http://menu.local:8080/api/v1/menu", {})
            == {}
        )

    assert (updated, discrepancies) == (1, 0)
    assert await repo.get_price("offer-summary-1") == Decimal("150.0")
    assert (await repo.get_catalog_info("offer-summary-1"))["station"] == "parrilla"


@pytest.mark.asyncio
async def test_catalog_repo_bidirectional_source_to_offer_propagation() -> None:
    repo = CatalogRepository(_MemRedisHash())
    await repo.upsert("offer-combo", price="200.00", available=True, source_ids="rec-a,rec-b")
    await repo.upsert("rec-a", available=True, linked_offers="offer-combo")
    await repo.upsert("rec-a", available=True, linked_offers="offer-combo,offer-extra")
    await repo.upsert("rec-b", available=True, linked_offers="offer-combo")

    # Deplete rec-a -> offer-combo becomes unavailable
    await repo.set_availability("rec-a", False)
    await repo.propagate_source_availability("rec-a", False)
    assert (await repo.get("offer-combo"))["available"] == "false"

    # Deplete rec-b, then restore rec-a -> offer-combo must stay false because rec-b is false
    await repo.set_availability("rec-b", False)
    await repo.set_availability("rec-a", True)
    await repo.propagate_source_availability("rec-a", True)
    assert (await repo.get("offer-combo"))["available"] == "false"

    # Restore rec-b -> now both sources are true -> offer-combo becomes true
    await repo.set_availability("rec-b", True)
    await repo.propagate_source_availability("rec-b", True)
    assert (await repo.get("offer-combo"))["available"] == "true"

    # Source without linked offers is a safe no-op
    await repo.propagate_source_availability("unlinked-src", False)


def test_infer_station_from_menu_item_and_consumer_aliases() -> None:
    assert infer_station_from_menu_item({"estacion": "Asador"}) == "Asador"
    assert (
        infer_station_from_menu_item({"composition": {"slots": ["skip", {"station": "Plancha"}]}})
        == "Plancha"
    )
    assert (
        infer_station_from_menu_item(
            {
                "categoryName": "Entradas y Ensaladas",
                "composition": {"slots": [{"name": "Entrada", "course": "entrada"}]},
            }
        )
        == "fria"
    )
    assert (
        infer_station_from_menu_item(
            {
                "composition": {
                    "slots": [{"name": "Cortes a la Parrilla", "course": "plato fuerte"}]
                }
            }
        )
        == "parrilla"
    )
    assert infer_station_from_menu_item({"categoryName": "Especial del Chef"}) is None


@pytest.mark.asyncio
async def test_catalog_consumer_source_lifecycle_and_menu_item_aliases() -> None:
    repo = CatalogRepository(_MemRedisHash())
    consumer = CatalogConsumer(repo)

    assert await consumer.consume(EventName.MENU_CATALOG_UPDATED, {"data": "invalid"}) is False
    assert (
        await consumer.consume(
            EventName.MENU_CATALOG_UPDATED,
            {
                "offerId": "offer-tag",
                "presentationTag": "Combo Familiar",
                "status": "ARCHIVED",
                "basePrice": "310.00",
            },
        )
        is True
    )
    stored_tag = await repo.get("offer-tag")
    assert stored_tag["category"] == "Combo Familiar"
    assert stored_tag["available"] == "false"

    # PlateDtoContract with items list does not expand ingredients as catalog entries
    assert (
        await consumer.consume(
            EventName.MENU_SOURCE_CREATED,
            {
                "sourceId": "rec-real-1",
                "name": "Arrachera",
                "items": [{"id": "inv-meat", "quantity": 1}],
            },
        )
        is True
    )
    assert await repo.get("inv-meat") == {}

    # MENU_SOURCE_UPDATED without available/updated_at preserves current availability
    assert (
        await consumer.consume(
            EventName.MENU_SOURCE_UPDATED,
            {"sourceId": "rec-real-1", "name": "Arrachera v2"},
        )
        is True
    )
    assert (await repo.get("rec-real-1"))["available"] == "true"

    # MENU_ITEM_DEPLETED -> false; MENU_ITEM_AVAILABLE -> true; MENU_SOURCE_DELETED -> false
    assert await consumer.consume(EventName.MENU_ITEM_DEPLETED, {"sourceId": "rec-real-1"}) is True
    assert (await repo.get("rec-real-1"))["available"] == "false"

    assert (
        await consumer.consume(EventName.MENU_ITEM_AVAILABLE, {"sourceId": "rec-real-1"}) is True
    )
    assert (await repo.get("rec-real-1"))["available"] == "true"

    assert (
        await consumer.consume(EventName.MENU_SOURCE_DELETED, {"sourceId": "rec-real-1"}) is True
    )
    assert (await repo.get("rec-real-1"))["available"] == "false"
