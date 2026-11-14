"""Unit tests for Stage 2 Auth gateway integration, kitchen mounts, and PBAC inference."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.dependencies.auth import get_auth_context, resolve_auth_context
from app.db.session import get_session
from app.domain.errors import UnauthenticatedError
from app.domain.roles import Role
from app.main import app
from tests.unit.conftest import FakeAsyncSession, TokenFactory

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/api/v1/health",
        "/api/v1/orders/health",
        "/api/v1/kitchen/health",
    ],
)
def test_health_endpoints_return_ok(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_kitchen_prefix_mounts_kds_queue_and_enforces_pbac(client: TestClient) -> None:
    unauthenticated = client.get("/api/v1/kitchen/kds/queue")
    assert unauthenticated.status_code == 401

    fake_session = FakeAsyncSession()

    async def _override_session() -> AsyncGenerator[FakeAsyncSession, None]:
        yield fake_session

    app.dependency_overrides[get_session] = _override_session
    try:
        authorized = client.get(
            "/api/v1/kitchen/kds/queue",
            headers={
                "x-user-id": "chef-1",
                "x-user-roles": "CHEF_MASTER",
                "x-user-permissions": "kitchen:queues:view,orders:orders:view",
            },
        )
        assert authorized.status_code == 200
        assert authorized.json() == []
    finally:
        app.dependency_overrides.clear()


def test_gateway_headers_resolve_admin_from_user_type_without_roles() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-user-id", b"admin-gw-1"),
                (b"x-user-type", b"ADMIN"),
            ],
        }
    )

    context = get_auth_context(request)

    assert context.user_id == "admin-gw-1"
    assert context.role is Role.ADMIN
    assert Role.ADMIN in context.effective_roles


def test_gateway_headers_resolve_admin_from_wildcard_permission_without_roles() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-user-id", b"wildcard-gw-1"),
                (b"x-user-permissions", b"*"),
            ],
        }
    )

    context = get_auth_context(request)

    assert context.user_id == "wildcard-gw-1"
    assert context.role is Role.ADMIN


def test_jwt_claims_resolve_admin_from_user_type_without_roles(make_token: TokenFactory) -> None:
    token = make_token(user_id="jwt-admin-1", role=None, userType="ADMIN")

    context = resolve_auth_context(f"Bearer {token}")

    assert context.user_id == "jwt-admin-1"
    assert context.role is Role.ADMIN


@pytest.mark.parametrize(
    ("permission", "expected_role"),
    [
        ("kitchen:orders:void_error", Role.CHEF),
        ("kitchen:queues:view", Role.KITCHEN),
        ("orders:orders:create", Role.WAITER),
    ],
)
def test_pbac_role_inference_when_raw_role_is_unrecognized(
    make_token: TokenFactory,
    permission: str,
    expected_role: Role,
) -> None:
    token = make_token(user_id="pbac-user-1", role="SUPERVISOR", permissions=[permission])
    jwt_context = resolve_auth_context(f"Bearer {token}")
    assert jwt_context.role is expected_role

    gateway_request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-user-id", b"pbac-gw-1"),
                (b"x-user-roles", b"SUPERVISOR"),
                (b"x-user-permissions", permission.encode()),
            ],
        }
    )
    gw_context = get_auth_context(gateway_request)
    assert gw_context.role is expected_role


def test_unrelated_permissions_with_unknown_role_still_reject_unauthenticated(
    make_token: TokenFactory,
) -> None:
    token = make_token(
        user_id="external-user-1",
        role="ALMACENISTA",
        permissions=["inventory:items:read"],
    )

    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(f"Bearer {token}")
