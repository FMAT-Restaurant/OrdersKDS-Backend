"""WebSocket endpoints of the KDS and waiter channels.

Role matrix (OP-02 / ADR-0002): ``/ws/kds`` -> kitchen, chef, admin;
``/ws/waiter`` -> waiter, admin. An unauthenticated caller is closed with ``4401`` and a
role that does not match the endpoint with ``4403``.

Satisfies: RNF-03, RNF-04, RF-18

TODO(RNF-04, RF-18, TASK-29): channels (``kds:station:{station}``, ``kds:all``, ``floor``,
``user:{userId}``), the ``subscribe`` message, ``FORBIDDEN_CHANNEL`` and the push of
``order.status_changed`` / ``item.status_changed``. Until then inbound messages are ignored.

TODO(RNF-03, TASK-29, TASK-30): browsers cannot set an ``Authorization`` header on
``new WebSocket`` and the token must not travel in the URL. Agree with the Gateway how the
token reaches this service (forwarded header vs. subprotocol) before the frontend connects.
"""

import logging
from collections.abc import Mapping
from typing import Final

from fastapi import APIRouter, WebSocket

from app.api.dependencies.auth import authorize_websocket
from app.domain.roles import KITCHEN_ROLES, WAITER_ROLES, Role

logger = logging.getLogger(__name__)

KDS_WS_PATH: Final = "/ws/kds"
WAITER_WS_PATH: Final = "/ws/waiter"

# Role x WebSocket endpoint matrix (TASK-20), as data.
WS_ROLES_BY_PATH: Final[Mapping[str, frozenset[Role]]] = {
    KDS_WS_PATH: KITCHEN_ROLES,
    WAITER_WS_PATH: WAITER_ROLES,
}

router = APIRouter()


async def _serve(websocket: WebSocket, path: str) -> None:
    context = await authorize_websocket(websocket, WS_ROLES_BY_PATH[path])
    if context is None:
        return
    # iter_text ends cleanly when the client disconnects.
    async for _ignored in websocket.iter_text():
        continue
    logger.debug("websocket closed", extra={"path": path, "user_id": context.user_id})


@router.websocket(KDS_WS_PATH)
async def kds_websocket(websocket: WebSocket) -> None:
    """Kitchen channel. Roles: kitchen, chef, admin."""
    await _serve(websocket, KDS_WS_PATH)


@router.websocket(WAITER_WS_PATH)
async def waiter_websocket(websocket: WebSocket) -> None:
    """Waiter channel. Roles: waiter, admin."""
    await _serve(websocket, WAITER_WS_PATH)
