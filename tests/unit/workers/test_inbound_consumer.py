"""Unit tests for the InboundConsumerWorker."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.messaging.consumers.base import DispatchOutcome
from app.workers.inbound_consumer import (
    DEFAULT_DLX_EXCHANGE,
    INBOUND_BINDINGS,
    InboundConsumerWorker,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbound_consumer_setup_and_dispatch() -> None:
    dispatcher = MagicMock()
    dispatcher.dispatch_with_outcome = AsyncMock(
        return_value=DispatchOutcome(applied=True, dead_lettered=False)
    )

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
    mock_dlq = MagicMock()
    mock_dlq.name = "orders.inventory.inbound.dlq"
    mock_dlq.bind = AsyncMock()
    mock_queue = MagicMock()
    mock_queue.name = "orders.inventory.inbound"
    mock_queue.bind = AsyncMock()
    mock_queue.consume = AsyncMock(return_value="ctag-123")
    mock_queue.cancel = AsyncMock()
    mock_channel.declare_queue = AsyncMock(side_effect=[mock_dlq, mock_queue])
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
    # 4 topic exchanges + 1 direct DLX exchange
    assert mock_channel.declare_exchange.await_count == 5
    mock_channel.declare_queue.assert_any_await("orders.inventory.inbound.dlq", durable=True)
    mock_channel.declare_queue.assert_any_await(
        "orders.inventory.inbound",
        durable=True,
        arguments={
            "x-dead-letter-exchange": DEFAULT_DLX_EXCHANGE,
            "x-dead-letter-routing-key": "orders.inventory.inbound.dlq",
        },
    )
    mock_dlq.bind.assert_awaited_once_with(
        mock_exchange, routing_key="orders.inventory.inbound.dlq"
    )
    assert mock_queue.bind.await_count == len(INBOUND_BINDINGS)
    mock_queue.cancel.assert_awaited_once_with("ctag-123")
    mock_channel.close.assert_awaited_once()
    mock_conn.close.assert_awaited_once()

    # Verify valid message processing ACKs message
    assert captured_handler is not None
    mock_msg = MagicMock()
    mock_msg.body = b'{"orderId": "123", "status": "confirmed"}'
    mock_msg.routing_key = "inventory.reservation.confirmed"
    mock_msg.message_id = "msg-1"
    mock_msg.ack = AsyncMock()
    mock_msg.nack = AsyncMock()

    await captured_handler(mock_msg)
    dispatcher.dispatch_with_outcome.assert_awaited_once_with(
        raw_message=b'{"orderId": "123", "status": "confirmed"}',
        queue_name="orders.inventory.inbound",
        event_type="inventory.reservation.confirmed",
    )
    mock_msg.ack.assert_awaited_once()
    mock_msg.nack.assert_not_awaited()

    # Verify malformed/poison message NACKs with requeue=False into RabbitMQ DLQ (SEC-12)
    dispatcher.dispatch_with_outcome.reset_mock()
    dispatcher.dispatch_with_outcome.return_value = DispatchOutcome(
        applied=False,
        dead_lettered=True,
        reason="Invalid JSON",
    )
    poison_msg = MagicMock()
    poison_msg.body = b"{malformed"
    poison_msg.routing_key = "inventory.reservation.confirmed"
    poison_msg.message_id = "msg-poison"
    poison_msg.ack = AsyncMock()
    poison_msg.nack = AsyncMock()

    await captured_handler(poison_msg)
    poison_msg.nack.assert_awaited_once_with(requeue=False)
    poison_msg.ack.assert_not_awaited()

    # Verify unhandled exception in dispatcher also dead-letters safely without requeue
    dispatcher.dispatch_with_outcome.side_effect = RuntimeError("unexpected boom")
    crash_msg = MagicMock()
    crash_msg.body = b"{}"
    crash_msg.routing_key = "inventory.reservation.confirmed"
    crash_msg.message_id = "msg-crash"
    crash_msg.ack = AsyncMock()
    crash_msg.nack = AsyncMock()

    await captured_handler(crash_msg)
    crash_msg.nack.assert_awaited_once_with(requeue=False)
    crash_msg.ack.assert_not_awaited()


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
