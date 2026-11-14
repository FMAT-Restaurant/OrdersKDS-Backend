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
DEFAULT_MENU_EXCHANGE = "menu.topic"
DEFAULT_FLOOR_EXCHANGE = "sala.events"
DEFAULT_ORDERS_EXCHANGE = "ordenes.topic"
INBOUND_BINDINGS: tuple[tuple[str, str], ...] = (
    (DEFAULT_INVENTORY_EXCHANGE, "inventory.reservation.*"),
    (DEFAULT_INVENTORY_EXCHANGE, "inventory.recipe.*"),
    (DEFAULT_INVENTORY_EXCHANGE, "inventario.stock.*"),
    (DEFAULT_MENU_EXCHANGE, "menu.#"),
    (DEFAULT_FLOOR_EXCHANGE, "sala.#"),
    (DEFAULT_FLOOR_EXCHANGE, "dining.#"),
    (DEFAULT_ORDERS_EXCHANGE, "inventory.reservation.*"),
    (DEFAULT_ORDERS_EXCHANGE, "inventario.stock.*"),
    (DEFAULT_ORDERS_EXCHANGE, "pagos.#"),
    (DEFAULT_ORDERS_EXCHANGE, "payments.#"),
    (DEFAULT_ORDERS_EXCHANGE, "pago.#"),
    (DEFAULT_ORDERS_EXCHANGE, "sala.#"),
    (DEFAULT_ORDERS_EXCHANGE, "dining.#"),
    (DEFAULT_ORDERS_EXCHANGE, "menu.*"),
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
        menu_exchange: str | None = None,
        floor_exchange: str | None = None,
        orders_exchange: str | None = None,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._dispatcher = dispatcher
        self._url = url
        self._vhost = vhost
        self._queue_name: str = (
            queue_name or os.getenv("RABBITMQ_INBOUND_QUEUE") or DEFAULT_INBOUND_QUEUE
        )
        self._inventory_exchange: str = (
            inventory_exchange
            or os.getenv("RABBITMQ_INVENTORY_EXCHANGE")
            or DEFAULT_INVENTORY_EXCHANGE
        )
        self._menu_exchange: str = (
            menu_exchange or os.getenv("RABBITMQ_MENU_EXCHANGE") or DEFAULT_MENU_EXCHANGE
        )
        self._floor_exchange: str = (
            floor_exchange or os.getenv("RABBITMQ_FLOOR_EXCHANGE") or DEFAULT_FLOOR_EXCHANGE
        )
        self._orders_exchange: str = (
            orders_exchange or os.getenv("RABBITMQ_EXCHANGE") or DEFAULT_ORDERS_EXCHANGE
        )
        self._sleeper = sleeper
        self._connection: AbstractRobustConnection | None = None

    async def _setup_topology(
        self, channel: aio_pika.abc.AbstractRobustChannel
    ) -> aio_pika.abc.AbstractRobustQueue:
        """Declare durable exchanges, queue, and bindings."""
        exchange_names = {
            DEFAULT_INVENTORY_EXCHANGE: self._inventory_exchange,
            DEFAULT_MENU_EXCHANGE: self._menu_exchange,
            DEFAULT_FLOOR_EXCHANGE: self._floor_exchange,
            DEFAULT_ORDERS_EXCHANGE: self._orders_exchange,
        }
        declared_exchanges: dict[str, aio_pika.abc.AbstractRobustExchange] = {}
        for resolved_name in exchange_names.values():
            if resolved_name not in declared_exchanges:
                declared_exchanges[resolved_name] = await channel.declare_exchange(
                    name=resolved_name,
                    type=aio_pika.ExchangeType.TOPIC,
                    durable=True,
                )
        queue = await channel.declare_queue(self._queue_name, durable=True)

        for exchange_name, binding_key in INBOUND_BINDINGS:
            resolved_name = exchange_names.get(exchange_name, exchange_name)
            await queue.bind(declared_exchanges[resolved_name], routing_key=binding_key)

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
                if self._connection and not self._connection.is_closed:
                    await self._connection.close()
                raise
            except Exception as exc:
                logger.warning(
                    "Inbound consumer connection error: %s. Reconnecting in 3s...",
                    exc,
                )
                if self._connection and not self._connection.is_closed:
                    await self._connection.close()
                await self._sleeper(3.0)

