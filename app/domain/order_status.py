"""Order lifecycle state machine.

The only way to change an order's status is through
:func:`ensure_transition_allowed`, which enforces the allowed-transition
table below. Item sub-states live in :class:`ItemStatus` and are governed
by :func:`ensure_item_transition_allowed`.

Satisfies: RF-09, RF-10, RF-11, RF-18, RF-22, RF-24, RF-26, RF-28, RF-33, RF-34, RF-35 (ERS 1.3)
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.errors import InvalidStateTransitionError


class OrderStatus(StrEnum):
    """Lifecycle states of an order (normative codes, Guidelines 2.4)."""

    CREATED = "CREATED"
    RESERVED = "RESERVED"
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
    CANCELLED = "CANCELLED"


_ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
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
    # Final states: no outgoing transitions.
    OrderStatus.PAID: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.VOIDED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}

_ALLOWED_ITEM_TRANSITIONS: dict[ItemStatus, frozenset[ItemStatus]] = {
    ItemStatus.PENDING: frozenset({ItemStatus.READY, ItemStatus.CANCELLED}),
    ItemStatus.READY: frozenset({ItemStatus.DELIVERED}),
    ItemStatus.DELIVERED: frozenset({ItemStatus.PENDING}),
    ItemStatus.CANCELLED: frozenset(),
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


def ensure_item_transition_allowed(current: ItemStatus, target: ItemStatus) -> None:
    """Raise if ``current -> target`` is not part of the item lifecycle (RF-09, RF-11, RF-22).

    Allowed item transitions:
    - PENDING -> READY (kitchen marks item ready)
    - PENDING -> CANCELLED (individual item cancellation before preparation)
    - READY -> DELIVERED (automatic when all items ready and order transitions)
    - DELIVERED -> PENDING (redo dish from kitchen, TASK-32)

    Args:
        current: Status the item is currently in.
        target: Status that was requested.

    Raises:
        InvalidStateTransitionError: If the transition is not allowed.
    """
    if target not in _ALLOWED_ITEM_TRANSITIONS.get(current, frozenset()):
        raise InvalidStateTransitionError(current=current, target=target)
