"""Asynchronous RabbitMQ publisher with publisher confirms and topology setup.

Satisfies: RNF-01, RNF-13, RF-10, RF-19, OP-10
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import TracebackType
from uuid import UUID

import aio_pika
from aio_pika.abc import (
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustExchange,
)

from app.messaging.validator import validate_event_payload


class EventPublishError(RuntimeError):
    """Raised when an event cannot be published or confirmed by the broker."""


class EventPublisher:
    """Publishes domain events to a RabbitMQ Topic Exchange with Publisher Confirms."""

    def __init__(
        self,
        url: str,
        exchange_name: str,
        vhost: str,
    ) -> None:
        """Initialize publisher settings.

        Args:
            url: RabbitMQ connection string.
            exchange_name: Name of the topic exchange.
            vhost: Virtual host to connect to.

        Raises:
            ValueError: If url, exchange_name or vhost is empty.
        """
        if not url or not url.strip():
            raise ValueError("url must be a non-empty string")
        if not exchange_name or not exchange_name.strip():
            raise ValueError("exchange_name must be a non-empty string")
        if not vhost or not vhost.strip():
            raise ValueError("vhost must be a non-empty string")

        self._url = url.strip()
        self._exchange_name = exchange_name.strip()
        self._vhost = vhost.strip()
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._exchange: AbstractRobustExchange | None = None

    @property
    def is_connected(self) -> bool:
        """Return True if connection and channel are open."""
        return (
            self._connection is not None
            and not self._connection.is_closed
            and self._channel is not None
            and not self._channel.is_closed
        )

    async def connect(self) -> None:
        """Establish connection, open channel with confirms, and declare exchange.

        Raises:
            EventPublishError: If connection or topology declaration fails.
        """
        try:
            self._connection = await aio_pika.connect_robust(
                url=self._url,
                virtualhost=self._vhost,
            )
            self._channel = await self._connection.channel(publisher_confirms=True)
            self._exchange = await self._channel.declare_exchange(
                name=self._exchange_name,
                type=aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
        except Exception as exc:
            await self.close()
            raise EventPublishError(f"Failed to connect to RabbitMQ: {exc}") from exc

    async def declare_queue_binding(
        self,
        queue_name: str,
        routing_key: str,
        durable: bool = True,
    ) -> None:
        """Declare a queue and bind it to the exchange idempotently.

        Args:
            queue_name: Name of the queue.
            routing_key: Routing key binding pattern.
            durable: Whether the queue survives broker restarts.
        """
        if self._channel is None or self._exchange is None:
            raise RuntimeError("Publisher must be connected before declaring bindings")
        queue = await self._channel.declare_queue(queue_name, durable=durable)
        await queue.bind(self._exchange, routing_key=routing_key)

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, object],
        event_id: UUID,
        correlation_id: UUID | None = None,
        mandatory: bool = False,
    ) -> None:
        """Validate payload and publish to topic exchange with publisher confirms.

        Args:
            event_type: Event routing key (e.g. ordenes.orden.creada).
            payload: Event data matching contracts/events schema.
            event_id: Unique deterministic event identifier.
            correlation_id: Optional correlation tracking identifier.
            mandatory: If True, broker returns message if unroutable.

        Raises:
            EventContractError: If payload violates JSON schema.
            EventPublishError: If publish or confirmation fails.
        """
        validate_event_payload(event_type=event_type, payload=payload)

        if not self.is_connected or self._exchange is None:
            await self.connect()

        assert self._exchange is not None
        message = aio_pika.Message(
            body=json.dumps(dict(payload)).encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            message_id=str(event_id),
            correlation_id=str(correlation_id) if correlation_id is not None else None,
        )

        try:
            await self._exchange.publish(
                message=message,
                routing_key=event_type,
                mandatory=mandatory,
            )
        except Exception as exc:
            raise EventPublishError(
                f"Broker rejected or failed to confirm event '{event_id}': {exc}"
            ) from exc

    async def close(self) -> None:
        """Close channel and connection safely."""
        if self._channel is not None and not self._channel.is_closed:
            await self._channel.close()
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        self._exchange = None
        self._channel = None
        self._connection = None

    async def __aenter__(self) -> EventPublisher:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
