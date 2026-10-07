"""Central registry of stable error codes.

Every error condition surfaced to clients carries one of these codes;
raw exception messages never leave the service.

Satisfies: RNF-03
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable codes shared with the frontend mirror (src/shared/api/errorCodes.ts)."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN_ROLE = "FORBIDDEN_ROLE"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    NOTE_REQUIRED = "NOTE_REQUIRED"
    DISH_UNAVAILABLE = "DISH_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
