"""Domain errors for the orders bounded context.

Domain errors carry a stable :class:`ErrorCode`; the API layer maps
them to HTTP status codes. Detailed context goes to logs, never to
the client payload.

Satisfies: RF-24, RF-26, RF-28, RNF-03, RNF-09
"""

from __future__ import annotations

from app.core.error_codes import ErrorCode


class DomainError(Exception):
    """Base class of all domain errors."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = 500

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code.value)
        self.message = message or self.code.value


class OrderNotFoundError(DomainError):
    """Raised when no order matches the requested public id."""

    code: ErrorCode = ErrorCode.ORDER_NOT_FOUND
    http_status: int = 404


class InvalidStateTransitionError(DomainError):
    """Raised when ``current -> target`` is not part of the lifecycle.

    Args:
        current: Status the order is currently in.
        target: Status that was requested.
    """

    code: ErrorCode = ErrorCode.INVALID_STATE_TRANSITION
    http_status: int = 409

    def __init__(self, current: object, target: object) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current} to {target}.")


class VersionConflictError(DomainError):
    """Raised when an update carries a stale optimistic-concurrency version."""

    code: ErrorCode = ErrorCode.VERSION_CONFLICT
    http_status: int = 409
