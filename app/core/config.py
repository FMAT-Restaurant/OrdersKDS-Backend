"""Application runtime configuration and environment variable loading.

Satisfies: RNF-01, OP-10
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class RabbitMQSettings:
    """RabbitMQ connection and topology settings.

    Attributes:
        url: Full AMQP connection URL.
        exchange: Name of the durable topic exchange.
        vhost: Target virtual host.
    """

    url: str
    exchange: str
    vhost: str


@dataclass(frozen=True)
class OutboxWorkerSettings:
    """Outbox worker scheduling and retry settings.

    Attributes:
        poll_interval_seconds: Polling interval in seconds.
        batch_size: Maximum rows to process in each tick.
    """

    poll_interval_seconds: float = 1.0
    batch_size: int = 100


@dataclass(frozen=True)
class CatalogReconciliationSettings:
    """Optional Menu catalog bootstrap and reconciliation settings."""

    sync_url: str | None
    interval_seconds: int | None


DEFAULT_SAGA_WATCHER_INTERVAL_SECONDS = 5.0

ALLOWED_JWT_ALGORITHMS: Final[frozenset[str]] = frozenset(
    {"HS256", "HS384", "HS512", "RS256", "ES256"}
)
DEFAULT_JWT_ALGORITHMS: Final[tuple[str, ...]] = ("HS256", "HS384", "HS512", "RS256", "ES256")
DEFAULT_CORS_ALLOWED_ORIGINS: Final[tuple[str, ...]] = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5176",
    "http://127.0.0.1:5176",
    "http://localhost:5177",
    "http://127.0.0.1:5177",
    "http://localhost:5178",
    "http://127.0.0.1:5178",
    "http://localhost:5179",
    "http://127.0.0.1:5179",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
)
_TRUTHY_ENV_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def get_cors_allowed_origins() -> list[str]:
    """Parse explicit CORS origin allowlist, never permitting '*' with credentials."""
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return list(DEFAULT_CORS_ALLOWED_ORIGINS)
    origins: list[str] = []
    for candidate in raw.split(","):
        cleaned = candidate.strip().rstrip("/")
        if not cleaned or cleaned == "*":
            continue
        if cleaned not in origins:
            origins.append(cleaned)
    return origins or list(DEFAULT_CORS_ALLOWED_ORIGINS)


@dataclass(frozen=True)
class AuthSettings:
    """Authentication and gateway perimeter configuration.

    Attributes:
        jwt_secret: Shared HMAC secret for ``HS256``/``HS384``/``HS512`` tokens.
        jwt_public_key: PEM public key for ``RS256``/``ES256`` tokens.
        jwt_algorithms: Explicit allowlist of permitted signing algorithms.
        trust_gateway_headers: When True, requests without a Bearer token may rely
            on perimeter ``X-User-*`` headers (optionally guarded by ``gateway_shared_secret``).
        gateway_shared_secret: Optional shared secret expected in ``X-Gateway-Secret``
            when trusting perimeter ``X-User-*`` headers without a Bearer token.
    """

    jwt_secret: str | None
    jwt_public_key: str | None
    jwt_algorithms: tuple[str, ...]
    trust_gateway_headers: bool = False
    gateway_shared_secret: str | None = None
    enforce_waiter_ownership: bool = True


def _parse_jwt_algorithms(raw: str | None) -> tuple[str, ...]:
    """Parse configured JWT algorithms strictly intersecting the safe allowlist."""
    if not raw or not raw.strip():
        return DEFAULT_JWT_ALGORITHMS
    parsed = tuple(
        alg
        for item in raw.split(",")
        if (alg := item.strip().upper()) and alg in ALLOWED_JWT_ALGORITHMS
    )
    return parsed


def get_auth_settings() -> AuthSettings:
    """Load JWT verification and gateway-header trust settings from environment."""
    raw_secret = os.getenv("JWT_SECRET", "").strip()
    raw_public_key = os.getenv("JWT_PUBLIC_KEY", "").replace("\\n", "\n").strip()
    raw_gateway_secret = os.getenv("GATEWAY_SHARED_SECRET", "").strip()
    raw_trust_gateway = os.getenv("TRUST_GATEWAY_HEADERS", "false").strip().lower()
    raw_waiter_ownership = os.getenv("ENFORCE_WAITER_ORDER_OWNERSHIP", "true").strip().lower()
    return AuthSettings(
        jwt_secret=raw_secret or None,
        jwt_public_key=raw_public_key or None,
        jwt_algorithms=_parse_jwt_algorithms(os.getenv("JWT_ALGORITHMS")),
        trust_gateway_headers=raw_trust_gateway in _TRUTHY_ENV_VALUES,
        gateway_shared_secret=raw_gateway_secret or None,
        enforce_waiter_ownership=raw_waiter_ownership in _TRUTHY_ENV_VALUES,
    )


@dataclass(frozen=True)
class SagaTimeoutSettings:
    """Saga Timeout Watcher configuration (OP-01 / ADR-0001).

    Attributes:
        timeout_seconds: Maximum seconds a confirmed order may stay in
            ``CREATED`` waiting for Inventory before it expires.
        watcher_interval_seconds: Delay between scans of stale orders.
    """

    timeout_seconds: int
    watcher_interval_seconds: float = DEFAULT_SAGA_WATCHER_INTERVAL_SECONDS


def get_redis_url() -> str:
    """Return the Redis URL used by the catalog projection."""
    return os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()


def get_catalog_reconciliation_settings() -> CatalogReconciliationSettings:
    """Load optional catalog synchronization settings without a frequency default.

    A configured URL always enables one startup bootstrap. Periodic
    reconciliation is enabled only when its explicit interval is provided.
    """
    raw_url = os.getenv("CATALOG_SYNC_URL", "").strip()
    sync_url = raw_url or None
    raw_interval = os.getenv("CATALOG_RECONCILIATION_INTERVAL_SECONDS", "").strip()
    interval: int | None = None
    if raw_interval:
        try:
            interval = int(raw_interval)
        except ValueError as exc:
            raise RuntimeError(
                "Invalid CATALOG_RECONCILIATION_INTERVAL_SECONDS: "
                f"expected a positive integer, got {raw_interval!r}"
            ) from exc
        if interval <= 0:
            raise RuntimeError(
                "Invalid CATALOG_RECONCILIATION_INTERVAL_SECONDS: "
                f"expected a positive integer, got {interval}"
            )
        if sync_url is None:
            raise RuntimeError(
                "CATALOG_SYNC_URL is required when "
                "CATALOG_RECONCILIATION_INTERVAL_SECONDS is configured"
            )
    return CatalogReconciliationSettings(
        sync_url=sync_url,
        interval_seconds=interval,
    )


def _get_required_env(key: str) -> str:
    """Read a required environment variable or raise an error.

    Args:
        key: The environment variable name.

    Returns:
        The non-empty string value.

    Raises:
        RuntimeError: If the variable is unset or empty.
    """
    value = os.getenv(key)
    if not value or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value.strip()


def get_rabbitmq_settings() -> RabbitMQSettings:
    """Load RabbitMQ topology settings without fallback defaults.

    Returns:
        Validated RabbitMQSettings instance.

    Raises:
        RuntimeError: If URL, exchange or vhost is missing.
    """
    return RabbitMQSettings(
        url=_get_required_env("RABBITMQ_URL"),
        exchange=_get_required_env("RABBITMQ_EXCHANGE"),
        vhost=_get_required_env("RABBITMQ_VHOST"),
    )


def get_outbox_worker_settings() -> OutboxWorkerSettings:
    """Load settings for the outbox worker loop.

    Returns:
        Configured OutboxWorkerSettings.
    """
    raw_interval = os.getenv("OUTBOX_POLL_INTERVAL_SECONDS", "1")
    try:
        interval = float(raw_interval)
    except ValueError:
        interval = 1.0

    raw_batch = os.getenv("OUTBOX_BATCH_SIZE", "100")
    try:
        batch = int(raw_batch)
    except ValueError:
        batch = 100

    return OutboxWorkerSettings(
        poll_interval_seconds=interval,
        batch_size=batch,
    )


def get_saga_timeout_settings() -> SagaTimeoutSettings:
    """Load Saga Timeout Watcher settings.

    ``SAGA_TIMEOUT_SECONDS`` is required and has no code default: starting the
    worker without it must fail fast (OP-01 / ADR-0001).

    Returns:
        Validated SagaTimeoutSettings instance.

    Raises:
        RuntimeError: If ``SAGA_TIMEOUT_SECONDS`` is missing, non-numeric or
            not strictly positive.
    """
    raw_timeout = _get_required_env("SAGA_TIMEOUT_SECONDS")
    try:
        timeout = int(raw_timeout)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid SAGA_TIMEOUT_SECONDS: expected an integer, got {raw_timeout!r}"
        ) from exc
    if timeout <= 0:
        raise RuntimeError(
            f"Invalid SAGA_TIMEOUT_SECONDS: expected a positive integer, got {timeout}"
        )

    raw_interval = os.getenv("SAGA_WATCHER_INTERVAL_SECONDS", "").strip()
    if raw_interval:
        try:
            interval = float(raw_interval)
        except ValueError:
            interval = DEFAULT_SAGA_WATCHER_INTERVAL_SECONDS
    else:
        interval = DEFAULT_SAGA_WATCHER_INTERVAL_SECONDS
    if interval <= 0:
        interval = DEFAULT_SAGA_WATCHER_INTERVAL_SECONDS

    return SagaTimeoutSettings(
        timeout_seconds=timeout,
        watcher_interval_seconds=interval,
    )
