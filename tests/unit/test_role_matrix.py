"""Role x endpoint matrix over the real application (HTTP and WebSocket).

The expected matrices below are written out by hand on purpose: they must not be derived
from the production tables, otherwise a wrong table would still pass.

Satisfies: RNF-03, RF-15, RNF-04 (UT-BE-55, UT-BE-56, CT-18, CT-19, SEC-04)

TODO(RNF-03, TASK-34): port this matrix to the Newman collection (CT-18, CT-19) and add the
Gateway-level cases that this service cannot check (IT-16 tampered signature, SEC-03/SEC-09).
TODO(RNF-04, TASK-29): extend the WebSocket cases with channel subscription (`FORBIDDEN_CHANNEL`).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from uuid import UUID

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.websockets.routes import KDS_WS_PATH, WAITER_WS_PATH, WS_ROLES_BY_PATH
from app.core.error_codes import ErrorCode
from app.domain.roles import KITCHEN_ROLES, WAITER_ROLES, Role
from app.main import app

pytestmark = [pytest.mark.unit, pytest.mark.requirement("RNF-03", "RF-15", "RNF-04")]

ORDER_ID = UUID("00000000-0000-4000-8000-000000000001")
ITEM_ID = UUID("00000000-0000-4000-8000-000000000002")

PUBLIC_PATHS = {"/health"}
PATH_PARAMETER = re.compile(r"\{[^}]+\}")

# Kitchen routes (§5.7: kitchen, chef, admin). An allowed role passes the guard and reaches the
# placeholder handler, which answers 501 until TASK-13.
KITCHEN_ROUTES = [
    f"/orders/{ORDER_ID}/ready",
    f"/orders/{ORDER_ID}/items/{ITEM_ID}/ready",
]
KITCHEN_HTTP_MATRIX = [
    ("waiter", 403),
    ("kitchen", 501),
    ("chef", 501),
    ("admin", 501),
]

# (endpoint, role) pairs that keep the connection open / get closed with 4403.
WS_ALLOWED_MATRIX = [
    (KDS_WS_PATH, "kitchen"),
    (KDS_WS_PATH, "chef"),
    (KDS_WS_PATH, "admin"),
    (WAITER_WS_PATH, "waiter"),
    (WAITER_WS_PATH, "admin"),
]
WS_FORBIDDEN_MATRIX = [
    (KDS_WS_PATH, "waiter"),
    (WAITER_WS_PATH, "kitchen"),
    (WAITER_WS_PATH, "chef"),
]


def _protected_http_routes() -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []

    def _collect(items: Iterable[object]) -> None:
        for route in items:
            if isinstance(route, APIRoute) and route.path not in PUBLIC_PATHS:
                for method in sorted(route.methods or ()):
                    concrete_path = PATH_PARAMETER.sub(str(ORDER_ID), route.path)
                    routes.append((method, concrete_path))
            # FastAPI >= 0.115 wraps include_router() results as _IncludedRouter;
            # the real APIRoute objects live inside original_router.routes.
            inner = getattr(route, "original_router", None)
            if inner is not None:
                _collect(inner.routes)

    _collect(app.routes)
    return routes


def _error_code(response_json: dict[str, dict[str, str]]) -> str:
    return response_json["error"]["code"]


@pytest.mark.parametrize(("method", "path"), _protected_http_routes())
def test_protected_routes_without_token_return_401(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path)

    assert response.status_code == 401
    assert _error_code(response.json()) == ErrorCode.UNAUTHENTICATED


@pytest.mark.parametrize("path", KITCHEN_ROUTES)
@pytest.mark.parametrize(("role", "expected_status"), KITCHEN_HTTP_MATRIX)
def test_kitchen_routes_role_matrix(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    path: str,
    role: str,
    expected_status: int,
) -> None:
    response = client.patch(path, headers=auth_headers(role))

    assert response.status_code == expected_status
    if expected_status == 403:
        assert response.json() == {
            "error": {
                "code": ErrorCode.FORBIDDEN_ROLE,
                "message": "Role not allowed for this action.",
            }
        }


@pytest.mark.parametrize("path", KITCHEN_ROUTES)
def test_kitchen_routes_with_expired_token_return_401(
    client: TestClient, make_token: Callable[..., str], path: str
) -> None:
    headers = {"Authorization": f"Bearer {make_token(role='admin', expires_in=-60)}"}

    response = client.patch(path, headers=headers)

    assert response.status_code == 401


@pytest.mark.parametrize("path", KITCHEN_ROUTES)
@pytest.mark.parametrize("body", [None, {}])
def test_kitchen_routes_accept_an_absent_or_empty_body(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    path: str,
    body: dict[str, str] | None,
) -> None:
    response = client.patch(path, headers=auth_headers("kitchen"), json=body)

    assert response.status_code == 501


@pytest.mark.parametrize("path", KITCHEN_ROUTES)
@pytest.mark.parametrize("body", [{"userId": "attacker"}, {"status": "READY"}, {"note": "x"}])
def test_kitchen_routes_reject_unexpected_body_fields_as_mass_assignment(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    path: str,
    body: dict[str, str],
) -> None:
    response = client.patch(path, headers=auth_headers("admin"), json=body)

    assert response.status_code == 422
    assert _error_code(response.json()) == ErrorCode.VALIDATION_ERROR
    assert "attacker" not in response.text


def test_kitchen_routes_with_a_non_uuid_order_id_return_validation_error(
    client: TestClient, auth_headers: Callable[[str], dict[str, str]]
) -> None:
    response = client.patch("/orders/123/ready", headers=auth_headers("kitchen"))

    assert response.status_code == 422
    assert _error_code(response.json()) == ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize(("path", "role"), WS_ALLOWED_MATRIX)
def test_websocket_with_allowed_role_stays_open(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    path: str,
    role: str,
) -> None:
    with client.websocket_connect(path, headers=auth_headers(role)) as websocket:
        websocket.send_text("ignored until TASK-29")


@pytest.mark.parametrize(("path", "role"), WS_FORBIDDEN_MATRIX)
def test_websocket_with_wrong_role_closes_with_4403(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    path: str,
    role: str,
) -> None:
    with (
        client.websocket_connect(path, headers=auth_headers(role)) as websocket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        websocket.receive_text()

    assert closed.value.code == 4403
    assert closed.value.reason == ErrorCode.FORBIDDEN_ROLE


@pytest.mark.parametrize("path", [KDS_WS_PATH, WAITER_WS_PATH])
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic dXNlcjpwYXNz"}],
    ids=["no-token", "malformed-token", "wrong-scheme"],
)
def test_websocket_without_valid_token_closes_with_4401(
    client: TestClient, path: str, headers: dict[str, str]
) -> None:
    with (
        client.websocket_connect(path, headers=headers) as websocket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        websocket.receive_text()

    assert closed.value.code == 4401
    assert closed.value.reason == ErrorCode.UNAUTHENTICATED


def test_websocket_with_expired_token_closes_with_4401(
    client: TestClient, make_token: Callable[..., str]
) -> None:
    headers = {"Authorization": f"Bearer {make_token(role='admin', expires_in=-60)}"}

    with (
        client.websocket_connect(KDS_WS_PATH, headers=headers) as websocket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        websocket.receive_text()

    assert closed.value.code == 4401


def test_websocket_role_table_matches_the_authorization_matrix() -> None:
    expected = {
        KDS_WS_PATH: frozenset({Role.KITCHEN, Role.CHEF, Role.ADMIN}),
        WAITER_WS_PATH: frozenset({Role.WAITER, Role.ADMIN}),
    }

    assert expected == WS_ROLES_BY_PATH
    assert WS_ROLES_BY_PATH[KDS_WS_PATH] == KITCHEN_ROLES
    assert WS_ROLES_BY_PATH[WAITER_WS_PATH] == WAITER_ROLES
