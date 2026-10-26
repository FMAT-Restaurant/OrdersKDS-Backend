"""API behavior tests for order confirmation response codes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order
from app.db.session import get_session
from app.domain.order_status import OrderStatus
from app.main import app


def test_confirm_cancelled_order_returns_invalid_state_transition(
    make_token: Callable[..., str],
) -> None:
    order_id = uuid4()
    order = Order(public_id=order_id, status=OrderStatus.CANCELLED)
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = order
    session.begin = Mock(return_value=AsyncMock())

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    previous_override = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        token = make_token(role="waiter", user_id=str(uuid4()))
        response = client.post(
            f"/orders/{order_id}/confirm",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous_override

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
    session.add.assert_not_called()
