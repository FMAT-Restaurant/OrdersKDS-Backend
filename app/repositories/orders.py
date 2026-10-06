"""Persistence operations for orders."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order
from app.domain.errors import VersionConflictError
from app.domain.state_machine import OrderStatus


class OrderRepository:
    """Repository for versioned order updates."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def update_status(
        self,
        public_id: UUID,
        expected_version: int,
        target: OrderStatus,
    ) -> Order:
        """Update an order only when its version is still current."""
        statement = (
            update(Order)
            .where(Order.public_id == public_id, Order.version == expected_version)
            .values(status=target, version=Order.version + 1)
            .returning(Order)
        )
        result = await self._session.execute(statement)
        order = result.scalar_one_or_none()
        if order is None:
            raise VersionConflictError()
        return order
