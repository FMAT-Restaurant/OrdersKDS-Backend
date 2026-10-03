"""Development JWT token generator.

Generates short-lived JWTs that simulate the tokens the Auth microservice
would issue after a successful login.  These tokens are only valid for local
development and manual testing — they are NOT a replacement for the real Auth
service.

IMPORTANT — security context
------------------------------
Authentication is handled by the Auth microservice; this backend only reads
``user_id`` and ``role`` from the JWT claims (DEVELOPMENT_GUIDELINES §5.7).
This script exists solely to let developers call the local API without running
the full Auth service.

The signing key (``--secret``) must match whatever the API Gateway is
configured to use for verification.  In local development both this script and
the FastAPI dependency in ``app/api/dependencies/auth.py`` should use the same
shared secret.  In production the API Gateway holds the real key; this script
is never used there.

Usage:
    python scripts/dev_token.py --role waiter
    python scripts/dev_token.py --role kitchen --user-id 00000000-0000-0000-0000-000000000002
    python scripts/dev_token.py --role admin --ttl 3600 --secret my-dev-secret

Roles understood by this service (DEVELOPMENT_GUIDELINES §2.4):
    waiter    — takes / edits / cancels orders, requests bill
    kitchen   — reads KDS queue, marks dishes ready
    chef      — same as kitchen plus void-kitchen-error and intermediate dishes
    admin     — full access including administrative void (WASTED)

Dependencies:
    pip install pyjwt  (already in requirements/dev.txt)

NOTE: PyJWT is imported lazily so that this script does not fail at import time
if the package is not installed; it prints a friendly error instead.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any


# ---------------------------------------------------------------------------
# Allowed roles match the Auth microservice contract (GUIDELINES §2.4).
# ---------------------------------------------------------------------------
_VALID_ROLES: frozenset[str] = frozenset({"waiter", "kitchen", "chef", "admin"})

# Default signing secret for local development ONLY.
# This value must never be used in any environment beyond localhost.
# Replace it with the value agreed by your team or pass --secret explicitly.
_DEFAULT_DEV_SECRET = "dev-secret-change-me"

# Default token lifetime in seconds (1 hour is enough for a dev session).
_DEFAULT_TTL_SECONDS = 3600


def _build_payload(
    role: str,
    user_id: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Build the JWT claims payload.

    The claim names mirror what the Auth microservice is expected to emit so
    that ``app/api/dependencies/auth.py`` can read them without special-casing
    dev tokens.

    Args:
        role: One of the four valid roles (waiter, kitchen, chef, admin).
        user_id: UUID string that identifies the actor.
        ttl_seconds: Token lifetime in seconds from now.

    Returns:
        A dict suitable for ``jwt.encode``.
    """
    now = datetime.now(UTC)
    return {
        # Standard JWT claims
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        # Application-specific claims read by app/api/dependencies/auth.py
        "role": role,
        "user_id": user_id,
    }


def _generate_token(
    role: str,
    user_id: str,
    ttl_seconds: int,
    secret: str,
) -> str:
    """Sign and return the JWT string.

    Args:
        role: Actor role.
        user_id: Actor UUID.
        ttl_seconds: Token lifetime.
        secret: HMAC-SHA256 signing key.

    Returns:
        A signed JWT string.

    Raises:
        SystemExit: If PyJWT is not installed.
    """
    try:
        # pyrefly: ignore [missing-import]
        import jwt  # noqa: PLC0415 — lazy import to give a clear error message
    except ImportError:
        sys.exit(
            "ERROR: PyJWT is not installed.\n"
            "  pip install pyjwt\n"
            "or:\n"
            "  pip install -r requirements/dev.txt"
        )

    payload = _build_payload(role=role, user_id=user_id, ttl_seconds=ttl_seconds)
    return jwt.encode(payload, secret, algorithm="HS256")  # type: ignore[no-untyped-call]


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate a dev JWT for local API testing.\n\n"
            "The token simulates what the Auth microservice issues.  "
            "It is only valid for local development."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--role",
        required=True,
        choices=sorted(_VALID_ROLES),
        help="Role claim embedded in the token.",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        metavar="UUID",
        help=(
            "UUID to use as user_id / sub.  "
            "A random UUID is generated if omitted."
        ),
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=_DEFAULT_TTL_SECONDS,
        metavar="SECONDS",
        help=f"Token lifetime in seconds (default: {_DEFAULT_TTL_SECONDS}).",
    )
    parser.add_argument(
        "--secret",
        default=_DEFAULT_DEV_SECRET,
        metavar="KEY",
        help=(
            "HMAC-SHA256 signing key.  "
            "Must match the key configured in the API dependency.  "
            f"Default: '{_DEFAULT_DEV_SECRET}' — only safe on localhost."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Entry point for the dev token generator."""
    args = _parse_args()

    user_id = args.user_id or str(uuid.uuid4())
    token = _generate_token(
        role=args.role,
        user_id=user_id,
        ttl_seconds=args.ttl,
        secret=args.secret,
    )

    expiry = datetime.now(UTC) + timedelta(seconds=args.ttl)
    print(f"role     : {args.role}")
    print(f"user_id  : {user_id}")
    print(f"expires  : {expiry.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"\nBearer {token}")
    print(
        "\nUsage example:\n"
        f"  curl -H 'Authorization: Bearer {token}' http://localhost:8000/orders"
    )


if __name__ == "__main__":
    main()
