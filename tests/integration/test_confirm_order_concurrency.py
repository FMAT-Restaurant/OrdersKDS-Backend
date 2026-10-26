"""Integration coverage for concurrent order confirmation.

Satisfies: RF-32, RNF-01, RNF-09
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import docker
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from testcontainers.core.container import DockerContainer

from app.db.base import Base
from app.db.models import Order, OrderItem, Outbox
from app.domain.order_status import OrderStatus
from app.services.orders import confirm_order


def _is_docker_daemon_running() -> bool:
    """Return whether Docker can run the PostgreSQL integration dependency."""
    try:
        return bool(docker.from_env().ping())
    except docker.errors.DockerException:
        return False


async def _wait_for_postgres(engine: AsyncEngine) -> None:
    """Wait until PostgreSQL accepts queries."""
    for _ in range(30):
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return
        except SQLAlchemyError:
            await asyncio.sleep(0.5)
    raise TimeoutError("PostgreSQL container did not become ready in time")


@pytest.mark.integration
@pytest.mark.requirement("RF-32", "RNF-01", "RNF-09")
@pytest.mark.asyncio
async def test_concurrent_confirmations_stage_only_one_order_created_event() -> None:
    """Serialize simultaneous confirmations and persist exactly one outbox event."""
    if not _is_docker_daemon_running():
        pytest.skip("Docker daemon is not running; skipping concurrent confirmation test.")

    with (
        DockerContainer("postgres:16-alpine")
        .with_env("POSTGRES_USER", "test_user")
        .with_env("POSTGRES_PASSWORD", "test_pass")
        .with_env("POSTGRES_DB", "test_orders")
        .with_exposed_ports(5432) as container
    ):
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        engine = create_async_engine(
            f"postgresql+asyncpg://test_user:test_pass@{host}:{port}/test_orders",
            echo=False,
        )
        try:
            await _wait_for_postgres(engine)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            order_id = uuid4()
            async with session_factory() as session:
                order = Order(public_id=order_id, status=OrderStatus.CREATED)
                order.items.append(
                    OrderItem(
                        public_id=uuid4(),
                        dish_id="dish-1",
                        name="Tacos",
                        quantity=2,
                        unit_price=Decimal("10.00"),
                    )
                )
                session.add(order)
                await session.commit()

            actor_id = uuid4()

            async def confirm_in_own_session() -> None:
                async with session_factory() as session, session.begin():
                    await confirm_order(session, order_id, actor_id)

            await asyncio.gather(confirm_in_own_session(), confirm_in_own_session())

            async with session_factory() as session:
                stored_order = await session.scalar(
                    select(Order).where(Order.public_id == order_id)
                )
                outbox_rows = list(await session.scalars(select(Outbox)))

            assert stored_order is not None
            assert stored_order.confirmed_at is not None
            assert stored_order.confirmed_by == actor_id
            assert stored_order.version == 2
            assert len(outbox_rows) == 1
            assert outbox_rows[0].payload["orderId"] == str(order_id)
            assert outbox_rows[0].payload["items"] == [
                {
                    "itemId": str(order.items[0].public_id),
                    "name": "Tacos",
                    "quantity": 2,
                }
            ]
        finally:
            await engine.dispose()
