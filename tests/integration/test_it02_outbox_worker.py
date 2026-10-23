"""Integration test IT-02: End-to-end Outbox worker publishing to RabbitMQ.

Satisfies: RNF-01, RNF-13, RF-10, RF-19
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import aio_pika
import docker
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from testcontainers.core.container import DockerContainer

from app.db.base import Base
from app.db.models import Outbox
from app.messaging.events import EventName, OrderCreatedEvent
from app.messaging.publisher import EventPublisher
from app.messaging.validator import validate_event_payload
from app.repositories.outbox import OutboxRepository
from app.workers.outbox_worker import OutboxWorker


def _is_docker_daemon_running() -> bool:
    """Return True if Docker is available to launch Testcontainers."""
    try:
        client = docker.from_env()
        return bool(client.ping())
    except Exception:
        return False


async def _wait_for_postgres_ready(engine: AsyncEngine, max_attempts: int = 30) -> None:
    """Active readiness probe polling until PostgreSQL accepts queries."""
    for _ in range(max_attempts):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                return
        except Exception:
            await asyncio.sleep(0.5)
    raise TimeoutError("PostgreSQL container did not become ready in time")


async def _wait_for_rabbitmq_ready(url: str, max_attempts: int = 30) -> None:
    """Active readiness probe polling until RabbitMQ accepts AMQP connections."""
    for _ in range(max_attempts):
        try:
            conn = await aio_pika.connect_robust(url)
            await conn.close()
            return
        except Exception:
            await asyncio.sleep(0.5)
    raise TimeoutError("RabbitMQ container did not become ready in time")


@pytest.mark.integration
@pytest.mark.requirement("RNF-01", "RNF-13", "RF-10", "RF-19")
@pytest.mark.asyncio
async def test_outbox_worker_publishes_to_real_broker_with_correct_routing_key() -> None:
    """IT-02: Worker publishes from DB to real RabbitMQ, validates schema and sets published_at."""
    if not _is_docker_daemon_running():
        pytest.skip("Docker daemon is not running; skipping integration test IT-02.")

    # 1. Start RabbitMQ and PostgreSQL containers
    with (
        DockerContainer("rabbitmq:3-alpine").with_exposed_ports(5672) as rmq_container,
        DockerContainer("postgres:16-alpine")
        .with_env("POSTGRES_USER", "test_user")
        .with_env("POSTGRES_PASSWORD", "test_pass")
        .with_env("POSTGRES_DB", "test_orders")
        .with_exposed_ports(5432) as pg_container,
    ):
        rmq_host = rmq_container.get_container_host_ip()
        rmq_port = rmq_container.get_exposed_port(5672)
        rmq_url = f"amqp://guest:guest@{rmq_host}:{rmq_port}/"

        pg_host = pg_container.get_container_host_ip()
        pg_port = pg_container.get_exposed_port(5432)
        db_url = f"postgresql+asyncpg://test_user:test_pass@{pg_host}:{pg_port}/test_orders"

        # 2. Wait actively for readiness of both services
        engine = create_async_engine(db_url, echo=False)
        await _wait_for_postgres_ready(engine)
        await _wait_for_rabbitmq_ready(rmq_url)

        # 3. Setup database schema and stage an event in outbox
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        order_id = uuid4()
        event = OrderCreatedEvent.for_order(order_id)

        async with session_factory() as session:
            outbox_repo = OutboxRepository(session)
            staged = outbox_repo.add(event)
            await session.commit()
            staged_event_id = staged.event_id

        # 4. Setup RabbitMQ topology and test queue binding
        exchange_name = "test.restaurant.topic"
        publisher = EventPublisher(url=rmq_url, exchange_name=exchange_name, vhost="/")
        await publisher.connect()

        # Bind test consumer queue to receive the published event
        queue_name = "integration.test.orders.queue"
        await publisher.declare_queue_binding(
            queue_name=queue_name,
            routing_key=EventName.ORDER_CREATED,
        )

        # 5. Run outbox worker process_batch
        worker = OutboxWorker(session_factory=session_factory, publisher=publisher)
        count = await worker.process_batch(limit=10)
        assert count == 1

        # 6. Verify database outbox row has published_at populated
        async with session_factory() as session:
            db_row = await session.scalar(select(Outbox).where(Outbox.event_id == staged_event_id))
            assert db_row is not None
            assert db_row.published_at is not None

        # 7. Consume from RabbitMQ queue and verify message properties and payload
        connection = await aio_pika.connect_robust(rmq_url)
        channel = await connection.channel()
        queue = await channel.get_queue(queue_name)

        incoming_message = await queue.get(timeout=10.0)
        assert incoming_message is not None
        assert incoming_message.routing_key == EventName.ORDER_CREATED
        assert incoming_message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT

        payload = json.loads(incoming_message.body.decode("utf-8"))
        assert payload["eventId"] == str(staged_event_id)
        assert payload["orderId"] == str(order_id)

        # Validate against JSON Schema contract
        validate_event_payload(EventName.ORDER_CREATED, payload)

        await incoming_message.ack()
        await connection.close()
        await publisher.close()
        await engine.dispose()
