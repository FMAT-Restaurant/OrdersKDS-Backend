"""PostgreSQL integration test for concurrent idempotent confirmation (CT-05)."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Order, Outbox
from app.domain.order_status import OrderStatus
from app.messaging.events import OrderCreatedEvent
from app.services.orders import confirm_order

_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
_POSTGRES_TEST_DATABASE_URL = (
    _TEST_DATABASE_URL
    if _TEST_DATABASE_URL and _TEST_DATABASE_URL.startswith("postgresql+")
    else None
)


@pytest.mark.integration
@pytest.mark.skipif(
    _POSTGRES_TEST_DATABASE_URL is None,
    reason="Set TEST_DATABASE_URL to a dedicated PostgreSQL test database.",
)
async def test_concurrent_confirmations_create_one_outbox_event() -> None:
    assert _POSTGRES_TEST_DATABASE_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    order_id = uuid4()
    event_id = OrderCreatedEvent._event_id_for_order(order_id)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session, session.begin():
            session.add(Order(public_id=order_id, status=OrderStatus.CREATED))

        async def confirm_once() -> None:
            async with session_factory() as session, session.begin():
                await confirm_order(session, order_id, uuid4())

        await asyncio.gather(confirm_once(), confirm_once())

        async with session_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(Outbox).where(Outbox.event_id == event_id)
            )
            order = await session.scalar(select(Order).where(Order.public_id == order_id))
        assert count == 1
        assert order is not None
        assert order.confirmed_at is not None
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(delete(Outbox).where(Outbox.event_id == event_id))
            await session.execute(delete(Order).where(Order.public_id == order_id))
        await engine.dispose()
