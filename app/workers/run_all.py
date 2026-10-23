"""Entrypoint to run all background workers for Orders & KDS.

Usage:
    python -m app.workers.run_all

Satisfies: RNF-01, RNF-06, RNF-12
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_outbox_worker_settings, get_rabbitmq_settings
from app.messaging.publisher import EventPublisher
from app.workers.outbox_worker import OutboxWorker

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app.workers")


def _get_database_url() -> str:
    """Read database connection URL or fail fast."""
    url = os.getenv("DATABASE_URL")
    if not url or not url.strip():
        raise RuntimeError("DATABASE_URL environment variable is required")
    return url.strip()


async def main() -> None:
    """Bootstrap connections and run background workers until stopped."""
    logger.info("Starting Orders & KDS background workers...")
    db_url = _get_database_url()
    rabbitmq_settings = get_rabbitmq_settings()
    worker_settings = get_outbox_worker_settings()

    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    publisher = EventPublisher(
        url=rabbitmq_settings.url,
        exchange_name=rabbitmq_settings.exchange,
        vhost=rabbitmq_settings.vhost,
    )

    stop_event = asyncio.Event()

    def _on_signal(*_: object) -> None:
        logger.info("Shutdown signal received, stopping workers...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except RuntimeError:
            # Signal handlers via loop are not implemented on Windows
            signal.signal(sig, lambda *_: _on_signal())

    try:
        await publisher.connect()
        logger.info("Connected to RabbitMQ and declared exchange '%s'", rabbitmq_settings.exchange)

        worker = OutboxWorker(
            session_factory=session_factory,
            publisher=publisher,
            poll_interval_seconds=worker_settings.poll_interval_seconds,
        )

        await worker.run(stop_event=stop_event)
    finally:
        logger.info("Cleaning up worker resources...")
        await publisher.close()
        await engine.dispose()
        logger.info("Workers shut down successfully.")


if __name__ == "__main__":  # pragma: no cover
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Workers stopped by KeyboardInterrupt.")
        sys.exit(0)
