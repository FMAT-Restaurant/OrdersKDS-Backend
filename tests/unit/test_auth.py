"""Unit tests of the JWT-claims authentication context and the role guards.

Satisfies: RNF-03, RF-15 (UT-BE-55, UT-BE-56, SEC-03)
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest

from app.api.dependencies.auth import (
    AuthContext,
    ensure_role_allowed,
    require_roles,
    resolve_auth_context,
)
from app.core.error_codes import ErrorCode
from app.domain.errors import ForbiddenRoleError, UnauthenticatedError
from app.domain.roles import KITCHEN_ROLES, WAITER_ROLES, Role

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
