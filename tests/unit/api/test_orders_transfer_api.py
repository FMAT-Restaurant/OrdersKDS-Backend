"""Unit tests for Orders table transfer endpoint.

Covers PATCH /orders/{orderId}/transfer.
Satisfies: RF-16, RNF-03, RNF-09, UT-BE-29, CT-04
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.error_codes import ErrorCode
from app.db.models import Order
from app.domain.errors import VersionConflictError
from app.domain.order_status import OrderStatus
from app.services.orders import OrderService
from tests.unit.api.conftest import FakeAsyncSession, SpyOrderNotifier

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-16", "RNF-03", "RNF-09"),
]


def test_transfer_order_changes_table_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    spy_notifier: SpyOrderNotifier,
) -> None:
    """UT-BE-29 & CT-04: Transfer changes table, clears takeaway, increments version, notifies."""
    order = Order(
        public_id=uuid4(),
        table_number=5,
        is_takeaway=True,
        status=OrderStatus.CREATED,
        version=1,
    )
    test_db[order.public_id] = order

    payload = {"tableNumber": 12}
    response = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["orderId"] == str(order.public_id)
    assert data["tableNumber"] == 12
    assert data["isTakeaway"] is False
    assert data["version"] == 2

    # Verify DB state
    persisted = test_db[order.public_id]
    assert persisted.table_number == 12
    assert persisted.is_takeaway is False
    assert persisted.version == 2

    # Verify real-time notification broadcast (UT-BE-29)
    assert len(spy_notifier.notified_orders) == 1
    assert spy_notifier.notified_orders[0].public_id == order.public_id


def test_transfer_order_with_matching_version(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Transfer succeeds when matching version is passed in payload (RNF-09, CT-04)."""
    order = Order(
        public_id=uuid4(),
        table_number=2,
        is_takeaway=False,
        status=OrderStatus.IN_PREPARATION,
        version=3,
    )
    test_db[order.public_id] = order

    payload = {"tableNumber": 9, "version": 3}
    response = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["tableNumber"] == 9
    assert data["version"] == 4


def test_transfer_order_stale_version_conflict_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Transfer rejects with 409 Conflict if payload version does not match (RNF-09, CT-04)."""
    order = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus.CREATED,
        version=3,
    )
    test_db[order.public_id] = order

    payload = {"tableNumber": 9, "version": 1}
    response = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == ErrorCode.VERSION_CONFLICT


def test_transfer_order_with_if_match_header(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """If-Match header is parsed as optimistic lock version (RNF-09, CT-04)."""
    order = Order(
        public_id=uuid4(),
        table_number=4,
        status=OrderStatus.CREATED,
        version=1,
    )
    test_db[order.public_id] = order

    # Matching If-Match header succeeds
    headers = {**auth_headers("waiter"), "If-Match": '"1"'}
    response = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=headers,
        json={"tableNumber": 6},
    )
    assert response.status_code == 200
    assert response.json()["tableNumber"] == 6
    assert response.json()["version"] == 2

    # Stale If-Match header rejects with 409
    stale_headers = {**auth_headers("waiter"), "If-Match": '"1"'}
    response2 = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=stale_headers,
        json={"tableNumber": 7},
    )
    assert response2.status_code == 409
    assert response2.json()["error"]["code"] == ErrorCode.VERSION_CONFLICT


@pytest.mark.parametrize(
    "terminal_status",
    [
        OrderStatus.PAID,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.VOIDED,
    ],
)
def test_transfer_order_terminal_status_rejects_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    terminal_status: OrderStatus,
) -> None:
    """Orders in terminal status cannot be transferred to a new table (RF-16, CT-04)."""
    order = Order(
        public_id=uuid4(),
        table_number=4,
        status=terminal_status,
        version=1,
    )
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": 8},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION


def test_transfer_order_not_found_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Transfer non-existent order returns 404 ORDER_NOT_FOUND (CT-04)."""
    missing_id = uuid4()
    response = client.patch(
        f"/orders/{missing_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": 8},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND


def test_transfer_order_invalid_payload_422(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Invalid payload values or extra fields return HTTP 422 (RNF-03, CT-04)."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED, table_number=1)
    test_db[order.public_id] = order

    # tableNumber <= 0
    resp_zero = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": 0},
    )
    assert resp_zero.status_code == 422

    # Extra fields forbidden
    resp_extra = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": 2, "forbiddenField": "not_allowed"},
    )
    assert resp_extra.status_code == 422

    # version < 1
    resp_ver_zero = client.patch(
        f"/orders/{order.public_id}/transfer",
        headers=auth_headers("waiter"),
        json={"tableNumber": 2, "version": 0},
    )
    assert resp_ver_zero.status_code == 422


def test_transfer_order_role_matrix(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Waiters and admins can transfer orders; kitchen/chef are forbidden (CT-04)."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED, table_number=1)
    test_db[order.public_id] = order

    payload = {"tableNumber": 15}

    for role in ["waiter", "admin"]:
        resp = client.patch(
            f"/orders/{order.public_id}/transfer",
            headers=auth_headers(role),
            json=payload,
        )
        assert resp.status_code == 200

    for role in ["kitchen", "chef"]:
        resp = client.patch(
            f"/orders/{order.public_id}/transfer",
            headers=auth_headers(role),
            json=payload,
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_unauth = client.patch(f"/orders/{order.public_id}/transfer", json=payload)
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


async def test_transfer_order_stale_data_error_raises_version_conflict(
    fake_session: FakeAsyncSession,
) -> None:
    """StaleDataError from concurrent DB update is mapped to VersionConflictError (RNF-09)."""
    order = Order(public_id=uuid4(), table_number=1, status=OrderStatus.CREATED, version=1)
    fake_session.add(order)

    service = OrderService(cast(AsyncSession, fake_session))

    async def _failing_commit() -> None:
        raise StaleDataError("Row was updated or deleted by another transaction")

    fake_session.commit = _failing_commit  # type: ignore[method-assign]

    with pytest.raises(VersionConflictError):
        await service.transfer_order(order.public_id, new_table_number=2)
