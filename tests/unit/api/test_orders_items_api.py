"""Unit tests for Orders items modification endpoint.

Covers PATCH /orders/{orderId}/items.
Satisfies: RF-14, RF-17, RF-23, OP-05, RNF-03, UT-BE-25, UT-BE-26, CT-03, UT-BE-60
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.domain.order_status import ItemStatus, OrderStatus
from tests.unit.api.conftest import EMPTY_MODIFIERS, FakeCatalogValidator, SpyOrderNotifier

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-14", "RF-17", "RF-23", "OP-05", "RNF-03"),
]


def test_update_order_items_success(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Replace items in draft order and recalculate total (RF-14, UT-BE-26, CT-03)."""
    order = Order(
        public_id=uuid4(),
        table_number=5,
        is_takeaway=False,
        status=OrderStatus.CREATED,
    )
    test_db[order.public_id] = order

    items_payload: dict[str, object] = {
        "items": [
            {
                "dishId": "dish-01",
                "name": "Tacos al Pastor",
                "quantity": 3,
                "unitPrice": "45.00",
                "modifiers": ["sin cebolla", "con salsa verde"],
                "notes": "Bien dorada la carne",
            },
            {
                "dishId": "dish-02",
                "name": "Horchata 500ml",
                "quantity": 2,
                "unitPrice": "25.50",
                "modifiers": EMPTY_MODIFIERS,
                "notes": None,
            },
        ]
    }

    url = f"/orders/{order.public_id}/items"
    response = client.patch(url, headers=auth_headers("waiter"), json=items_payload)

    assert response.status_code == 200
    data = response.json()
    assert data["orderId"] == str(order.public_id)
    assert len(data["items"]) == 2
    # Total = 3 * 45.00 + 2 * 25.50 = 135.00 + 51.00 = 186.00
    assert data["totalAmount"] == "186.00"

    item1 = data["items"][0]
    assert item1["dishId"] == "dish-01"
    assert item1["name"] == "Tacos al Pastor"
    assert item1["quantity"] == 3
    assert item1["unitPrice"] == "45.00"
    assert item1["modifiers"] == ["sin cebolla", "con salsa verde"]
    assert item1["notes"] == "Bien dorada la carne"
    assert item1["status"] == "PENDING"
    assert "itemId" in item1

    item2 = data["items"][1]
    assert item2["dishId"] == "dish-02"
    assert item2["quantity"] == 2
    assert item2["unitPrice"] == "25.50"
    assert item2["modifiers"] == []
    assert item2["notes"] is None

    # Verify DB state
    assert len(order.items) == 2
    assert order.total_amount == Decimal("186.00")


def test_update_order_items_replaces_previous_items(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Full replacement of items when new items are submitted (RF-14)."""
    order = Order(
        public_id=uuid4(),
        table_number=2,
        status=OrderStatus.CREATED,
    )
    old_item = OrderItem(
        public_id=uuid4(),
        dish_id="old-dish",
        name="Old Item",
        quantity=1,
        unit_price=Decimal("100.00"),
    )
    order.items.append(old_item)
    order.recalculate_total()
    test_db[order.public_id] = order

    replacement_payload: dict[str, object] = {
        "items": [
            {
                "dishId": "new-dish",
                "name": "New Item",
                "quantity": 1,
                "unitPrice": "50.00",
                "modifiers": EMPTY_MODIFIERS,
                "notes": None,
            }
        ]
    }
    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=replacement_payload,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["dishId"] == "new-dish"
    assert data["totalAmount"] == "50.00"
    assert len(order.items) == 1


def test_update_order_items_dish_unavailable_raises_422(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_catalog: FakeCatalogValidator,
) -> None:
    """Reject modification when dish is inactive/unavailable in catalog (RF-17, UT-BE-25)."""
    order = Order(
        public_id=uuid4(),
        table_number=1,
        status=OrderStatus.CREATED,
    )
    test_db[order.public_id] = order
    fake_catalog.unavailable_ids.add("dish-sold-out")

    payload: dict[str, object] = {
        "items": [
            {
                "dishId": "dish-sold-out",
                "name": "Sold Out Dish",
                "quantity": 1,
                "unitPrice": "60.00",
                "modifiers": EMPTY_MODIFIERS,
                "notes": None,
            }
        ]
    }
    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == ErrorCode.DISH_UNAVAILABLE
    assert "dish-sold-out" in data["error"]["message"]
    # Order items were not changed
    assert len(order.items) == 0


def test_update_order_items_rejected_when_order_confirmed(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Reject item modification if order is confirmed / awaiting stock (OP-05 / ADR-0004)."""
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.CREATED,
        confirmed_at=datetime.now(UTC),
    )
    test_db[order.public_id] = order

    payload: dict[str, object] = {
        "items": [
            {
                "dishId": "dish-1",
                "name": "Any Dish",
                "quantity": 1,
                "unitPrice": "20.00",
            }
        ]
    }
    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION
    assert "confirmed" in data["error"]["message"].lower()


