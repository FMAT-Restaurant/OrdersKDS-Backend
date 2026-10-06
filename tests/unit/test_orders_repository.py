from datetime import UTC
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, utc_now
from app.domain.errors import VersionConflictError
from app.domain.state_machine import OrderStatus
from app.repositories.orders import OrderRepository


def build_session(result: object) -> AsyncSession:
    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_update_status_increments_version_and_returns_order() -> None:
    order = Order(public_id=uuid4(), status=OrderStatus.IN_PREPARATION, version=2)
    result = Mock()
    result.scalar_one_or_none.return_value = order
    session = build_session(result)

    updated = await OrderRepository(session).update_status(
        order.public_id, 1, OrderStatus.IN_PREPARATION
    )

    assert updated is order


@pytest.mark.asyncio
async def test_update_status_raises_version_conflict_when_order_is_stale() -> None:
    result = Mock()
    result.scalar_one_or_none.return_value = None
    session = build_session(result)

    with pytest.raises(VersionConflictError):
        await OrderRepository(session).update_status(uuid4(), 1, OrderStatus.PAID)


def test_utc_now_returns_timezone_aware_utc_datetime() -> None:
    assert utc_now().tzinfo is UTC
