"""Billing & Payments contract stub simulating monetary settlement.

Architecture & Language Convention Note:
-----------------------------------------
1. Inter-Team Convention: As formally agreed with Edwin in `contrato_ordenes_pagos_edwin.md`,
   all attributes and enums in payments are 100% in English:
   - ``orderId``, ``sessionId``, ``paymentId``, ``totalAmount``, ``totalPaid``, ``status: "PAID"``
   For backwards compatibility, legacy aliases (``montoTotal``) are included under HYBRID mode.

2. Sibling Implementation Discrepancy:
   Inspection of `Payments-BE` revealed that the repository currently contains only ASP.NET Core
   boilerplate with `/health` and OpenAPI. AMQP messaging has not been implemented yet.
   This stub provides the complete behavior required by Orders & KDS.

Satisfies: RF-21, RNF-13, OP-11
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.stubs.config import StubLanguageConvention, StubSettings
from app.stubs.protocol import StubPublisherProtocol

logger = logging.getLogger(__name__)


class BillingStub:
    """Executable contract stub for the Payments & Billing microservice."""

    def __init__(
        self,
        publisher: StubPublisherProtocol,
        settings: StubSettings | None = None,
    ) -> None:
        self.publisher = publisher
        self.settings = settings or StubSettings()
        self.auto_pay = self.settings.billing_auto_pay
        self.ack_delay = self.settings.billing_ack_delay_seconds
        self.settled_orders: dict[str, dict[str, Any]] = {}

    def set_auto_pay(self, enabled: bool) -> None:
        """Enable or disable automatic payment upon bill/charge request."""
        self.auto_pay = enabled

    async def handle_bill_requested(
        self,
        payload: Mapping[str, Any],
    ) -> UUID | None:
        """React to regular bill requested event (DELIVERED orders)."""
        order_id = str(payload.get("orderId", ""))
        session_id = payload.get("sessionId")
        total_amount = payload.get("totalAmount") or payload.get("montoTotal") or 100.0

        logger.info("BillingStub: received bill.requested for order %s", order_id)
        if not self.auto_pay:
            return None

        if self.ack_delay > 0:
            await asyncio.sleep(self.ack_delay)

        return await self.pay_order(
            order_id=order_id,
            amount=total_amount,
            session_id=session_id,
        )

    async def handle_charge_requested(
        self,
        payload: Mapping[str, Any],
    ) -> UUID | None:
        """React to administrative waste charge requested event (WASTED orders, RF-26)."""
        order_id = str(payload.get("orderId", ""))
        session_id = payload.get("sessionId")
        total_amount = payload.get("totalAmount") or payload.get("montoTotal") or 50.0

        logger.info("BillingStub: received charge.requested (wasted) for order %s", order_id)
        if not self.auto_pay:
            return None

        if self.ack_delay > 0:
            await asyncio.sleep(self.ack_delay)

        return await self.pay_order(
            order_id=order_id,
            amount=total_amount,
            session_id=session_id,
        )

    async def pay_order(
        self,
        order_id: UUID | str,
        amount: float | Decimal | str = 100.0,
        session_id: UUID | str | None = None,
        correlation_id: UUID | None = None,
    ) -> UUID:
        """Emit a payment completed event to transition order to PAID."""
        event_id = uuid4()
        payment_id = f"PAY-{uuid4().hex[:8].upper()}"
        num_amount = float(amount)
        order_str = str(order_id)
        session_str = str(session_id) if session_id else str(uuid4())

        payload: dict[str, Any] = {
            "eventId": str(event_id),
            "orderId": order_str,
            "correlationId": str(correlation_id) if correlation_id else None,
            "sessionId": session_str,
            "paymentId": payment_id,
            "paymentMethod": "CREDIT_CARD",
            "totalPaid": num_amount,
            "status": "PAID",
        }

        # Language convention: English totalAmount + hybrid montoTotal
        if self.settings.language_convention in (
            StubLanguageConvention.ENGLISH,
            StubLanguageConvention.HYBRID,
        ):
            payload["totalAmount"] = num_amount
        if self.settings.language_convention in (
            StubLanguageConvention.SPANISH_LEGACY,
            StubLanguageConvention.HYBRID,
        ):
            payload["montoTotal"] = num_amount

        await self.publisher.publish(
            event_type="pagos.pago.completado",
            payload=payload,
            event_id=event_id,
            correlation_id=correlation_id,
        )

        self.settled_orders[order_str] = payload
        logger.info("BillingStub: settled payment for order %s ($%.2f)", order_str, num_amount)
        return event_id
