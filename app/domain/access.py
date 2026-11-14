"""PBAC catalogue of this service and the access policy of every protected action.

Auth issues permissions as ``modulo:recurso:accion`` strings (see
``docs/analisis_integracion_auth_permisos_pbac.md``). This module is the single place where
"which permission protects which action" is declared; routers, services and WebSocket
endpoints consume the same :class:`AccessPolicy` objects so they can never drift apart.

A policy is satisfied by:

* **PBAC** — any of its permissions, when the caller's identity carries a permission list
  (the ``*`` wildcard and the ``admin`` role always pass); or
* **Role fallback** — one of its roles, when the identity carries *no* permission list at all
  (development tokens that only have a ``role`` claim). An explicit, even empty, permission
  list is authoritative: revoking a permission in Auth takes effect immediately.

Satisfies: RNF-03, RF-15 (Guidelines §5.7)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.domain.roles import (
    ADMIN_ONLY_ROLES,
    KITCHEN_ROLES,
    VOID_KITCHEN_ERROR_ROLES,
    WAITER_ROLES,
    Role,
)

WILDCARD_PERMISSION: Final = "*"


class Permission(StrEnum):
    """Permissions owned by this service (prefixes ``kitchen:`` and ``orders:``)."""

    # Module ``kitchen`` (KDS)
    KITCHEN_QUEUES_VIEW = "kitchen:queues:view"
    KITCHEN_ORDERS_START_PREPARATION = "kitchen:orders:start_preparation"
    KITCHEN_ITEMS_READY = "kitchen:items:ready"
    KITCHEN_ORDERS_READY = "kitchen:orders:ready"
    KITCHEN_ITEMS_REJECT = "kitchen:items:reject"
    KITCHEN_ITEMS_CANCEL = "kitchen:items:cancel"
    KITCHEN_ORDERS_VOID_ERROR = "kitchen:orders:void_error"
    # Module ``orders`` (waiter / floor)
    ORDERS_ORDERS_VIEW = "orders:orders:view"
    ORDERS_ORDERS_CREATE = "orders:orders:create"
    ORDERS_ORDERS_UPDATE = "orders:orders:update"
    ORDERS_ORDERS_CONFIRM = "orders:orders:confirm"
    ORDERS_ORDERS_CANCEL = "orders:orders:cancel"
    ORDERS_ORDERS_VOID_ADMIN = "orders:orders:void_admin"
    ORDERS_ORDERS_TRANSFER = "orders:orders:transfer"
    ORDERS_ITEMS_REDO = "orders:items:redo"
    ORDERS_ORDERS_REQUEST_BILL = "orders:orders:request_bill"


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """What a caller needs to perform one action.

    Attributes:
        permissions: Satisfied by **any** of these PBAC permissions.
        fallback_roles: Roles accepted only when the caller has no permission list.
    """

    permissions: frozenset[str]
    fallback_roles: frozenset[Role]


def _policy(fallback_roles: frozenset[Role], *permissions: Permission) -> AccessPolicy:
    return AccessPolicy(frozenset(permissions), fallback_roles)


# --- Kitchen (KDS) -------------------------------------------------------------------
KDS_QUEUE_VIEW: Final = _policy(KITCHEN_ROLES, Permission.KITCHEN_QUEUES_VIEW)
START_PREPARATION: Final = _policy(KITCHEN_ROLES, Permission.KITCHEN_ORDERS_START_PREPARATION)
ITEM_READY: Final = _policy(KITCHEN_ROLES, Permission.KITCHEN_ITEMS_READY)
ORDER_READY: Final = _policy(KITCHEN_ROLES, Permission.KITCHEN_ORDERS_READY)
ITEM_REJECT: Final = _policy(KITCHEN_ROLES, Permission.KITCHEN_ITEMS_REJECT)
ITEM_CANCEL: Final = _policy(KITCHEN_ROLES, Permission.KITCHEN_ITEMS_CANCEL)
VOID_KITCHEN_ERROR: Final = _policy(VOID_KITCHEN_ERROR_ROLES, Permission.KITCHEN_ORDERS_VOID_ERROR)

# --- Orders (waiter / floor) ---------------------------------------------------------
ORDER_LIST: Final = _policy(WAITER_ROLES, Permission.ORDERS_ORDERS_VIEW)
# The detail of one order is also read by the kitchen, hence the second permission.
ORDER_DETAIL: Final = _policy(
    WAITER_ROLES | KITCHEN_ROLES,
    Permission.ORDERS_ORDERS_VIEW,
    Permission.KITCHEN_QUEUES_VIEW,
)
ORDER_CREATE: Final = _policy(WAITER_ROLES, Permission.ORDERS_ORDERS_CREATE)
ORDER_UPDATE: Final = _policy(WAITER_ROLES, Permission.ORDERS_ORDERS_UPDATE)
ORDER_CONFIRM: Final = _policy(WAITER_ROLES, Permission.ORDERS_ORDERS_CONFIRM)
ORDER_CANCEL: Final = _policy(WAITER_ROLES, Permission.ORDERS_ORDERS_CANCEL)
ORDER_TRANSFER: Final = _policy(WAITER_ROLES, Permission.ORDERS_ORDERS_TRANSFER)
ITEM_REDO: Final = _policy(WAITER_ROLES, Permission.ORDERS_ITEMS_REDO)
REQUEST_BILL: Final = _policy(WAITER_ROLES, Permission.ORDERS_ORDERS_REQUEST_BILL)
VOID_ADMIN: Final = _policy(ADMIN_ONLY_ROLES, Permission.ORDERS_ORDERS_VOID_ADMIN)

# --- WebSocket endpoints (handshake) -------------------------------------------------
KDS_WS: Final = KDS_QUEUE_VIEW
WAITER_WS: Final = ORDER_LIST
