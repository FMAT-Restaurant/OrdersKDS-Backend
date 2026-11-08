"""Bootstrap and periodically reconcile the local Redis catalog from Menu."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

import httpx

from app.cache.catalog import CatalogRepository
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.events import EventName

logger = logging.getLogger(__name__)

CATALOG_RECONCILIATION_ALERT_THRESHOLD = 0


class CatalogSnapshotError(ValueError):
    """Raised when Menu returns a snapshot that violates the catalog contract."""


def _extract_items(payload: object) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        raise CatalogSnapshotError("Menu catalog snapshot must be a JSON object")

    items = payload.get("items")
    if items is None:
        items = payload.get("data")
        if isinstance(items, Mapping):
            items = items.get("items")
    if not isinstance(items, list):
        raise CatalogSnapshotError("Menu catalog snapshot must contain an items list")
    if any(not isinstance(item, Mapping) for item in items):
        raise CatalogSnapshotError("Every Menu catalog item must be a JSON object")
    return items


class CatalogReconciliationWorker:
    """Project Menu snapshots through the existing catalog anti-corruption layer."""

    def __init__(
        self,
        repository: CatalogRepository,
        *,
        sync_url: str,
        interval_seconds: int | None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._consumer = CatalogConsumer(repository)
        self._sync_url = sync_url
        self._interval_seconds = interval_seconds
        self._http_client = http_client

    async def reconcile_once(self, client: httpx.AsyncClient) -> tuple[int, int]:
        """Fetch and project a complete snapshot; return (updated, discrepancies)."""
        response = await client.get(self._sync_url)
        response.raise_for_status()
        try:
            items = _extract_items(response.json())
        except json.JSONDecodeError as exc:
            raise CatalogSnapshotError("Menu returned invalid JSON") from exc

        updated_count = 0
        discrepancy_count = 0
        for item in items:
            projected = await self._consumer.consume(
                EventName.MENU_CATALOG_UPDATED,
                {"data": item},
            )
            if projected:
                updated_count += 1
            else:
                discrepancy_count += 1
                item_id = next(
                    (
                        item[key]
                        for key in ("offerId", "id", "ofertaId", "platilloId", "dishId")
                        if item.get(key) is not None
                    ),
                    None,
                )
                logger.info(
                    "Catalog snapshot item was not projected; cached data was preserved",
                    extra={"catalog_item_id": str(item_id) if item_id is not None else None},
                )

        if discrepancy_count > CATALOG_RECONCILIATION_ALERT_THRESHOLD:
            logger.warning(
                "Catalog reconciliation found %d discrepancy item(s), exceeding alert "
                "threshold %d; the local Redis view was preserved",
                discrepancy_count,
                CATALOG_RECONCILIATION_ALERT_THRESHOLD,
                extra={"catalog_discrepancy_count": discrepancy_count},
            )
        logger.info(
            "Catalog reconciliation completed: %d item(s) updated, %d discrepancy item(s)",
            updated_count,
            discrepancy_count,
        )
        return updated_count, discrepancy_count

    async def _reconcile_safely(self, client: httpx.AsyncClient) -> None:
        try:
            await self.reconcile_once(client)
        except (httpx.HTTPError, CatalogSnapshotError) as exc:
            logger.warning(
                "Menu catalog reconciliation failed; continuing with the local Redis snapshot: %s",
                exc,
                extra={"catalog_sync_url": self._sync_url},
            )

    async def _run(self, client: httpx.AsyncClient, stop_event: asyncio.Event | None) -> None:
        await self._reconcile_safely(client)
        while self._interval_seconds is not None and (
            stop_event is None or not stop_event.is_set()
        ):
            if stop_event is None:
                await asyncio.sleep(self._interval_seconds)
            else:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self._interval_seconds,
                    )
                if stop_event.is_set():
                    break
            await self._reconcile_safely(client)

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Bootstrap once, then reconcile until stopped when an interval is configured."""
        if self._http_client is not None:
            await self._run(self._http_client, stop_event)
            return
        async with httpx.AsyncClient(timeout=10.0) as client:
            await self._run(client, stop_event)
