"""Unit tests for Menu catalog bootstrap and reconciliation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import httpx
import pytest

from app.cache.catalog import CatalogRepository
from app.workers.catalog_reconciliation import CatalogReconciliationWorker


class InMemoryRedisHash:
    def __init__(self) -> None:
        self.values: dict[str, dict[object, object]] = {}

    async def hgetall(self, name: str) -> Mapping[object, object]:
        return self.values.get(name, {})

    async def hset(self, name: str, mapping: Mapping[str, str]) -> int:
        self.values.setdefault(name, {}).update(mapping)
        return len(mapping)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_snapshot_projects_offer_fields_through_catalog_consumer() -> None:
    redis = InMemoryRedisHash()
    repository = CatalogRepository(redis)
    handler = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "items": [
                    {
                        "offerId": "offer-1",
                        "status": "ACTIVE",
                        "basePrice": 15.5,
                        "estacion": "  Plancha Á  ",
                        "tiempoPreparacion": "10 minutos",
                        "updated_at": "2026-10-25T10:00:00Z",
                    }
                ]
            },
        )
    )

    async with httpx.AsyncClient(transport=handler) as client:
        worker = CatalogReconciliationWorker(
            repository,
            sync_url="http://menu.example/menu/catalogo/completo",
            interval_seconds=None,
            http_client=client,
        )
        updated, discrepancies = await worker.reconcile_once(client)

    assert (updated, discrepancies) == (1, 0)
    assert await repository.get("offer-1") == {
        "price": "15.5",
        "available": "true",
        "station": "plancha a",
        "preparation_time_text": "10 minutos",
        "updated_at": "2026-10-25T10:00:00Z",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_menu_entries_endpoint_data_envelope_is_supported() -> None:
    redis = InMemoryRedisHash()
    repository = CatalogRepository(redis)
    handler = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "offer-entries",
                        "status": "ACTIVE",
                        "basePrice": 25,
                        "estacion": "Bar",
                        "tiempoPreparacion": "5 min",
                    }
                ]
            },
        )
    )

    async with httpx.AsyncClient(transport=handler) as client:
        worker = CatalogReconciliationWorker(
            repository,
            sync_url="http://menu.example/api/v1/menu/entries",
            interval_seconds=None,
        )
        updated, discrepancies = await worker.reconcile_once(client)

    assert (updated, discrepancies) == (1, 0)
    assert (await repository.get("offer-entries"))["preparation_time_text"] == "5 min"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_snapshot_preserves_newer_station_and_preparation_time() -> None:
    redis = InMemoryRedisHash()
    repository = CatalogRepository(redis)
    await repository.upsert(
        "offer-1",
        station="Grill",
        preparation_time_text="20 minutes",
        updated_at="2026-10-25T12:00:00Z",
    )
    handler = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "offer-1",
                        "status": "ACTIVE",
                        "basePrice": 15.5,
                        "estacion": "Bar",
                        "tiempoPreparacion": "5 minutes",
                        "updated_at": "2026-10-25T11:00:00Z",
                    }
                ]
            },
        )
    )

    async with httpx.AsyncClient(transport=handler) as client:
        worker = CatalogReconciliationWorker(
            repository,
            sync_url="http://menu.example/menu/catalogo/completo",
            interval_seconds=None,
            http_client=client,
        )
        updated, discrepancies = await worker.reconcile_once(client)

    assert (updated, discrepancies) == (0, 1)
    cached = await repository.get("offer-1")
    assert cached["station"] == "grill"
    assert cached["preparation_time_text"] == "20 minutes"
    assert cached["updated_at"] == "2026-10-25T12:00:00Z"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_menu_failure_keeps_local_snapshot_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = InMemoryRedisHash()
    repository = CatalogRepository(redis)
    await repository.upsert("offer-1", station="Grill", preparation_time_text="20 minutes")
    handler = httpx.MockTransport(
        lambda _request: httpx.Response(503, json={"detail": "Menu unavailable"})
    )

    async with httpx.AsyncClient(transport=handler) as client:
        worker = CatalogReconciliationWorker(
            repository,
            sync_url="http://menu.example/menu/catalogo/completo",
            interval_seconds=None,
            http_client=client,
        )
        with caplog.at_level("WARNING"):
            await worker.run()

    assert await repository.get("offer-1") == {
        "station": "grill",
        "preparation_time_text": "20 minutes",
    }
    assert "continuing with the local Redis snapshot" in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_worker_bootstraps_then_reconciles_at_configured_interval() -> None:
    redis = InMemoryRedisHash()
    stop_event = asyncio.Event()
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            stop_event.set()
        return httpx.Response(200, json={"items": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        worker = CatalogReconciliationWorker(
            CatalogRepository(redis),
            sync_url="http://menu.example/menu/catalogo/completo",
            interval_seconds=1,
            http_client=client,
        )
        await worker.run(stop_event)

    assert call_count == 2
