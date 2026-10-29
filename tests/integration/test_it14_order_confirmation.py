"""IT-14: order confirmation records one outbox event under concurrent requests."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.models import Order, OrderItem, Outbox
from app.db.session import get_session
from app.domain.order_status import OrderStatus
from app.main import app


class ConfirmationDatabase:
    def __init__(self, order: Order) -> None:
        self.order = order
        self.outbox_rows: list[Outbox] = []
        self.order_lock = Lock()


class ConfirmationSession:
    def __init__(self, database: ConfirmationDatabase) -> None:
        self.database = database
        self.lock_acquired = False

    def add(self, instance: object) -> None:
        if isinstance(instance, Outbox):
            self.database.outbox_rows.append(instance)

    async def scalar(self, statement: Any) -> Order | None:
        if getattr(statement, "_for_update_arg", None) is not None:
            await asyncio.to_thread(self.database.order_lock.acquire)
            self.lock_acquired = True
        params = statement.compile().params
        order_id = next((value for value in params.values() if isinstance(value, UUID)), None)
        return self.database.order if order_id == self.database.order.public_id else None

    async def commit(self) -> None:
        if self.lock_acquired:
            self.database.order_lock.release()
            self.lock_acquired = False

    async def close(self) -> None:
        if self.lock_acquired:
            self.database.order_lock.release()
            self.lock_acquired = False


def _jwt_segment(payload: dict[str, str | int]) -> str:
    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).rstrip(b"=").decode("ascii")


def _auth_header() -> dict[str, str]:
    header = _jwt_segment({"alg": "HS256", "typ": "JWT"})
    claims = _jwt_segment(
        {
            "user_id": str(uuid4()),
            "role": "waiter",
            "exp": int(datetime.now(UTC).timestamp()) + 300,
        }
    )
    return {"Authorization": f"Bearer {header}.{claims}.c2ln"}


@pytest.mark.integration
@pytest.mark.requirement("OP-05", "RNF-09")
def test_concurrent_confirm_requests_stage_exactly_one_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = Order(
        public_id=uuid4(),
        status=OrderStatus.CREATED,
        items=[
            OrderItem(
                public_id=uuid4(),
                dish_id="dish-1",
                name="Dish",
                quantity=1,
            )
        ],
    )
    database = ConfirmationDatabase(order)

    async def get_test_session() -> AsyncGenerator[ConfirmationSession, None]:
        session = ConfirmationSession(database)
        try:
            yield session
        finally:
            await session.close()

    monkeypatch.setitem(app.dependency_overrides, get_session, get_test_session)

    def confirm() -> int:
        with TestClient(app) as client:
            return client.post(
                f"/orders/{order.public_id}/confirm",
                headers=_auth_header(),
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(confirm), executor.submit(confirm)]
        responses = [future.result() for future in futures]

    assert responses == [200, 200]
    assert order.confirmed_at is not None
    assert order.confirmed_by is not None
    assert len(database.outbox_rows) == 1
    assert database.outbox_rows[0].payload["items"] == [{"offerId": "dish-1", "quantity": 1}]


@pytest.mark.integration
@pytest.mark.requirement("OP-05")
def test_confirm_cancelled_order_returns_409_without_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = Order(public_id=uuid4(), status=OrderStatus.CANCELLED)
    database = ConfirmationDatabase(order)

    async def get_test_session() -> AsyncGenerator[ConfirmationSession, None]:
        session = ConfirmationSession(database)
        try:
            yield session
        finally:
            await session.close()

    monkeypatch.setitem(app.dependency_overrides, get_session, get_test_session)

    with TestClient(app) as client:
        response = client.post(
            f"/orders/{order.public_id}/confirm",
            headers=_auth_header(),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
    assert database.outbox_rows == []
