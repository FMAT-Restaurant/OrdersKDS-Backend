"""Regression tests for critical security and price-integrity fixes.

Covers Team 6 QA Audit findings:
- DEF-E5-002: Cryptographic JWT signature verification (HS256/HS384/HS512 + fail-closed keys).
- DEF-E5-003: Algorithm allowlist enforcement and non-empty signature check.
- DEF-E5-004: Spoofed X-User-* gateway header rejection without perimeter trust/secret.
- DEF-E5-005: Prevention of X-User-Roles / X-User-Permissions / X-User-Type escalation over JWT.
- DEF-E5-009: Server-side catalog unitPrice enforcement on PATCH /orders/{orderId}/items.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.dependencies.auth import get_auth_context, resolve_auth_context
from app.cache.catalog import CatalogRepository
from app.core.config import get_auth_settings
from app.core.error_codes import ErrorCode
from app.domain.errors import UnauthenticatedError
from app.domain.roles import Role
from app.services.catalog import CatalogRepositoryValidator, DefaultCatalogValidator
from tests.conftest import TEST_JWT_SECRET

pytestmark = [pytest.mark.unit, pytest.mark.requirement("RNF-03", "SEC-03", "RF-14", "RF-17")]

TokenFactory = Callable[..., str]


def _b64url(data: dict[str, object]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signed_token(
    *,
    secret: str = TEST_JWT_SECRET,
    algorithm: str = "HS256",
    role: str = "waiter",
    user_id: str = "waiter-1",
    **extra: object,
) -> str:
    payload: dict[str, object] = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.now(UTC) + timedelta(minutes=15),
        **extra,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


# ---------------------------------------------------------------------------
# AuthSettings + DEF-E5-002 / DEF-E5-003: JWT signature & algorithm enforcement
# ---------------------------------------------------------------------------


def test_auth_settings_filters_unsafe_algorithms_and_normalizes_pem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_ALGORITHMS", "none,HS256,INVALID,rs256")
    monkeypatch.setenv("JWT_PUBLIC_KEY", "line1\\nline2")
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "true")

    settings = get_auth_settings()

    assert settings.jwt_algorithms == ("HS256", "RS256")
    assert settings.jwt_public_key == "line1\nline2"
    assert settings.trust_gateway_headers is True


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_valid_hmac_algorithms_verify_successfully(algorithm: str) -> None:
    token = _signed_token(algorithm=algorithm, role="chef", user_id="chef-hmac")
    ctx = resolve_auth_context(f"Bearer {token}")

    assert ctx.user_id == "chef-hmac"
    assert ctx.role is Role.CHEF


def test_wrong_hmac_secret_and_missing_key_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    wrong_secret_token = _signed_token(secret="wrong-key-of-sufficient-length-32bytes")
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(f"Bearer {wrong_secret_token}")

    monkeypatch.delenv("JWT_SECRET", raising=False)
    valid_token = _signed_token()
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(f"Bearer {valid_token}")


@pytest.mark.parametrize("alg_header", ["none", "NONE", "HS999", ""])
def test_empty_signature_and_disallowed_algorithms_rejected(alg_header: str) -> None:
    header = _b64url({"alg": alg_header, "typ": "JWT"})
    payload = _b64url(
        {
            "user_id": "attacker-1",
            "role": "admin",
            "exp": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
        }
    )
    empty_sig_token = f"{header}.{payload}."
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(f"Bearer {empty_sig_token}")

    non_empty_fake_sig_token = f"{header}.{payload}.ZmFrZS1zaWc"
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(f"Bearer {non_empty_fake_sig_token}")


def test_algorithm_not_in_configured_allowlist_and_rs256_confusion_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hs512_token = _signed_token(algorithm="HS512")
    monkeypatch.setenv("JWT_ALGORITHMS", "HS256")
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(f"Bearer {hs512_token}")

    # Algorithm confusion attempt: RS256 header when only HMAC JWT_SECRET is configured
    monkeypatch.setenv("JWT_ALGORITHMS", "HS256,RS256")
    rs_header = _b64url({"alg": "RS256", "typ": "JWT"})
    claims = _b64url(
        {
            "user_id": "attacker",
            "role": "admin",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        }
    )
    with pytest.raises(UnauthenticatedError):
        resolve_auth_context(f"Bearer {rs_header}.{claims}.ZmFrZS1yc2Etc2ln")


def test_http_endpoint_rejects_forged_jwt_with_401(client: TestClient) -> None:
    forged = _signed_token().rsplit(".", 1)[0] + ".forgedsig"
    response = client.get("/orders", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED.value


# ---------------------------------------------------------------------------
# DEF-E5-004 & DEF-E5-005: Gateway spoofing & privilege escalation defense
# ---------------------------------------------------------------------------


def test_spoofed_gateway_headers_rejected_by_default(client: TestClient) -> None:
    response = client.get(
        "/orders",
        headers={
            "x-user-id": "spoofed-admin",
            "x-user-roles": "ADMIN",
            "x-user-permissions": "*",
            "x-user-type": "ADMIN",
        },
    )
    assert response.status_code == 401


def test_trusted_gateway_enforces_shared_secret_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "true")
    monkeypatch.setenv("GATEWAY_SHARED_SECRET", "perimeter-secret-token")

    headers = [
        (b"x-user-id", b"gw-waiter"),
        (b"x-user-roles", b"MESERO"),
    ]
    with pytest.raises(UnauthenticatedError):
        get_auth_context(Request({"type": "http", "headers": headers}))

    with pytest.raises(UnauthenticatedError):
        get_auth_context(
            Request(
                {
                    "type": "http",
                    "headers": [*headers, (b"x-gateway-secret", b"wrong-secret")],
                }
            )
        )

    valid_ctx = get_auth_context(
        Request(
            {
                "type": "http",
                "headers": [*headers, (b"x-gateway-secret", b"perimeter-secret-token")],
            }
        )
    )
    assert valid_ctx.user_id == "gw-waiter"
    assert valid_ctx.role is Role.WAITER


@pytest.mark.parametrize("trust_gateway", ["false", "true"])
def test_jwt_cannot_be_escalated_by_spoofed_gateway_role_headers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    trust_gateway: str,
) -> None:
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", trust_gateway)
    waiter_jwt = _signed_token(
        role="waiter",
        user_id="waiter-99",
        permissions=["orders:orders:view"],
    )
    order_id = uuid4()

    # Waiter tries to call admin-only void while injecting ADMIN gateway headers
    resp = client.patch(
        f"/orders/{order_id}/void",
        headers={
            "Authorization": f"Bearer {waiter_jwt}",
            "x-user-id": "waiter-99",
            "x-user-roles": "ADMINISTRADOR",
            "x-user-permissions": "*",
            "x-user-type": "ADMIN",
            "x-staff-id": "STF-99",
        },
        json={"reason": "Attempted escalation"},
    )
    assert resp.status_code == 403

    req = Request(
        {
            "type": "http",
            "headers": [
                (b"authorization", f"Bearer {waiter_jwt}".encode()),
                (b"x-user-id", b"waiter-99"),
                (b"x-user-roles", b"ADMINISTRADOR"),
                (b"x-user-permissions", b"*"),
                (b"x-staff-id", b"STF-99"),
            ],
        }
    )
    ctx = get_auth_context(req)
    assert ctx.role is Role.WAITER
    assert Role.ADMIN not in ctx.effective_roles
    if trust_gateway == "true":
        assert ctx.staff_id == "STF-99"


def test_mismatched_gateway_user_id_with_verified_jwt_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "true")
    waiter_jwt = _signed_token(role="waiter", user_id="waiter-99")
    req = Request(
        {
            "type": "http",
            "headers": [
                (b"authorization", f"Bearer {waiter_jwt}".encode()),
                (b"x-user-id", b"other-user"),
            ],
        }
    )
    with pytest.raises(UnauthenticatedError):
        get_auth_context(req)


# ---------------------------------------------------------------------------
# DEF-E5-009: Authoritative server-side catalog price enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_repository_get_price_parses_and_validates_prices() -> None:
    redis = AsyncMock()
    repo = CatalogRepository(redis)
    validator = CatalogRepositoryValidator(repo)

    redis.hgetall.return_value = {b"price": b"165.00", b"available": b"true"}
    assert await validator.get_dish_price("PLATE-BURGER-01") == Decimal("165.00")

    redis.hgetall.return_value = {b"price": b"0.00"}
    assert await repo.get_price("FREE-01") is None

    redis.hgetall.return_value = {b"price": b"not-a-number"}
    assert await repo.get_price("BAD-01") is None

    empty_hash: dict[bytes, bytes] = {}
    redis.hgetall.return_value = empty_hash
    assert await repo.get_price("MISSING-01") is None

    default_validator = DefaultCatalogValidator(prices={"DISH-1": Decimal("42.50")})
    default_validator.set_price("DISH-2", Decimal("99.00"))
    assert await default_validator.get_dish_price("DISH-1") == Decimal("42.50")
    assert await default_validator.get_dish_price("DISH-2") == Decimal("99.00")
    assert await default_validator.get_dish_price("UNKNOWN") is None
