"""WebSocket endpoints combining KDS and waiter channels.

Role matrix (OP-02 / ADR-0002): ``/ws/kds`` -> kitchen, chef, admin;
``/ws/waiter`` -> waiter, admin. An unauthenticated caller is closed with ``4401`` and a
role that does not match the endpoint with ``4403``.

Satisfies: RNF-03, RNF-04, RF-18, SEC-09, OP-02 (ADR-0002)
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Final

from fastapi import APIRouter

from app.api.websockets.kds_socket import (
    KDS_ALLOWED_ROLES,
    KDS_WS_PATH,
    kds_websocket,
)
from app.api.websockets.kds_socket import (
    router as kds_router,
)
from app.api.websockets.waiter_socket import (
    WAITER_ALLOWED_ROLES,
    WAITER_WS_PATH,
    waiter_websocket,
)
from app.api.websockets.waiter_socket import (
    router as waiter_router,
)
from app.domain.roles import Role

logger = logging.getLogger(__name__)

# Role x WebSocket endpoint matrix (TASK-20, TASK-29), as data.
WS_ROLES_BY_PATH: Final[Mapping[str, frozenset[Role]]] = {
    KDS_WS_PATH: KDS_ALLOWED_ROLES,
    WAITER_WS_PATH: WAITER_ALLOWED_ROLES,
}

router = APIRouter()
router.include_router(kds_router)
router.include_router(waiter_router)

__all__ = [
    "KDS_ALLOWED_ROLES",
    "KDS_WS_PATH",
    "WAITER_ALLOWED_ROLES",
    "WAITER_WS_PATH",
    "WS_ROLES_BY_PATH",
    "kds_websocket",
    "router",
    "waiter_websocket",
]
