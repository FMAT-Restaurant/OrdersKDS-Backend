"""Unit tests for the contract stubs background runner and publisher adapter.

Satisfies: RNF-13, RF-33, RF-34, RF-35, OP-11
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.messaging.publisher import EventPublisher
from app.stubs.billing_stub import BillingStub
from app.stubs.inventory_stub import InventoryStub
from app.stubs.protocol import InMemoryStubPublisher, StubEventPublisherAdapter
from app.stubs.runner import _dispatch_inbound_message, main

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RNF-13", "RF-33", "RF-34", "RF-35", "OP-11"),
]


@pytest.fixture
def mock_inventory_stub() -> AsyncMock:
    return AsyncMock(spec=InventoryStub)


@pytest.fixture
def mock_billing_stub() -> AsyncMock:
    return AsyncMock(spec=BillingStub)


@pytest.mark.parametrize(
    ("routing_key", "expected_handler"),
    [
        ("ordenes.orden.creada", "handle_order_created"),
        ("orders.order.created", "handle_order_created"),
        ("ordenes.orden.cancelada", "handle_order_cancelled"),
        ("orders.order.cancelled", "handle_order_cancelled"),
        ("ordenes.orden.expirada", "handle_order_expired"),
        ("orders.order.expired", "handle_order_expired"),
    ],
)
async def test_dispatch_inbound_message_inventory_events(
    routing_key: str,
    expected_handler: str,
    mock_inventory_stub: AsyncMock,
    mock_billing_stub: AsyncMock,
) -> None:
    body = {"order_id": str(uuid4())}
    await _dispatch_inbound_message(routing_key, body, mock_inventory_stub, mock_billing_stub)
    getattr(mock_inventory_stub, expected_handler).assert_awaited_once_with(body)
    assert not mock_billing_stub.method_calls


@pytest.mark.parametrize(
    ("routing_key", "expected_handler"),
    [
        ("ordenes.cuenta.solicitada", "handle_bill_requested"),
        ("orders.bill.requested", "handle_bill_requested"),
        ("ordenes.cobro.solicitado", "handle_charge_requested"),
        ("orders.charge.requested", "handle_charge_requested"),
    ],
)
async def test_dispatch_inbound_message_billing_events(
    routing_key: str,
    expected_handler: str,
    mock_inventory_stub: AsyncMock,
    mock_billing_stub: AsyncMock,
) -> None:
    body = {"order_id": str(uuid4()), "table_number": 5}
    await _dispatch_inbound_message(routing_key, body, mock_inventory_stub, mock_billing_stub)
    getattr(mock_billing_stub, expected_handler).assert_awaited_once_with(body)
    assert not mock_inventory_stub.method_calls


async def test_dispatch_inbound_message_unhandled_routing_key(
    mock_inventory_stub: AsyncMock,
    mock_billing_stub: AsyncMock,
) -> None:
    body = {"some": "data"}
    await _dispatch_inbound_message(
        "unknown.routing.key", body, mock_inventory_stub, mock_billing_stub
    )
    assert not mock_inventory_stub.method_calls
    assert not mock_billing_stub.method_calls


async def test_stub_event_publisher_adapter_with_metadata() -> None:
    mock_raw_publisher = AsyncMock(spec=EventPublisher)
    adapter = StubEventPublisherAdapter(mock_raw_publisher)

    eid = uuid4()
    cid = uuid4()
    await adapter.publish(
        event_type="test.event",
        payload={"k": "v"},
        event_id=eid,
        correlation_id=cid,
        headers={"header": "val"},
        exchange="custom.exchange",
    )

    mock_raw_publisher.publish.assert_awaited_once_with(
        event_type="test.event",
        payload={"k": "v"},
        event_id=eid,
        correlation_id=cid,
    )

    await adapter.close()
    mock_raw_publisher.close.assert_awaited_once()


def test_in_memory_stub_publisher_clear() -> None:
    publisher = InMemoryStubPublisher()
    publisher.published_events.append({"event_type": "dummy"})
    assert len(publisher.get_events()) == 1

    publisher.clear()
    assert len(publisher.get_events()) == 0


async def test_stubs_runner_main_lifecycle() -> None:
    mock_channel = AsyncMock()
    mock_exchange = AsyncMock()
    mock_queue = AsyncMock()
    mock_connection = AsyncMock()

    mock_channel.declare_exchange.return_value = mock_exchange
    mock_channel.declare_queue.return_value = mock_queue
    mock_connection.channel.return_value = mock_channel

    captured_consumer: Any = None

    async def _capture_consume(consumer_callback: Any) -> None:
        nonlocal captured_consumer
        captured_consumer = consumer_callback

    mock_queue.consume.side_effect = _capture_consume

    with (
        patch("app.stubs.runner.EventPublisher") as mock_pub_cls,
        patch("app.stubs.runner.aio_pika.connect_robust", return_value=mock_connection),
        patch("app.stubs.runner.asyncio.Event") as mock_event_cls,
    ):
        mock_pub_instance = AsyncMock(spec=EventPublisher)
        mock_pub_cls.return_value = mock_pub_instance

        mock_event = MagicMock()
        mock_event.wait = AsyncMock()
        mock_event_cls.return_value = mock_event

        await main()

        mock_pub_instance.connect.assert_awaited_once()
        mock_connection.channel.assert_awaited_once()
        mock_channel.set_qos.assert_awaited_once_with(prefetch_count=10)
        assert mock_queue.bind.await_count > 0
        mock_connection.close.assert_awaited_once()
        mock_pub_instance.close.assert_awaited_once()

        # Exercise the captured message consumer callback with valid JSON
        assert captured_consumer is not None
        mock_proc = AsyncMock()
        mock_proc.__aenter__.return_value = None
        mock_proc.__aexit__.return_value = False

        mock_msg = MagicMock()
        mock_msg.body = b'{"order_id": "test-123"}'
        mock_msg.routing_key = "orders.order.created"
        mock_msg.process = MagicMock(return_value=mock_proc)
        await captured_consumer(mock_msg)

        # Exercise the captured message consumer callback with malformed JSON
        bad_msg = MagicMock()
        bad_msg.body = b"not-valid-json"
        bad_msg.routing_key = "orders.order.created"
        bad_msg.process = MagicMock(return_value=mock_proc)
        await captured_consumer(bad_msg)
