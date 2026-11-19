"""Unit tests verifying sourceId/plateId/offerId alignment and 100-char bounds.

Satisfies: RF-10, RF-14, RF-17, RNF-13
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.schemas.order import OrderItemInput
from app.cache.catalog import CatalogRepository
from app.db.models import Order, OrderItem
from app.domain.constants import MAX_CATALOG_ID_LENGTH
from app.domain.order_status import ItemStatus, OrderStatus
from app.messaging.consumers.base import EventConsumerDispatcher, InMemoryDLQRouter
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.events import DishPreparedEvent, EventName, OrderCreatedItem
from app.messaging.publisher import EventPublisher
from app.messaging.validator import EventContractError, validate_event_payload
from app.services.orders import _consolidate_order_items

pytestmark = [pytest.mark.unit]


def test_consolidate_order_items_always_emits_plate_and_source_ids() -> None:
    """Emit plateId and sourceId even when offer_id equals dish_id or is None."""
    items = [
        OrderItem(
            public_id=uuid4(),
            dish_id="recipe-pasta-01",
            offer_id=None,
            name="Pasta Alfredo",
            quantity=1,
            unit_price=Decimal("120.00"),
            status=ItemStatus.PENDING,
        ),
        OrderItem(
            public_id=uuid4(),
            dish_id="recipe-pasta-01",
            offer_id="recipe-pasta-01",
            name="Pasta Alfredo",
            quantity=2,
            unit_price=Decimal("120.00"),
            status=ItemStatus.PENDING,
        ),
        OrderItem(
            public_id=uuid4(),
            dish_id="recipe-fries-02",
            offer_id="offer-combo-01",
            name="Combo Burger - Fries",
            quantity=1,
            unit_price=Decimal("0.00"),
            status=ItemStatus.PENDING,
        ),
        OrderItem(
            public_id=uuid4(),
            dish_id="recipe-cancelled-03",
            offer_id="offer-cancelled-03",
            name="Cancelled Soup",
            quantity=5,
            unit_price=Decimal("50.00"),
            status=ItemStatus.CANCELLED,
        ),
    ]

    consolidated = _consolidate_order_items(items)

    assert len(consolidated) == 2
    by_plate = {
        entry.plate_id: entry.model_dump(mode="json", by_alias=True) for entry in consolidated
    }
    assert by_plate["recipe-pasta-01"] == {
        "offerId": "recipe-pasta-01",
        "plateId": "recipe-pasta-01",
        "sourceId": "recipe-pasta-01",
        "quantity": 3,
    }
    assert by_plate["recipe-fries-02"] == {
        "offerId": "offer-combo-01",
        "plateId": "recipe-fries-02",
        "sourceId": "recipe-fries-02",
        "quantity": 1,
    }


def test_order_created_item_and_dish_prepared_populate_recipe_aliases() -> None:
    """Serialize plateId and sourceId consistently across domain event payloads."""
    created_item = OrderCreatedItem(offerId="legacy-dish-id", quantity=2)
    assert created_item.model_dump(mode="json", by_alias=True) == {
        "offerId": "legacy-dish-id",
        "plateId": "legacy-dish-id",
        "sourceId": "legacy-dish-id",
        "quantity": 2,
    }

    prepared = DishPreparedEvent.for_item(uuid4(), uuid4(), offer_id="offer-taco-1", quantity=1)
    prepared_payload = prepared.model_dump(mode="json", by_alias=True)
    assert prepared_payload["offerId"] == "offer-taco-1"
    assert prepared_payload["plateId"] == "offer-taco-1"
    assert prepared_payload["sourceId"] == "offer-taco-1"
    validate_event_payload(EventName.DISH_PREPARED, prepared_payload)


def test_order_item_input_enforces_100_char_limit_and_rejects_blanks() -> None:
    """Align catalog ID length to Inventory 100 chars and reject empty/overlong IDs."""
    exact_100 = "r" * MAX_CATALOG_ID_LENGTH
    valid = OrderItemInput(
        dishId=exact_100,
        sourceId=exact_100,
        offerId=exact_100,
        name="Dish with max length IDs",
        quantity=1,
    )
    assert valid.source_id == exact_100
    assert valid.offer_id == exact_100

    with pytest.raises(ValidationError):
        OrderItemInput(sourceId="r" * (MAX_CATALOG_ID_LENGTH + 1), name="Overlong", quantity=1)

    with pytest.raises(ValidationError):
        OrderItemInput(
            offerId="o" * (MAX_CATALOG_ID_LENGTH + 1),
            name="Overlong offer",
            quantity=1,
        )

    with pytest.raises(ValidationError):
        OrderItemInput(sourceId="   ", name="Blank source", quantity=1)

    with pytest.raises(ValidationError):
        OrderItemInput(offerId="   ", name="Blank offer", quantity=1)


def test_outbound_contract_rejects_overlong_plate_id() -> None:
    """Ensure JSON schema enforces maxLength=100 on plateId/sourceId/offerId."""
    overlong_payload: dict[str, object] = {
        "eventId": str(uuid4()),
        "orderId": str(uuid4()),
        "items": [
            {
                "offerId": "offer-1",
                "plateId": "p" * 101,
                "sourceId": "p" * 101,
                "quantity": 1,
            }
        ],
    }
    with pytest.raises(EventContractError):
        validate_event_payload(EventName.ORDER_CREATED, overlong_payload)


@pytest.mark.asyncio
async def test_publisher_enriches_cancel_without_injecting_fake_item_id() -> None:
    """Ensure dual routing adds plateId/sourceId on itemsCancelados without itemId collision."""
    mock_exchange = AsyncMock()
    publisher = EventPublisher(
        url="amqp://user:pass@localhost:5672/",
        exchange_name="ordenes.topic",
        vhost="/",
        dual_routing=True,
    )
    publisher._exchange = mock_exchange
    publisher._connection = MagicMock(is_closed=False)
    publisher._channel = MagicMock(is_closed=False)

    order_id = uuid4()
    event_id = uuid4()
    payload: dict[str, object] = {
        "eventId": str(event_id),
        "orderId": str(order_id),
        "reason": "Waiter cancelled",
        "itemsCancelados": [{"offerId": "offer-legacy-only", "quantity": 2}],
    }

    await publisher.publish(
        event_type=EventName.ORDER_CANCELLED,
        payload=payload,
        event_id=event_id,
    )

    assert mock_exchange.publish.await_count == 2
    secondary_msg = mock_exchange.publish.await_args_list[1].kwargs["message"]
    body = json.loads(secondary_msg.body.decode("utf-8"))
    item = body["itemsCancelados"][0]
    assert item["plateId"] == "offer-legacy-only"
    assert item["sourceId"] == "offer-legacy-only"
    assert "itemId" not in item


@pytest.mark.asyncio
async def test_dispatcher_routes_inventory_source_availability_updated() -> None:
    """Verify inventory.source.availability.updated updates local Redis availability."""
    redis = AsyncMock()
    redis.hgetall.return_value = {}
    catalog_consumer = CatalogConsumer(CatalogRepository(redis))
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=InMemoryDLQRouter(),
        catalog_consumer=catalog_consumer,
    )

    source_uuid = str(uuid4())
    dispatched = await dispatcher.dispatch_raw(
        raw_message={"sourceId": source_uuid, "isAvailable": False},
        queue_name="orders.inventory.inbound",
        event_type=EventName.INVENTORY_SOURCE_AVAILABILITY_UPDATED.value,
    )

    assert dispatched is True
    redis.hset.assert_awaited_once()
    call_args = redis.hset.await_args
    assert call_args.args[0] == f"catalog:platillo:{source_uuid}"
    assert call_args.kwargs["mapping"]["available"] == "false"
    assert Order(public_id=uuid4(), status=OrderStatus.CREATED).status == OrderStatus.CREATED
