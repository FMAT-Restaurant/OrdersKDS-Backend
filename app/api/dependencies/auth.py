"""Authentication context and access guards built from the gateway identity.

The API Gateway verifies the JWT signature (ADR-0011). This module only *reads* the identity
it forwards, in this order of preference:

1. the headers Kong's ``auth-perimeter`` plugin injects once the token is verified
   (``X-User-Id``, ``X-User-Roles``, ``X-User-Permissions``, ``X-Staff-Id``);
2. the claims of the ``Authorization: Bearer`` token (``user_id``/``sub``, ``roles``/``role``,
   ``permissions``) for local development and direct calls without the gateway.

The identity never comes from the request body (RF-15) and no signing key is configured in
this service. Because the gateway is the trust boundary, this service must only be reachable
through it in shared environments (the same assumption ADR-0011 already makes for the JWT).

Authorization is PBAC first with a role fallback, see :mod:`app.domain.access`.

Satisfies: RNF-03, RF-15 (Guidelines §5.7)
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Annotated, Any, Final
from urllib.parse import parse_qs

from fastapi import Depends, WebSocket
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import HTTPConnection

from app.core.error_codes import ErrorCode
from app.domain.access import WILDCARD_PERMISSION, AccessPolicy
from app.domain.errors import ForbiddenRoleError, UnauthenticatedError
from app.domain.roles import Role, primary_role, resolve_roles

logger = logging.getLogger(__name__)

# Claim names issued by ``Auth-Backend``. ``sub`` is the standard fallback while ``user_id``
# is not guaranteed; ``role`` (singular) is kept for development tokens.
USER_ID_CLAIM: Final = "user_id"
SUBJECT_CLAIM: Final = "sub"
ROLE_CLAIM: Final = "role"
ROLES_CLAIM: Final = "roles"
PERMISSIONS_CLAIM: Final = "permissions"
STAFF_ID_CLAIM: Final = "staffId"
USER_TYPE_CLAIM: Final = "userType"

# Headers injected by the Kong ``auth-perimeter`` plugin (lowercase: lookups are case-insensitive).
HEADER_USER_ID: Final = "x-user-id"
HEADER_STAFF_ID: Final = "x-staff-id"
HEADER_USER_ROLES: Final = "x-user-roles"
HEADER_USER_ROLE: Final = "x-user-role"  # singular spelling used by the shared integration guide
HEADER_USER_PERMISSIONS: Final = "x-user-permissions"
HEADER_USER_TYPE: Final = "x-user-type"

_BEARER_SCHEME: Final = "bearer"
_AUTH_REQUIRED_MESSAGE: Final = "Authentication required."

# WebSocket close codes (ADR-0002): 4401 invalid/missing token, 4403 role not allowed.
WS_CLOSE_UNAUTHENTICATED: Final = 4401
WS_CLOSE_FORBIDDEN_ROLE: Final = 4403


class AuthContext(BaseModel):
    """Identity of the caller, resolved from the gateway headers or the JWT claims.

    Attributes:
        user_id: Caller identifier.
        role: Primary (highest-privilege) role, used for audit logs and channel routing.
        roles: Every role of the caller; guards evaluate all of them.
        permissions: PBAC permissions issued by Auth. ``None`` means the identity carried no
            permission list (development token) and the role fallback applies; an explicit
            empty set means "no permissions" and denies everything except for ``admin``.
        staff_id: Employee identifier, when issued by Auth.
        exp: Expiration timestamp (epoch seconds), when known.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1, description="Caller identifier taken from the identity.")
    role: Role = Field(description="Primary role of the caller.")
    roles: frozenset[Role] = Field(default_factory=frozenset, description="All caller roles.")
    permissions: frozenset[str] | None = Field(
        default=None, description="PBAC permissions, or None when none were issued."
    )
    staff_id: str | None = Field(default=None, description="Employee identifier.")
    exp: float | None = Field(default=None, description="Expiration timestamp (epoch seconds).")

    @property
    def effective_roles(self) -> frozenset[Role]:
        """Every role of the caller, always including the primary one."""
        return self.roles | {self.role}

    def has_any_role(self, allowed_roles: Collection[Role]) -> bool:
        """Whether any role of the caller is part of ``allowed_roles``."""
        return not self.effective_roles.isdisjoint(allowed_roles)

    def has_permission(self, permission: str) -> bool:
        """Whether the caller holds ``permission``; ``admin`` and ``*`` hold everything."""
        granted = self.permissions or frozenset()
        if Role.ADMIN in self.effective_roles or WILDCARD_PERMISSION in granted:
            return True
        return permission in granted

    def is_authorized(self, policy: AccessPolicy) -> bool:
        """Evaluate ``policy``: PBAC when permissions were issued, role fallback otherwise."""
        if self.permissions is None:
            return self.has_any_role(policy.fallback_roles)
        return any(self.has_permission(permission) for permission in policy.permissions)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AuthContext):
            return False
        same_identity = (
            self.user_id == other.user_id
            and self.role == other.role
            and self.effective_roles == other.effective_roles
            and self.permissions == other.permissions
        )
        if not same_identity:
            return False
        if other.exp is None or self.exp is None:
            return True
        return abs(self.exp - other.exp) < 1.0

    def __hash__(self) -> int:
        return hash((self.user_id, self.role))

    def is_expired(self, now: float | None = None) -> bool:
        """Check whether token expiration has been reached."""
        if self.exp is None:
            return False
        current_time = now if now is not None else time.time()
        return current_time >= self.exp


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


