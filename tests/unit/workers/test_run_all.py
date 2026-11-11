"""Unit tests for the workers supervisor (run_all.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import (
    CatalogReconciliationSettings,
    OutboxWorkerSettings,
    RabbitMQSettings,
    SagaTimeoutSettings,
)
from app.workers import run_all


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_all_main_spawns_catalog_worker_when_configured() -> None:
    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()

    mock_publisher = MagicMock()
    mock_publisher.connect = AsyncMock()
    mock_publisher.close = AsyncMock()

    mock_outbox_worker = MagicMock()
    mock_outbox_worker.run = AsyncMock(return_value=None)

    mock_saga_watcher = MagicMock()
    mock_saga_watcher.run = AsyncMock(return_value=None)

    mock_catalog_worker = MagicMock()
    mock_catalog_worker.run = AsyncMock(return_value=None)

    mock_redis = MagicMock()
    mock_redis.aclose = AsyncMock()

    with (
        patch(
            "app.workers.run_all._get_database_url",
            return_value="postgresql+asyncpg://user:pass@localhost/db",
        ),
        patch(
            "app.workers.run_all.get_rabbitmq_settings",
            return_value=RabbitMQSettings(url="amqp://localhost", exchange="test", vhost="/"),
        ),
        patch(
            "app.workers.run_all.get_outbox_worker_settings",
            return_value=OutboxWorkerSettings(poll_interval_seconds=1.0, batch_size=10),
        ),
        patch(
            "app.workers.run_all.get_saga_timeout_settings",
            return_value=SagaTimeoutSettings(timeout_seconds=60, watcher_interval_seconds=1.0),
        ),
        patch(
            "app.workers.run_all.get_catalog_reconciliation_settings",
            return_value=CatalogReconciliationSettings(
                sync_url="http://menu.example/menu/catalogo/completo",
                interval_seconds=30,
            ),
        ),
        patch("app.workers.run_all.create_async_engine", return_value=mock_engine),
        patch("app.workers.run_all.EventPublisher", return_value=mock_publisher),
        patch("app.workers.run_all.OutboxWorker", return_value=mock_outbox_worker),
        patch("app.workers.run_all.SagaTimeoutWatcher", return_value=mock_saga_watcher),
        patch("app.workers.run_all.CatalogReconciliationWorker", return_value=mock_catalog_worker),
        patch("app.workers.run_all.Redis.from_url", return_value=mock_redis),
        patch("app.workers.run_all.get_redis_url", return_value="redis://localhost:6379/0"),
    ):
        await run_all.main()

    mock_publisher.connect.assert_awaited_once()
    mock_outbox_worker.run.assert_called_once()
    mock_saga_watcher.run.assert_called_once()
    mock_catalog_worker.run.assert_called_once()
    mock_redis.aclose.assert_awaited_once()
    mock_publisher.close.assert_awaited_once()
    mock_engine.dispose.assert_awaited_once()
