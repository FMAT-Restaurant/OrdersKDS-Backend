"""Unit tests of the JWT-claims authentication context and the role guards.

Satisfies: RNF-03, RF-15 (UT-BE-55, UT-BE-56, SEC-03)
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.dependencies.auth import (
    AuthContext,
    ensure_authorized,
    ensure_role_allowed,
    get_auth_context,
    require_access,
    require_permissions,
    require_roles,
    resolve_auth_context,
)
from app.core.error_codes import ErrorCode
from app.domain.access import AccessPolicy
from app.domain.errors import ForbiddenRoleError, UnauthenticatedError
from app.domain.roles import (
    KITCHEN_ROLES,
    WAITER_ROLES,
    Role,
    normalize_role,
    primary_role,
    resolve_roles,
)

pytestmark = [pytest.mark.unit, pytest.mark.requirement("RNF-03", "RF-15")]

TokenFactory = Callable[..., str]


def _bearer(token: str) -> str:
    return f"Bearer {token}"


@pytest.mark.parametrize("role", list(Role))
def test_resolve_auth_context_with_valid_token_returns_user_and_role(
    make_token: TokenFactory, role: Role
) -> None:
    token = make_token(role=role.value, user_id="user-42")

    context = resolve_auth_context(_bearer(token))

    assert context == AuthContext(user_id="user-42", role=role)


def test_resolve_auth_context_accepts_lowercase_bearer_scheme(make_token: TokenFactory) -> None:
    context = resolve_auth_context(f"bearer {make_token()}")

    assert context.role is Role.WAITER


def test_resolve_auth_context_falls_back_to_sub_when_user_id_is_missing(
    make_token: TokenFactory,
) -> None:
    token = make_token(user_id=None, sub="subject-7")

    assert resolve_auth_context(_bearer(token)).user_id == "subject-7"


def test_resolve_auth_context_prefers_user_id_over_sub(make_token: TokenFactory) -> None:
    token = make_token(user_id="user-1", sub="subject-7")

    assert resolve_auth_context(_bearer(token)).user_id == "user-1"


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Bearer", "Bearer    ", "Basic dXNlcjpwYXNz", "Token abc", "just-a-token"],
)
def test_resolve_auth_context_without_bearer_token_raises_unauthenticated(
    authorization: str | None,
) -> None:
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(authorization)


def test_resolve_auth_context_with_malformed_jwt_raises_unauthenticated() -> None:
    header = _bearer("not-a-jwt")
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(header)


def test_resolve_auth_context_with_expired_token_raises_unauthenticated(
    make_token: TokenFactory,
) -> None:
    header = _bearer(make_token(expires_in=-60))
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(header)


def test_resolve_auth_context_with_unsigned_token_raises_unauthenticated(
    make_token: TokenFactory,
) -> None:
    # SEC-03: the "none" algorithm must never be accepted.
    header = _bearer(make_token(algorithm="none"))
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(header)


def test_resolve_auth_context_does_not_verify_the_signature(make_token: TokenFactory) -> None:
    # ADR-0011: signature verification belongs to the API Gateway, not to this service.
    token = make_token()
    forged = token.rsplit(".", 1)[0] + ".forged-signature"

    assert resolve_auth_context(_bearer(forged)).user_id == "user-1"


@pytest.mark.parametrize(
    "claims",
    [
        {"role": None},
        {"role": "guest"},
        {"role": 7},
        {"user_id": None},
        {"user_id": ""},
        {"user_id": 123},
    ],
    ids=[
        "missing-role",
        "unknown-role",
        "non-string-role",
        "missing-user-id",
        "empty-user-id",
        "non-string-user-id",
    ],
)
def test_resolve_auth_context_with_invalid_claims_raises_unauthenticated(
    make_token: TokenFactory, claims: dict[str, object]
) -> None:
    header = _bearer(make_token(**claims))
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(header)


def test_resolve_auth_context_failure_never_logs_the_token(
    make_token: TokenFactory, caplog: pytest.LogCaptureFixture
) -> None:
    token = make_token(expires_in=-60)
    header = _bearer(token)

    with caplog.at_level(logging.INFO), pytest.raises(UnauthenticatedError):
        resolve_auth_context(header)

    assert [getattr(record, "reason", None) for record in caplog.records] == [
        "ExpiredSignatureError"
    ]
    assert token not in caplog.text


def test_ensure_role_allowed_with_allowed_role_does_not_raise() -> None:
    ensure_role_allowed(AuthContext(user_id="u", role=Role.CHEF), KITCHEN_ROLES)


def test_ensure_role_allowed_with_other_role_raises_forbidden_role() -> None:
    context = AuthContext(user_id="u", role=Role.WAITER)
    with pytest.raises(ForbiddenRoleError):
        ensure_role_allowed(context, KITCHEN_ROLES)


def test_require_roles_returns_the_context_for_an_allowed_role() -> None:
    context = AuthContext(user_id="u", role=Role.ADMIN)

    assert require_roles(*WAITER_ROLES)(context) is context


def test_require_roles_rejects_a_role_outside_the_allowed_set() -> None:
    guard = require_roles(Role.ADMIN)
    context = AuthContext(user_id="u", role=Role.CHEF)

    with pytest.raises(ForbiddenRoleError):
        guard(context)


def test_auth_errors_carry_stable_codes_and_http_status() -> None:
    assert (UnauthenticatedError().code, UnauthenticatedError().http_status) == (
        ErrorCode.UNAUTHENTICATED,
        401,
    )
    assert (ForbiddenRoleError().code, ForbiddenRoleError().http_status) == (
        ErrorCode.FORBIDDEN_ROLE,
        403,
    )


def test_role_normalization_and_aliases() -> None:
    assert normalize_role("waiter") == Role.WAITER
    assert normalize_role("MESERO") == Role.WAITER
    assert normalize_role("kitchen") == Role.KITCHEN
    assert normalize_role("COCINERO") == Role.KITCHEN
    assert normalize_role("chef") == Role.CHEF
    assert normalize_role("CHEF_MASTER") == Role.CHEF
    assert normalize_role("admin") == Role.ADMIN
    assert normalize_role("ADMINISTRADOR") == Role.ADMIN
    assert normalize_role("unknown") is None
    assert normalize_role(123) is None


def test_resolve_roles_and_primary_priority() -> None:
    resolved = resolve_roles(["MESERO", "CHEF_MASTER", "UNKNOWN_ROLE"])
    assert resolved == frozenset({Role.WAITER, Role.CHEF})
    assert primary_role(resolved) == Role.CHEF

    admin_and_chef = resolve_roles(["ADMINISTRADOR", "CHEF_MASTER"])
    assert primary_role(admin_and_chef) == Role.ADMIN
    assert primary_role(frozenset()) is None


def test_auth_context_from_claims_with_roles_array_and_permissions(
    make_token: TokenFactory,
) -> None:
    token = make_token(
        roles=["MESERO", "COCINERO"],
        role=None,
        permissions=["orders:orders:view", "orders:orders:create"],
        staffId="M001",
    )
    context = resolve_auth_context(_bearer(token))
    assert context.user_id == "user-1"
    assert context.role == Role.KITCHEN
    assert context.effective_roles == frozenset({Role.KITCHEN, Role.WAITER})
    assert context.staff_id == "M001"
    assert context.has_permission("orders:orders:view") is True
    assert context.has_permission("orders:orders:cancel") is False


def test_auth_context_admin_has_all_permissions() -> None:
    context = AuthContext(
        user_id="admin-1",
        role=Role.ADMIN,
        roles=frozenset({Role.ADMIN}),
        permissions=frozenset(),
    )
    assert context.has_permission("any:arbitrary:permission") is True


def test_auth_context_wildcard_permission() -> None:
    context = AuthContext(
        user_id="user-1",
        role=Role.WAITER,
        roles=frozenset({Role.WAITER}),
        permissions=frozenset(["*"]),
    )
    assert context.has_permission("any:permission") is True


def test_access_policy_evaluation() -> None:
    policy = AccessPolicy(
        permissions=frozenset(["orders:orders:create"]),
        fallback_roles=frozenset({Role.WAITER}),
    )

    allowed_by_pbac = AuthContext(
        user_id="u1",
        role=Role.WAITER,
        roles=frozenset({Role.WAITER}),
        permissions=frozenset(["orders:orders:create"]),
    )
    assert allowed_by_pbac.is_authorized(policy) is True

    denied_by_pbac = AuthContext(
        user_id="u2",
        role=Role.WAITER,
        roles=frozenset({Role.WAITER}),
        permissions=frozenset(["orders:orders:view"]),
    )
    assert denied_by_pbac.is_authorized(policy) is False

    allowed_by_role_fallback = AuthContext(
        user_id="u3",
        role=Role.WAITER,
        roles=frozenset({Role.WAITER}),
        permissions=None,
    )
    assert allowed_by_role_fallback.is_authorized(policy) is True

    denied_by_role_fallback = AuthContext(
        user_id="u4",
        role=Role.KITCHEN,
        roles=frozenset({Role.KITCHEN}),
        permissions=None,
    )
    assert denied_by_role_fallback.is_authorized(policy) is False


def test_get_auth_context_from_gateway_headers() -> None:
    scope = {
        "type": "http",
        "headers": [
            (b"x-user-id", b"kong-user-99"),
            (b"x-user-roles", b"MESERO,ADMINISTRADOR"),
            (b"x-user-permissions", b"orders:orders:create,orders:orders:confirm"),
            (b"x-staff-id", b"EMP-42"),
        ],
    }
    request = Request(scope)
    context = get_auth_context(request)
    assert context.user_id == "kong-user-99"
    assert context.role == Role.ADMIN
    assert context.effective_roles == frozenset({Role.ADMIN, Role.WAITER})
    assert context.staff_id == "EMP-42"
    assert context.permissions == frozenset(["orders:orders:create", "orders:orders:confirm"])


def test_get_auth_context_falls_back_to_bearer_when_gateway_headers_absent(
    make_token: TokenFactory,
) -> None:
    token = make_token(user_id="bearer-user", role="chef")
    scope = {
        "type": "http",
        "headers": [
            (b"authorization", f"Bearer {token}".encode()),
        ],
    }
    request = Request(scope)
    context = get_auth_context(request)
    assert context.user_id == "bearer-user"
    assert context.role == Role.CHEF


def test_ensure_authorized_and_require_access() -> None:
    policy = AccessPolicy(
        permissions=frozenset(["kitchen:queues:view"]),
        fallback_roles=frozenset({Role.KITCHEN}),
    )
    allowed_ctx = AuthContext(
        user_id="chef-1",
        role=Role.KITCHEN,
        roles=frozenset({Role.KITCHEN}),
        permissions=frozenset(["kitchen:queues:view"]),
    )
    ensure_authorized(allowed_ctx, policy)
    assert require_access(policy)(allowed_ctx) is allowed_ctx

    denied_ctx = AuthContext(
        user_id="waiter-1",
        role=Role.WAITER,
        roles=frozenset({Role.WAITER}),
        permissions=frozenset(["orders:orders:view"]),
    )
    with pytest.raises(ForbiddenRoleError):
        ensure_authorized(denied_ctx, policy)


def test_require_permissions_helper() -> None:
    guard = require_permissions(
        "kitchen:orders:start_preparation", fallback_roles=frozenset({Role.CHEF})
    )
    ctx = AuthContext(
        user_id="u1",
        role=Role.CHEF,
        roles=frozenset({Role.CHEF}),
        permissions=frozenset(["kitchen:orders:start_preparation"]),
    )
    assert guard(ctx) is ctx


def test_api_v1_and_orders_prefix_routes_mounted(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200

    assert client.get("/orders").status_code == 401
    assert client.get("/api/v1/orders").status_code == 401
    assert client.get("/api/v1/orders/orders").status_code == 401
    assert client.get("/kds/queue").status_code == 401
    assert client.get("/api/v1/kds/queue").status_code == 401
    assert client.get("/api/v1/orders/kds/queue").status_code == 401
