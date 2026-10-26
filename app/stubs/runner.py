"""CLI Entrypoint for running microservice contract stubs as a standalone background worker.

Usage:
    python -m app.stubs.runner

Satisfies: RNF-13, RF-33, RF-34, RF-35, OP-11
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from collections.abc import Mapping

import aio_pika

from app.messaging.publisher import EventPublisher
from app.stubs.billing_stub import BillingStub
from app.stubs.config import StubSettings
from app.stubs.floor_stub import FloorStub
from app.stubs.inventory_stub import InventoryStub
from app.stubs.menu_stub import MenuStub
from app.stubs.protocol import StubEventPublisherAdapter

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app.stubs.runner")

ROUTING_KEYS = [
    "ordenes.orden.creada",
    "ordenes.orden.cancelada",
    "ordenes.orden.expirada",
    "ordenes.cuenta.solicitada",
    "ordenes.cobro.solicitado",
    "orders.order.created",
    "orders.order.cancelled",
    "orders.bill.requested",
    "orders.charge.requested",
]


async def _dispatch_inbound_message(
    routing_key: str,
    body: Mapping[str, object],
    inventory_stub: InventoryStub,
    billing_stub: BillingStub,
) -> None:
    """Dispatch an inbound AMQP message to the appropriate contract stub."""
    if routing_key in ("ordenes.orden.creada", "orders.order.created"):
        await inventory_stub.handle_order_created(body)
    elif routing_key in ("ordenes.orden.cancelada", "orders.order.cancelled"):
        await inventory_stub.handle_order_cancelled(body)
    elif routing_key in ("ordenes.orden.expirada", "orders.order.expired"):
        await inventory_stub.handle_order_expired(body)
    elif routing_key in ("ordenes.cuenta.solicitada", "orders.bill.requested"):
        await billing_stub.handle_bill_requested(body)
    elif routing_key in ("ordenes.cobro.solicitado", "orders.charge.requested"):
        await billing_stub.handle_charge_requested(body)


async def main() -> None:
    """Run the contract stubs daemon listening on RabbitMQ."""
    settings = StubSettings()
    rmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    exchange_name = os.getenv("RABBITMQ_EXCHANGE", "amq.topic")
    vhost = os.getenv("RABBITMQ_VHOST", "/")

    logger.info(
        "Initializing stubs with mode=%s at exchange=%s",
        settings.inventory_mode,
        exchange_name,
    )

    raw_publisher = EventPublisher(url=rmq_url, exchange_name=exchange_name, vhost=vhost)
    await raw_publisher.connect()
    publisher = StubEventPublisherAdapter(raw_publisher)

    inventory_stub = InventoryStub(publisher=publisher, settings=settings)
    billing_stub = BillingStub(publisher=publisher, settings=settings)
    _floor_stub = FloorStub(publisher=publisher, settings=settings)
    _menu_stub = MenuStub(publisher=publisher, settings=settings)

    connection = await aio_pika.connect_robust(url=rmq_url, virtualhost=vhost)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)

    exchange = await channel.declare_exchange(
        name=exchange_name,
        type=aio_pika.ExchangeType.TOPIC,
        durable=True,
    )

    stubs_queue = await channel.declare_queue("stubs.orders.inbound", durable=True)
    for rk in ROUTING_KEYS:
        await stubs_queue.bind(exchange, routing_key=rk)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    async def _on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process():
            try:
                body = json.loads(message.body.decode("utf-8"))
                rk = message.routing_key or ""
                logger.info("Stubs runner received message on %s", rk)
                await _dispatch_inbound_message(rk, body, inventory_stub, billing_stub)
            except Exception as exc:
                logger.exception("Error processing message in stubs runner: %s", exc)

    await stubs_queue.consume(_on_message)
    logger.info("Contract stubs runner listening on stubs.orders.inbound. Ready.")

    await stop_event.wait()
    logger.info("Shutting down contract stubs runner...")
    await connection.close()
    await publisher.close()
    logger.info("Contract stubs runner stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
