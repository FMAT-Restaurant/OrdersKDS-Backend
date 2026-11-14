"""Contract validation tests for event JSON schemas and typed models.

Satisfies: RF-10, RNF-13, CT-23
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import jsonschema
import pytest

from app.messaging.events import (
    BillRequestedEvent,
    ChargeRequestedEvent,
    ChargeRequestedItem,
    DishPreparedEvent,
    EventName,
    KitchenDishRejectedEvent,
    OrderCancelledEvent,
    OrderCreatedEvent,
    OrderCreatedItem,
    OrderExpiredEvent,
    OrderInPreparationEvent,
    OrderWastedEvent,
)
from app.messaging.validator import EventContractError, validate_event_payload

_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "events"


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-23")
def test_all_contract_schemas_are_valid_draft7() -> None:
    """Verify every JSON schema file in contracts/events complies with Draft-07."""
    schema_files = list(_CONTRACTS_DIR.glob("*.json"))
    assert len(schema_files) >= 9

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
def test_order_in_preparation_event_satisfies_contract() -> None:
    """Verify OrderInPreparationEvent payload validates against its contract."""
    event = OrderInPreparationEvent.for_order(
        uuid4(),
        correlation_id=uuid4(),
        items=[OrderCreatedItem(offerId="dish-1", quantity=2)],
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.ORDER_IN_PREPARATION, payload)
    assert payload["items"] == [{"offerId": "dish-1", "quantity": 2}]


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-23")
def test_kitchen_dish_rejected_event_satisfies_contract() -> None:
    """Verify KitchenDishRejectedEvent payload validates against its contract."""
    event = KitchenDishRejectedEvent.for_item(
        order_id=uuid4(),
        item_id=uuid4(),
        offer_id="dish-1",
        quantity=2,
        reason="No hay carne fresca",
        correlation_id=uuid4(),
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.KITCHEN_DISH_REJECTED, payload)
    assert payload["offerId"] == "dish-1"
    assert payload["quantity"] == 2
    assert payload["reason"] == "No hay carne fresca"


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
    event = DishPreparedEvent.for_item(
        uuid4(),
        uuid4(),
        offer_id="dish-1",
        quantity=2,
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.DISH_PREPARED, payload)
    assert payload["quantity"] == 2


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-23", "RF-35", "OP-04")
def test_order_expired_event_satisfies_contract() -> None:
    """Verify OrderExpiredEvent payload validates against its contract."""
    event = OrderExpiredEvent.for_order(
        uuid4(),
        elapsed_seconds=60,
        items=[OrderCreatedItem(offerId="dish-1", quantity=2)],
        reason="timeout",
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.ORDER_EXPIRED, payload)
    assert payload["reason"] == "timeout"
    assert payload["motivo"] == "timeout"
    assert payload["items"] == [{"offerId": "dish-1", "quantity": 2}]


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


@pytest.mark.contract
@pytest.mark.requirement("RF-26", "RF-27", "RNF-13", "CT-23")
def test_order_wasted_event_satisfies_contract() -> None:
    """Verify OrderWastedEvent payload validates against its contract."""
    event = OrderWastedEvent.for_order(
        uuid4(),
        correlation_id=uuid4(),
        items=[OrderCreatedItem(offerId="dish-1", quantity=2)],
        reason="ADMINISTRATIVE",
        motivo="administrativo",
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.ORDER_WASTED, payload)
    assert payload["reason"] == "ADMINISTRATIVE"
    assert payload["motivo"] == "administrativo"
    assert payload["itemsAfectados"] == [{"offerId": "dish-1", "quantity": 2}]


@pytest.mark.contract
@pytest.mark.requirement("RF-20", "RF-26", "RF-27", "RNF-13", "CT-23")
def test_charge_requested_event_satisfies_contract() -> None:
    """Verify ChargeRequestedEvent payload validates against its contract."""
    order_id = uuid4()
    item_id = uuid4()
    charge_item = ChargeRequestedItem(
        itemId=item_id,
        plateId="dish-1",
        name="Hamburguesa Clásica",
        quantity=2,
        unitPrice=Decimal("180.00"),
        subtotal=Decimal("360.00"),
    )
    event = ChargeRequestedEvent.for_order(
        order_id,
        total_amount=Decimal("360.00"),
        items=[charge_item],
        table_id="MESA-04",
        status="WASTED",
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.CHARGE_REQUESTED, payload)
    assert payload["orderId"] == str(order_id)
    assert payload["totalAmount"] == 360.0
    assert payload["status"] == "WASTED"
    assert "data" in payload
    assert payload["data"]["orderId"] == str(order_id)


@pytest.mark.contract
@pytest.mark.requirement("RF-28", "RF-30", "RNF-13", "CT-13")
def test_void_kitchen_error_event_satisfies_contract() -> None:
    """CT-13: Verify OrderWastedEvent emitted for kitchen error validates against contract."""
    order_id = uuid4()
    item = OrderCreatedItem(offerId="dish-burnt-steak", quantity=2)
    event = OrderWastedEvent.for_order(
        order_id,
        items=[item],
        reason="KITCHEN_ERROR",
        motivo="error_cocina",
        note="Carne sobrecocida y quemada en plancha",
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.ORDER_WASTED, payload)
    assert payload["orderId"] == str(order_id)
    assert payload["reason"] == "KITCHEN_ERROR"
    assert payload["motivo"] == "error_cocina"
    assert payload["note"] == "Carne sobrecocida y quemada en plancha"
    items_afectados = cast(list[dict[str, Any]], payload.get("itemsAfectados", []))
    assert len(items_afectados) == 1
    assert items_afectados[0]["offerId"] == "dish-burnt-steak"
    assert items_afectados[0]["quantity"] == 2


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-23")
def test_dual_source_and_offer_identifiers_satisfy_event_contracts() -> None:
    """Verify OrderCreated, OrderInPreparation, KitchenDishRejected, DishPrepared carry IDs."""
    order_id = uuid4()
    item_id = uuid4()
    dual_item = OrderCreatedItem(
        offerId="offer-combo-1",
        plateId="recipe-burger-1",
        sourceId="recipe-burger-1",
        quantity=2,
    )

    created_payload = OrderCreatedEvent.for_order(order_id, items=[dual_item]).model_dump(
        mode="json", by_alias=True
    )
    validate_event_payload(EventName.ORDER_CREATED, created_payload)
    assert created_payload["items"][0]["plateId"] == "recipe-burger-1"
    assert created_payload["items"][0]["sourceId"] == "recipe-burger-1"
    assert created_payload["items"][0]["offerId"] == "offer-combo-1"

    prep_payload = OrderInPreparationEvent.for_order(order_id, items=[dual_item]).model_dump(
        mode="json", by_alias=True
    )
    validate_event_payload(EventName.ORDER_IN_PREPARATION, prep_payload)
    assert prep_payload["items"][0]["plateId"] == "recipe-burger-1"
    assert prep_payload["items"][0]["offerId"] == "offer-combo-1"

    rejected_payload = KitchenDishRejectedEvent.for_item(
        order_id,
        item_id,
        offer_id="offer-combo-1",
        plate_id="recipe-burger-1",
        quantity=2,
        reason="Sin pan brioche",
    ).model_dump(mode="json", by_alias=True)
    validate_event_payload(EventName.KITCHEN_DISH_REJECTED, rejected_payload)
    assert rejected_payload["plateId"] == "recipe-burger-1"
    assert rejected_payload["offerId"] == "offer-combo-1"

    prepared_payload = DishPreparedEvent.for_item(
        order_id,
        item_id,
        offer_id="offer-combo-1",
        plate_id="recipe-burger-1",
        source_id="recipe-burger-1",
        quantity=2,
    ).model_dump(mode="json", by_alias=True)
    validate_event_payload(EventName.DISH_PREPARED, prepared_payload)
    assert prepared_payload["plateId"] == "recipe-burger-1"
    assert prepared_payload["sourceId"] == "recipe-burger-1"
    assert prepared_payload["offerId"] == "offer-combo-1"


@pytest.mark.contract
@pytest.mark.requirement("RF-20", "RF-21", "RNF-13", "CT-08", "CT-15", "IT-18")
def test_bill_requested_event_satisfies_contract() -> None:
    """CT-08, CT-15: Verify BillRequestedEvent validates against its JSON Schema contract."""
    order_id = uuid4()
    session_id = uuid4()
    waiter_id = uuid4()
    item_id = uuid4()
    item = ChargeRequestedItem(
        itemId=item_id,
        plateId="dish-ribeye",
        name="Bife de Chorizo",
        quantity=1,
        unitPrice=Decimal("450.00"),
        subtotal=Decimal("450.00"),
        modifiers=["término medio"],
        notes="Sin sal adicional",
    )
    event = BillRequestedEvent.for_order(
        order_id,
        total_amount=Decimal("450.00"),
        items=[item],
        session_id=session_id,
        table_id="MESA-05",
        waiter_id=waiter_id,
        status="DELIVERED",
    )
    payload = event.model_dump(mode="json", by_alias=True)

    validate_event_payload(EventName.BILL_REQUESTED, payload)
    assert payload["orderId"] == str(order_id)
    assert payload["sessionId"] == str(session_id)
    assert payload["tableId"] == "MESA-05"
    assert payload["waiterId"] == str(waiter_id)
    assert payload["status"] == "DELIVERED"
    assert payload["totalAmount"] == 450.0
    items_list = cast(list[dict[str, Any]], payload.get("items", []))
    assert len(items_list) == 1
    assert items_list[0]["plateId"] == "dish-ribeye"
    assert items_list[0]["unitPrice"] == 450.0
    assert items_list[0]["modifiers"] == ["término medio"]
    assert items_list[0]["notes"] == "Sin sal adicional"
    assert "data" in payload
    data_dict = cast(dict[str, Any], payload.get("data", {}))
    assert data_dict["orderId"] == str(order_id)
    assert data_dict["sessionId"] == str(session_id)
