"""Contract validation tests for event JSON schemas and typed models.

Satisfies: RF-10, RNF-13, CT-23
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import jsonschema
import pytest

from app.messaging.events import (
    DishPreparedEvent,
    EventName,
    OrderCancelledEvent,
    OrderCreatedEvent,
    OrderCreatedItem,
    OrderExpiredEvent,
)
from app.messaging.validator import EventContractError, validate_event_payload

_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "events"


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-23")
def test_all_contract_schemas_are_valid_draft7() -> None:
    """Verify every JSON schema file in contracts/events complies with Draft-07."""
    schema_files = list(_CONTRACTS_DIR.glob("*.json"))
    assert len(schema_files) >= 7

    for schema_file in schema_files:
        content = json.loads(schema_file.read_text(encoding="utf-8"))
        jsonschema.Draft7Validator.check_schema(content)


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-23")
def test_order_created_event_satisfies_contract() -> None:
    """Verify OrderCreatedEvent payload validates against its contract."""
    event = OrderCreatedEvent.for_order(
        uuid4(),
        correlation_id=uuid4(),
        items=[OrderCreatedItem(offerId="dish-1", quantity=2)],
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.ORDER_CREATED, payload)
    assert payload["items"] == [{"offerId": "dish-1", "quantity": 2}]
    assert event.items[0].plate_id == "dish-1"


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-23")
def test_order_created_contract_rejects_invalid_items() -> None:
    payload: dict[str, object] = {
        "eventId": str(uuid4()),
        "orderId": str(uuid4()),
        "items": [{"offerId": "dish-1", "quantity": 0}],
    }

    with pytest.raises(EventContractError, match="failed contract validation"):
        validate_event_payload(EventName.ORDER_CREATED, payload)


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-23")
def test_dish_prepared_event_satisfies_contract() -> None:
    """Verify DishPreparedEvent payload validates against its contract."""
    event = DishPreparedEvent.for_order(uuid4())
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.DISH_PREPARED, payload)


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-23", "RF-35")
def test_order_expired_event_satisfies_contract() -> None:
    """Verify OrderExpiredEvent payload validates against its contract."""
    event = OrderExpiredEvent.for_order(uuid4())
    event.elapsed_seconds = 60
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.ORDER_EXPIRED, payload)


@pytest.mark.contract
@pytest.mark.requirement("RF-10", "RNF-13", "CT-23")
def test_dish_prepared_event_for_item_satisfies_contract() -> None:
    """Verify DishPreparedEvent.for_item payload validates against its contract."""
    event = DishPreparedEvent.for_item(
        order_id=uuid4(),
        item_id=uuid4(),
        correlation_id=uuid4(),
    )
    payload = event.model_dump(mode="json", by_alias=True)

    assert "itemId" in payload
    validate_event_payload(EventName.DISH_PREPARED, payload)


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-23")
def test_contract_rejects_empty_identifiers() -> None:
    """Verify schemas enforce minLength: 1 on eventId and orderId."""
    empty_event_id = {"eventId": "", "orderId": str(uuid4())}
    with pytest.raises(EventContractError, match="failed contract validation"):
        validate_event_payload(EventName.ORDER_CREATED, empty_event_id)

    empty_order_id = {"eventId": str(uuid4()), "orderId": ""}
    with pytest.raises(EventContractError, match="failed contract validation"):
        validate_event_payload(EventName.ORDER_CREATED, empty_order_id)


@pytest.mark.contract
@pytest.mark.requirement("RF-24", "RF-25", "RNF-13", "CT-23")
def test_order_cancelled_event_satisfies_contract() -> None:
    """Verify OrderCancelledEvent payload validates against its contract."""
    event = OrderCancelledEvent.for_order(
        uuid4(),
        items=[OrderCreatedItem(offerId="dish-1", quantity=2)],
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.ORDER_CANCELLED, payload)
    assert payload["itemsCancelados"] == [{"offerId": "dish-1", "quantity": 2}]
