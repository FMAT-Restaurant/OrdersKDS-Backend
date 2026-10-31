"""WebSocket endpoint for Kitchen Display System (KDS).

Roles: kitchen, chef, admin.
Channels: kds:station:{station}, kds:all.

Satisfies: RNF-03, RNF-04, RF-18, SEC-09, OP-02 (ADR-0002)
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import APIRouter, WebSocket

from app.domain.roles import KITCHEN_ROLES, Role
from app.realtime.router import KDS_WS_PATH, serve_websocket_connection

logger = logging.getLogger(__name__)

KDS_ALLOWED_ROLES: Final[frozenset[Role]] = KITCHEN_ROLES

router = APIRouter()


@router.websocket(KDS_WS_PATH)
async def kds_websocket(websocket: WebSocket) -> None:
    """Kitchen channel endpoint. Roles: kitchen, chef, admin."""
    await serve_websocket_connection(websocket, KDS_WS_PATH, KDS_ALLOWED_ROLES)


__all__ = [
    "KDS_ALLOWED_ROLES",
    "KDS_WS_PATH",
    "kds_websocket",
    "router",
]
