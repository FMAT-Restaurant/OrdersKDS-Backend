"""Unit tests for runtime configuration loading.

Satisfies: OP-10, RNF-01
"""

from __future__ import annotations

import pytest

from app.core.config import (
    DEFAULT_SAGA_WATCHER_INTERVAL_SECONDS,
    get_catalog_reconciliation_settings,
    get_outbox_worker_settings,
    get_rabbitmq_settings,
    get_saga_timeout_settings,
)


@pytest.mark.unit
@pytest.mark.requirement("OP-10", "RNF-01")
def test_rabbitmq_settings_fails_when_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    monkeypatch.setenv("RABBITMQ_EXCHANGE", "ordenes.topic")
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
    monkeypatch.setenv("RABBITMQ_EXCHANGE", "ordenes.topic")
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
    monkeypatch.setenv("RABBITMQ_EXCHANGE", "ordenes.topic")
    monkeypatch.setenv("RABBITMQ_VHOST", "/staging")

    settings = get_rabbitmq_settings()
    assert settings.url == "amqp://ordenes:ordenes@localhost:5672/"
    assert settings.exchange == "ordenes.topic"
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


@pytest.mark.unit
def test_catalog_reconciliation_settings_have_no_interval_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CATALOG_SYNC_URL", raising=False)
    monkeypatch.delenv("CATALOG_RECONCILIATION_INTERVAL_SECONDS", raising=False)

    settings = get_catalog_reconciliation_settings()

    assert settings.sync_url is None
    assert settings.interval_seconds is None


@pytest.mark.unit
@pytest.mark.parametrize("raw_interval", ["invalid", "0", "-5"])
def test_catalog_reconciliation_settings_reject_invalid_interval(
    monkeypatch: pytest.MonkeyPatch,
    raw_interval: str,
) -> None:
    monkeypatch.setenv("CATALOG_SYNC_URL", "http://menu.example/menu/catalogo/completo")
    monkeypatch.setenv("CATALOG_RECONCILIATION_INTERVAL_SECONDS", raw_interval)

    with pytest.raises(RuntimeError, match="Invalid CATALOG_RECONCILIATION_INTERVAL_SECONDS"):
        get_catalog_reconciliation_settings()


@pytest.mark.unit
def test_catalog_reconciliation_settings_requires_url_for_periodic_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CATALOG_SYNC_URL", raising=False)
    monkeypatch.setenv("CATALOG_RECONCILIATION_INTERVAL_SECONDS", "30")

    with pytest.raises(RuntimeError, match="CATALOG_SYNC_URL is required"):
        get_catalog_reconciliation_settings()


@pytest.mark.unit
def test_catalog_reconciliation_settings_loads_explicit_frequency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CATALOG_SYNC_URL", "http://menu.example/menu/catalogo/completo")
    monkeypatch.setenv("CATALOG_RECONCILIATION_INTERVAL_SECONDS", "30")

    settings = get_catalog_reconciliation_settings()

    assert settings.sync_url == "http://menu.example/menu/catalogo/completo"
    assert settings.interval_seconds == 30


@pytest.mark.unit
def test_catalog_url_without_interval_enables_only_startup_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CATALOG_SYNC_URL", "http://menu.example/menu/catalogo/completo")
    monkeypatch.delenv("CATALOG_RECONCILIATION_INTERVAL_SECONDS", raising=False)

    settings = get_catalog_reconciliation_settings()

    assert settings.sync_url == "http://menu.example/menu/catalogo/completo"
    assert settings.interval_seconds is None


@pytest.mark.unit
@pytest.mark.requirement("OP-01", "RNF-01")
def test_saga_timeout_settings_fails_when_variable_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SAGA_TIMEOUT_SECONDS", raising=False)

    with pytest.raises(
        RuntimeError,
        match="Missing required environment variable: SAGA_TIMEOUT_SECONDS",
    ):
        get_saga_timeout_settings()


@pytest.mark.unit
@pytest.mark.requirement("OP-01", "RNF-01")
@pytest.mark.parametrize("raw_timeout", ["invalid", "0", "-10"])
def test_saga_timeout_settings_rejects_invalid_timeout(
    monkeypatch: pytest.MonkeyPatch,
    raw_timeout: str,
) -> None:
    monkeypatch.setenv("SAGA_TIMEOUT_SECONDS", raw_timeout)

    with pytest.raises(RuntimeError, match="Invalid SAGA_TIMEOUT_SECONDS"):
        get_saga_timeout_settings()


@pytest.mark.unit
@pytest.mark.requirement("OP-01", "RNF-01")
def test_saga_timeout_settings_uses_interval_default_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAGA_TIMEOUT_SECONDS", "60")
    monkeypatch.delenv("SAGA_WATCHER_INTERVAL_SECONDS", raising=False)

    defaults = get_saga_timeout_settings()
    assert defaults.timeout_seconds == 60
    assert defaults.watcher_interval_seconds == DEFAULT_SAGA_WATCHER_INTERVAL_SECONDS

    monkeypatch.setenv("SAGA_WATCHER_INTERVAL_SECONDS", "2.5")
    custom = get_saga_timeout_settings()
    assert custom.watcher_interval_seconds == 2.5

    monkeypatch.setenv("SAGA_WATCHER_INTERVAL_SECONDS", "not-a-number")
    fallback = get_saga_timeout_settings()
    assert fallback.watcher_interval_seconds == DEFAULT_SAGA_WATCHER_INTERVAL_SECONDS

    monkeypatch.setenv("SAGA_WATCHER_INTERVAL_SECONDS", "0")
    non_positive = get_saga_timeout_settings()
    assert non_positive.watcher_interval_seconds == DEFAULT_SAGA_WATCHER_INTERVAL_SECONDS
