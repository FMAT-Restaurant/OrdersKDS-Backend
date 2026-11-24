"""Structured JSON logging and correlation ID context management.

Satisfies: DEF-E5-016, RNF-01, RNF-12
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

CORRELATION_ID_HEADER: Final[str] = "X-Correlation-ID"

_correlation_id_ctx: ContextVar[UUID | None] = ContextVar("correlation_id", default=None)
_log_correlation_id_ctx: ContextVar[UUID | None] = ContextVar("log_correlation_id", default=None)

_STANDARD_LOG_RECORD_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def get_correlation_id() -> UUID | None:
    """Return the explicit caller-supplied correlation UUID for domain event propagation."""
    return _correlation_id_ctx.get()


def set_correlation_id(value: UUID | None) -> Token[UUID | None]:
    """Bind an explicit correlation UUID for downstream Outbox/event propagation."""
    return _correlation_id_ctx.set(value)


def reset_correlation_id(token: Token[UUID | None]) -> None:
    """Restore the previous explicit correlation UUID context."""
    _correlation_id_ctx.reset(token)


def get_log_correlation_id() -> UUID | None:
    """Return the active trace/correlation UUID for structured log enrichment."""
    return _correlation_id_ctx.get() or _log_correlation_id_ctx.get()


def set_log_correlation_id(value: UUID | None) -> Token[UUID | None]:
    """Bind a request-scoped trace UUID used when no explicit header was supplied."""
    return _log_correlation_id_ctx.set(value)


def reset_log_correlation_id(token: Token[UUID | None]) -> None:
    """Restore the previous request-scoped trace UUID."""
    _log_correlation_id_ctx.reset(token)


def parse_or_generate_correlation_id(raw: str | None) -> UUID:
    """Parse an incoming correlation ID header or normalize/generate a UUID.

    Valid UUID strings are preserved as-is; arbitrary non-empty caller tokens map
    deterministically to UUIDv5; absent/blank headers generate a fresh UUIDv4.
    """
    cleaned = raw.strip() if raw else ""
    if not cleaned:
        return uuid4()
    try:
        return UUID(cleaned)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"correlation:{cleaned}")


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


class JsonLogFormatter(logging.Formatter):
    """Render Python LogRecord instances as single-line structured JSON."""

    def __init__(self, *, service: str = "orders-backend") -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        record_corr = record.__dict__.get("correlation_id")
        active_corr = get_log_correlation_id()
        if record_corr is not None:
            correlation_str: str | None = str(record_corr)
        elif active_corr is not None:
            correlation_str = str(active_corr)
        else:
            correlation_str = None

        payload: dict[str, object] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "message": record.getMessage(),
            "correlation_id": correlation_str,
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_ATTRS or key == "correlation_id" or key.startswith("_"):
                continue
            payload[key] = _json_safe(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_structured_logging(
    *,
    service: str = "orders-backend",
    level: str | None = None,
) -> None:
    """Configure root logging with single-line JSON output to stdout."""
    raw_level = level if level is not None else os.getenv("LOG_LEVEL", "INFO")
    resolved_level = raw_level.strip().upper() or "INFO"
    numeric_level = getattr(logging, resolved_level, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter(service=service))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)

    for uvicorn_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(uvicorn_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(handler)
        uvicorn_logger.setLevel(numeric_level)
        uvicorn_logger.propagate = False

    if service == "orders-backend":
        access_logger = logging.getLogger("uvicorn.access")
        access_logger.handlers.clear()
        access_logger.propagate = False
        access_logger.disabled = True
