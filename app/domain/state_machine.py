"""Order lifecycle state machine.

Satisfies: RF-18, RF-24, RF-26, RF-28, RF-33, RF-34, RF-35.
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.errors import InvalidStateTransitionError


class OrderStatus(StrEnum):
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
    OrderStatus.PAID: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.VOIDED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


def ensure_transition_allowed(current: OrderStatus, target: OrderStatus) -> None:
    """Raise if ``current -> target`` is not part of the lifecycle."""
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidStateTransitionError(current=current, target=target)
