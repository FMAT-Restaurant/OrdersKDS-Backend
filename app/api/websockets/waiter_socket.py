"""WebSocket endpoint for waiters and floor monitors.

Roles: waiter, admin.
Channels: floor, user:{userId}.

Satisfies: RNF-03, RNF-04, RF-18, SEC-09, OP-02 (ADR-0002)
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import APIRouter, WebSocket

from app.domain.roles import WAITER_ROLES, Role
from app.realtime.router import WAITER_WS_PATH, serve_websocket_connection

logger = logging.getLogger(__name__)

WAITER_ALLOWED_ROLES: Final[frozenset[Role]] = WAITER_ROLES

router = APIRouter()


@router.websocket(WAITER_WS_PATH)
async def waiter_websocket(websocket: WebSocket) -> None:
    """Waiter channel endpoint. Roles: waiter, admin."""
    await serve_websocket_connection(websocket, WAITER_WS_PATH, WAITER_ALLOWED_ROLES)


__all__ = [
    "WAITER_ALLOWED_ROLES",
    "WAITER_WS_PATH",
    "router",
    "waiter_websocket",
]
