"""Unit tests for Billing & Payments contract stub (TASK-33, RF-21, RNF-13).

Verifies bill requested, charge requested, manual pay, and contract schema compliance.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import uuid4

import jsonschema
import pytest

from app.stubs.billing_stub import BillingStub
from app.stubs.config import StubLanguageConvention, StubSettings
from app.stubs.protocol import InMemoryStubPublisher

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts" / "events"


@pytest.fixture
def publisher() -> InMemoryStubPublisher:
    return InMemoryStubPublisher()


@pytest.fixture
def payment_schema() -> dict[str, object]:
    path = CONTRACTS_DIR / "pagos.pago.completado.v1.json"
    with path.open("r", encoding="utf-8") as f:
        return cast(dict[str, object], json.load(f))


@pytest.mark.asyncio
async def test_billing_stub_auto_pay_on_bill_requested(
    publisher: InMemoryStubPublisher,
    payment_schema: dict[str, object],
) -> None:
    stub = BillingStub(publisher=publisher)
    order_id = str(uuid4())
    session_id = str(uuid4())

    event_id = await stub.handle_bill_requested(
        {
            "orderId": order_id,
            "sessionId": session_id,
            "totalAmount": 280.50,
        }
    )

    assert event_id is not None
    events = publisher.get_events("pagos.pago.completado")
    assert len(events) == 1

    payload = events[0]["payload"]
    assert payload["orderId"] == order_id
    assert payload["sessionId"] == session_id
    assert payload["totalPaid"] == 280.50
    assert payload["status"] == "PAID"
    assert "totalAmount" in payload
    assert "montoTotal" in payload

    jsonschema.validate(instance=payload, schema=payment_schema)


@pytest.mark.asyncio
async def test_billing_stub_auto_pay_on_charge_requested_waste(
    publisher: InMemoryStubPublisher,
    payment_schema: dict[str, object],
) -> None:
    stub = BillingStub(publisher=publisher)
    order_id = str(uuid4())

    event_id = await stub.handle_charge_requested(
        {
            "orderId": order_id,
            "totalAmount": 95.00,
        }
    )

    assert event_id is not None
    events = publisher.get_events("pagos.pago.completado")
    assert len(events) == 1
    jsonschema.validate(instance=events[0]["payload"], schema=payment_schema)


@pytest.mark.asyncio
async def test_billing_stub_manual_pay_order(
    publisher: InMemoryStubPublisher,
    payment_schema: dict[str, object],
) -> None:
    stub = BillingStub(publisher=publisher)
    stub.set_auto_pay(False)

    order_id = uuid4()
    session_id = uuid4()
    event_id = await stub.pay_order(
        order_id=order_id,
        amount=Decimal("150.00"),
        session_id=session_id,
    )

    assert event_id is not None
    events = publisher.get_events("pagos.pago.completado")
    assert len(events) == 1

    payload = events[0]["payload"]
    assert payload["orderId"] == str(order_id)
    assert payload["sessionId"] == str(session_id)
    assert payload["totalPaid"] == 150.00

    jsonschema.validate(instance=payload, schema=payment_schema)


@pytest.mark.asyncio
async def test_billing_stub_auto_pay_disabled(
    publisher: InMemoryStubPublisher,
) -> None:
    stub = BillingStub(publisher=publisher)
    stub.set_auto_pay(False)

    ev1 = await stub.handle_bill_requested({"orderId": str(uuid4())})
    assert ev1 is None

    ev2 = await stub.handle_charge_requested({"orderId": str(uuid4())})
    assert ev2 is None

    assert len(publisher.get_events()) == 0


@pytest.mark.asyncio
async def test_billing_stub_language_conventions(
    publisher: InMemoryStubPublisher,
) -> None:
    settings_en = StubSettings(language_convention=StubLanguageConvention.ENGLISH)
    stub_en = BillingStub(publisher=publisher, settings=settings_en)
    await stub_en.pay_order(order_id=str(uuid4()), amount=50.0)

    payload_en = publisher.get_events()[-1]["payload"]
    assert "totalAmount" in payload_en
    assert "montoTotal" not in payload_en
