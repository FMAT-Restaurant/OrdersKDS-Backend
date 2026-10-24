"""Unit tests for Floor contract stub (TASK-33, RF-17, RNF-13).

Verifies dining session started emission, Floor-Module EventEnvelope compliance,
headers x-event-id/x-correlation-id, and idempotency replay.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import uuid4

import jsonschema
import pytest

from app.stubs.floor_stub import FloorStub
from app.stubs.protocol import InMemoryStubPublisher

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts" / "events"


@pytest.fixture
def publisher() -> InMemoryStubPublisher:
    return InMemoryStubPublisher()


@pytest.fixture
def session_schema() -> dict[str, object]:
    path = CONTRACTS_DIR / "sala.dining_session.iniciada.v1.json"
    with path.open("r", encoding="utf-8") as f:
        return cast(dict[str, object], json.load(f))


@pytest.mark.asyncio
async def test_floor_stub_emits_session_started_matching_floor_module(
    publisher: InMemoryStubPublisher,
    session_schema: dict[str, object],
) -> None:
    stub = FloorStub(publisher=publisher)
    session_id = uuid4()
    waiter_id = uuid4()

    event_id = await stub.emit_session_started(
        session_id=session_id,
        table_id="MESA_5",
        waiter_id=waiter_id,
        diners_count=4,
    )

    events = publisher.get_events("sala.dining_session.iniciada")
    assert len(events) == 1
    event = events[0]

    assert event["event_id"] == event_id
    assert event["exchange"] == "sala.events"
    assert event["headers"]["x-event-id"] == str(event_id)
    assert "x-correlation-id" in event["headers"]

    payload = event["payload"]
    assert payload["eventId"] == str(event_id)
    assert payload["eventType"] == "sala.dining_session.iniciada"

    # Verify Floor-Module data envelope structure
    data = payload["data"]
    assert data["sessionId"] == str(session_id)
    assert data["tableId"] == "MESA_5"
    assert data["mesaId"] == "MESA_5"
    assert data["waiterId"] == str(waiter_id)
    assert data["meseroId"] == str(waiter_id)
    assert data["dinersCount"] == 4
    assert data["comensales"] == 4

    # Strict JSON Schema validation
    jsonschema.validate(instance=payload, schema=session_schema)


@pytest.mark.asyncio
async def test_floor_stub_emits_duplicate_session_started(
    publisher: InMemoryStubPublisher,
) -> None:
    stub = FloorStub(publisher=publisher)
    session_id = uuid4()

    ev1 = await stub.emit_session_started(session_id=session_id, table_id="MESA_1")
    ev2 = await stub.emit_duplicate_session_started(session_id=session_id)

    assert ev1 == ev2
    events = publisher.get_events("sala.dining_session.iniciada")
    assert len(events) == 2
    assert events[0]["payload"]["eventId"] == events[1]["payload"]["eventId"]


@pytest.mark.asyncio
async def test_floor_stub_duplicate_unemitted_session_raises(
    publisher: InMemoryStubPublisher,
) -> None:
    stub = FloorStub(publisher=publisher)
    with pytest.raises(KeyError):
        await stub.emit_duplicate_session_started(session_id=uuid4())
