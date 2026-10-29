"""Menu & Culinary Catalog contract stub simulating catalog queries and update events.

Architecture & Language Convention Note:
-----------------------------------------
1. Inter-Team Convention:
   All cross-microservice communication is agreed to be in English:
   - Items contain ``itemId``, ``plateId``, ``name``, ``available``, ``station``,
     ``preparationTime``, and ``basePrice``.
   To support backwards compatibility and older documentation, legacy Spanish aliases
   (``nombre``, ``disponible``, ``estacion``, ``tiempoPreparacion``) are included
   under HYBRID mode.

2. Sibling Implementation Discrepancy:
   `Menu-Backend` exposes REST endpoints (`/api/v1/menu/entries`, `/offers`, `/recipes`),
   but its RabbitMQ messaging is pending (as stated in their `RabbitMqConnectionTest.java`:
   "No declara exchanges, colas ni bindings").
   This stub provides both the catalog event emission and the HTTP endpoints.

Satisfies: ADR-0008, OP-09, RNF-13, IT-14
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status

from app.stubs.config import StubLanguageConvention, StubSettings
from app.stubs.protocol import StubPublisherProtocol

logger = logging.getLogger(__name__)

DEFAULT_CATALOG_ITEMS: list[dict[str, Any]] = [
    {
        "id": "PLT-BURGER-01",
        "offerId": "PLT-BURGER-01",
        "itemId": "ITEM-BURGER-01",
        "plateId": "PLT-BURGER-01",
        "name": "Hamburguesa Clásica FMAT",
        "available": True,
        "station": "PARRILLA",
        "preparationTime": "15 min",
        "basePrice": 145.00,
    },
    {
        "id": "PLT-SALAD-01",
        "offerId": "PLT-SALAD-01",
        "itemId": "ITEM-SALAD-01",
        "plateId": "PLT-SALAD-01",
        "name": "Ensalada César con Pollo",
        "available": True,
        "station": "FRIOS",
        "preparationTime": "8 min",
        "basePrice": 110.00,
    },
    {
        "id": "PLT-DRINK-01",
        "offerId": "PLT-DRINK-01",
        "itemId": "ITEM-DRINK-01",
        "plateId": "PLT-DRINK-01",
        "name": "Limonada Natural",
        "available": True,
        "station": "BAR",
        "preparationTime": "5 min",
        "basePrice": 45.00,
    },
    {
        "id": "PLT-NO-STATION",
        "offerId": "PLT-NO-STATION",
        "itemId": "ITEM-NO-STATION",
        "plateId": "PLT-NO-STATION",
        "name": "Postre del Día Casero",
        "available": True,
        "station": None,  # Intentionally None to test fallback to GENERAL station (ADR-0008)
        "preparationTime": "5 min",
        "basePrice": 75.00,
    },
]


class MenuStub:
    """Executable contract stub for Menu microservice."""

    def __init__(
        self,
        publisher: StubPublisherProtocol,
        settings: StubSettings | None = None,
        catalog_items: list[dict[str, Any]] | None = None,
    ) -> None:
        self.publisher = publisher
        self.settings = settings or StubSettings()
        self.items: list[dict[str, Any]] = list(catalog_items or DEFAULT_CATALOG_ITEMS)
        self.failure_mode: bool = self.settings.menu_failure_mode

    def set_failure_mode(self, enabled: bool) -> None:
        """Enable or disable HTTP 500 error simulation for resilience tests (IT-14)."""
        self.failure_mode = enabled
        logger.info("MenuStub failure_mode set to %s", enabled)

    def set_item_availability(self, plate_id: str, available: bool) -> None:
        """Update an item's availability in the catalog snapshot."""
        for item in self.items:
            if plate_id in (
                item.get("offerId"),
                item.get("id"),
                item.get("plateId"),
                item.get("itemId"),
            ):
                item["available"] = available

    async def emit_catalog_updated(
        self,
        items: list[dict[str, Any]] | None = None,
        correlation_id: UUID | None = None,
    ) -> UUID:
        """Emit a menu catalog updated event over the message bus."""
        event_id = uuid4()
        now_iso = datetime.now(UTC).isoformat()
        source_items = items if items is not None else self.items

        normalized_items: list[dict[str, Any]] = []
        for item in source_items:
            entry = dict(item)
            if self.settings.language_convention in (
                StubLanguageConvention.SPANISH_LEGACY,
                StubLanguageConvention.HYBRID,
            ):
                entry["nombre"] = item.get("name")
                entry["disponible"] = item.get("available")
                entry["estacion"] = item.get("station")
                entry["tiempoPreparacion"] = item.get("preparationTime")
            normalized_items.append(entry)

        payload: dict[str, Any] = {
            "eventId": str(event_id),
            "timestamp": now_iso,
            "correlationId": str(correlation_id) if correlation_id else None,
            "items": normalized_items,
        }

        await self.publisher.publish(
            event_type="menu.catalogo.actualizado",
            payload=payload,
            event_id=event_id,
            correlation_id=correlation_id,
        )
        logger.info("MenuStub: emitted catalog updated with %d items", len(normalized_items))
        return event_id

    def create_fastapi_router(self) -> APIRouter:
        """Create a FastAPI APIRouter serving catalog HTTP endpoints."""
        router = APIRouter()

        @router.get("/menu/catalogo/completo")
        async def get_full_catalog() -> dict[str, Any]:
            if self.failure_mode:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Menu Service Unavailable (simulated failure mode)",
                )
            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "items": self.items,
                "total": len(self.items),
            }

        @router.get("/api/v1/menu/entries")
        async def get_menu_entries() -> dict[str, Any]:
            """Compatibility endpoint matching Menu-Backend's route."""
            if self.failure_mode:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Menu Service Unavailable (simulated failure mode)",
                )
            return {
                "data": self.items,
                "meta": {"page": 1, "pageSize": len(self.items), "totalElements": len(self.items)},
            }

        return router
