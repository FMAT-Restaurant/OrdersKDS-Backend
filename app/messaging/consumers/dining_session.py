"""Consumer for Floor dining-session events."""

from __future__ import annotations

import logging
from uuid import uuid4

from app.db.models import Order
from app.domain.errors import ShiftClosedError
from app.domain.order_status import OrderStatus
from app.messaging.events import DiningSessionCreatedMessage, EventName
from app.repositories.unit_of_work import UnitOfWork
from app.services.shifts import ShiftService

logger = logging.getLogger(__name__)


async def handle_dining_session_created(
    message: DiningSessionCreatedMessage,
    uow: UnitOfWork,
) -> bool:
    """Create one CREATED order for a dining session, ignoring redelivery."""
    async with uow:
        assert uow.orders is not None
        assert uow.processed_events is not None

        existing = await uow.orders.get_by_dining_session_id(message.session_id)
        if existing is not None:
            logger.info(
                "Dining session %s already has order %s",
                message.session_id,
                existing.public_id,
            )
            return False

        if uow.session is None:
            raise RuntimeError("UnitOfWork session is required to create an order")

        shift_id: int | None = None
        shift_folio: int | None = None
        try:
            shift, shift_folio = await ShiftService(uow.session).allocate_next_folio_for_order(
                actor_uuid=message.waiter_id
            )
            shift_id = shift.id
        except ShiftClosedError:
            logger.warning(
                "Dining session %s arrived while shift is closed; leaving shift_folio unset.",
                message.session_id,
            )

        order = Order(
            public_id=uuid4(),
            status=OrderStatus.CREATED,
            dining_session_id=message.session_id,
            responsible_waiter_id=message.waiter_id,
            shift_id=shift_id,
            shift_folio=shift_folio,
            items=[],
        )
        uow.orders.save(order)
        await uow.session.flush()
        uow.processed_events.add(
            event_id=message.event_id,
            order_id=order.public_id,
            event_type=EventName.DINING_SESSION_STARTED,
        )
        await uow.commit()
        return True