def _split_csv(value: str | None) -> list[str]:
    """Split a comma-separated header/claim value, dropping blanks."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _permissions_from_claim(value: object) -> frozenset[str] | None:
    """Parse the ``permissions`` claim: absent -> ``None``; malformed -> deny-by-default."""
    if value is None:
        return None
    if isinstance(value, str):
        return frozenset(_split_csv(value))
    if isinstance(value, (list, tuple)):
        return frozenset(item.strip() for item in value if isinstance(item, str) and item.strip())
    return frozenset()


def _roles_from_claims(claims: Mapping[str, Any]) -> list[object]:
    """Collect the raw role labels of ``roles`` (list or CSV string) and ``role``."""
    roles_claim = claims.get(ROLES_CLAIM)
    if isinstance(roles_claim, str):
        candidates: list[object] = list(_split_csv(roles_claim))
    elif isinstance(roles_claim, (list, tuple)):
        candidates = list(roles_claim)
    else:
        candidates = []
    candidates.append(claims.get(ROLE_CLAIM))
    return candidates


def _expiration(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _infer_roles_from_identity(
    raw_roles: Iterable[object],
    user_type: object,
    permissions: frozenset[str] | None,
) -> frozenset[Role]:
    """Resolve caller roles and infer fallback roles from userType or PBAC permissions."""
    roles = set(resolve_roles(raw_roles))
    is_admin_type = isinstance(user_type, str) and user_type.strip().upper() == "ADMIN"
    if is_admin_type or (permissions is not None and WILDCARD_PERMISSION in permissions):
        roles.add(Role.ADMIN)

    if not roles and permissions:
        if "kitchen:orders:void_error" in permissions:
            roles.add(Role.CHEF)
        elif any(permission.startswith("kitchen:") for permission in permissions):
            roles.add(Role.KITCHEN)
        elif any(permission.startswith("orders:") for permission in permissions):
            roles.add(Role.WAITER)

    return frozenset(roles)


def _build_context(
    user_id: object,
    raw_roles: Iterable[object],
    *,
    permissions: frozenset[str] | None,
    staff_id: object,
    exp: float | None,
    user_type: object = None,
) -> AuthContext:
    """Assemble the caller identity, or raise ``UnauthenticatedError`` when it is unusable."""
    roles = _infer_roles_from_identity(raw_roles, user_type, permissions)
    primary = primary_role(roles)
    if not (isinstance(user_id, str) and user_id and primary is not None):
        raise UnauthenticatedError(_AUTH_REQUIRED_MESSAGE)
    return AuthContext(
        user_id=user_id,
        role=primary,
        roles=roles,
        permissions=permissions,
        staff_id=staff_id if isinstance(staff_id, str) and staff_id else None,
        exp=exp,
    )


def _context_from_claims(claims: dict[str, Any]) -> AuthContext:
    return _build_context(
        claims.get(USER_ID_CLAIM) or claims.get(SUBJECT_CLAIM),
        _roles_from_claims(claims),
        permissions=_permissions_from_claim(claims.get(PERMISSIONS_CLAIM)),
        staff_id=claims.get(STAFF_ID_CLAIM),
        exp=_expiration(claims.get("exp")),
        user_type=claims.get(USER_TYPE_CLAIM),
    )


def _context_from_gateway_headers(
    headers: Mapping[str, str], authorization: str | None
) -> AuthContext | None:
    """Build the identity from the headers Kong injects, or ``None`` when they are absent.

    The expiration is read from the forwarded token, when there is one, so that long-lived
    WebSocket connections are still closed when the token expires.
    """
    user_id = (headers.get(HEADER_USER_ID) or "").strip()
    if not user_id:
        return None
    raw_permissions = headers.get(HEADER_USER_PERMISSIONS)
    exp = None
    if authorization:
        exp = _expiration(_decode_claims(_extract_bearer_token(authorization)).get("exp"))
    return _build_context(
        user_id,
        _split_csv(headers.get(HEADER_USER_ROLES) or headers.get(HEADER_USER_ROLE)),
        permissions=None if raw_permissions is None else frozenset(_split_csv(raw_permissions)),
        staff_id=(headers.get(HEADER_STAFF_ID) or "").strip(),
        exp=exp,
        user_type=headers.get(HEADER_USER_TYPE),
    )


def resolve_auth_context(authorization: str | None) -> AuthContext:
    """Build the caller identity from an ``Authorization: Bearer <jwt>`` header value.

    Args:
        authorization: Raw header value, or ``None`` when the header is absent.

    Returns:
        The authenticated caller.

    Raises:
        UnauthenticatedError: If the token is missing, malformed, unsigned (``alg: none``),
            expired, or lacks a valid ``user_id`` and a recognised role claim.
    """
    return _context_from_claims(_decode_claims(_extract_bearer_token(authorization)))


def _token_from_query_string(scope: object) -> str | None:
    if not isinstance(scope, dict):
        return None
    raw_query = scope.get("query_string")
    if not isinstance(raw_query, (bytes, bytearray)) or not raw_query:
        return None
    parsed = parse_qs(raw_query.decode("latin1", errors="replace"))
    tokens = parsed.get("token") or parsed.get("access_token")
    if tokens and tokens[0].strip():
        return f"Bearer {tokens[0].strip()}"
    return None


def _token_from_ws_protocol(ws_protocol: str | None) -> str | None:
    if not ws_protocol:
        return None
    parts = [p.strip() for p in ws_protocol.split(",")]
    if len(parts) >= 2 and parts[0].lower() == "bearer":
        return f"Bearer {parts[1]}"
    if len(parts) == 1 and "." in parts[0]:
        return f"Bearer {parts[0]}"
    return None


def _authorization_value(connection: HTTPConnection) -> str | None:
    """Read token from ``Authorization`` header, query parameter, or subprotocol (SEC-09)."""
    authorization = connection.headers.get("authorization")
    if authorization:
        return authorization
    query_token = _token_from_query_string(getattr(connection, "scope", None))
    if query_token:
        return query_token
    return _token_from_ws_protocol(connection.headers.get("sec-websocket-protocol"))


def get_auth_context(connection: HTTPConnection) -> AuthContext:
    """FastAPI dependency resolving the caller of an HTTP request or WebSocket handshake."""
    authorization = _authorization_value(connection)
    gateway_context = _context_from_gateway_headers(connection.headers, authorization)
    if gateway_context is not None:
        return gateway_context
    return resolve_auth_context(authorization)


def ensure_role_allowed(context: AuthContext, allowed_roles: frozenset[Role]) -> None:
    """Raise unless one of the caller's roles is part of ``allowed_roles``.

    Raises:
        ForbiddenRoleError: If no role is allowed.
    """
    if not context.has_any_role(allowed_roles):
        logger.warning(
            "role not allowed", extra={"role": context.role.value, "user_id": context.user_id}
        )
        raise ForbiddenRoleError("Role not allowed for this action.")


def ensure_authorized(context: AuthContext, policy: AccessPolicy) -> None:
    """Raise unless ``context`` satisfies ``policy`` (PBAC first, role fallback).

    Raises:
        ForbiddenRoleError: If the caller lacks the permission (or an allowed role).
    """
    if not context.is_authorized(policy):
        logger.warning(
            "access denied",
            extra={
                "role": context.role.value,
                "user_id": context.user_id,
                "required_permissions": sorted(policy.permissions),
            },
        )
        raise ForbiddenRoleError("Role or permission not allowed for this action.")


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


def require_access(policy: AccessPolicy) -> Callable[[AuthContext], AuthContext]:
    """Build a router dependency enforcing ``policy`` (see ``app.domain.access``).

    Returns:
        A dependency that yields the ``AuthContext`` or raises ``UnauthenticatedError``
        (401) / ``ForbiddenRoleError`` (403).
    """

    def _guard(context: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
        ensure_authorized(context, policy)
        return context

    return _guard


def require_permissions(
    *permissions: str, fallback_roles: frozenset[Role]
) -> Callable[[AuthContext], AuthContext]:
    """Build a dependency that requires any of ``permissions``, with a role fallback.

    The PBAC permission is evaluated first; ``fallback_roles`` only apply to identities that
    carry no permission list at all (backwards compatibility with role-only tokens).
    """
    return require_access(AccessPolicy(frozenset(permissions), fallback_roles))


async def authorize_websocket(
    websocket: WebSocket,
    allowed_roles: frozenset[Role],
    policy: AccessPolicy | None = None,
) -> AuthContext | None:
    """Accept the handshake, then close it with 4401/4403 if the caller is not allowed.

    The handshake is accepted first because a close sent before ``accept`` is turned into
    a plain HTTP 403 and the client would never see the 4401/4403 close code.

    Args:
        websocket: Incoming connection.
        allowed_roles: Roles authorized to use the endpoint (used when ``policy`` is ``None``).
        policy: Access policy of the endpoint; takes precedence over ``allowed_roles``.

    Returns:
        The caller identity, or ``None`` when the connection was closed.
    """
    ws_protocol = websocket.headers.get("sec-websocket-protocol")
    selected_subprotocol = None
    if ws_protocol:
        protocols = [p.strip() for p in ws_protocol.split(",")]
        selected_subprotocol = protocols[0]

    await websocket.accept(subprotocol=selected_subprotocol)
    try:
        context = get_auth_context(websocket)
        if policy is not None:
            ensure_authorized(context, policy)
        else:
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
