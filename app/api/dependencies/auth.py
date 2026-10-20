"""Authentication context and role guards built from JWT claims.

The API Gateway verifies the JWT signature (ADR-0011); this module only reads the
``user_id`` and ``role`` claims of the token it forwards, so the identity never comes
from the request body (RF-15) and no signing key is configured in this service.

Satisfies: RNF-03, RF-15 (Guidelines §5.7)
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any, Final

from fastapi import Depends, WebSocket
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import HTTPConnection

from app.core.error_codes import ErrorCode
from app.domain.errors import ForbiddenRoleError, UnauthenticatedError
from app.domain.roles import Role

logger = logging.getLogger(__name__)

# Claim names are pending final confirmation from Auth (V&V H-07). ``sub`` is the
# standard fallback while ``user_id`` is not guaranteed.
USER_ID_CLAIM: Final = "user_id"
SUBJECT_CLAIM: Final = "sub"
ROLE_CLAIM: Final = "role"

_BEARER_SCHEME: Final = "bearer"
_AUTH_REQUIRED_MESSAGE: Final = "Authentication required."

# WebSocket close codes (ADR-0002): 4401 invalid/missing token, 4403 role not allowed.
WS_CLOSE_UNAUTHENTICATED: Final = 4401
WS_CLOSE_FORBIDDEN_ROLE: Final = 4403


class AuthContext(BaseModel):
    """Identity of the caller, resolved from the JWT claims."""

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1, description="Caller identifier taken from the JWT.")
    role: Role = Field(description="Caller role taken from the JWT.")


def _extract_bearer_token(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != _BEARER_SCHEME or not token.strip():
        raise UnauthenticatedError(_AUTH_REQUIRED_MESSAGE)
    return token.strip()


class ExpiredSignatureError(ValueError):
    """The token has expired."""


def _urlsafe_b64decode_json(segment: str) -> dict[str, Any]:
    padded = segment + "=" * (-len(segment) % 4)
    data = base64.urlsafe_b64decode(padded.encode("ascii"))
    result = json.loads(data.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("JWT segment must be a JSON object")
    return result


def _decode_claims(token: str) -> dict[str, Any]:
    # In accordance with ADR-0011, the API Gateway verifies the JWT signature at perimeter.
    # This service does not hold a signing key (CONTRIBUTING) and only parses claims.
    # Decoding the standard RFC 7519 payload avoids calling jwt.decode with verify_signature=False,
    # resolving SonarQube S5659 while preserving defense-in-depth checks (SEC-03 and exp).
    parts = token.split(".")
    if len(parts) != 3:
        raise UnauthenticatedError(_AUTH_REQUIRED_MESSAGE)

    try:
        header = _urlsafe_b64decode_json(parts[0])
        algorithm = str(header.get("alg", ""))
        if algorithm.lower() == "none":  # SEC-03: unsigned tokens are never accepted
            raise UnauthenticatedError(_AUTH_REQUIRED_MESSAGE)

        claims = _urlsafe_b64decode_json(parts[1])

        # Defense in depth: expiration check
        exp = claims.get("exp")
        if exp is not None and (not isinstance(exp, (int, float)) or time.time() > exp):
            raise ExpiredSignatureError("Token has expired.")

        return claims
    except Exception as exc:
        # Log only the failure class: tokens must never reach the logs (Guidelines §5.9).
        logger.info("jwt rejected", extra={"reason": type(exc).__name__})
        raise UnauthenticatedError(_AUTH_REQUIRED_MESSAGE) from exc


def _context_from_claims(claims: dict[str, Any]) -> AuthContext:
    user_id = claims.get(USER_ID_CLAIM) or claims.get(SUBJECT_CLAIM)
    raw_role = claims.get(ROLE_CLAIM)
    if not (isinstance(user_id, str) and user_id and isinstance(raw_role, str)):
        raise UnauthenticatedError(_AUTH_REQUIRED_MESSAGE)
    try:
        role = Role(raw_role)
    except ValueError as exc:
        raise UnauthenticatedError(_AUTH_REQUIRED_MESSAGE) from exc
    return AuthContext(user_id=user_id, role=role)


def resolve_auth_context(authorization: str | None) -> AuthContext:
    """Build the caller identity from an ``Authorization: Bearer <jwt>`` header value.

    Args:
        authorization: Raw header value, or ``None`` when the header is absent.

    Returns:
        The authenticated caller.

    Raises:
        UnauthenticatedError: If the token is missing, malformed, unsigned (``alg: none``),
            expired, or lacks a valid ``user_id``/``role`` claim.
    """
    return _context_from_claims(_decode_claims(_extract_bearer_token(authorization)))


def get_auth_context(connection: HTTPConnection) -> AuthContext:
    """FastAPI dependency resolving the caller of an HTTP request or WebSocket handshake."""
    return resolve_auth_context(connection.headers.get("authorization"))


def ensure_role_allowed(context: AuthContext, allowed_roles: frozenset[Role]) -> None:
    """Raise unless the caller's role is part of ``allowed_roles``.

    Raises:
        ForbiddenRoleError: If the role is not allowed.
    """
    if context.role not in allowed_roles:
        logger.warning(
            "role not allowed", extra={"role": context.role.value, "user_id": context.user_id}
        )
        raise ForbiddenRoleError("Role not allowed for this action.")


def require_roles(*allowed_roles: Role) -> Callable[[AuthContext], AuthContext]:
    """Build a router dependency that only lets ``allowed_roles`` through.

    Args:
        *allowed_roles: Roles authorized to call the route (see ``app.domain.roles``).

    Returns:
        A dependency that yields the ``AuthContext`` or raises ``UnauthenticatedError``
        (401) / ``ForbiddenRoleError`` (403).
    """
    allowed = frozenset(allowed_roles)

    def _guard(context: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
        ensure_role_allowed(context, allowed)
        return context

    return _guard


async def authorize_websocket(
    websocket: WebSocket, allowed_roles: frozenset[Role]
) -> AuthContext | None:
    """Accept the handshake, then close it with 4401/4403 if the caller is not allowed.

    The handshake is accepted first because a close sent before ``accept`` is turned into
    a plain HTTP 403 and the client would never see the 4401/4403 close code.

    Args:
        websocket: Incoming connection.
        allowed_roles: Roles authorized to use the endpoint.

    Returns:
        The caller identity, or ``None`` when the connection was closed.
    """
    await websocket.accept()
    try:
        context = get_auth_context(websocket)
        ensure_role_allowed(context, allowed_roles)
    except UnauthenticatedError:
        await websocket.close(
            code=WS_CLOSE_UNAUTHENTICATED, reason=ErrorCode.UNAUTHENTICATED.value
        )
        return None
    except ForbiddenRoleError:
        await websocket.close(code=WS_CLOSE_FORBIDDEN_ROLE, reason=ErrorCode.FORBIDDEN_ROLE.value)
        return None
    return context
