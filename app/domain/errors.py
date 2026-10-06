"""Domain errors and stable error codes."""

from __future__ import annotations

from app.core.error_codes import ErrorCode


class DomainError(Exception):
    """Base class for errors raised by domain rules."""

    code: ErrorCode
    http_status: int = 409


class InvalidStateTransitionError(DomainError):
    """Raised when an order transition is not allowed."""

    code = ErrorCode.INVALID_STATE_TRANSITION

    def __init__(self, current: OrderStatusValue, target: OrderStatusValue) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Transition from {current} to {target} is not allowed")


class VersionConflictError(DomainError):
    """Raised when an optimistic-lock update affects no row."""

    code = ErrorCode.VERSION_CONFLICT

    def __init__(self) -> None:
        super().__init__("The resource was modified by another request")


type OrderStatusValue = str
