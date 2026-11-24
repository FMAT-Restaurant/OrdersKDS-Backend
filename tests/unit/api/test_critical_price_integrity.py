"""Unit tests verifying server-side catalog price enforcement on PATCH /items (DEF-E5-009)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.domain.order_status import ItemStatus, OrderStatus
from tests.unit.api.conftest import FakeAsyncSession, FakeCatalogValidator

pytestmark = [pytest.mark.unit, pytest.mark.requirement("RF-14", "RF-17")]


def test_patch_items_enforces_catalog_price_over_client_tampered_price(
    client: TestClient,
    fake_session: FakeAsyncSession,
    fake_catalog: FakeCatalogValidator,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    order = Order(public_id=uuid4(), table_number=4, status=OrderStatus.CREATED, items=[])
    fake_session.add(order)

    fake_catalog.set_price("PLATE-BURGER-01", Decimal("165.00"))
    fake_catalog.set_price("OFFER-COMBO-01", Decimal("220.00"))
    fake_catalog.set_price("PLATE-COMBO-BASE", Decimal("150.00"))

    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={
            "items": [
                {
                    "dishId": "PLATE-BURGER-01",
                    "name": "Hamburguesa clásica",
                    "quantity": 2,
                    "unitPrice": "0.01",
                },
                {
                    "dishId": "PLATE-COMBO-BASE",
                    "offerId": "OFFER-COMBO-01",
                    "name": "Combo Especial",
                    "quantity": 1,
                    "unitPrice": "0.00",
                },
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert Decimal(body["items"][0]["unitPrice"]) == Decimal("165.00")
    assert Decimal(body["items"][1]["unitPrice"]) == Decimal("220.00")
    assert Decimal(body["totalAmount"]) == Decimal("550.00")


def test_patch_items_rejects_uncataloged_zero_price_item_and_preserves_existing_fallback(
    client: TestClient,
    fake_session: FakeAsyncSession,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    order = Order(
        public_id=uuid4(),
        table_number=7,
        status=OrderStatus.CREATED,
        items=[
            OrderItem(
                public_id=uuid4(),
                dish_id="LEGACY-DISH",
                name="Platillo Previo",
                quantity=1,
                unit_price=Decimal("95.00"),
                status=ItemStatus.PENDING,
            )
        ],
    )
    fake_session.add(order)

    rejected = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={
            "items": [
                {
                    "dishId": "UNCATALOGED-DISH",
                    "name": "Nuevo Sin Precio",
                    "quantity": 1,
                    "unitPrice": "0.00",
                }
            ]
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value

    ok_resp = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={
            "items": [
                {
                    "dishId": "LEGACY-DISH",
                    "name": "Platillo Previo",
                    "quantity": 2,
                    "unitPrice": "0.00",
                }
            ]
        },
    )
    assert ok_resp.status_code == 200
    assert Decimal(ok_resp.json()["totalAmount"]) == Decimal("190.00")
