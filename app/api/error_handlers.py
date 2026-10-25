"""Translation of domain errors and exceptions into the client-facing error envelope.

Domain errors carry a stable ``ErrorCode``; raw exception messages and internal
details stay in the server logs (Guidelines §5.6, §9.2).

Satisfies: RNF-03
"""

from __future__ import annotations

import logging
from typing import cast

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.schemas.errors import ErrorBody, ErrorResponse
from app.core.error_codes import ErrorCode
from app.domain.errors import DomainError

logger = logging.getLogger(__name__)

VALIDATION_ERROR_MESSAGE = "Request validation failed."
INTERNAL_ERROR_MESSAGE = "An internal server error occurred."


def _envelope(code: ErrorCode, message: str, status_code: int) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code.value, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _handle_domain_error(_request: Request, exc: Exception) -> JSONResponse:
    error = cast(DomainError, exc)
    return _envelope(error.code, error.message, error.http_status)


def _handle_validation_error(_request: Request, _exc: Exception) -> JSONResponse:
    # Pydantic details echo rejected input, so they are intentionally not returned.
    return _envelope(
        ErrorCode.VALIDATION_ERROR,
        VALIDATION_ERROR_MESSAGE,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


def _handle_http_exception(_request: Request, exc: Exception) -> JSONResponse:
    http_exc = cast(HTTPException, exc)
    code = ErrorCode.INTERNAL_ERROR
    if http_exc.status_code == status.HTTP_401_UNAUTHORIZED:
        code = ErrorCode.UNAUTHENTICATED
    elif http_exc.status_code == status.HTTP_403_FORBIDDEN:
        code = ErrorCode.FORBIDDEN_ROLE
    elif http_exc.status_code == status.HTTP_404_NOT_FOUND:
        code = ErrorCode.ORDER_NOT_FOUND
    return _envelope(code, http_exc.detail, http_exc.status_code)


def _handle_unexpected_exception(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unexpected unhandled server exception: %s", exc)
    return _envelope(
        ErrorCode.INTERNAL_ERROR,
        INTERNAL_ERROR_MESSAGE,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Install the handlers that render every error with the standard envelope."""
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(HTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_exception)
