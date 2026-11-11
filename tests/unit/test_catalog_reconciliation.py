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


@pytest.mark.unit
def test_extract_items_validation_branches() -> None:
    from app.workers.catalog_reconciliation import CatalogSnapshotError, _extract_items

    with pytest.raises(CatalogSnapshotError, match="must be a JSON object"):
        _extract_items(["not", "a", "mapping"])

    with pytest.raises(CatalogSnapshotError, match="must contain an items list"):
        _extract_items({"items": "not a list"})

    with pytest.raises(
        CatalogSnapshotError, match="Every Menu catalog item must be a JSON object"
    ):
        _extract_items({"items": ["string_instead_of_dict"]})

    nested = _extract_items({"data": {"items": [{"id": "item-1"}]}})
    assert len(nested) == 1
    assert nested[0]["id"] == "item-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_once_invalid_json_raises_catalog_snapshot_error() -> None:
    from app.workers.catalog_reconciliation import CatalogSnapshotError

    redis = InMemoryRedisHash()
    repository = CatalogRepository(redis)
    handler = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            text="invalid json <not json>",
            headers={"content-type": "application/json"},
        )
    )

    async with httpx.AsyncClient(transport=handler) as client:
        worker = CatalogReconciliationWorker(
            repository,
            sync_url="http://menu.example/menu/catalogo/completo",
            interval_seconds=None,
        )
        with pytest.raises(CatalogSnapshotError, match="Menu returned invalid JSON"):
            await worker.reconcile_once(client)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_discrepancy_alert_logged_and_item_without_id_handled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = InMemoryRedisHash()
    repository = CatalogRepository(redis)
    # Item without any ID field will fail projection in CatalogConsumer
    handler = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"items": [{"name": "No ID Item", "status": "ACTIVE"}]},
        )
    )

    async with httpx.AsyncClient(transport=handler) as client:
        worker = CatalogReconciliationWorker(
            repository,
            sync_url="http://menu.example/menu/catalogo/completo",
            interval_seconds=None,
        )
        with caplog.at_level("WARNING"):
            updated, discrepancies = await worker.reconcile_once(client)

    assert updated == 0
    assert discrepancies == 1
    assert "exceeding alert threshold" in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_worker_run_creates_own_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = InMemoryRedisHash()
    repository = CatalogRepository(redis)
    handler = httpx.MockTransport(lambda _request: httpx.Response(200, json={"items": []}))

    from typing import Any

    original_client_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = handler
        original_client_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    worker = CatalogReconciliationWorker(
        repository,
        sync_url="http://menu.example/menu/catalogo/completo",
        interval_seconds=None,
    )
    # Should run with its own created client and terminate cleanly
    await worker.run()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_worker_run_with_none_stop_event_terminates_after_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = InMemoryRedisHash()
    repository = CatalogRepository(redis)
    handler = httpx.MockTransport(lambda _request: httpx.Response(200, json={"items": []}))

    call_count = 0

    async def mock_sleep(_duration: float) -> None:
        nonlocal call_count
        call_count += 1
        worker._interval_seconds = None  # Stop the loop

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    async with httpx.AsyncClient(transport=handler) as client:
        worker = CatalogReconciliationWorker(
            repository,
            sync_url="http://menu.example/menu/catalogo/completo",
            interval_seconds=1,
            http_client=client,
        )
        await worker.run(stop_event=None)

    assert call_count == 1
