"""Contract validation tests for inbound consumed events.

Verifies schemas in contracts/events/ and compatibility with Pydantic ACL models.
Satisfies: RNF-02, RNF-13
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import jsonschema
import pytest

from app.messaging.events import (
    PaymentCompletedMessage,
    StockInsufficientMessage,
    StockReservedMessage,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "events"


@pytest.mark.contract
def test_all_consumed_contracts_are_valid_draft7_schemas() -> None:
    schema_names = [
        "inventario.stock.reservado.v1.json",
        "inventario.stock.insuficiente.v1.json",
        "pagos.pago.completado.v1.json",
    ]
    for schema_name in schema_names:
        schema_path = CONTRACTS_DIR / schema_name
        assert schema_path.is_file(), f"Missing contract schema: {schema_path}"
        with schema_path.open("r", encoding="utf-8") as f:
            schema_data = json.load(f)
        # Check against Draft-07 metaschema
        jsonschema.Draft7Validator.check_schema(schema_data)


@pytest.mark.contract
def test_stock_reserved_schema_and_pydantic_compatibility() -> None:
    schema_path = CONTRACTS_DIR / "inventario.stock.reservado.v1.json"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)

    order_id = str(uuid4())
    event_id = str(uuid4())
    valid_payload = {
        "eventId": event_id,
        "orderId": order_id,
        "correlationId": str(uuid4()),
        "reservas": [{"offerId": "PLT-1", "quantity": 2}],
    }

    # Validate against JSON schema
    jsonschema.validate(instance=valid_payload, schema=schema)

    # Validate against Pydantic model
    model = StockReservedMessage.model_validate(valid_payload)
    assert str(model.event_id) == event_id
    assert str(model.order_id) == order_id
    assert len(model.reservations) == 1

    # Missing required field
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={"eventId": event_id}, schema=schema)


@pytest.mark.contract
def test_stock_insufficient_schema_and_pydantic_compatibility() -> None:
    schema_path = CONTRACTS_DIR / "inventario.stock.insuficiente.v1.json"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)

    order_id = str(uuid4())
    event_id = str(uuid4())
    valid_payload = {
        "eventId": event_id,
        "orderId": order_id,
        "faltantes": ["carne", "queso"],
    }

    jsonschema.validate(instance=valid_payload, schema=schema)

    model = StockInsufficientMessage.model_validate(valid_payload)
    assert str(model.event_id) == event_id
    assert str(model.order_id) == order_id
    assert model.missing_ingredients == ["carne", "queso"]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={"orderId": order_id}, schema=schema)


@pytest.mark.contract
def test_payment_completed_schema_and_pydantic_compatibility() -> None:
    schema_path = CONTRACTS_DIR / "pagos.pago.completado.v1.json"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)

    order_id = str(uuid4())
    event_id = str(uuid4())
    valid_payload = {
        "eventId": event_id,
        "orderId": order_id,
        "sessionId": str(uuid4()),
        "montoTotal": 245.50,
        "totalPaid": 245.50,
        "status": "PAID",
    }

    jsonschema.validate(instance=valid_payload, schema=schema)

    model = PaymentCompletedMessage.model_validate(valid_payload)
    assert str(model.event_id) == event_id
    assert str(model.order_id) == order_id
    assert model.total_amount == 245.50 or model.total_amount == pytest.approx(245.50)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={"status": "PAID"}, schema=schema)
