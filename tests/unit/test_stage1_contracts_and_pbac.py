"""Unit tests verifying Stage 1 integration contracts, dual routing, catalog projection, and PBAC.

Covers:
- EventPublisher dual routing keys and inventory payload enrichment (plateId/sourceId vs offerId)
- EventConsumerDispatcher + CatalogConsumer projection for menu.source.* and inventory.recipe.*
- OrderService outbox dual identifiers (plateId vs offerId) and defense-in-depth PBAC checks
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import AuthContext
from app.cache.catalog import CatalogRepository
from app.db.models import Order, OrderItem
from app.domain.access import ITEM_READY, ITEM_REJECT, START_PREPARATION
from app.domain.errors import ForbiddenRoleError
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role
from app.messaging.consumers.base import EventConsumerDispatcher, InMemoryDLQRouter
from app.messaging.consumers.catalog import CatalogConsumer
from app.messaging.events import (
    DishPreparedEvent,
    EventName,
    KitchenDishRejectedEvent,
    OrderCreatedItem,
    OrderExpiredEvent,
    OrderWastedEvent,
)
from app.messaging.publisher import EventPublisher
from app.services.orders import OrderService
from tests.unit.api.conftest import FakeAsyncSession, FakeCatalogValidator, SpyOrderNotifier

pytestmark = [pytest.mark.unit]

EMPTY_CACHE: dict[str, str] = {}


def _make_connected_publisher() -> tuple[EventPublisher, AsyncMock]:
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
    return publisher, mock_exchange


def _build_dual_order(status: OrderStatus = OrderStatus.RESERVED) -> Order:
    order = Order(
        public_id=uuid4(),
        table_number=12,
        status=status,
    )
    order.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="recipe-physical-01",
            offer_id="offer-commercial-99",
            name="Enchiladas Suizas Promo",
            quantity=2,
            unit_price=Decimal("145.00"),
            status=ItemStatus.PENDING,
        )
    )
    order.recalculate_total()
    return order


@pytest.mark.asyncio
async def test_publisher_emits_dual_routing_keys_for_stage1_events() -> None:
    """Verify dual routing keys for rejected, wasted, prepared, and expired domain events."""
    publisher, mock_exchange = _make_connected_publisher()
    order_id = uuid4()
    item_id = uuid4()
    item = OrderCreatedItem(
        offerId="offer-commercial-99",
        plateId="recipe-physical-01",
        sourceId="recipe-physical-01",
        quantity=2,
    )

    events_and_expected_keys: list[tuple[EventName, Any, list[str]]] = [
        (
            EventName.KITCHEN_DISH_REJECTED,
            KitchenDishRejectedEvent.for_item(
                order_id,
                item_id,
                offer_id="offer-commercial-99",
                plate_id="recipe-physical-01",
                quantity=2,
                reason="Sin pollo",
            ),
            ["ordenes.platillo.rechazado_cocina", "ordenes.dish.rejected"],
        ),
        (
            EventName.ORDER_WASTED,
            OrderWastedEvent.for_order(order_id, items=[item], reason="ADMINISTRATIVE"),
            ["ordenes.comanda.mermada", "ordenes.order.wasted"],
        ),
        (
            EventName.DISH_PREPARED,
            DishPreparedEvent.for_item(
                order_id,
                item_id,
                offer_id="offer-commercial-99",
                plate_id="recipe-physical-01",
                source_id="recipe-physical-01",
                quantity=2,
            ),
            ["ordenes.platillo.preparado", "ordenes.dish.prepared"],
        ),
        (
            EventName.ORDER_EXPIRED,
            OrderExpiredEvent.for_order(order_id, elapsed_seconds=900, items=[item]),
            ["ordenes.orden.expirada", "ordenes.order.cancelled", "ordenes.order.expired"],
        ),
    ]

    for event_name, event_model, expected_routing_keys in events_and_expected_keys:
        mock_exchange.publish.reset_mock()
        payload = event_model.model_dump(mode="json", by_alias=True)
        await publisher.publish(
            event_type=event_name,
            payload=payload,
            event_id=event_model.event_id,
            correlation_id=event_model.correlation_id,
        )
        emitted_keys = [
            call.kwargs["routing_key"] for call in mock_exchange.publish.await_args_list
        ]
        assert emitted_keys == expected_routing_keys


@pytest.mark.asyncio
async def test_publisher_enriches_inventory_items_prioritizing_plate_and_source_id() -> None:
    """Verify secondary inventory emission maps plateId/sourceId ahead of offerId."""
    publisher, mock_exchange = _make_connected_publisher()
    order_id = uuid4()
    wasted_event = OrderWastedEvent.for_order(
        order_id,
        items=[
            OrderCreatedItem(
                offerId="offer-commercial-99",
                plateId="recipe-physical-01",
                sourceId="recipe-physical-01",
                quantity=3,
            )
        ],
        reason="KITCHEN_ERROR",
    )
    payload = wasted_event.model_dump(mode="json", by_alias=True)

    await publisher.publish(
        event_type=EventName.ORDER_WASTED,
        payload=payload,
        event_id=wasted_event.event_id,
        correlation_id=wasted_event.correlation_id,
    )

    assert mock_exchange.publish.await_count == 2
    secondary_call = mock_exchange.publish.await_args_list[1]
    assert secondary_call.kwargs["routing_key"] == "ordenes.order.wasted"
    body = json.loads(secondary_call.kwargs["message"].body.decode("utf-8"))
    assert body["items"][0]["plateId"] == "recipe-physical-01"
    assert body["items"][0]["sourceId"] == "recipe-physical-01"
    assert body["items"][0]["offerId"] == "offer-commercial-99"
    assert body["itemsAfectados"][0]["plateId"] == "recipe-physical-01"
    assert body["itemsAfectados"][0]["sourceId"] == "recipe-physical-01"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("routing_key", "payload", "expected_key", "expected_available"),
    [
        (
            EventName.MENU_SOURCE_CREATED,
            {"sourceId": "recipe-sope-01", "name": "Sope de Chorizo", "isAvailable": True},
            "catalog:platillo:recipe-sope-01",
            "true",
        ),
        (
            EventName.MENU_SOURCE_DELETED,
            {"sourceId": "recipe-sope-01"},
            "catalog:platillo:recipe-sope-01",
            "false",
        ),
        (
            EventName.INVENTORY_RECIPE_AVAILABILITY_CHANGED,
            {"plateId": "recipe-pozole-02", "isAvailable": False},
            "catalog:platillo:recipe-pozole-02",
            "false",
        ),
        (
            EventName.INVENTORY_RECIPE_NOT_AVAILABLE,
            {"sourceId": "recipe-tamal-03", "name": "Tamal Verde"},
            "catalog:platillo:recipe-tamal-03",
            "false",
        ),
    ],
)
async def test_dispatcher_routes_menu_source_and_inventory_recipe_events_to_catalog(
    routing_key: EventName,
    payload: dict[str, Any],
    expected_key: str,
    expected_available: str,
) -> None:
    """Verify EventConsumerDispatcher routes menu.source.* and inventory.recipe.* cleanly."""
    redis = AsyncMock()
    redis.hgetall.return_value = EMPTY_CACHE
    catalog_consumer = CatalogConsumer(CatalogRepository(redis))
    dispatcher = EventConsumerDispatcher(
        session_factory=lambda: AsyncMock(),
        dlq_router=InMemoryDLQRouter(),
        catalog_consumer=catalog_consumer,
    )

    dispatched = await dispatcher.dispatch_raw(
        raw_message=payload,
        queue_name="orders.catalog.inbound",
        event_type=routing_key.value,
    )

    assert dispatched is True
    redis.hset.assert_awaited_once()
    call_args = redis.hset.await_args
    assert call_args.args[0] == expected_key
    assert call_args.kwargs["mapping"]["available"] == expected_available


@pytest.mark.asyncio
async def test_order_service_stages_dual_identifiers_and_enforces_pbac_policies() -> None:
    """Verify OrderService writes plateId + offerId to outbox and honors PBAC permissions."""
    order = _build_dual_order(status=OrderStatus.RESERVED)
    fake_session = FakeAsyncSession({order.public_id: order})
    catalog = FakeCatalogValidator()
    service = OrderService(
        session=cast(AsyncSession, fake_session),
        notifier=SpyOrderNotifier(),
        catalog_repo=catalog,
    )

    revoked_chef = AuthContext(
        user_id="chef-revoked",
        role=Role.CHEF,
        roles=frozenset({Role.CHEF}),
        permissions=frozenset(),
    )
    with pytest.raises(ForbiddenRoleError):
        await service.start_order_preparation(order.public_id, revoked_chef)

    # Waiter with explicit PBAC permissions can execute kitchen transitions in service layer
    waiter_start = AuthContext(
        user_id="waiter-pbac",
        role=Role.WAITER,
        roles=frozenset({Role.WAITER}),
        permissions=START_PREPARATION.permissions,
    )
    await service.start_order_preparation(order.public_id, waiter_start)
    assert order.status is OrderStatus.IN_PREPARATION
    prep_outbox = fake_session.outbox_rows[-1]
    assert prep_outbox.event_type == EventName.ORDER_IN_PREPARATION
    prep_items = cast(list[dict[str, Any]], prep_outbox.payload["items"])
    assert prep_items[0]["plateId"] == "recipe-physical-01"
    assert prep_items[0]["sourceId"] == "recipe-physical-01"
    assert prep_items[0]["offerId"] == "offer-commercial-99"

    item = order.items[0]
    waiter_ready = AuthContext(
        user_id="waiter-pbac",
        role=Role.WAITER,
        roles=frozenset({Role.WAITER}),
        permissions=ITEM_READY.permissions,
    )
    await service.mark_item_ready(order.public_id, item.public_id, waiter_ready)
    ready_outbox = fake_session.outbox_rows[-1]
    assert ready_outbox.event_type == EventName.DISH_PREPARED
    assert ready_outbox.payload["plateId"] == "recipe-physical-01"
    assert ready_outbox.payload["sourceId"] == "recipe-physical-01"
    assert ready_outbox.payload["offerId"] == "offer-commercial-99"

    # Reset order item to PENDING on another order to verify kitchen_reject_item dual IDs
    reject_order = _build_dual_order(status=OrderStatus.RESERVED)
    fake_session.db[reject_order.public_id] = reject_order
    reject_item = reject_order.items[0]
    waiter_reject = AuthContext(
        user_id="waiter-pbac",
        role=Role.WAITER,
        roles=frozenset({Role.WAITER}),
        permissions=ITEM_REJECT.permissions,
    )
    await service.kitchen_reject_item(
        reject_order.public_id,
        reject_item.public_id,
        waiter_reject,
        reason="Sin insumo físico",
    )
    reject_outbox = fake_session.outbox_rows[-1]
    assert reject_outbox.event_type == EventName.KITCHEN_DISH_REJECTED
    assert reject_outbox.payload["plateId"] == "recipe-physical-01"
    assert reject_outbox.payload["offerId"] == "offer-commercial-99"
