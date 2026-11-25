"""Bootstrap and periodically reconcile the local Redis catalog from Menu."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
import jwt

from app.cache.catalog import CatalogRepository
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.events import EventName

logger = logging.getLogger(__name__)

CATALOG_RECONCILIATION_ALERT_THRESHOLD = 0
_MENU_RECONCILIATION_PERMISSIONS: tuple[str, ...] = (
    "menu:catalog:read",
    "menu:offers:read",
    "menu:categories:read",
)


class CatalogSnapshotError(ValueError):
    """Raised when Menu returns a snapshot that violates the catalog contract."""


def _build_reconciliation_headers(sync_token: str | None) -> dict[str, str]:
    """Build Authorization header for Menu-Backend when token or JWT_SECRET is configured."""
    if sync_token and sync_token.strip():
        raw = sync_token.strip()
        bearer = raw if raw.lower().startswith("bearer ") else f"Bearer {raw}"
        return {"Authorization": bearer}

    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    if not jwt_secret:
        return {}

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "orders-catalog-reconciler",
            "user_id": "orders-catalog-reconciler",
            "role": "service",
            "roles": ["service"],
            "permissions": list(_MENU_RECONCILIATION_PERMISSIONS),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _with_query_params(url: str, **params: int | str) -> str:
    parsed = urlsplit(url)
    existing = parsed.query
    extra = urlencode(params)
    merged = f"{existing}&{extra}" if existing else extra
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, merged, parsed.fragment))


def _derive_menu_api_root(sync_url: str) -> str | None:
    """Return ``scheme://netloc/api/v1/menu`` when ``sync_url`` targets Menu v1 API."""
    parsed = urlsplit(sync_url)
    marker = "/api/v1/menu"
    idx = parsed.path.find(marker)
    if idx == -1:
        return None
    root_path = parsed.path[: idx + len(marker)]
    return urlunsplit((parsed.scheme, parsed.netloc, root_path, "", ""))


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


def _is_menu_catalog_entries_list(items: list[Mapping[str, Any]]) -> bool:
    """Detect Menu-Backend ``GET /api/v1/menu/catalog`` entries carrying nested ``offers``."""
    return bool(items) and any(isinstance(entry.get("offers"), list) for entry in items)


def _is_menu_offer_summaries_list(sync_url: str, items: list[Mapping[str, Any]]) -> bool:
    """Detect Menu-Backend ``GET /api/v1/menu/offers`` summary rows."""
    path = urlsplit(sync_url).path.rstrip("/")
    if not path.endswith("/menu/offers"):
        return False
    return bool(items) and any("slotCount" in row or "entryId" in row for row in items)


def _iter_active_composition_options(composition: object) -> Iterator[Mapping[str, Any]]:
    """Yield active option mappings across all slots of an offer composition."""
    if not isinstance(composition, Mapping):
        return
    slots = composition.get("slots")
    if not isinstance(slots, list):
        return
    for slot in slots:
        if not isinstance(slot, Mapping):
            continue
        options = slot.get("options")
        if not isinstance(options, list):
            continue
        for option in options:
            if (
                isinstance(option, Mapping)
                and str(option.get("status", "ACTIVE")).upper() == "ACTIVE"
            ):
                yield option


def _extract_primary_source(option: Mapping[str, Any]) -> str | None:
    """Return the primary physical source identifier for a composition option."""
    src_type = str(option.get("sourceType", "")).upper()
    primary = option.get("recipeId") if src_type == "RECIPE" else option.get("optionId")
    return str(primary) if primary is not None else None


def _collect_option_sources(
    option: Mapping[str, Any],
    seen: set[str],
) -> list[tuple[str, bool | None]]:
    """Collect unique (source_id, is_available) pairs referenced by a single option."""
    raw_opt_avail = option.get("isAvailable")
    opt_avail = raw_opt_avail if isinstance(raw_opt_avail, bool) else None
    pairs: list[tuple[str, bool | None]] = []
    # Project recipeId, inventoryItemId, and optionId (Menu's sourceId for INVENTORY_ITEM)
    for key in ("recipeId", "inventoryItemId", "optionId"):
        candidate = option.get(key)
        if candidate is None:
            continue
        sid = str(candidate).strip()
        if sid and sid not in seen:
            seen.add(sid)
            pairs.append((sid, opt_avail))
    return pairs


