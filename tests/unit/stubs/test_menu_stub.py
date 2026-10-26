"""Unit tests for Menu contract stub (TASK-33, ADR-0008, OP-09, RNF-13).

Verifies catalog updated events, Draft-07 schema compliance, HTTP endpoints,
and failure mode simulation for IT-14 resilience.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import uuid4

import jsonschema
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.stubs.menu_stub import MenuStub
from app.stubs.protocol import InMemoryStubPublisher

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts" / "events"


@pytest.fixture
def publisher() -> InMemoryStubPublisher:
    return InMemoryStubPublisher()


@pytest.fixture
def catalog_schema() -> dict[str, object]:
    path = CONTRACTS_DIR / "menu.catalogo.actualizado.v1.json"
    with path.open("r", encoding="utf-8") as f:
        return cast(dict[str, object], json.load(f))


@pytest.mark.asyncio
async def test_menu_stub_emits_catalog_updated(
    publisher: InMemoryStubPublisher,
    catalog_schema: dict[str, object],
) -> None:
    stub = MenuStub(publisher=publisher)
    correlation_id = uuid4()

    event_id = await stub.emit_catalog_updated(correlation_id=correlation_id)
    assert event_id is not None

    events = publisher.get_events("menu.catalogo.actualizado")
    assert len(events) == 1
    payload = events[0]["payload"]

    assert payload["eventId"] == str(event_id)
    assert payload["correlationId"] == str(correlation_id)
    assert len(payload["items"]) >= 3

    # Check station metadata presence (ADR-0008)
    first_item = payload["items"][0]
    assert "station" in first_item
    assert "preparationTime" in first_item

    jsonschema.validate(instance=payload, schema=catalog_schema)


def test_menu_stub_http_endpoints_success(
    publisher: InMemoryStubPublisher,
) -> None:
    stub = MenuStub(publisher=publisher)
    app = FastAPI()
    app.include_router(stub.create_fastapi_router())
    client = TestClient(app)

    # Full catalog endpoint
    res1 = client.get("/menu/catalogo/completo")
    assert res1.status_code == 200
    data1 = res1.json()
    assert "items" in data1
    assert data1["total"] == len(data1["items"])

    # Menu-Backend compatible entries endpoint
    res2 = client.get("/api/v1/menu/entries")
    assert res2.status_code == 200
    data2 = res2.json()
    assert "data" in data2
    assert "meta" in data2


def test_menu_stub_http_endpoints_failure_mode(
    publisher: InMemoryStubPublisher,
) -> None:
    stub = MenuStub(publisher=publisher)
    stub.set_failure_mode(True)

    app = FastAPI()
    app.include_router(stub.create_fastapi_router())
    client = TestClient(app)

    res = client.get("/menu/catalogo/completo")
    assert res.status_code == 500

    res2 = client.get("/api/v1/menu/entries")
    assert res2.status_code == 500
