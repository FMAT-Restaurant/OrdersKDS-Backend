"""Stable error codes exposed by the application contract."""

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    VERSION_CONFLICT = "VERSION_CONFLICT"