def _extract_sources_from_composition(
    composition: object,
) -> tuple[list[tuple[str, bool | None]], list[str]]:
    """Return ``(all_source_pairs, primary_source_ids)`` from an offer composition."""
    discovered: list[tuple[str, bool | None]] = []
    primary_sources: list[str] = []
    seen: set[str] = set()
    for option in _iter_active_composition_options(composition):
        primary = _extract_primary_source(option)
        if primary is not None and primary not in primary_sources:
            primary_sources.append(primary)
        discovered.extend(_collect_option_sources(option, seen))
    return discovered, primary_sources


def _build_projection_dict(
    base_fields: dict[str, Any],
    category_name: str | None,
) -> dict[str, Any]:
    if category_name:
        base_fields["category"] = category_name
        base_fields["categoryName"] = category_name
    return base_fields


def _expand_offer_projection_items(
    offer: Mapping[str, Any],
    *,
    offer_available: bool,
    category_name: str | None = None,
) -> list[dict[str, Any]]:
    """Build projection payloads for a commercial offer and all its physical source IDs."""
    offer_id = offer.get("id") or offer.get("offerId")
    if offer_id is None:
        return []
    offer_id_str = str(offer_id)
    composition = offer.get("composition")
    sources, primary_sources = _extract_sources_from_composition(composition)

    offer_payload = _build_projection_dict(
        {
            "offerId": offer_id_str,
            "basePrice": offer.get("basePrice"),
            "isAvailable": offer_available,
            "presentationTag": offer.get("presentationTag"),
            "composition": composition,
        },
        category_name,
    )
    if primary_sources:
        offer_payload["_source_ids"] = ",".join(primary_sources)

    expanded: list[dict[str, Any]] = [offer_payload]
    for source_id, opt_avail in sources:
        if source_id == offer_id_str:
            continue
        expanded.append(
            _build_projection_dict(
                {
                    "sourceId": source_id,
                    "basePrice": offer.get("basePrice"),
                    "isAvailable": offer_available if opt_avail is None else opt_avail,
                    "presentationTag": offer.get("presentationTag"),
                    "composition": composition,
                    "_linked_offers": offer_id_str,
                },
                category_name,
            )
        )
    return expanded


