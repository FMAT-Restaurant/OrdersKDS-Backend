"""Inventory contract stub simulating warehouse stock management.

Architecture & Language Convention Note:
-----------------------------------------
1. Inter-Team Convention: All cross-microservice communication is agreed to be in English.
   Payloads emitted by this stub canonically use English field names:
   - ``orderId``, ``eventId``, ``correlationId``
   - ``reservations``: list of reserved items
   - ``missingIngredients``: list of unavailable ingredients
   To guarantee seamless backwards compatibility with older components or legacy Spanish aliases,
   the stub operates in HYBRID mode by default, injecting both canonical English fields and
   legacy aliases (``reservas``, ``faltantes``).

2. Sibling Implementation Discrepancy:
   Inspection of the `Inventory-Backend` repository revealed that while ingredient models
   contain internal `reservedStock` fields in MongoDB, the AMQP integration with Orders
   (subscribing to `ordenes.orden.creada` and publishing `inventario.stock.reservado`) is
   NOT yet implemented in their codebase. This stub fulfills that contract completely.

Satisfies: RF-33, RF-34, RF-35, RNF-13, OP-11
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from app.stubs.config import InventoryStubMode, StubLanguageConvention, StubSettings
from app.stubs.protocol import StubPublisherProtocol

logger = logging.getLogger(__name__)


class InventoryStub:
    """Executable contract stub for the Inventory microservice."""

    def __init__(
        self,
        publisher: StubPublisherProtocol,
        settings: StubSettings | None = None,
        mode: InventoryStubMode | None = None,
    ) -> None:
        self.publisher = publisher
        self.settings = settings or StubSettings()
        self.mode = mode or self.settings.inventory_mode
        self.delay_seconds = self.settings.inventory_delay_seconds
        self.atomic_stock: int = self.settings.inventory_initial_stock
        self.cancelled_orders: set[str] = set()
        self.expired_orders: set[str] = set()

    def set_mode(self, mode: InventoryStubMode) -> None:
        """Switch operating mode dynamically for test scenarios."""
        self.mode = mode
        logger.info("InventoryStub mode changed to %s", mode)

    def set_delay_seconds(self, seconds: float) -> None:
        """Configure delay for DELAYED mode."""
        self.delay_seconds = seconds

    def set_atomic_stock(self, stock: int) -> None:
        """Set atomic stock pool for ATOMIC concurrency mode."""
        self.atomic_stock = stock

    async def handle_order_created(
        self,
        payload: Mapping[str, Any],
    ) -> UUID | None:
        """React to an order created event from Orders & KDS.

        Args:
            payload: Payload of ordenes.orden.creada matching contract schema.

        Returns:
            The event_id of the emitted response, or None if in SILENT mode.
        """
        order_id_str = str(payload.get("orderId", ""))
        correlation_id_str = payload.get("correlationId")
        correlation_id = UUID(str(correlation_id_str)) if correlation_id_str else None

        if self.mode == InventoryStubMode.SILENT:
            logger.info("InventoryStub [SILENT]: ignoring order %s to test timeout", order_id_str)
            return None

        if self.mode == InventoryStubMode.DELAYED:
            logger.info(
                "InventoryStub [DELAYED]: delaying response by %.2f s for order %s",
                self.delay_seconds,
                order_id_str,
            )
            await asyncio.sleep(self.delay_seconds)

        if self.mode == InventoryStubMode.REJECT:
            return await self._emit_stock_insufficient(
                order_id=order_id_str,
                missing=["harina_trigo", "queso_mozzarella"],
                correlation_id=correlation_id,
            )

        if self.mode == InventoryStubMode.ATOMIC:
            if self.atomic_stock > 0:
                self.atomic_stock -= 1
                logger.info(
                    "InventoryStub [ATOMIC]: reserved 1 unit for %s (remaining: %d)",
                    order_id_str,
                    self.atomic_stock,
                )
                return await self._emit_stock_reserved(
                    order_id=order_id_str,
                    items=payload.get("items", []),
                    correlation_id=correlation_id,
                )
            logger.info(
                "InventoryStub [ATOMIC]: stock exhausted for order %s, rejecting",
                order_id_str,
            )
            return await self._emit_stock_insufficient(
                order_id=order_id_str,
                missing=["agotado_general"],
                correlation_id=correlation_id,
            )

        if self.mode == InventoryStubMode.LATE_ON_CANCELLED:
            # Emits late reservation deliberately to test discarded late responses
            logger.info(
                "InventoryStub [LATE_ON_CANCELLED]: emitting late reservation for order %s",
                order_id_str,
            )
            return await self._emit_stock_reserved(
                order_id=order_id_str,
                items=payload.get("items", []),
                correlation_id=correlation_id,
            )

        # Default RESERVE mode
        return await self._emit_stock_reserved(
            order_id=order_id_str,
            items=payload.get("items", []),
            correlation_id=correlation_id,
        )

    async def handle_order_cancelled(self, payload: Mapping[str, Any]) -> None:
        """Handle compensation event when an order is cancelled."""
        order_id = str(payload.get("orderId", ""))
        self.cancelled_orders.add(order_id)
        logger.info(
            "InventoryStub: received order.cancelled for %s, released allocated stock",
            order_id,
        )

    async def handle_order_expired(self, payload: Mapping[str, Any]) -> None:
        """Handle compensation event when an order expires due to timeout."""
        order_id = str(payload.get("orderId", ""))
        self.expired_orders.add(order_id)
        logger.info(
            "InventoryStub: received order.expired for %s, released locks",
            order_id,
        )

    async def _emit_stock_reserved(
        self,
        order_id: str,
        items: Any,
        correlation_id: UUID | None,
    ) -> UUID:
        event_id = uuid4()
        reservations_data = (
            items
            if isinstance(items, list) and len(items) > 0
            else [{"plateId": "PLATE-01", "quantity": 1}]
        )

        payload: dict[str, Any] = {
            "eventId": str(event_id),
            "orderId": order_id,
            "correlationId": str(correlation_id) if correlation_id else None,
        }

        # Apply agreed English naming and hybrid compatibility
        if self.settings.language_convention in (
            StubLanguageConvention.ENGLISH,
            StubLanguageConvention.HYBRID,
        ):
            payload["reservations"] = reservations_data
        if self.settings.language_convention in (
            StubLanguageConvention.SPANISH_LEGACY,
            StubLanguageConvention.HYBRID,
        ):
            payload["reservas"] = reservations_data

        await self.publisher.publish(
            event_type="inventario.stock.reservado",
            payload=payload,
            event_id=event_id,
            correlation_id=correlation_id,
        )
        return event_id

    async def _emit_stock_insufficient(
        self,
        order_id: str,
        missing: list[str],
        correlation_id: UUID | None,
    ) -> UUID:
        event_id = uuid4()
        payload: dict[str, Any] = {
            "eventId": str(event_id),
            "orderId": order_id,
            "correlationId": str(correlation_id) if correlation_id else None,
        }

        if self.settings.language_convention in (
            StubLanguageConvention.ENGLISH,
            StubLanguageConvention.HYBRID,
        ):
            payload["missingIngredients"] = missing
        if self.settings.language_convention in (
            StubLanguageConvention.SPANISH_LEGACY,
            StubLanguageConvention.HYBRID,
        ):
            payload["faltantes"] = missing

        await self.publisher.publish(
            event_type="inventario.stock.insuficiente",
            payload=payload,
            event_id=event_id,
            correlation_id=correlation_id,
        )
        return event_id
