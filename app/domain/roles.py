"""Roles and the role groups of the authorization matrix.

The groups mirror DEVELOPMENT_GUIDELINES §5.7 and are the single place where
"who may do what" is declared. Routers consume them through ``require_roles``;
services re-check the role-dependent domain rules (defense in depth).

Satisfies: RNF-03, RF-15

NOTE: only ``KITCHEN_ROLES`` is wired to routes so far (TASK-20). The other groups are ready
for the routers that arrive later and must be reused as-is with ``require_roles(*GROUP)``:
``WAITER_ROLES`` in TASK-10/11/17/31 (create, edit, confirm, cancel, transfer, request bill),
``ADMIN_ONLY_ROLES`` in TASK-18 (``/void``) and ``VOID_KITCHEN_ERROR_ROLES`` in TASK-19
(``/void-kitchen-error``). The ``chef`` intermediate-dish route (TASK-27, optional) needs its
own group when it is built. Services must re-check the role-dependent domain rules.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Role(StrEnum):
    """Role claim values issued by the Auth microservice (Guidelines §2.4)."""

    WAITER = "waiter"
    KITCHEN = "kitchen"
    CHEF = "chef"
    ADMIN = "admin"


# Create / edit / cancel / confirm / transfer / request bill / redo.
WAITER_ROLES: Final[frozenset[Role]] = frozenset({Role.WAITER, Role.ADMIN})

# Mark dish or order as ready, read the KDS queue.
KITCHEN_ROLES: Final[frozenset[Role]] = frozenset({Role.KITCHEN, Role.CHEF, Role.ADMIN})

# Administrative void (-> WASTED, charged).
ADMIN_ONLY_ROLES: Final[frozenset[Role]] = frozenset({Role.ADMIN})

# Kitchen-error void (-> VOIDED, not charged).
VOID_KITCHEN_ERROR_ROLES: Final[frozenset[Role]] = frozenset({Role.ADMIN, Role.CHEF})