@pytest.mark.parametrize(
    "invalid_status",
    [
        OrderStatus.RESERVED,
        OrderStatus.IN_PREPARATION,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
        OrderStatus.PAID,
        OrderStatus.WASTED,
        OrderStatus.VOIDED,
    ],
)
def test_update_order_items_rejected_when_status_not_created(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    invalid_status: OrderStatus,
) -> None:
    """Reject item modification when order is past CREATED status (RF-23)."""
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=invalid_status,
    )
    test_db[order.public_id] = order

    payload: dict[str, object] = {
        "items": [
            {
                "dishId": "dish-1",
                "name": "Any Dish",
                "quantity": 1,
                "unitPrice": "20.00",
            }
        ]
    }
    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION


def test_update_order_items_non_existent_order_returns_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Return 404 ORDER_NOT_FOUND when order does not exist."""
    missing_id = uuid4()
    payload: dict[str, object] = {
        "items": [
            {
                "dishId": "dish-1",
                "name": "Any Dish",
                "quantity": 1,
                "unitPrice": "20.00",
            }
        ]
    }
    response = client.patch(
        f"/orders/{missing_id}/items",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == ErrorCode.ORDER_NOT_FOUND


def test_update_order_items_rejects_empty_items_list(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Reject empty items list with 422 VALIDATION_ERROR."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={"items": []},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize(
    "invalid_item",
    [
        {"dishId": "d1", "name": "A", "quantity": 0, "unitPrice": "10.00"},
        {"dishId": "d1", "name": "A", "quantity": -2, "unitPrice": "10.00"},
        {"dishId": "d1", "name": "A", "quantity": 1, "unitPrice": "-5.00"},
        {"dishId": "", "name": "A", "quantity": 1, "unitPrice": "10.00"},
        {"dishId": "d1", "name": "", "quantity": 1, "unitPrice": "10.00"},
    ],
)
def test_update_order_items_invalid_field_values_fail_validation(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    invalid_item: dict[str, Any],
) -> None:
    """Validate positive quantities and prices on item lines."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={"items": [invalid_item]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


def test_update_order_items_rejects_extra_fields_as_mass_assignment(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Reject forbidden fields inside items payload (UT-BE-60)."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    payload: dict[str, object] = {
        "items": [
            {
                "dishId": "d1",
                "name": "Tacos",
                "quantity": 1,
                "unitPrice": "10.00",
                "status": "READY",
            }
        ]
    }
    response = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


