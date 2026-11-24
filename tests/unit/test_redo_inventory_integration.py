"""Unit tests for dish redo Inventory integration helpers and domain invariants."""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import AuthContext
from app.db.models import Order, OrderItem
from app.domain.errors import DishUnavailableError, InvalidStateTransitionError
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role
from app.services.orders import OrderService
from app.workers.inbound_consumer import (
    DEFAULT_INVENTORY_EXCHANGE,
    DEFAULT_ORDERS_EXCHANGE,
    INBOUND_BINDINGS,
)
from tests.unit.api.conftest import FakeCatalogValidator, SpyOrderNotifier
from tests.unit.conftest import FakeAsyncSession

pytestmark = [pytest.mark.unit]


def test_order_item_revert_redo_to_delivered_enforces_pending_state() -> None:
    pending_item = OrderItem(
        public_id=uuid4(),
        name="Sope",
        quantity=1,
        status=ItemStatus.PENDING,
    )
    pending_item.revert_redo_to_delivered()
    assert pending_item.status is ItemStatus.DELIVERED

    with pytest.raises(InvalidStateTransitionError):
        pending_item.revert_redo_to_delivered()


def test_order_redo_rejection_metadata_lifecycle() -> None:
    first_item_id = uuid4()
    other_item_id = uuid4()
    order = Order(public_id=uuid4(), status=OrderStatus.DELIVERED, table_number=3)
    first_item = OrderItem(
        public_id=first_item_id,
        name="Pozole",
        quantity=1,
        unit_price=Decimal("95.00"),
        notes="Extra rábano",
        status=ItemStatus.DELIVERED,
    )
    order.items.append(first_item)

    order.save_pre_redo_note(first_item)
    first_item.mark_pending_for_redo("Llegó frío")
    order.record_redo_rejection(
        item=first_item,
        reason="Sin maíz pozolero",
        missing_ingredients=["Maíz"],
        redo_id=uuid4(),
    )
    assert isinstance(order.rejection_details, dict)
    assert "redoRejection" in order.rejection_details

    # Non-matching item_id preserves the recorded rejection metadata
    order.clear_redo_rejection(other_item_id)
    assert order.rejection_details is not None
    assert "redoRejection" in order.rejection_details

    # Restoring note and clearing matching item removes metadata completely
    order.restore_pre_redo_note(first_item)
    order.clear_redo_rejection(first_item_id)
    assert first_item.notes == "Extra rábano"
    assert order.rejection_details is None


@pytest.mark.asyncio
async def test_redo_validates_distinct_offer_id_and_mark_ready_clears_redo_rejection() -> None:
    order = Order(public_id=uuid4(), status=OrderStatus.DELIVERED, table_number=6)
    item = OrderItem(
        public_id=uuid4(),
        dish_id="PLATE-TACO-01",
        offer_id="OFFER-COMBO-01",
        name="Combo Tacos",
        quantity=1,
        unit_price=Decimal("120.00"),
        status=ItemStatus.DELIVERED,
    )
    order.items.append(item)

    db: dict[UUID, Order] = {order.public_id: order}
    session = FakeAsyncSession(db)
    catalog = FakeCatalogValidator(unavailable_ids={"OFFER-COMBO-01"})
    notifier = SpyOrderNotifier()
    service = OrderService(
        session=cast(AsyncSession, session),
        notifier=notifier,
        catalog_repo=catalog,
    )

    waiter_auth = AuthContext(
        user_id=str(uuid4()),
        role=Role.WAITER,
        permissions=frozenset({"orders:items:redo"}),
    )
    with pytest.raises(DishUnavailableError, match="OFFER-COMBO-01"):
        await service.redo_order_item(
            order.public_id,
            item.public_id,
            waiter_auth,
            note="Cambiar orden",
        )

    # Now allow offer, record prior redo rejection, transition to redo and complete in kitchen
    catalog.unavailable_ids.clear()
    order.record_redo_rejection(
        item=item,
        reason="Sin tortilla",
        missing_ingredients=["Tortilla"],
    )
    await service.redo_order_item(
        order.public_id,
        item.public_id,
        waiter_auth,
        note="Rehacer con doble tortilla",
    )
    assert order.rejection_details is not None
    assert "redoRejection" not in order.rejection_details

    order.record_redo_rejection(
        item=item,
        reason="Aviso anterior",
        missing_ingredients=[],
    )
    kitchen_auth = AuthContext(
        user_id=str(uuid4()),
        role=Role.KITCHEN,
        permissions=frozenset({"kitchen:items:ready"}),
    )
    await service.mark_item_ready(order.public_id, item.public_id, kitchen_auth)
    assert item.status is ItemStatus.DELIVERED
    assert order.rejection_details is None


def test_inbound_bindings_include_inventory_redo_events() -> None:
    assert (DEFAULT_INVENTORY_EXCHANGE, "inventory.redo.*") in INBOUND_BINDINGS
    assert (DEFAULT_ORDERS_EXCHANGE, "inventory.redo.*") in INBOUND_BINDINGS
