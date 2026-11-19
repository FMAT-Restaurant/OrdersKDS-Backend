"""Pydantic schemas for manual shift lifecycle endpoints.

Satisfies: RF-12, RF-15, RNF-03, RNF-06
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import Shift
from app.domain.constants import MAX_NOTE_LENGTH
from app.domain.shift import ShiftStatus
from app.services.shifts import ShiftStatusSnapshot


class ShiftActionRequest(BaseModel):
    """Optional payload when manually opening or closing a shift."""

    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)

    @field_validator("notes", mode="before")
    @classmethod
    def _normalize_notes(cls, raw: object) -> object:
        if isinstance(raw, str):
            stripped = raw.strip()
            return stripped or None
        return raw


class ShiftResponse(BaseModel):
    """Public representation of an operational kitchen shift."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    shift_id: UUID = Field(alias="shiftId")
    shift_number: int = Field(alias="shiftNumber")
    status: ShiftStatus
    last_order_folio: int = Field(alias="lastOrderFolio")
    opened_at: datetime = Field(alias="openedAt")
    opened_by: UUID | None = Field(default=None, alias="openedBy")
    closed_at: datetime | None = Field(default=None, alias="closedAt")
    closed_by: UUID | None = Field(default=None, alias="closedBy")
    notes: str | None = None
    version: int

    @classmethod
    def from_model(cls, shift: Shift) -> ShiftResponse:
        """Build API schema from a Shift ORM aggregate."""
        now = datetime.now(UTC)
        public_id = shift.public_id or uuid4()
        return cls(
            id=str(public_id),
            shiftId=public_id,
            shiftNumber=shift.shift_number,
            status=shift.status,
            lastOrderFolio=shift.last_order_folio or 0,
            openedAt=shift.opened_at or now,
            openedBy=shift.opened_by,
            closedAt=shift.closed_at,
            closedBy=shift.closed_by,
            notes=shift.notes,
            version=shift.version if shift.version is not None else 1,
        )


class ShiftStatusResponse(BaseModel):
    """Aggregate shift status payload for Floor and KDS clients."""

    model_config = ConfigDict(populate_by_name=True)

    is_open: bool = Field(alias="isOpen")
    can_accept_orders: bool = Field(alias="canAcceptOrders")
    current_shift: ShiftResponse | None = Field(default=None, alias="currentShift")
    last_closed_shift: ShiftResponse | None = Field(default=None, alias="lastClosedShift")
    next_shift_number: int = Field(alias="nextShiftNumber")

    @classmethod
    def from_snapshot(cls, snapshot: ShiftStatusSnapshot) -> ShiftStatusResponse:
        """Build status response from a domain snapshot."""
        return cls(
            isOpen=snapshot.active_shift is not None,
            canAcceptOrders=snapshot.can_accept_orders,
            currentShift=(
                ShiftResponse.from_model(snapshot.active_shift)
                if snapshot.active_shift is not None
                else None
            ),
            lastClosedShift=(
                ShiftResponse.from_model(snapshot.last_closed_shift)
                if snapshot.last_closed_shift is not None
                else None
            ),
            nextShiftNumber=snapshot.next_shift_number,
        )
