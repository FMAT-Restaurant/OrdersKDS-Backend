"""Parametrized matrix tests for the order lifecycle state machine.

Covers the full 9x9 transition matrix: every (current, target) pair is
exercised, valid jumps pass silently and invalid jumps raise
``InvalidStateTransitionError`` (mapped to HTTP 409).

Satisfies: RF-18, RF-24, RF-26, RF-28, RNF-03
"""

from __future__ import annotations

import itertools

import pytest

from app.core.error_codes import ErrorCode
from app.domain import order_status as sm
from app.domain.errors import InvalidStateTransitionError
from app.domain.order_status import OrderStatus, ensure_transition_allowed

ALL_STATUSES: list[OrderStatus] = list(OrderStatus)

# Independent oracle of the lifecycle (mirrors Guidelines 5.4, hardcoded
# here so the test does not tautologically import the implementation map).
EXPECTED_VALID: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset(
        {
            OrderStatus.IN_PREPARATION,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.IN_PREPARATION: frozenset(
        {
            OrderStatus.DELIVERED,
            OrderStatus.WASTED,
            OrderStatus.VOIDED,
        }
    ),
    OrderStatus.DELIVERED: frozenset({OrderStatus.PAID}),
    OrderStatus.WASTED: frozenset({OrderStatus.PAID}),
    OrderStatus.PAID: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.VOIDED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}

ALL_PAIRS: list[tuple[OrderStatus, OrderStatus]] = [
    (current, target) for current, target in itertools.product(ALL_STATUSES, ALL_STATUSES)
]


def _is_valid(current: OrderStatus, target: OrderStatus) -> bool:
    return target in EXPECTED_VALID[current]


VALID_PAIRS: list[tuple[OrderStatus, OrderStatus]] = [
    pair for pair in ALL_PAIRS if _is_valid(*pair)
]
INVALID_PAIRS: list[tuple[OrderStatus, OrderStatus]] = [
    pair for pair in ALL_PAIRS if not _is_valid(*pair)
]


def test_matrix_covers_full_9x9() -> None:
    assert len(ALL_STATUSES) == 9
    assert len(ALL_PAIRS) == 81
    assert len(VALID_PAIRS) == 9
    assert len(INVALID_PAIRS) == 72


@pytest.mark.unit
@pytest.mark.parametrize(("current", "target"), VALID_PAIRS)
def test_valid_transition_does_not_raise(current: OrderStatus, target: OrderStatus) -> None:
    ensure_transition_allowed(current, target)


@pytest.mark.unit
@pytest.mark.parametrize(("current", "target"), INVALID_PAIRS)
def test_invalid_transition_raises_409_error(current: OrderStatus, target: OrderStatus) -> None:
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        ensure_transition_allowed(current, target)
    err = exc_info.value
    assert err.code is ErrorCode.INVALID_STATE_TRANSITION
    assert err.http_status == 409
    assert err.current is current
    assert err.target is target


def test_allowed_transitions_table_is_complete_and_immutable() -> None:
    assert set(sm._ALLOWED_TRANSITIONS.keys()) == set(ALL_STATUSES)
    for targets in sm._ALLOWED_TRANSITIONS.values():
        assert isinstance(targets, frozenset)
    for current in ALL_STATUSES:
        assert sm._ALLOWED_TRANSITIONS[current] == EXPECTED_VALID[current]
