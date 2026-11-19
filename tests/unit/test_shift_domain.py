"""Unit tests for operational kitchen Shift domain rules and model transitions.

Satisfies: RF-12, RF-15, RNF-03, RNF-09
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.models import Shift
from app.domain.errors import NoActiveShiftError, ShiftAlreadyOpenError, ShiftClosedError
from app.domain.shift import ShiftStatus, ensure_shift_can_close, ensure_shift_can_open

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-12", "RF-15", "RNF-03", "RNF-09"),
]


def test_ensure_shift_can_close_allows_open_and_rejects_closed() -> None:
    """Only OPEN shifts may be manually closed."""
    ensure_shift_can_close(ShiftStatus.OPEN)

    with pytest.raises(NoActiveShiftError):
        ensure_shift_can_close(ShiftStatus.CLOSED)


def test_ensure_shift_can_open_rejects_when_active_shift_exists() -> None:
    """Cannot open a new shift while another shift remains OPEN."""
    ensure_shift_can_open(False)

    with pytest.raises(ShiftAlreadyOpenError):
        ensure_shift_can_open(True)


def test_shift_model_allocates_sequential_folios_and_rejects_after_close() -> None:
    """Shift increments folios from 1 and blocks further allocation once closed."""
    actor_id = uuid4()
    shift = Shift(
        public_id=uuid4(),
        shift_number=1,
        status=ShiftStatus.OPEN,
        last_order_folio=0,
        opened_by=actor_id,
    )

    assert shift.allocate_next_folio() == 1
    assert shift.allocate_next_folio() == 2
    assert shift.last_order_folio == 2

    shift.close(closed_by=actor_id, notes="End of morning shift")
    assert shift.status == ShiftStatus.CLOSED
    assert shift.closed_by == actor_id
    assert shift.closed_at is not None
    assert shift.notes == "End of morning shift"

    with pytest.raises(ShiftClosedError):
        shift.allocate_next_folio()

    with pytest.raises(NoActiveShiftError):
        shift.close(closed_by=actor_id)
