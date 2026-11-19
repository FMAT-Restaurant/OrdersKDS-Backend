"""Shifts router: manual kitchen shift lifecycle and per-shift folio status.

Opening and closing shifts manually is restricted strictly to ``CHEF`` (Master Chef)
and ``ADMIN`` via :data:`app.domain.access.SHIFT_MANAGE`. Reading shift status is
available to authenticated Kitchen and Waiter staff via :data:`SHIFT_VIEW`.

Satisfies: RF-12, RF-15, RNF-03, RNF-06
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, status

from app.api.dependencies.auth import AuthContext, require_access
from app.api.schemas.errors import ErrorResponse
from app.api.schemas.shift import ShiftActionRequest, ShiftResponse, ShiftStatusResponse
from app.domain.access import SHIFT_MANAGE, SHIFT_VIEW
from app.services.shifts import ShiftService, get_shift_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/shifts",
    tags=["shifts"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing, expired or malformed JWT (`UNAUTHENTICATED`).",
        },
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Caller lacks Master Chef or Admin permissions (`FORBIDDEN_ROLE`).",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": (
                "Invalid shift state transition or version conflict "
                "(`INVALID_STATE_TRANSITION`, `VERSION_CONFLICT`)."
            ),
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Request validation failed (`VALIDATION_ERROR`).",
        },
    },
)


@router.get(
    "/current",
    summary="Get active operational shift status and folio sequence",
    description=(
        "Returns the currently open shift or last closed shift alongside next shift metadata."
    ),
)
async def get_current_shift_status(
    _auth: Annotated[AuthContext, Depends(require_access(SHIFT_VIEW))],
    shift_service: Annotated[ShiftService, Depends(get_shift_service)],
) -> ShiftStatusResponse:
    """Return active shift state and folio sequence metadata."""
    snapshot = await shift_service.get_current_status()
    return ShiftStatusResponse.from_snapshot(snapshot)


@router.post(
    "/open",
    status_code=status.HTTP_201_CREATED,
    summary="Open a new operational shift (resetting order folios to 1)",
    description="Manually opens a new kitchen shift. Restricted to Master Chef and Admin.",
)
async def open_shift(
    auth: Annotated[AuthContext, Depends(require_access(SHIFT_MANAGE))],
    shift_service: Annotated[ShiftService, Depends(get_shift_service)],
    payload: Annotated[ShiftActionRequest | None, Body()] = None,
) -> ShiftResponse:
    """Open a new shift with per-shift order folio counter initialized to 0."""
    shift = await shift_service.open_shift(
        actor=auth,
        notes=payload.notes if payload is not None else None,
    )
    logger.info("Opened shift #%s by actor %s", shift.shift_number, auth.user_id)
    return ShiftResponse.from_model(shift)


@router.post(
    "/current/close",
    status_code=status.HTTP_200_OK,
    summary="Close the active operational shift",
    description=(
        "Manually closes the currently active kitchen shift. Restricted to Master Chef and Admin."
    ),
)
async def close_current_shift(
    auth: Annotated[AuthContext, Depends(require_access(SHIFT_MANAGE))],
    shift_service: Annotated[ShiftService, Depends(get_shift_service)],
    payload: Annotated[ShiftActionRequest | None, Body()] = None,
) -> ShiftResponse:
    """Close the currently active operational shift."""
    shift = await shift_service.close_current_shift(
        actor=auth,
        notes=payload.notes if payload is not None else None,
    )
    logger.info("Closed shift #%s by actor %s", shift.shift_number, auth.user_id)
    return ShiftResponse.from_model(shift)
