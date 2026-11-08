"""Staging connectivity smoke test for the API and RabbitMQ."""

from __future__ import annotations

import asyncio
import json
import os
from urllib.request import urlopen
from uuid import uuid4

import aio_pika


async def main() -> None:
    """Verify health and one publish/consume round trip."""
    health_url = os.environ["API_HEALTH_URL"]
    with urlopen(health_url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"Health check returned HTTP {response.status}")

    connection = await aio_pika.connect_robust(
        os.environ["RABBITMQ_URL"],
        virtualhost=os.environ["RABBITMQ_VHOST"],
    )
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            "staging.smoke",
            aio_pika.ExchangeType.TOPIC,
            durable=False,
            auto_delete=True,
        )
        queue = await channel.declare_queue("", exclusive=True, auto_delete=True)
        await queue.bind(exchange, routing_key="staging.smoke")
        payload = {"event": "staging.smoke", "id": str(uuid4())}
        await exchange.publish(
            aio_pika.Message(body=json.dumps(payload).encode()),
            routing_key="staging.smoke",
        )
        message = await queue.get(timeout=10, fail=True)
        if json.loads(message.body) != payload:
            raise RuntimeError("RabbitMQ smoke payload did not round-trip unchanged")
        await message.ack()
    finally:
        await connection.close()

    print("Staging smoke passed: /health and RabbitMQ publish/consume")


if __name__ == "__main__":
    asyncio.run(main())
