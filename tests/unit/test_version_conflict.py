"""Optimistic-concurrency tests (RNF-09).

A caller that sends a stale ``version`` gets ``VERSION_CONFLICT``
(HTTP 409) instead of overwriting another writer.

Satisfies: RNF-09 (UT-BE-37)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm.exc import StaleDataError

from app.core.error_codes import ErrorCode
from app.db.models import Order
from app.domain.errors import OrderNotFoundError, VersionConflictError
from app.domain.order_status import OrderStatus
from app.repositories.orders import transition_order


def _order(version: int = 1) -> Order:
    return Order(
        public_id=uuid.uuid4(),
        status=OrderStatus.CREATED,
        version=version,
    )


def _session_with(order: Order | None) -> AsyncMock:
    session = AsyncMock()
    session.scalar.return_value = order
    return session


@pytest.mark.unit
async def test_stale_version_raises_version_conflict() -> None:
    order = _order(version=2)
    session = _session_with(order)
    with pytest.raises(VersionConflictError) as exc_info:
        await transition_order(session, order.public_id, OrderStatus.CANCELLED, expected_version=1)
    assert exc_info.value.code is ErrorCode.VERSION_CONFLICT
    assert exc_info.value.http_status == 409
    session.flush.assert_not_awaited()


@pytest.mark.unit
async def test_concurrent_flush_conflict_raises_version_conflict() -> None:
    order = _order(version=1)
    session = _session_with(order)
    session.flush.side_effect = StaleDataError("row was updated")
    with pytest.raises(VersionConflictError) as exc_info:
        await transition_order(session, order.public_id, OrderStatus.CANCELLED, expected_version=1)
    assert exc_info.value.code is ErrorCode.VERSION_CONFLICT
    assert exc_info.value.http_status == 409


@pytest.mark.unit
async def test_unknown_public_id_raises_order_not_found() -> None:
    session = _session_with(None)
    public_id = uuid.uuid4()
    with pytest.raises(OrderNotFoundError) as exc_info:
        await transition_order(session, public_id, OrderStatus.CANCELLED, expected_version=1)
    assert exc_info.value.code is ErrorCode.ORDER_NOT_FOUND
    assert exc_info.value.http_status == 404


@pytest.mark.unit
async def test_current_version_transitions_and_returns_order() -> None:
    order = _order(version=1)
    session = _session_with(order)
    result = await transition_order(
        session, order.public_id, OrderStatus.CANCELLED, expected_version=1
    )
    assert result.status is OrderStatus.CANCELLED
    session.flush.assert_awaited_once()
