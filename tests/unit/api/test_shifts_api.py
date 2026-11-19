"""API unit tests for /shifts lifecycle endpoints and per-shift folio resets.

Satisfies: RF-12, RF-15, RNF-03, RNF-09
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-12", "RF-15", "RNF-03", "RNF-09"),
]


@pytest.mark.parametrize(
    ("role", "permissions", "expected_status"),
    [
        ("chef", None, 201),
        ("admin", None, 201),
        ("chef", ["kitchen:orders:void_error"], 201),
        ("chef", ["kitchen:shifts:manage"], 201),
        ("kitchen", None, 403),
        ("waiter", None, 403),
        ("kitchen", ["kitchen:items:ready"], 403),
        ("chef", [], 403),
    ],
)
def test_open_shift_rbac_and_pbac_matrix(
    client: TestClient,
    make_token: Callable[..., str],
    role: str,
    permissions: list[str] | None,
    expected_status: int,
) -> None:
    """Only Master Chef (CHEF) and Admin can open shifts; revoked PBAC tokens get 403."""
    extra: dict[str, object] = {}
    if permissions is not None:
        extra["permissions"] = permissions
    token = make_token(role=role, **extra)

    response = client.post(
        "/shifts/open",
        headers={"Authorization": f"Bearer {token}"},
        json={"notes": "Opening shift"},
    )
    assert response.status_code == expected_status
    if expected_status == 201:
        body = response.json()
        assert body["shiftNumber"] == 1
        assert body["status"] == "OPEN"
        assert body["lastOrderFolio"] == 0
    else:
        assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value


def test_close_shift_rejects_waiter_and_kitchen_roles(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Waiters and regular cooks cannot manually close shifts."""
    assert client.post("/shifts/open", headers=auth_headers("chef"), json={}).status_code == 201

    for forbidden_role in ("waiter", "kitchen"):
        resp = client.post("/shifts/current/close", headers=auth_headers(forbidden_role))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value


def test_get_current_shift_accessible_to_authenticated_staff_and_requires_auth(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Authenticated floor/kitchen roles can inspect shift status; anonymous gets 401."""
    assert client.get("/shifts/current").status_code == 401

    for role in ("waiter", "kitchen", "chef", "admin"):
        resp = client.get("/shifts/current", headers=auth_headers(role))
        assert resp.status_code == 200
        body = resp.json()
        assert body["isOpen"] is False
        assert body["canAcceptOrders"] is True
        assert body["nextShiftNumber"] == 1


def test_end_to_end_shift_close_and_open_resets_order_folio(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Orders increment 1,2 in Shift #1; closing blocks new orders; Shift #2 restarts at 1."""
    first = client.post(
        "/orders",
        headers=auth_headers("waiter"),
        json={"tableNumber": 5, "isTakeaway": False},
    )
    second = client.post(
        "/orders",
        headers=auth_headers("waiter"),
        json={"tableNumber": 6, "isTakeaway": False},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["shiftFolio"] == 1
    assert first.json()["orderNumber"] == 1
    assert second.json()["shiftFolio"] == 2
    assert second.json()["orderNumber"] == 2

    status_before_close = client.get("/shifts/current", headers=auth_headers("chef")).json()
    assert status_before_close["isOpen"] is True
    assert status_before_close["currentShift"]["shiftNumber"] == 1
    assert status_before_close["currentShift"]["lastOrderFolio"] == 2

    closed = client.post(
        "/shifts/current/close",
        headers=auth_headers("chef"),
        json={"notes": "Cierre turno matutino"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"

    blocked_order: Any = client.post(
        "/orders",
        headers=auth_headers("waiter"),
        json={"tableNumber": 7, "isTakeaway": False},
    )
    assert blocked_order.status_code == 409
    assert blocked_order.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION.value

    reopened = client.post(
        "/shifts/open",
        headers=auth_headers("admin"),
        json={"notes": "Apertura turno vespertino"},
    )
    assert reopened.status_code == 201
    assert reopened.json()["shiftNumber"] == 2
    assert reopened.json()["lastOrderFolio"] == 0

    third = client.post(
        "/orders",
        headers=auth_headers("waiter"),
        json={"tableNumber": 8, "isTakeaway": False},
    )
    assert third.status_code == 201
    assert third.json()["shiftFolio"] == 1
    assert third.json()["orderNumber"] == 1


def test_shift_conflict_transitions_and_blank_notes_normalization(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Double close/open return 409 and blank notes normalize to None."""
    close_when_empty = client.post(
        "/shifts/current/close",
        headers=auth_headers("chef"),
        json={"notes": "   "},
    )
    assert close_when_empty.status_code == 409
    assert close_when_empty.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION.value

    opened = client.post(
        "/shifts/open",
        headers=auth_headers("admin"),
        json={"notes": "   "},
    )
    assert opened.status_code == 201
    assert opened.json()["notes"] is None

    double_open = client.post("/shifts/open", headers=auth_headers("chef"))
    assert double_open.status_code == 409
    assert double_open.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION.value
