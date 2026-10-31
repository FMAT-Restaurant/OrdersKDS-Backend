"""Unit tests for Inventory contract stub (TASK-33, RF-33..35, RNF-13).

Verifies all operating modes, Draft-07 schema compliance, and language conventions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import uuid4

import jsonschema
import pytest

from app.stubs.config import InventoryStubMode, StubLanguageConvention, StubSettings
from app.stubs.inventory_stub import InventoryStub
from app.stubs.protocol import InMemoryStubPublisher

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts" / "events"


@pytest.fixture
def publisher() -> InMemoryStubPublisher:
    return InMemoryStubPublisher()


@pytest.fixture
def reserved_schema() -> dict[str, object]:
    path = CONTRACTS_DIR / "inventario.stock.reservado.v1.json"
    with path.open("r", encoding="utf-8") as f:
        return cast(dict[str, object], json.load(f))


@pytest.fixture
def insufficient_schema() -> dict[str, object]:
    path = CONTRACTS_DIR / "inventario.stock.insuficiente.v1.json"
    with path.open("r", encoding="utf-8") as f:
        return cast(dict[str, object], json.load(f))


@pytest.mark.asyncio
async def test_inventory_stub_reserve_mode_happy_path(
    publisher: InMemoryStubPublisher,
    reserved_schema: dict[str, object],
) -> None:
    stub = InventoryStub(publisher=publisher, mode=InventoryStubMode.RESERVE)
    order_id = str(uuid4())
    correlation_id = str(uuid4())

    event_id = await stub.handle_order_created(
        {
            "orderId": order_id,
            "correlationId": correlation_id,
            "items": [{"offerId": "PLT-01", "quantity": 2}],
        }
    )

    assert event_id is not None
    events = publisher.get_events("inventario.stock.reservado")
    assert len(events) == 1

    payload = events[0]["payload"]
    assert payload["orderId"] == order_id
    assert payload["correlationId"] == correlation_id
    assert "reservations" in payload
    assert "reservas" in payload

    # Strict validation against Draft-07 schema
    jsonschema.validate(instance=payload, schema=reserved_schema)


@pytest.mark.asyncio
async def test_inventory_stub_reject_mode(
    publisher: InMemoryStubPublisher,
    insufficient_schema: dict[str, object],
) -> None:
    stub = InventoryStub(publisher=publisher, mode=InventoryStubMode.REJECT)
    order_id = str(uuid4())

    event_id = await stub.handle_order_created(
        {
            "orderId": order_id,
            "items": [{"offerId": "PLT-02", "quantity": 1}],
        }
    )

    assert event_id is not None
    events = publisher.get_events("inventario.stock.insuficiente")
    assert len(events) == 1

    payload = events[0]["payload"]
    assert payload["orderId"] == order_id
    assert "missingIngredients" in payload
    assert "faltantes" in payload

    jsonschema.validate(instance=payload, schema=insufficient_schema)


@pytest.mark.asyncio
async def test_inventory_stub_silent_mode_emits_no_events(
    publisher: InMemoryStubPublisher,
) -> None:
    stub = InventoryStub(publisher=publisher, mode=InventoryStubMode.SILENT)
    order_id = str(uuid4())

    event_id = await stub.handle_order_created({"orderId": order_id})
    assert event_id is None
    assert len(publisher.get_events()) == 0


@pytest.mark.asyncio
async def test_inventory_stub_delayed_mode(
    publisher: InMemoryStubPublisher,
) -> None:
    stub = InventoryStub(publisher=publisher, mode=InventoryStubMode.DELAYED)
    stub.set_delay_seconds(0.01)

    order_id = str(uuid4())
    event_id = await stub.handle_order_created({"orderId": order_id})
    assert event_id is not None
    assert len(publisher.get_events("inventario.stock.reservado")) == 1


@pytest.mark.asyncio
async def test_inventory_stub_atomic_concurrency_mode(
    publisher: InMemoryStubPublisher,
) -> None:
    stub = InventoryStub(publisher=publisher, mode=InventoryStubMode.ATOMIC)
    stub.set_atomic_stock(1)

    order1 = str(uuid4())
    order2 = str(uuid4())

    # First order succeeds
    ev1 = await stub.handle_order_created({"orderId": order1})
    assert ev1 is not None
    assert len(publisher.get_events("inventario.stock.reservado")) == 1

    # Second order is rejected due to exhausted atomic pool
    ev2 = await stub.handle_order_created({"orderId": order2})
    assert ev2 is not None
    assert len(publisher.get_events("inventario.stock.insuficiente")) == 1


@pytest.mark.asyncio
async def test_inventory_stub_cancellation_and_expiration_handling(
    publisher: InMemoryStubPublisher,
) -> None:
    stub = InventoryStub(publisher=publisher)
    order_id = str(uuid4())

    await stub.handle_order_cancelled({"orderId": order_id})
    assert order_id in stub.cancelled_orders

    exp_id = str(uuid4())
    await stub.handle_order_expired({"orderId": exp_id})
    assert exp_id in stub.expired_orders


@pytest.mark.asyncio
async def test_inventory_stub_language_conventions(
    publisher: InMemoryStubPublisher,
) -> None:
    # Pure English
    settings_en = StubSettings(
        language_convention=StubLanguageConvention.ENGLISH,
        inventory_mode=InventoryStubMode.RESERVE,
    )
    stub_en = InventoryStub(publisher=publisher, settings=settings_en)
    await stub_en.handle_order_created({"orderId": str(uuid4())})
    payload_en = publisher.get_events()[-1]["payload"]
    assert "reservations" in payload_en
    assert "reservas" not in payload_en

    # Pure Spanish Legacy
    settings_es = StubSettings(
        language_convention=StubLanguageConvention.SPANISH_LEGACY,
        inventory_mode=InventoryStubMode.RESERVE,
    )
    stub_es = InventoryStub(publisher=publisher, settings=settings_es)
    await stub_es.handle_order_created({"orderId": str(uuid4())})
    payload_es = publisher.get_events()[-1]["payload"]
    assert "reservas" in payload_es
    assert "reservations" not in payload_es
