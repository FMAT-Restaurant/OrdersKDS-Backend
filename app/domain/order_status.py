"""Order lifecycle state machine.

The only way to change an order's status is through
:func:`ensure_transition_allowed`, which enforces the allowed-transition
table below. Item sub-states live in :class:`ItemStatus` and are never
mixed with :class:`OrderStatus`.

Satisfies: RF-18, RF-24, RF-26, RF-28, RF-33, RF-34, RF-35 (ERS 1.3)
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.errors import InvalidStateTransitionError


class OrderStatus(StrEnum):
    """Lifecycle states of an order (normative codes, Guidelines 2.4)."""

    CREATED = "CREATED"
    IN_PREPARATION = "IN_PREPARATION"
    DELIVERED = "DELIVERED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    WASTED = "WASTED"
    VOIDED = "VOIDED"
    EXPIRED = "EXPIRED"


class ItemStatus(StrEnum):
    """Internal sub-states of a single order item."""

    PENDING = "PENDING"
    READY = "READY"
    DELIVERED = "DELIVERED"


_ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
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
    # Final states: no outgoing transitions.
    OrderStatus.PAID: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.VOIDED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


def ensure_transition_allowed(current: OrderStatus, target: OrderStatus) -> None:
    """Raise if ``current -> target`` is not part of the lifecycle.

    Args:
        current: Status the order is currently in.
        target: Status that was requested.

    Raises:
        InvalidStateTransitionError: If the transition is not allowed.
    """
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidStateTransitionError(current=current, target=target)
