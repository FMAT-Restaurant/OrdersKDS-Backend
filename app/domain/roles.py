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

from collections.abc import Collection, Iterable, Mapping
from enum import StrEnum
from typing import Final


class Role(StrEnum):
    """Role claim values issued by the Auth microservice (Guidelines §2.4)."""

    WAITER = "waiter"
    KITCHEN = "kitchen"
    CHEF = "chef"
    ADMIN = "admin"


# Labels emitted by ``Auth-Backend`` (``MESERO``, ``COCINERO``, ``CHEF_MASTER``,
# ``ADMINISTRADOR``) and the lowercase development roles, both mapped to the internal enum.
# Keys are compared lowercased, so the casing of the claim is irrelevant.
_ROLE_ALIASES: Final[Mapping[str, Role]] = {
    "waiter": Role.WAITER,
    "mesero": Role.WAITER,
    "kitchen": Role.KITCHEN,
    "cocinero": Role.KITCHEN,
    "chef": Role.CHEF,
    "chef_master": Role.CHEF,
    "admin": Role.ADMIN,
    "administrador": Role.ADMIN,
}

# Highest privilege first. A user may carry several roles (``ADMINISTRADOR,CHEF``); this
# order picks the single "primary" role used for audit logs and channel routing, while
# the guards evaluate *all* of the caller's roles.
ROLE_PRIORITY: Final[tuple[Role, ...]] = (Role.ADMIN, Role.CHEF, Role.KITCHEN, Role.WAITER)


def normalize_role(raw: object) -> Role | None:
    """Map a raw role label to a :class:`Role`, or ``None`` when it is not recognised."""
    if not isinstance(raw, str):
        return None
    return _ROLE_ALIASES.get(raw.strip().lower())


def resolve_roles(raw_roles: Iterable[object]) -> frozenset[Role]:
    """Normalise raw role labels, silently dropping the ones this service does not know."""
    return frozenset(role for role in map(normalize_role, raw_roles) if role is not None)


def primary_role(roles: Collection[Role]) -> Role | None:
    """Return the highest-privilege role of ``roles``, or ``None`` when it is empty."""
    return next((role for role in ROLE_PRIORITY if role in roles), None)


# Create / edit / cancel / confirm / transfer / request bill / redo.
WAITER_ROLES: Final[frozenset[Role]] = frozenset({Role.WAITER, Role.ADMIN})

# Mark dish or order as ready, read the KDS queue.
KITCHEN_ROLES: Final[frozenset[Role]] = frozenset({Role.KITCHEN, Role.CHEF, Role.ADMIN})

# Administrative void (-> WASTED, charged).
ADMIN_ONLY_ROLES: Final[frozenset[Role]] = frozenset({Role.ADMIN})

# Kitchen-error void (-> VOIDED, not charged).
VOID_KITCHEN_ERROR_ROLES: Final[frozenset[Role]] = frozenset({Role.ADMIN, Role.CHEF})
