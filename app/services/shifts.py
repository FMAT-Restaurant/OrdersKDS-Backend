"""Application service for operational shifts and per-shift folio allocation.

Satisfies: RF-12, RF-15, RNF-03, RNF-09
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.api.dependencies.auth import AuthContext
from app.db.models import Shift
from app.db.session import get_session
from app.domain.access import SHIFT_MANAGE
from app.domain.errors import (
    ForbiddenRoleError,
    NoActiveShiftError,
    ShiftClosedError,
    VersionConflictError,
)
from app.domain.roles import parse_actor_uuid
from app.domain.shift import ShiftStatus, ensure_shift_can_open
from app.repositories.shifts import ShiftRepository


@dataclass(frozen=True, slots=True)
class ShiftStatusSnapshot:
    """Current shift state and next folio sequence metadata."""

    active_shift: Shift | None
    last_closed_shift: Shift | None
    next_shift_number: int
    can_accept_orders: bool


class ShiftService:
    """Orchestrates shift lifecycle operations and sequential per-shift order folios."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._shifts = ShiftRepository(session)

    async def _commit_or_raise_conflict(self, shift_label: str) -> None:
        """Commit transaction or rollback and raise 409 on optimistic concurrency conflict."""
        try:
            await self._session.commit()
        except StaleDataError as exc:
            await self._session.rollback()
            raise VersionConflictError(f"Concurrent update lost for {shift_label}.") from exc

    async def get_current_status(self) -> ShiftStatusSnapshot:
        """Return active or last closed shift status along with the next shift number."""
        active_shift = await self._shifts.get_active_shift()
        latest_shift = await self._shifts.get_latest_shift()
        last_closed = (
            latest_shift
            if latest_shift is not None and latest_shift.status == ShiftStatus.CLOSED
            else None
        )
        next_shift_number = (latest_shift.shift_number + 1) if latest_shift is not None else 1
        can_accept_orders = active_shift is not None or latest_shift is None
        return ShiftStatusSnapshot(
            active_shift=active_shift,
            last_closed_shift=last_closed,
            next_shift_number=next_shift_number,
            can_accept_orders=can_accept_orders,
        )

    async def allocate_next_folio_for_order(
        self,
        *,
        actor_uuid: UUID | None = None,
    ) -> tuple[Shift, int]:
        """Allocate the next 1-based folio from the active shift without committing."""
        active = await self._shifts.get_active_shift(for_update=True)
        if active is None:
            latest = await self._shifts.get_latest_shift(for_update=True)
            if latest is not None:
                raise ShiftClosedError()
            active = Shift(
                public_id=uuid4(),
                shift_number=1,
                status=ShiftStatus.OPEN,
                last_order_folio=0,
                opened_by=actor_uuid,
                notes="Initial shift auto-opened",
            )
            self._shifts.save(active)
            await self._session.flush()

        folio = active.allocate_next_folio()
        self._shifts.save(active)
        return active, folio

    async def open_shift(
        self,
        *,
        actor: AuthContext,
        notes: str | None = None,
    ) -> Shift:
        """Manually open a new operational shift with folio counter reset to zero."""
        if not actor.is_authorized(SHIFT_MANAGE):
            raise ForbiddenRoleError("Only Master Chef or Admin can open a shift.")

        active = await self._shifts.get_active_shift(for_update=True)
        ensure_shift_can_open(active is not None)

        latest = await self._shifts.get_latest_shift(for_update=True)
        next_number = (latest.shift_number + 1) if latest is not None else 1

        shift = Shift(
            public_id=uuid4(),
            shift_number=next_number,
            status=ShiftStatus.OPEN,
            last_order_folio=0,
            opened_by=parse_actor_uuid(actor.user_id),
            notes=notes,
        )
        self._shifts.save(shift)
        await self._commit_or_raise_conflict(f"shift #{next_number}")
        return shift

    async def close_current_shift(
        self,
        *,
        actor: AuthContext,
        notes: str | None = None,
    ) -> Shift:
        """Manually close the currently active shift."""
        if not actor.is_authorized(SHIFT_MANAGE):
            raise ForbiddenRoleError("Only Master Chef or Admin can close a shift.")

        active = await self._shifts.get_active_shift(for_update=True)
        if active is None:
            raise NoActiveShiftError()

        active.close(closed_by=parse_actor_uuid(actor.user_id), notes=notes)
        self._shifts.save(active)
        await self._commit_or_raise_conflict(f"shift #{active.shift_number}")
        return active


def get_shift_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ShiftService:
    """FastAPI dependency provider for ShiftService."""
    return ShiftService(session=session)
