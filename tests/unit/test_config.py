"""Unit tests for runtime configuration loading.

Satisfies: OP-10, RNF-01
"""

from __future__ import annotations

import pytest

from app.core.config import get_outbox_worker_settings, get_rabbitmq_settings


@pytest.mark.unit
@pytest.mark.requirement("OP-10", "RNF-01")
def test_rabbitmq_settings_fails_when_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    monkeypatch.setenv("RABBITMQ_EXCHANGE", "restaurant.topic")
    monkeypatch.setenv("RABBITMQ_VHOST", "/")

    with pytest.raises(RuntimeError, match="Missing required environment variable: RABBITMQ_URL"):
        get_rabbitmq_settings()


@pytest.mark.unit
@pytest.mark.requirement("OP-10", "RNF-01")
def test_rabbitmq_settings_fails_when_exchange_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RABBITMQ_URL", "amqp://user:pass@localhost:5672/")
    monkeypatch.delenv("RABBITMQ_EXCHANGE", raising=False)
    monkeypatch.setenv("RABBITMQ_VHOST", "/")

    with pytest.raises(
        RuntimeError, match="Missing required environment variable: RABBITMQ_EXCHANGE"
    ):
        get_rabbitmq_settings()


@pytest.mark.unit
@pytest.mark.requirement("OP-10", "RNF-01")
def test_rabbitmq_settings_fails_when_vhost_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RABBITMQ_URL", "amqp://user:pass@localhost:5672/")
    monkeypatch.setenv("RABBITMQ_EXCHANGE", "restaurant.topic")
    monkeypatch.delenv("RABBITMQ_VHOST", raising=False)

    with pytest.raises(
        RuntimeError, match="Missing required environment variable: RABBITMQ_VHOST"
    ):
        get_rabbitmq_settings()


@pytest.mark.unit
@pytest.mark.requirement("OP-10", "RNF-01")
def test_rabbitmq_settings_succeeds_when_all_variables_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RABBITMQ_URL", "amqp://ordenes:ordenes@localhost:5672/")
    monkeypatch.setenv("RABBITMQ_EXCHANGE", "restaurant.topic")
    monkeypatch.setenv("RABBITMQ_VHOST", "/staging")

    settings = get_rabbitmq_settings()
    assert settings.url == "amqp://ordenes:ordenes@localhost:5672/"
    assert settings.exchange == "restaurant.topic"
    assert settings.vhost == "/staging"


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
def test_outbox_worker_settings_defaults_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OUTBOX_POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("OUTBOX_BATCH_SIZE", raising=False)
    defaults = get_outbox_worker_settings()
    assert defaults.poll_interval_seconds == 1.0
    assert defaults.batch_size == 100

    monkeypatch.setenv("OUTBOX_POLL_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("OUTBOX_BATCH_SIZE", "50")
    custom = get_outbox_worker_settings()
    assert custom.poll_interval_seconds == 2.5
    assert custom.batch_size == 50

    # Test invalid string fallbacks
    monkeypatch.setenv("OUTBOX_POLL_INTERVAL_SECONDS", "invalid_float")
    monkeypatch.setenv("OUTBOX_BATCH_SIZE", "invalid_int")
    fallback = get_outbox_worker_settings()
    assert fallback.poll_interval_seconds == 1.0
    assert fallback.batch_size == 100