def test_update_order_items_role_matrix(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Role matrix for PATCH /orders/{orderId}/items (RNF-03)."""
    order = Order(public_id=uuid4(), status=OrderStatus.CREATED)
    test_db[order.public_id] = order

    payload: dict[str, object] = {
        "items": [{"dishId": "d1", "name": "Tacos", "quantity": 1, "unitPrice": "10.00"}]
    }

    assert (
        client.patch(
            f"/orders/{order.public_id}/items",
            headers=auth_headers("waiter"),
            json=payload,
        ).status_code
        == 200
    )

    assert (
        client.patch(
            f"/orders/{order.public_id}/items",
            headers=auth_headers("admin"),
            json=payload,
        ).status_code
        == 200
    )

    resp_kitchen = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("kitchen"),
        json=payload,
    )
    assert resp_kitchen.status_code == 403
    assert resp_kitchen.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_chef = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("chef"),
        json=payload,
    )
    assert resp_chef.status_code == 403
    assert resp_chef.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_unauth = client.patch(f"/orders/{order.public_id}/items", json=payload)
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


def test_update_order_items_on_rejected_order_reopens_to_created(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Modifying items on a REJECTED order reopens it to CREATED and clears rejection details."""
    order = Order(
        public_id=uuid4(),
        table_number=3,
        status=OrderStatus.REJECTED,
        rejection_details={"source": "kitchen", "reason": "No stock"},
    )
    test_db[order.public_id] = order

    resp = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={
            "items": [
                {
                    "dishId": "dish-101",
                    "name": "Guacamole",
                    "quantity": 1,
                    "unitPrice": "50.00",
                }
            ]
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CREATED"
    assert data["rejectionDetails"] is None
    assert data["confirmedAt"] is None
    assert test_db[order.public_id].status == OrderStatus.CREATED
    assert test_db[order.public_id].rejection_details is None


def test_update_order_items_supports_client_identifiers_and_recovers_price(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    spy_notifier: SpyOrderNotifier,
) -> None:
    """Accept client id/itemId and recover existing price when unitPrice is omitted."""
    existing_item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-burger-01",
        name="Hamburguesa Clásica",
        quantity=2,
        unit_price=Decimal("120.00"),
        status=ItemStatus.PENDING,
    )
    order = Order(
        public_id=uuid4(),
        table_number=7,
        status=OrderStatus.CREATED,
        items=[existing_item],
    )
    order.recalculate_total()
    test_db[order.public_id] = order

    # Client sends id and itemId without unitPrice (editing quantity only)
    resp = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={
            "items": [
                {
                    "id": str(existing_item.public_id),
                    "itemId": str(existing_item.public_id),
                    "dishId": "dish-burger-01",
                    "name": "Hamburguesa Clásica",
                    "quantity": 1,
                    "modifiers": ["Sin cebolla"],
                    "notes": "Bien cocida",
                }
            ]
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["totalAmount"] == "120.00"
    assert len(data["items"]) == 1
    assert data["items"][0]["quantity"] == 1
    assert data["items"][0]["unitPrice"] == "120.00"
    assert data["items"][0]["modifiers"] == ["Sin cebolla"]
    assert data["items"][0]["notes"] == "Bien cocida"
    # Verifies notify_order_changed was called
    assert any(o.public_id == order.public_id for o in spy_notifier.notified_orders)


def test_update_order_items_persists_distinct_source_id_and_offer_id(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_catalog: FakeCatalogValidator,
) -> None:
    """Persist physical sourceId in dish_id and commercial offerId in offer_id, returning both."""
    order = Order(
        public_id=uuid4(),
        table_number=9,
        status=OrderStatus.CREATED,
    )
    test_db[order.public_id] = order

    resp = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={
            "items": [
                {
                    "dishId": "offer-promo-1",
                    "offerId": "offer-promo-1",
                    "sourceId": "recipe-taco-al-pastor",
                    "name": "Orden de Tacos al Pastor",
                    "quantity": 2,
                    "unitPrice": "110.00",
                    "modifiers": [],
                }
            ]
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    item_json = body["items"][0]
    assert item_json["dishId"] == "recipe-taco-al-pastor"
    assert item_json["sourceId"] == "recipe-taco-al-pastor"
    assert item_json["offerId"] == "offer-promo-1"

    saved_item = order.items[0]
    assert saved_item.dish_id == "recipe-taco-al-pastor"
    assert saved_item.offer_id == "offer-promo-1"

    # Unavailable offerId is also rejected even when physical sourceId is available
    fake_catalog.unavailable_ids.add("offer-promo-1")
    blocked_resp = client.patch(
        f"/orders/{order.public_id}/items",
        headers=auth_headers("waiter"),
        json={
            "items": [
                {
                    "dishId": "offer-promo-1",
                    "offerId": "offer-promo-1",
                    "sourceId": "recipe-taco-al-pastor",
                    "name": "Orden de Tacos al Pastor",
                    "quantity": 1,
                    "unitPrice": "110.00",
                }
            ]
        },
    )
    assert blocked_resp.status_code == 422
    assert blocked_resp.json()["error"]["code"] == ErrorCode.DISH_UNAVAILABLE
