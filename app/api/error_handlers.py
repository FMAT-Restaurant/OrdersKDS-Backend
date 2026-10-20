"""Translation of domain errors into the client-facing error envelope.

Domain errors carry a stable ``ErrorCode``; the raw exception message and any internal
detail stay in the logs (Guidelines §5.6, §9.2).

Satisfies: RNF-03

TODO(RNF-03, TASK-10): add a catch-all handler for unexpected exceptions that logs with
``exc_info`` and answers ``500 INTERNAL_ERROR`` with a generic message (Guidelines §5.6), and
map ``HTTPException`` to the envelope once every route raises domain errors.
"""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.schemas.errors import ErrorBody, ErrorResponse
from app.core.error_codes import ErrorCode
from app.domain.errors import DomainError

VALIDATION_ERROR_MESSAGE = "Request validation failed."


def _envelope(code: ErrorCode, message: str, status_code: int) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code.value, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _handle_domain_error(_request: Request, exc: Exception) -> JSONResponse:
    error = cast(DomainError, exc)
    return _envelope(error.code, error.message, error.http_status)


def _handle_validation_error(_request: Request, _exc: Exception) -> JSONResponse:
    # Pydantic details echo the rejected input, so they are intentionally not returned.
    return _envelope(
        ErrorCode.VALIDATION_ERROR,
        VALIDATION_ERROR_MESSAGE,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Install the handlers that render every error with the standard envelope."""
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
