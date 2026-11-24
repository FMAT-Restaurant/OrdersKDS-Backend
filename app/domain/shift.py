"""Domain rules for operational kitchen shifts and per-shift order folios.

Satisfies: RF-12, RF-15, RNF-03, RNF-09
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.errors import NoActiveShiftError, ShiftAlreadyOpenError


class ShiftStatus(StrEnum):
    """Lifecycle states of an operational shift."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


def ensure_shift_can_close(status: ShiftStatus) -> None:
    """Validate that a shift can transition to CLOSED."""
    if status != ShiftStatus.OPEN:
        raise NoActiveShiftError("Current shift is already closed.")


def ensure_shift_can_open(has_active_shift: bool) -> None:
    """Validate that a new shift can be opened manually."""
    if has_active_shift:
        raise ShiftAlreadyOpenError(
            "An open shift is already active; close it before opening a new one."
        )
