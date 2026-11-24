"""ASGI middleware for X-Correlation-ID propagation and structured access logs.

Satisfies: DEF-E5-016, RNF-01, RNF-12
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from uuid import uuid4

from app.core.logging import (
    CORRELATION_ID_HEADER,
    parse_or_generate_correlation_id,
    reset_correlation_id,
    reset_log_correlation_id,
    set_correlation_id,
    set_log_correlation_id,
)

logger = logging.getLogger("app.api.access")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_CORRELATION_HEADER_BYTES = CORRELATION_ID_HEADER.lower().encode("latin-1")


def _extract_header(scope: Scope, header_name: bytes) -> str | None:
    raw_headers = scope.get("headers") or ()
    for name, value in raw_headers:
        if isinstance(name, bytes) and name.lower() == header_name and isinstance(value, bytes):
            return value.decode("latin-1")
    return None


class CorrelationIdMiddleware:
    """Pure ASGI middleware binding X-Correlation-ID across HTTP and WebSocket scopes."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self._app(scope, receive, send)
            return

        raw_header = _extract_header(scope, _CORRELATION_HEADER_BYTES)
        explicit_cid = (
            parse_or_generate_correlation_id(raw_header)
            if raw_header and raw_header.strip()
            else None
        )
        trace_cid = explicit_cid or uuid4()
        explicit_token = set_correlation_id(explicit_cid)
        log_token = set_log_correlation_id(trace_cid)

        if scope_type == "websocket":
            try:
                await self._app(scope, receive, send)
            finally:
                reset_log_correlation_id(log_token)
                reset_correlation_id(explicit_token)
            return

        start = time.perf_counter()
        status_code = 500
        corr_bytes = str(trace_cid).encode("latin-1")

        async def _send_with_correlation(message: Message) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 200))
                headers = [
                    (k, v)
                    for k, v in (message.get("headers") or [])
                    if not (isinstance(k, bytes) and k.lower() == _CORRELATION_HEADER_BYTES)
                ]
                headers.append((_CORRELATION_HEADER_BYTES, corr_bytes))
                message["headers"] = headers
            await send(message)

        try:
            await self._app(scope, receive, _send_with_correlation)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            path = str(scope.get("path", ""))
            method = str(scope.get("method", ""))
            log_level = logging.DEBUG if path.endswith("/health") else logging.INFO
            logger.log(
                log_level,
                "%s %s -> %s (%.2f ms)",
                method,
                path,
                status_code,
                duration_ms,
                extra={
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "correlation_id": str(trace_cid),
                },
            )
            reset_log_correlation_id(log_token)
            reset_correlation_id(explicit_token)
