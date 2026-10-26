"""Unit tests for JSON Schema event contract validation.

Satisfies: RNF-13, CT-23
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.messaging.events import (
    DishPreparedEvent,
    EventName,
    OrderCreatedEvent,
    OrderCreatedItem,
)
from app.messaging.validator import EventContractError, load_schema, validate_event_payload


@pytest.mark.unit
@pytest.mark.requirement("RNF-13", "CT-23")
def test_order_created_event_payload_validates_against_contract() -> None:
    event = OrderCreatedEvent.for_order(uuid4(), correlation_id=uuid4())
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.ORDER_CREATED, payload)


@pytest.mark.unit
@pytest.mark.requirement("RF-32", "RNF-13")
def test_order_created_event_payload_includes_inventory_items() -> None:
    event = OrderCreatedEvent.for_order(uuid4()).model_copy(
        update={"items": [OrderCreatedItem(plateId="dish-1", quantity=2)]}
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.ORDER_CREATED, payload)


@pytest.mark.unit
@pytest.mark.requirement("RNF-13", "CT-23")
def test_dish_prepared_event_payload_validates_against_contract() -> None:
    event = DishPreparedEvent.for_order(uuid4())
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.DISH_PREPARED, payload)


@pytest.mark.unit
@pytest.mark.requirement("RNF-13", "CT-23")
def test_validation_fails_when_required_property_missing() -> None:
    invalid_payload = {
        "eventId": str(uuid4()),
        # missing orderId
    }

    with pytest.raises(EventContractError, match="failed contract validation"):
        validate_event_payload(EventName.ORDER_CREATED, invalid_payload)


@pytest.mark.unit
@pytest.mark.requirement("RNF-13", "CT-23")
def test_validation_fails_when_field_type_is_invalid() -> None:
    invalid_payload = {
        "eventId": 12345,  # must be string
        "orderId": str(uuid4()),
    }

    with pytest.raises(EventContractError, match="failed contract validation"):
        validate_event_payload(EventName.ORDER_CREATED, invalid_payload)


@pytest.mark.unit
@pytest.mark.requirement("RNF-13", "CT-23")
def test_validation_fails_when_schema_does_not_exist() -> None:
    payload = {"eventId": str(uuid4()), "orderId": str(uuid4())}

    with pytest.raises(EventContractError, match="No schema contract found"):
        validate_event_payload("ordenes.evento.inexistente", payload)


@pytest.mark.unit
@pytest.mark.requirement("RNF-13", "CT-23")
def test_plain_schema_fallback_resolves(tmp_path: Path) -> None:
    schema = {"type": "object", "required": ["eventId"]}
    (tmp_path / "custom.event.json").write_text(json.dumps(schema), encoding="utf-8")

    loaded = load_schema("custom.event", schema_dir=tmp_path)
    assert loaded == schema


@pytest.mark.unit
@pytest.mark.requirement("RNF-13", "CT-23")
def test_schema_with_invalid_json_raises(tmp_path: Path) -> None:
    (tmp_path / "broken.v1.json").write_text("not a valid json", encoding="utf-8")

    with pytest.raises(EventContractError, match="Failed to read schema"):
        load_schema("broken", schema_dir=tmp_path)


@pytest.mark.unit
@pytest.mark.requirement("RNF-13", "CT-23")
def test_schema_non_dict_raises(tmp_path: Path) -> None:
    (tmp_path / "array.v1.json").write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(EventContractError, match="not a valid JSON object"):
        load_schema("array", schema_dir=tmp_path)
