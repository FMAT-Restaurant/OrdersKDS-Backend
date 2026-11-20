"""Async repository for operational kitchen Shift aggregates.

Satisfies: RF-12, RF-15, RNF-03, RNF-09
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Shift
from app.domain.shift import ShiftStatus


class ShiftRepository:
    """Data-access abstraction for operational kitchen shifts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_shift(self, *, for_update: bool = False) -> Shift | None:
        """Return the currently OPEN shift ordered by latest shift_number."""
        stmt = (
            select(Shift)
            .where(Shift.status == ShiftStatus.OPEN)
            .order_by(Shift.shift_number.desc())
            .limit(1)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)

    async def get_latest_shift(self, *, for_update: bool = False) -> Shift | None:
        """Return the most recently created shift regardless of status."""
        stmt = select(Shift).order_by(Shift.shift_number.desc()).limit(1)
        if for_update:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)

    def save(self, shift: Shift) -> None:
        """Persist or track a Shift aggregate inside the unit-of-work session."""
        self._session.add(shift)
