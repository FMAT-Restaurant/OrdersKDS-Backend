"""Unit tests for the InboundConsumerWorker."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.inbound_consumer import INBOUND_BINDINGS, InboundConsumerWorker


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbound_consumer_setup_and_dispatch() -> None:
    dispatcher = MagicMock()
    dispatcher.dispatch_raw = AsyncMock(return_value=True)

    worker = InboundConsumerWorker(
        dispatcher=dispatcher,
        url="amqp://localhost",
        vhost="/",
        queue_name="orders.inventory.inbound",
        inventory_exchange="inventory.topic",
    )

    mock_channel = MagicMock()
    mock_exchange = MagicMock()
    mock_channel.declare_exchange = AsyncMock(return_value=mock_exchange)
    mock_queue = MagicMock()
    mock_queue.name = "orders.inventory.inbound"
    mock_queue.bind = AsyncMock()
    mock_queue.consume = AsyncMock(return_value="ctag-123")
    mock_queue.cancel = AsyncMock()
    mock_channel.declare_queue = AsyncMock(return_value=mock_queue)
    mock_channel.set_qos = AsyncMock()
    mock_channel.close = AsyncMock()

    mock_conn = MagicMock()
    mock_conn.channel = AsyncMock(return_value=mock_channel)
    mock_conn.close = AsyncMock()
    mock_conn.is_closed = False

    stop_event = asyncio.Event()

    # Capture the message handler passed to queue.consume
    captured_handler: Callable[[Any], Awaitable[None]] | None = None

    async def fake_consume(handler: Callable[[Any], Awaitable[None]]) -> str:
        nonlocal captured_handler
        captured_handler = handler
        # Trigger stop event after consumer is registered
        stop_event.set()
        return "ctag-123"

    mock_queue.consume = AsyncMock(side_effect=fake_consume)

    with patch("aio_pika.connect_robust", AsyncMock(return_value=mock_conn)):
        await worker.run(stop_event)

    mock_channel.set_qos.assert_awaited_once_with(prefetch_count=10)
    assert mock_channel.declare_exchange.await_count == 4
    mock_channel.declare_queue.assert_awaited_once_with("orders.inventory.inbound", durable=True)
    assert mock_queue.bind.await_count == len(INBOUND_BINDINGS)
    mock_queue.cancel.assert_awaited_once_with("ctag-123")
    mock_channel.close.assert_awaited_once()
    mock_conn.close.assert_awaited_once()

    # Verify message processing
    assert captured_handler is not None
    mock_msg = MagicMock()
    mock_msg.body = b'{"orderId": "123", "status": "confirmed"}'
    mock_msg.routing_key = "inventory.reservation.confirmed"
    mock_msg.message_id = "msg-1"
    mock_msg.process = MagicMock()
    mock_msg.process.return_value.__aenter__ = AsyncMock()
    mock_msg.process.return_value.__aexit__ = AsyncMock()

    await captured_handler(mock_msg)
    dispatcher.dispatch_raw.assert_awaited_once_with(
        raw_message=b'{"orderId": "123", "status": "confirmed"}',
        queue_name="orders.inventory.inbound",
        event_type="inventory.reservation.confirmed",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbound_consumer_reconnect_on_error() -> None:
    dispatcher = MagicMock()
    worker = InboundConsumerWorker(
        dispatcher=dispatcher,
        url="amqp://localhost",
        vhost="/",
    )

    stop_event = asyncio.Event()
    attempts = 0

    async def fail_then_stop(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("Broker unreachable")
        stop_event.set()

    fake_sleeper = AsyncMock()

    worker._sleeper = fake_sleeper
    with patch("aio_pika.connect_robust", AsyncMock(side_effect=fail_then_stop)):
        await worker.run(stop_event)

    assert attempts >= 1
    fake_sleeper.assert_awaited_with(3.0)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbound_consumer_reraises_cancelled_error_and_closes_connection() -> None:
    dispatcher = MagicMock()
    worker = InboundConsumerWorker(
        dispatcher=dispatcher,
        url="amqp://localhost",
        vhost="/",
    )

    mock_channel = MagicMock()
    mock_exchange = MagicMock()
    mock_channel.declare_exchange = AsyncMock(return_value=mock_exchange)
    mock_queue = MagicMock()
    mock_queue.bind = AsyncMock()
    mock_queue.consume = AsyncMock(side_effect=asyncio.CancelledError())
    mock_channel.declare_queue = AsyncMock(return_value=mock_queue)
    mock_channel.set_qos = AsyncMock()

    mock_conn = MagicMock()
    mock_conn.channel = AsyncMock(return_value=mock_channel)
    mock_conn.close = AsyncMock()
    mock_conn.is_closed = False

    stop_event = asyncio.Event()

    with (
        patch("aio_pika.connect_robust", AsyncMock(return_value=mock_conn)),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker.run(stop_event)

    mock_conn.close.assert_awaited_once()

