"""Contract parity checks for shared backend/frontend enum values.

Satisfies: RNF-13, CT-27.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from app.core.error_codes import ErrorCode
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "enums.v1.json"


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-27")
def test_shared_enum_contract_matches_backend_values() -> None:
    """Verify the canonical enum contract contains the backend values exactly."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(contract)
    values = contract["properties"]

    assert values["orderStatus"]["const"] == [status.value for status in OrderStatus]
    assert values["itemStatus"]["const"] == [status.value for status in ItemStatus]
    assert values["roles"]["const"] == [role.value for role in Role]
    assert values["errorCode"]["const"] == [code.value for code in ErrorCode] + [
        "FORBIDDEN_CHANNEL"
    ]

    assert len(values["orderStatus"]["const"]) == 10
    assert len(values["itemStatus"]["const"]) == 4
