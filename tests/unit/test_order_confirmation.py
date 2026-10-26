"""Unit tests for idempotent order confirmation (UT-BE-13/14)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem, Outbox
from app.domain.errors import InvalidStateTransitionError
from app.domain.order_status import OrderStatus
from app.messaging.events import OrderCreatedEvent, OrderCreatedItem
from app.services.orders import confirm_order


def _order(
    status: OrderStatus = OrderStatus.CREATED,
    *,
    confirmed_at: datetime | None = None,
    confirmed_by: UUID | None = None,
) -> Order:
    order = Order(
        public_id=uuid4(),
        status=status,
        version=1,
        confirmed_at=confirmed_at,
        confirmed_by=confirmed_by,
    )
    order.items = [
        OrderItem(
            public_id=uuid4(),
            name="Tacos",
            quantity=2,
        )
    ]
    return order


def _session_with(order: Order) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = order
    return session


@pytest.mark.unit
async def test_first_confirmation_stamps_order_and_stages_validated_event() -> None:
    order = _order()
    actor_id = uuid4()
    session = _session_with(order)

    result = await confirm_order(session, order.public_id, actor_id)

    assert result is order
    assert order.confirmed_at is not None
    assert order.confirmed_at.tzinfo == UTC
    assert order.confirmed_by == actor_id
    statement = session.scalar.await_args.args[0]
    assert statement._for_update_arg is not None
    session.flush.assert_awaited_once()
    staged = session.add.call_args.args[0]
    assert isinstance(staged, Outbox)
    assert staged.event_type == "ordenes.orden.creada"
    assert staged.payload["orderId"] == str(order.public_id)
    validated = OrderCreatedEvent.model_validate(staged.payload)
    assert validated.items[0].item_id == order.items[0].public_id
    assert validated.items[0].quantity == 2


@pytest.mark.unit
async def test_confirmed_created_order_does_not_publish_again() -> None:
    order = _order(
        confirmed_at=datetime.now(UTC),
        confirmed_by=uuid4(),
    )
    session = _session_with(order)

    await confirm_order(session, order.public_id, uuid4())

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.unit
async def test_confirmed_created_order_repairs_missing_actor_without_event() -> None:
    order = _order(confirmed_at=datetime.now(UTC))
    actor_id = uuid4()
    session = _session_with(order)

    await confirm_order(session, order.public_id, actor_id)

    assert order.confirmed_by == actor_id
    session.add.assert_not_called()
    session.flush.assert_awaited_once()


@pytest.mark.parametrize(
    "order_status",
    [
        OrderStatus.IN_PREPARATION,
        OrderStatus.DELIVERED,
        OrderStatus.WASTED,
        OrderStatus.PAID,
    ],
)
@pytest.mark.unit
async def test_already_confirmed_status_returns_without_event(order_status: OrderStatus) -> None:
    order = _order(order_status)
    session = _session_with(order)

    result = await confirm_order(session, order.public_id, uuid4())

    assert result.status is order_status
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.parametrize(
    "order_status",
    [
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.VOIDED,
    ],
)
@pytest.mark.unit
async def test_terminal_status_is_rejected(order_status: OrderStatus) -> None:
    order = _order(order_status)
    session = _session_with(order)

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        await confirm_order(session, order.public_id, uuid4())

    assert exc_info.value.http_status == 409
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.unit
def test_order_created_event_rejects_payload_outside_json_schema_constraints() -> None:
    order_id = uuid4()
    with pytest.raises(ValidationError):
        OrderCreatedEvent.for_order(
            order_id,
            [OrderCreatedItem(itemId=uuid4(), name="", quantity=0)],
        )

    schema_path = Path(__file__).parents[2] / "contracts" / "events" / "order-created.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["required"] == ["eventId", "orderId", "items"]
