"""Unit tests for RabbitMQ event publisher.

Satisfies: RNF-01, RNF-13, RF-10, RF-19, OP-10
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import aio_pika
import pytest

from app.messaging.events import EventName, OrderCreatedEvent
from app.messaging.publisher import EventPublisher, EventPublishError


@pytest.mark.unit
@pytest.mark.requirement("OP-10")
def test_publisher_init_validates_non_empty_arguments() -> None:
    """Verify EventPublisher constructor rejects empty parameters."""
    with pytest.raises(ValueError, match="url must be a non-empty string"):
        EventPublisher(url="", exchange_name="test", vhost="/")

    with pytest.raises(ValueError, match="exchange_name must be a non-empty string"):
        EventPublisher(url="amqp://localhost", exchange_name="  ", vhost="/")

    with pytest.raises(ValueError, match="vhost must be a non-empty string"):
        EventPublisher(url="amqp://localhost", exchange_name="test", vhost="")


@pytest.mark.unit
@pytest.mark.requirement("RNF-01", "OP-10")
@pytest.mark.asyncio
async def test_connect_declares_topic_exchange_with_publisher_confirms() -> None:
    mock_exchange = MagicMock()
    mock_channel = AsyncMock()
    mock_channel.is_closed = False
    mock_channel.declare_exchange.return_value = mock_exchange
    mock_connection = AsyncMock()
    mock_connection.is_closed = False
    mock_connection.channel.return_value = mock_channel

    with patch("aio_pika.connect_robust", AsyncMock(return_value=mock_connection)):
        publisher = EventPublisher(
            url="amqp://user:pass@localhost:5672/",
            exchange_name="test.topic",
            vhost="/test",
        )
        await publisher.connect()

        mock_connection.channel.assert_awaited_once_with(publisher_confirms=True)
        mock_channel.declare_exchange.assert_awaited_once_with(
            name="test.topic",
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        assert publisher.is_connected


@pytest.mark.unit
@pytest.mark.requirement("RNF-01", "OP-10")
@pytest.mark.asyncio
async def test_connect_raises_event_publish_error_on_failure() -> None:
    with patch(
        "aio_pika.connect_robust",
        AsyncMock(side_effect=RuntimeError("Connection refused")),
    ):
        publisher = EventPublisher(
            url="amqp://user:pass@localhost:5672/",
            exchange_name="test.topic",
            vhost="/",
        )
        with pytest.raises(EventPublishError, match="Failed to connect to RabbitMQ"):
            await publisher.connect()
        assert not publisher.is_connected


@pytest.mark.unit
@pytest.mark.requirement("RNF-01", "RF-10", "RF-19")
@pytest.mark.asyncio
async def test_publish_validates_and_emits_persistent_message() -> None:
    mock_exchange = AsyncMock()
    publisher = EventPublisher(
        url="amqp://user:pass@localhost:5672/",
        exchange_name="test.topic",
        vhost="/",
    )
    publisher._exchange = mock_exchange
    publisher._connection = MagicMock(is_closed=False)
    publisher._channel = MagicMock(is_closed=False)

    event = OrderCreatedEvent.for_order(uuid4(), correlation_id=uuid4())
    payload = event.model_dump(mode="json", by_alias=True)

    await publisher.publish(
        event_type=EventName.ORDER_CREATED,
        payload=payload,
        event_id=event.event_id,
        correlation_id=event.correlation_id,
    )

    mock_exchange.publish.assert_awaited_once()
    call_args = mock_exchange.publish.call_args
    message = call_args.kwargs["message"]
    routing_key = call_args.kwargs["routing_key"]

    assert routing_key == EventName.ORDER_CREATED
    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert message.content_type == "application/json"
    assert message.message_id == str(event.event_id)
    assert message.correlation_id == str(event.correlation_id)


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
@pytest.mark.asyncio
async def test_publish_raises_event_publish_error_on_nack() -> None:
    mock_exchange = AsyncMock()
    mock_exchange.publish.side_effect = RuntimeError("Broker NACK / Delivery failure")
    publisher = EventPublisher(
        url="amqp://user:pass@localhost:5672/",
        exchange_name="test.topic",
        vhost="/",
    )
    publisher._exchange = mock_exchange
    publisher._connection = MagicMock(is_closed=False)
    publisher._channel = MagicMock(is_closed=False)

    event = OrderCreatedEvent.for_order(uuid4())
    payload = event.model_dump(mode="json", by_alias=True)

    with pytest.raises(EventPublishError, match="Broker rejected or failed to confirm"):
        await publisher.publish(
            event_type=EventName.ORDER_CREATED,
            payload=payload,
            event_id=event.event_id,
        )


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
@pytest.mark.asyncio
async def test_publish_reconnects_when_disconnected() -> None:
    publisher = EventPublisher(
        url="amqp://user:pass@localhost:5672/",
        exchange_name="test.topic",
        vhost="/",
    )
    mock_exchange = AsyncMock()

    async def fake_connect() -> None:
        publisher._exchange = mock_exchange
        publisher._connection = MagicMock(is_closed=False)
        publisher._channel = MagicMock(is_closed=False)

    publisher.connect = fake_connect  # type: ignore[method-assign]

    event = OrderCreatedEvent.for_order(uuid4())
    payload = event.model_dump(mode="json", by_alias=True)

    await publisher.publish(
        event_type=EventName.ORDER_CREATED,
        payload=payload,
        event_id=event.event_id,
    )
    mock_exchange.publish.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
@pytest.mark.asyncio
async def test_declare_queue_binding_and_close() -> None:
    mock_queue = AsyncMock()
    mock_exchange = MagicMock()
    mock_channel = AsyncMock()
    mock_channel.declare_queue.return_value = mock_queue
    mock_channel.is_closed = False
    mock_connection = AsyncMock()
    mock_connection.is_closed = False

    publisher = EventPublisher(
        url="amqp://user:pass@localhost:5672/",
        exchange_name="test.topic",
        vhost="/",
    )
    publisher._channel = mock_channel
    publisher._exchange = mock_exchange
    publisher._connection = mock_connection

    await publisher.declare_queue_binding("test.queue", "ordenes.#")
    mock_channel.declare_queue.assert_awaited_once_with("test.queue", durable=True)
    mock_queue.bind.assert_awaited_once_with(mock_exchange, routing_key="ordenes.#")

    await publisher.close()
    mock_channel.close.assert_awaited_once()
    mock_connection.close.assert_awaited_once()
    assert not publisher.is_connected


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
@pytest.mark.asyncio
async def test_declare_queue_binding_raises_when_not_connected() -> None:
    publisher = EventPublisher(
        url="amqp://user:pass@localhost:5672/",
        exchange_name="test.topic",
        vhost="/",
    )
    with pytest.raises(RuntimeError, match="Publisher must be connected"):
        await publisher.declare_queue_binding("test.queue", "ordenes.#")


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
@pytest.mark.asyncio
async def test_async_context_manager() -> None:
    mock_exchange = MagicMock()
    mock_channel = AsyncMock()
    mock_channel.is_closed = False
    mock_channel.declare_exchange.return_value = mock_exchange
    mock_connection = AsyncMock()
    mock_connection.is_closed = False
    mock_connection.channel.return_value = mock_channel

    with patch("aio_pika.connect_robust", AsyncMock(return_value=mock_connection)):
        async with EventPublisher(
            url="amqp://user:pass@localhost:5672/",
            exchange_name="test.topic",
            vhost="/",
        ) as pub:
            assert pub.is_connected
        assert not pub.is_connected
