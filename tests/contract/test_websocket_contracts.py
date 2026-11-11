"""Contract checks for WebSocket message envelopes.

Satisfies: RNF-03, RNF-13, CT-27.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import jsonschema
import pytest

from app.realtime.schemas import (
    FORBIDDEN_CHANNEL,
    FORBIDDEN_CHANNEL_ERROR,
    ItemStatusChangedMessage,
    OrderStatusChangedMessage,
)

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "websocket" / "messages.v1.json"
)


@pytest.mark.contract
@pytest.mark.requirement("RNF-03", "RNF-13", "CT-27")
def test_websocket_messages_validate_against_contract() -> None:
    """Verify order and item status notifications match the WebSocket schema."""
    schema = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)

    order_message = OrderStatusChangedMessage(
        orderId=uuid4(),
        status="IN_PREPARATION",
        version=3,
    )
    item_message = ItemStatusChangedMessage(
        orderId=uuid4(),
        itemId=uuid4(),
        status="READY",
        version=4,
    )

    jsonschema.validate(order_message.to_envelope(), schema)
    jsonschema.validate(item_message.to_envelope(), schema)


@pytest.mark.contract
@pytest.mark.requirement("RNF-03", "RNF-13", "CT-27")
def test_forbidden_channel_message_is_stable() -> None:
    """Verify the forbidden subscription response remains a stable envelope."""
    assert FORBIDDEN_CHANNEL == "FORBIDDEN_CHANNEL"
    assert FORBIDDEN_CHANNEL_ERROR == {"error": FORBIDDEN_CHANNEL}
