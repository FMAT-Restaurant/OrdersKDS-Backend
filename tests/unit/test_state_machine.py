import pytest

from app.core.error_codes import ErrorCode
from app.domain.errors import InvalidStateTransitionError, VersionConflictError
from app.domain.state_machine import (
    ItemStatus,
    OrderStatus,
    ensure_transition_allowed,
)

VALID_TRANSITIONS = {
    (OrderStatus.CREATED, OrderStatus.IN_PREPARATION),
    (OrderStatus.CREATED, OrderStatus.REJECTED),
    (OrderStatus.CREATED, OrderStatus.CANCELLED),
    (OrderStatus.CREATED, OrderStatus.EXPIRED),
    (OrderStatus.IN_PREPARATION, OrderStatus.DELIVERED),
    (OrderStatus.IN_PREPARATION, OrderStatus.WASTED),
    (OrderStatus.IN_PREPARATION, OrderStatus.VOIDED),
    (OrderStatus.DELIVERED, OrderStatus.PAID),
    (OrderStatus.WASTED, OrderStatus.PAID),
}

INVALID_TRANSITIONS = [
    (current, target)
    for current in OrderStatus
    for target in OrderStatus
    if (current, target) not in VALID_TRANSITIONS
]


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderStatus.CREATED, OrderStatus.IN_PREPARATION),
        (OrderStatus.CREATED, OrderStatus.REJECTED),
        (OrderStatus.CREATED, OrderStatus.CANCELLED),
        (OrderStatus.CREATED, OrderStatus.EXPIRED),
        (OrderStatus.IN_PREPARATION, OrderStatus.DELIVERED),
        (OrderStatus.IN_PREPARATION, OrderStatus.WASTED),
        (OrderStatus.IN_PREPARATION, OrderStatus.VOIDED),
        (OrderStatus.DELIVERED, OrderStatus.PAID),
        (OrderStatus.WASTED, OrderStatus.PAID),
    ],
)
def test_allowed_transitions(current: OrderStatus, target: OrderStatus) -> None:
    ensure_transition_allowed(current, target)


def test_item_statuses_are_separate_from_order_statuses() -> None:
    assert tuple(ItemStatus) == (
        ItemStatus.PENDING,
        ItemStatus.READY,
        ItemStatus.DELIVERED,
    )


def test_invalid_transition_raises_stable_domain_error() -> None:
    with pytest.raises(InvalidStateTransitionError) as error:
        ensure_transition_allowed(OrderStatus.IN_PREPARATION, OrderStatus.CANCELLED)

    assert error.value.code is ErrorCode.INVALID_STATE_TRANSITION
    assert error.value.current is OrderStatus.IN_PREPARATION
    assert error.value.target is OrderStatus.CANCELLED


@pytest.mark.parametrize(("current", "target"), INVALID_TRANSITIONS)
def test_every_transition_outside_matrix_raises(current: OrderStatus, target: OrderStatus) -> None:
    with pytest.raises(InvalidStateTransitionError):
        ensure_transition_allowed(current, target)


def test_version_conflict_is_a_client_conflict() -> None:
    error = VersionConflictError()

    assert error.code is ErrorCode.VERSION_CONFLICT
    assert error.http_status == 409


@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.PAID,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.VOIDED,
        OrderStatus.EXPIRED,
    ],
)
def test_final_states_have_no_outgoing_transitions(status: OrderStatus) -> None:
    for target in OrderStatus:
        with pytest.raises(InvalidStateTransitionError):
            ensure_transition_allowed(status, target)
