"""Shared fixtures for unit tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from app.db.models import Order, Outbox, Shift
from app.domain.order_status import OrderStatus
from app.domain.shift import ShiftStatus
from app.main import app
from tests.conftest import TEST_JWT_SECRET

_TEST_SIGNING_KEY = TEST_JWT_SECRET

TokenFactory = Callable[..., str]


class FakeAsyncSession:
    """In-memory AsyncSession double for testing FastAPI endpoints without I/O."""

    def __init__(self, db: dict[UUID, Order] | None = None) -> None:
        self.db: dict[UUID, Order] = db if db is not None else {}
        self.outbox_rows: list[Outbox] = []
        self.shifts: list[Shift] = []
        self.committed = False
        self.rolled_back = False

    @staticmethod
    def _assign_shift_id_if_missing(shift: Shift, next_id: int) -> None:
        if getattr(shift, "id", None) is None:
            shift.id = next_id

    def add(self, instance: object) -> None:
        if isinstance(instance, Order):
            self.db[instance.public_id] = instance
        elif isinstance(instance, Shift):
            if instance not in self.shifts:
                self.shifts.append(instance)
            self._assign_shift_id_if_missing(instance, len(self.shifts))
        elif isinstance(instance, Outbox):
            self.outbox_rows.append(instance)

    async def flush(self) -> None:
        for index, shift in enumerate(self.shifts, start=1):
            self._assign_shift_id_if_missing(shift, index)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        params = statement.compile().params
        status_val = next((v for v in params.values() if isinstance(v, OrderStatus)), None)
        session_val = next((v for v in params.values() if isinstance(v, UUID)), None)
        orders = list(self.db.values())
        if status_val is not None:
            orders = [o for o in orders if o.status == status_val]
        if session_val is not None:
            orders = [o for o in orders if o.dining_session_id == session_val]
        orders.sort(
            key=lambda o: (
                o.created_at or datetime.min.replace(tzinfo=UTC),
                o.public_id,
            ),
            reverse=True,
        )
        offset_val = getattr(getattr(statement, "_offset_clause", None), "value", None)
        limit_val = getattr(getattr(statement, "_limit_clause", None), "value", None)
        start = offset_val if isinstance(offset_val, int) and offset_val > 0 else 0
        end = start + limit_val if isinstance(limit_val, int) and limit_val >= 0 else None
        orders = orders[start:end]

        class _Result:
            def unique(self) -> _Result:
                return self

            def all(self) -> list[Order]:
                return orders

            def __iter__(self) -> Any:
                return iter(orders)

        return _Result()

    async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        column_descs = getattr(statement, "column_descriptions", ())
        entity = column_descs[0].get("entity") if column_descs else None
        params = statement.compile().params
        if entity is Shift:
            shift_statuses = {status.value for status in ShiftStatus}
            status_val = next(
                (
                    v
                    for v in params.values()
                    if isinstance(v, ShiftStatus) or (isinstance(v, str) and v in shift_statuses)
                ),
                None,
            )
            candidates = list(self.shifts)
            if status_val is not None:
                expected_status = ShiftStatus(status_val)
                candidates = [s for s in candidates if s.status == expected_status]
            return max(candidates, key=lambda s: s.shift_number) if candidates else None

        order_id = next((v for v in params.values() if isinstance(v, UUID)), None)
        if order_id is not None:
            return self.db.get(order_id)
        return None

    async def close(self) -> None:
        pass


@pytest.fixture
def make_token() -> TokenFactory:
    """Build JWTs like the ones the Gateway forwards.

    ``make_token(role="chef", expires_in=-60)`` overrides claims; passing ``None`` for a
    claim removes it from the payload.
    """

    def _make(*, expires_in: int = 3600, algorithm: str = "HS256", **claims: object) -> str:
        payload: dict[str, object] = {
            "user_id": "user-1",
            "role": "waiter",
            "exp": datetime.now(UTC) + timedelta(seconds=expires_in),
        }
        payload.update(claims)
        payload = {name: value for name, value in payload.items() if value is not None}
        key = None if algorithm == "none" else _TEST_SIGNING_KEY
        return jwt.encode(payload, key, algorithm=algorithm)

    return _make


@pytest.fixture
def auth_headers(make_token: TokenFactory) -> Callable[[str], dict[str, str]]:
    """Return ``Authorization`` headers for a token carrying the given role."""

    def _headers(role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(role=role)}"}

    return _headers


@pytest.fixture
def client() -> TestClient:
    """Test client over the real application (routers, handlers and WebSockets mounted)."""
    return TestClient(app)
