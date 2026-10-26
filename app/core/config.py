"""Application runtime configuration and environment variable loading.

Satisfies: RNF-01, OP-10
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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
