"""Shared fixtures for unit tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from app.db.models import Order, Outbox
from app.main import app

# Any key works: the service never verifies signatures (ADR-0011).
_TEST_SIGNING_KEY = "unit-test-key-not-a-secret-0123456789"

TokenFactory = Callable[..., str]


class FakeAsyncSession:
    """In-memory AsyncSession double for testing FastAPI endpoints without I/O."""

    def __init__(self, db: dict[UUID, Order] | None = None) -> None:
        self.db: dict[UUID, Order] = db if db is not None else {}
        self.outbox_rows: list[Outbox] = []
        self.committed = False
        self.rolled_back = False

    def add(self, instance: object) -> None:
        if isinstance(instance, Order):
            self.db[instance.public_id] = instance
        elif isinstance(instance, Outbox):
            self.outbox_rows.append(instance)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        params = statement.compile().params
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
