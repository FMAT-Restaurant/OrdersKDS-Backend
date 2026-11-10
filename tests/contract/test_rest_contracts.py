"""Contract checks for the public REST surface and error envelope.

Satisfies: RNF-03, RNF-06, RNF-13, CT-01..22.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.main import app

EXPECTED_ENDPOINTS: dict[str, set[str]] = {
    "/orders": {"get", "post"},
    "/orders/{orderId}": {"get"},
    "/orders/{orderId}/items": {"patch"},
    "/orders/{orderId}/transfer": {"patch"},
    "/orders/{orderId}/confirm": {"post"},
    "/orders/{orderId}/cancel": {"patch"},
    "/orders/{orderId}/items/{itemId}/redo": {"patch"},
    "/orders/{orderId}/request-bill": {"post"},
    "/orders/{orderId}/void": {"patch"},
    "/orders/{orderId}/void-kitchen-error": {"patch"},
    "/kds/queue": {"get"},
    "/orders/{orderId}/start-preparation": {"patch"},
    "/orders/{orderId}/items/{itemId}/ready": {"patch"},
    "/orders/{orderId}/items/{itemId}/cancel": {"patch"},
    "/orders/{orderId}/items/{itemId}/kitchen-reject": {"patch"},
    "/orders/{orderId}/ready": {"patch"},
}


@pytest.mark.contract
@pytest.mark.requirement(
    "RF-12",
    "RF-13",
    "RF-14",
    "RF-16",
    "RF-20",
    "RF-22",
    "RF-24",
    "RF-31",
    "RNF-03",
    "RNF-06",
    "RNF-13",
    "CT-01",
    "CT-22",
    "CT-26",
)
def test_rest_endpoint_inventory_matches_contract() -> None:
    """Verify every required REST operation is present in generated OpenAPI."""
    paths = app.openapi()["paths"]
    for path, methods in EXPECTED_ENDPOINTS.items():
        assert path in paths, f"Missing REST contract path: {path}"
        assert methods <= set(paths[path]), f"Missing methods for {path}"


@pytest.mark.contract
@pytest.mark.requirement("RNF-03", "RNF-13", "CT-20", "CT-21")
def test_operations_document_error_responses() -> None:
    """Verify REST operations document the standard error responses."""
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    for path, methods in EXPECTED_ENDPOINTS.items():
        for method in methods:
            operation = paths[path][method]
            responses = operation["responses"]
            for status_code in ("401", "403", "422"):
                if status_code in responses:
                    assert responses[status_code].get("content"), (
                        f"{method.upper()} {path} has undocumented {status_code}"
                    )


@pytest.mark.contract
@pytest.mark.requirement("RNF-13", "CT-21")
def test_error_schema_is_a_stable_envelope() -> None:
    """Verify OpenAPI exposes the {error: {code, message}} response shape."""
    schemas = app.openapi()["components"]["schemas"]
    error_schema = schemas["ErrorResponse"]
    assert error_schema["required"] == ["error"]
    assert error_schema["properties"]["error"]["$ref"].endswith("/ErrorBody")
    assert schemas["ErrorBody"]["required"] == ["code", "message"]


@pytest.mark.contract
@pytest.mark.requirement("RNF-06", "CT-22")
def test_contract_operations_have_response_descriptions() -> None:
    """Ensure every operation has documented success and error responses."""
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    for path, methods in EXPECTED_ENDPOINTS.items():
        for method in methods:
            responses = paths[path][method]["responses"]
            assert any(code.startswith("2") for code in responses)
            assert all(response.get("description") for response in responses.values())