class CatalogReconciliationWorker:
    """Project Menu snapshots through the existing catalog anti-corruption layer."""

    def __init__(
        self,
        repository: CatalogRepository,
        *,
        sync_url: str,
        interval_seconds: int | None,
        sync_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._consumer = CatalogConsumer(repository)
        self._sync_url = sync_url
        self._interval_seconds = interval_seconds
        self._sync_token = sync_token
        self._http_client = http_client

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: Mapping[str, str],
    ) -> object:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise CatalogSnapshotError("Menu returned invalid JSON") from exc

    async def _fetch_all_pages(
        self,
        client: httpx.AsyncClient,
        headers: Mapping[str, str],
    ) -> list[Mapping[str, Any]]:
        """Fetch initial snapshot and follow Menu pagination (``meta.totalPages``) if present."""
        first_payload = await self._get_json(client, self._sync_url, headers)
        items = list(_extract_items(first_payload))
        if not isinstance(first_payload, Mapping):
            return items

        meta = first_payload.get("meta")
        if not isinstance(meta, Mapping):
            return items
        total_pages = meta.get("totalPages")
        current_page = meta.get("page", 1)
        page_size = meta.get("pageSize", 50)
        if not isinstance(total_pages, int) or not isinstance(current_page, int):
            return items

        for next_page in range(current_page + 1, total_pages + 1):
            paged_url = _with_query_params(self._sync_url, page=next_page, pageSize=page_size)
            page_payload = await self._get_json(client, paged_url, headers)
            items.extend(_extract_items(page_payload))
        return items

    async def _fetch_category_names(
        self,
        client: httpx.AsyncClient,
        menu_root: str | None,
        headers: Mapping[str, str],
    ) -> dict[str, str]:
        if menu_root is None:
            return {}
        try:
            resp = await client.get(f"{menu_root}/catalog/categories", headers=headers)
            if resp.status_code != 200:
                return {}
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            return {}
        if not isinstance(body, Mapping) or not isinstance(body.get("data"), list):
            return {}
        return {
            str(cat["id"]): cat["name"]
            for cat in body["data"]
            if isinstance(cat, Mapping) and cat.get("id") and isinstance(cat.get("name"), str)
        }

    async def _fetch_offer_detail(
        self,
        client: httpx.AsyncClient,
        menu_root: str | None,
        offer_id: str,
        headers: Mapping[str, str],
    ) -> dict[str, Any] | None:
        if menu_root is None:
            return None
        try:
            resp = await client.get(f"{menu_root}/offers/{offer_id}", headers=headers)
            if resp.status_code != 200:
                return None
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        if isinstance(body, Mapping) and isinstance(body.get("data"), Mapping):
            return dict(body["data"])
        return None

    @staticmethod
    def _resolve_entry_category_name(
        entry: Mapping[str, Any],
        categories: Mapping[str, str],
    ) -> str | None:
        cat_ids = entry.get("categoryIds")
        if not isinstance(cat_ids, list):
            return None
        return next((categories[str(cid)] for cid in cat_ids if str(cid) in categories), None)

    async def _expand_single_entry_offers(
        self,
        client: httpx.AsyncClient,
        entry: Mapping[str, Any],
        cat_name: str | None,
        menu_root: str | None,
        headers: Mapping[str, str],
    ) -> list[Mapping[str, Any]]:
        offers = entry.get("offers")
        if not isinstance(offers, list):
            return []
        entry_avail = bool(entry.get("isAvailable", True))
        projected: list[Mapping[str, Any]] = []
        for offer in offers:
            if not isinstance(offer, Mapping):
                continue
            oid = offer.get("id")
            detail = (
                await self._fetch_offer_detail(client, menu_root, str(oid), headers)
                if oid is not None
                else None
            )
            effective_offer: Mapping[str, Any] = detail if detail is not None else offer
            offer_avail = entry_avail and bool(effective_offer.get("isAvailable", True))
            projected.extend(
                _expand_offer_projection_items(
                    effective_offer,
                    offer_available=offer_avail,
                    category_name=cat_name,
                )
            )
        return projected

    async def _expand_catalog_entries(
        self,
        client: httpx.AsyncClient,
        entries: list[Mapping[str, Any]],
        menu_root: str | None,
        headers: Mapping[str, str],
    ) -> list[Mapping[str, Any]]:
        categories = await self._fetch_category_names(client, menu_root, headers)
        projected: list[Mapping[str, Any]] = []
        for entry in entries:
            cat_name = self._resolve_entry_category_name(entry, categories)
            projected.extend(
                await self._expand_single_entry_offers(
                    client,
                    entry,
                    cat_name,
                    menu_root,
                    headers,
                )
            )
        return projected

    async def _expand_offer_summaries(
        self,
        client: httpx.AsyncClient,
        summaries: list[Mapping[str, Any]],
        menu_root: str | None,
        headers: Mapping[str, str],
    ) -> list[Mapping[str, Any]]:
        projected: list[Mapping[str, Any]] = []
        for summary in summaries:
            oid = summary.get("id")
            if oid is None:
                continue
            detail = await self._fetch_offer_detail(client, menu_root, str(oid), headers)
            target: Mapping[str, Any] = detail if detail is not None else summary
            is_active = str(target.get("status", "ACTIVE")).upper() == "ACTIVE"
            offer_avail = is_active and bool(target.get("isAvailable", True))
            entry_name = summary.get("entryName")
            projected.extend(
                _expand_offer_projection_items(
                    target,
                    offer_available=offer_avail,
                    category_name=entry_name if isinstance(entry_name, str) else None,
                )
            )
        return projected

    async def _normalize_menu_items(
        self,
        client: httpx.AsyncClient,
        raw_items: list[Mapping[str, Any]],
        headers: Mapping[str, str],
    ) -> list[Mapping[str, Any]]:
        """Normalize real Menu-Backend nested catalog/offer responses or return flat items."""
        menu_root = _derive_menu_api_root(self._sync_url)
        if _is_menu_catalog_entries_list(raw_items):
            return await self._expand_catalog_entries(client, raw_items, menu_root, headers)
        if _is_menu_offer_summaries_list(self._sync_url, raw_items):
            return await self._expand_offer_summaries(client, raw_items, menu_root, headers)
        return raw_items

    async def reconcile_once(self, client: httpx.AsyncClient) -> tuple[int, int]:
        """Fetch and project a complete snapshot; return (updated, discrepancies)."""
        headers = _build_reconciliation_headers(self._sync_token)
        raw_items = await self._fetch_all_pages(client, headers)
        items = await self._normalize_menu_items(client, raw_items, headers)

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
                        for key in (
                            "offerId",
                            "sourceId",
                            "id",
                            "ofertaId",
                            "platilloId",
                            "dishId",
                        )
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
