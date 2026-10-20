"""Client-facing error envelope (Guidelines §9.2).

Satisfies: RNF-03
"""

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """Stable error code plus a generic message; never internal details."""

    code: str = Field(description="Stable ErrorCode shared with the frontend mirror.")
    message: str = Field(description="Generic, client-safe description.")


class ErrorResponse(BaseModel):
    """Envelope returned for every error handled by the API."""

    error: ErrorBody
