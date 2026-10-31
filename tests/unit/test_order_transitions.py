"""Parametrized matrix tests for the order lifecycle state machine.

Covers the full 10x10 transition matrix: every (current, target) pair is
exercised, valid jumps pass silently and invalid jumps raise
``InvalidStateTransitionError`` (mapped to HTTP 409).
Also covers the 4x4 item state transition matrix.

Satisfies: RF-09, RF-10, RF-11, RF-18, RF-22, RF-24, RF-26, RF-28, RNF-03
"""

from __future__ import annotations

import itertools

import pytest

from app.core.error_codes import ErrorCode
from app.domain import order_status as sm
from app.domain.errors import InvalidStateTransitionError
from app.domain.order_status import (
    ItemStatus,
    OrderStatus,
    ensure_item_transition_allowed,
    ensure_transition_allowed,
)

ALL_STATUSES: list[OrderStatus] = list(OrderStatus)

# Independent oracle of the lifecycle (hardcoded here so the test does not
# tautologically import the implementation map).
EXPECTED_VALID: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset(
        {
            OrderStatus.RESERVED,
            OrderStatus.IN_PREPARATION,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.RESERVED: frozenset(
        {
            OrderStatus.IN_PREPARATION,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
        }
    ),
    OrderStatus.REJECTED: frozenset(
        {
            OrderStatus.CREATED,
            OrderStatus.CANCELLED,
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


def test_matrix_covers_full_10x10() -> None:
    assert len(ALL_STATUSES) == 10
    assert len(ALL_PAIRS) == 100
    assert len(VALID_PAIRS) == 15
    assert len(INVALID_PAIRS) == 85


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


ALL_ITEM_STATUSES: list[ItemStatus] = list(ItemStatus)

EXPECTED_VALID_ITEM_TRANSITIONS: dict[ItemStatus, frozenset[ItemStatus]] = {
    ItemStatus.PENDING: frozenset({ItemStatus.READY, ItemStatus.CANCELLED}),
    ItemStatus.READY: frozenset({ItemStatus.DELIVERED}),
    ItemStatus.DELIVERED: frozenset({ItemStatus.PENDING}),
    ItemStatus.CANCELLED: frozenset(),
}

ALL_ITEM_PAIRS: list[tuple[ItemStatus, ItemStatus]] = [
    (current, target)
    for current, target in itertools.product(ALL_ITEM_STATUSES, ALL_ITEM_STATUSES)
]

VALID_ITEM_PAIRS: list[tuple[ItemStatus, ItemStatus]] = [
    pair for pair in ALL_ITEM_PAIRS if pair[1] in EXPECTED_VALID_ITEM_TRANSITIONS[pair[0]]
]

INVALID_ITEM_PAIRS: list[tuple[ItemStatus, ItemStatus]] = [
    pair for pair in ALL_ITEM_PAIRS if pair[1] not in EXPECTED_VALID_ITEM_TRANSITIONS[pair[0]]
]


def test_item_matrix_covers_full_4x4() -> None:
    assert len(ALL_ITEM_STATUSES) == 4
    assert len(ALL_ITEM_PAIRS) == 16
    assert len(VALID_ITEM_PAIRS) == 4
    assert len(INVALID_ITEM_PAIRS) == 12


@pytest.mark.unit
@pytest.mark.parametrize(("current", "target"), VALID_ITEM_PAIRS)
def test_valid_item_transition_does_not_raise(current: ItemStatus, target: ItemStatus) -> None:
    ensure_item_transition_allowed(current, target)


@pytest.mark.unit
@pytest.mark.parametrize(("current", "target"), INVALID_ITEM_PAIRS)
def test_invalid_item_transition_raises_409_error(current: ItemStatus, target: ItemStatus) -> None:
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        ensure_item_transition_allowed(current, target)
    err = exc_info.value
    assert err.code is ErrorCode.INVALID_STATE_TRANSITION
    assert err.http_status == 409
    assert err.current is current
    assert err.target is target


def test_allowed_item_transitions_table_is_complete_and_immutable() -> None:
    assert set(sm._ALLOWED_ITEM_TRANSITIONS.keys()) == set(ALL_ITEM_STATUSES)
    for targets in sm._ALLOWED_ITEM_TRANSITIONS.values():
        assert isinstance(targets, frozenset)
    for current in ALL_ITEM_STATUSES:
        assert sm._ALLOWED_ITEM_TRANSITIONS[current] == EXPECTED_VALID_ITEM_TRANSITIONS[current]
