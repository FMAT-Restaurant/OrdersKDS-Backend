"""Inbound RabbitMQ consumer worker for inventory and domain responses.

Satisfies: RF-18, RF-33, RF-34, RNF-02, RNF-13
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection

from app.messaging.consumers.base import EventConsumerDispatcher

logger = logging.getLogger(__name__)

Sleeper = Callable[[float], Awaitable[None]]

DEFAULT_INBOUND_QUEUE = "orders.inventory.inbound"
DEFAULT_INVENTORY_EXCHANGE = "inventory.topic"
INBOUND_BINDINGS: tuple[tuple[str, str], ...] = (
    (DEFAULT_INVENTORY_EXCHANGE, "inventory.reservation.*"),
    (DEFAULT_INVENTORY_EXCHANGE, "inventario.stock.*"),
)


class InboundConsumerWorker:
    """Consumes inbound reservation and inventory events from RabbitMQ."""

    def __init__(
        self,
        dispatcher: EventConsumerDispatcher,
        *,
        url: str,
        vhost: str = "/",
        queue_name: str | None = None,
        inventory_exchange: str | None = None,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._dispatcher = dispatcher
        self._url = url
        self._vhost = vhost
        self._queue_name = queue_name or os.getenv("RABBITMQ_INBOUND_QUEUE", DEFAULT_INBOUND_QUEUE)
        self._inventory_exchange = inventory_exchange or os.getenv(
            "RABBITMQ_INVENTORY_EXCHANGE", DEFAULT_INVENTORY_EXCHANGE
        )
        self._sleeper = sleeper
        self._connection: AbstractRobustConnection | None = None

    async def _setup_topology(
        self, channel: aio_pika.abc.AbstractRobustChannel
    ) -> aio_pika.abc.AbstractRobustQueue:
        """Declare durable exchanges, queue, and bindings."""
        inv_exchange = await channel.declare_exchange(
            name=self._inventory_exchange,
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        queue = await channel.declare_queue(self._queue_name, durable=True)

        for exchange_name, binding_key in INBOUND_BINDINGS:
            effective_ex = (
                inv_exchange if exchange_name == DEFAULT_INVENTORY_EXCHANGE else inv_exchange
            )
            await queue.bind(effective_ex, routing_key=binding_key)

        return queue

    async def _handle_message(self, message: AbstractIncomingMessage) -> None:
        """Process a single incoming broker message with dispatcher and automatic ACK."""
        async with message.process():
            routing_key = message.routing_key or ""
            logger.debug(
                "Received inbound message on %s (id=%s)",
                routing_key,
                message.message_id,
            )
            await self._dispatcher.dispatch_raw(
                raw_message=message.body,
                queue_name=self._queue_name,
                event_type=routing_key,
            )

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run consumer loop until stop_event is set."""
        while not stop_event.is_set():
            try:
                self._connection = await aio_pika.connect_robust(
                    url=self._url,
                    virtualhost=self._vhost,
                )
                channel = await self._connection.channel()
                await channel.set_qos(prefetch_count=10)

                queue = await self._setup_topology(channel)
                consumer_tag = await queue.consume(self._handle_message)
                logger.info(
                    "Inbound consumer listening on queue '%s' (exchange '%s', tag=%s)",
                    self._queue_name,
                    self._inventory_exchange,
                    consumer_tag,
                )

                await stop_event.wait()
                await queue.cancel(consumer_tag)
                await channel.close()
                await self._connection.close()
                break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    "Inbound consumer connection error: %s. Reconnecting in 3s...",
                    exc,
                )
                if self._connection and not self._connection.is_closed:
                    await self._connection.close()
                await self._sleeper(3.0)

