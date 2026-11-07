"""Unit tests for Orders voiding endpoints.

Covers:
- PATCH /orders/{orderId}/void (Administrative void with charge - TASK-18, ADR-0012)
- PATCH /orders/{orderId}/void-kitchen-error (Kitchen error void - TASK-19, ADR-0012)
Satisfies: RF-26, RF-27, RF-28, RF-29, RF-30, RF-31, RNF-03, ADR-0012
Covers: UT-BE-16, UT-BE-17, UT-BE-18, UT-BE-19, UT-BE-20, UT-BE-21, UT-BE-22, CT-12, CT-17
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode
from app.db.models import Order, OrderItem
from app.domain.order_status import ItemStatus, OrderStatus
from tests.unit.api.conftest import FakeAsyncSession, SpyOrderNotifier

pytestmark = [
    pytest.mark.unit,
    pytest.mark.requirement("RF-26", "RF-27", "RF-28", "RF-29", "RF-30", "RF-31", "RNF-03"),
]


# ===========================================================================
# TASK-18: Administrative Void (Mermada, con cobro)
# Satisfies: RF-26, RF-27, RF-21, ADR-0012
# Tests: UT-BE-16, UT-BE-17, CT-12, CT-17
# ===========================================================================


def test_void_administrative_by_waiter_is_forbidden(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """UT-BE-16: Waiter attempting administrative void is rejected with 403 FORBIDDEN_ROLE."""
    order = Order(
        public_id=uuid4(),
        status=OrderStatus.IN_PREPARATION,
        table_number=3,
    )
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/void",
        headers=auth_headers("waiter"),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value
    assert test_db[order.public_id].status == OrderStatus.IN_PREPARATION


def test_void_administrative_by_kitchen_and_chef_is_forbidden(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """UT-BE-16: Kitchen staff and chef cannot perform administrative void (admin only)."""
    for role in ("kitchen", "chef"):
        order = Order(
            public_id=uuid4(),
            status=OrderStatus.IN_PREPARATION,
            table_number=4,
        )
        test_db[order.public_id] = order

        response = client.patch(
            f"/orders/{order.public_id}/void",
            headers=auth_headers(role),
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value
        assert test_db[order.public_id].status == OrderStatus.IN_PREPARATION


def test_void_administrative_publishes_wasted_and_charge_requested(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
) -> None:
    """UT-BE-17 & CT-12: Admin void transitions to WASTED and publishes wasted + charge events."""
    order_id = uuid4()
    item_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=5,
        status=OrderStatus.IN_PREPARATION,
        dining_session_id=uuid4(),
        total_amount=Decimal("150.00"),
    )
    item = OrderItem(
        public_id=item_id,
        dish_id="dish-202",
        name="Ceviche Clásico",
        quantity=2,
        unit_price=Decimal("75.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    test_db[order_id] = order

    response = client.patch(
        f"/orders/{order_id}/void",
        headers=auth_headers("admin"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["orderId"] == str(order_id)
    assert data["status"] == "WASTED"

    # Verify DB state
    assert test_db[order_id].status == OrderStatus.WASTED

    # Verify staged events in outbox (both wasted and charge requested)
    assert len(fake_session.outbox_rows) == 2
    event_types = [entry.event_type for entry in fake_session.outbox_rows]
    assert "ordenes.comanda.mermada" in event_types
    assert "ordenes.cobro.solicitado" in event_types

    # Verify ordenes.comanda.mermada payload
    wasted_entry = next(
        e for e in fake_session.outbox_rows if e.event_type == "ordenes.comanda.mermada"
    )
    assert wasted_entry.payload["orderId"] == str(order_id)
    assert wasted_entry.payload["motivo"] == "administrativo"
    assert wasted_entry.payload["reason"] == "ADMINISTRATIVE"
    items_afectados = cast(list[dict[str, Any]], wasted_entry.payload.get("itemsAfectados", []))
    assert len(items_afectados) == 1
    assert items_afectados[0]["offerId"] == "dish-202"
    assert items_afectados[0]["quantity"] == 2

    # Verify ordenes.cobro.solicitado payload
    charge_entry = next(
        e for e in fake_session.outbox_rows if e.event_type == "ordenes.cobro.solicitado"
    )
    assert charge_entry.payload["orderId"] == str(order_id)
    assert charge_entry.payload["status"] == "WASTED"
    assert charge_entry.payload["totalAmount"] == 150.0
    charge_items = cast(list[dict[str, Any]], charge_entry.payload.get("items", []))
    assert len(charge_items) == 1
    assert charge_items[0]["plateId"] == "dish-202"
    assert charge_items[0]["unitPrice"] == 75.0
    assert charge_items[0]["subtotal"] == 150.0

    # Verify WebSocket broadcast
    assert any(o.public_id == order_id for o in spy_notifier.notified_orders)


@pytest.mark.parametrize(
    "invalid_status",
    [
        OrderStatus.CREATED,
        OrderStatus.RESERVED,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
        OrderStatus.PAID,
        OrderStatus.VOIDED,
        OrderStatus.EXPIRED,
    ],
)
def test_void_order_in_non_in_preparation_status_raises_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    invalid_status: OrderStatus,
) -> None:
    """CT-12: Administrative void only applies to IN_PREPARATION; all other statuses raise 409."""
    order = Order(
        public_id=uuid4(),
        status=invalid_status,
        table_number=2,
    )
    test_db[order.public_id] = order

    response = client.patch(
        f"/orders/{order.public_id}/void",
        headers=auth_headers("admin"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION.value
    assert test_db[order.public_id].status == invalid_status
    assert len(fake_session.outbox_rows) == 0


def test_void_order_not_found_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Void non-existent order ID returns 404 ORDER_NOT_FOUND."""
    missing_id = uuid4()
    response = client.patch(
        f"/orders/{missing_id}/void",
        headers=auth_headers("admin"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND.value


def test_void_order_with_reason_payload(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Void order accepts optional reason; unexpected fields reject with 422."""
    order = Order(
        public_id=uuid4(),
        status=OrderStatus.IN_PREPARATION,
        table_number=8,
    )
    test_db[order.public_id] = order

    # Valid payload with reason
    resp = client.patch(
        f"/orders/{order.public_id}/void",
        headers=auth_headers("admin"),
        json={"reason": "Mesa se fue sin consumir"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "WASTED"

    # Reset for extra field test
    order2 = Order(
        public_id=uuid4(),
        status=OrderStatus.IN_PREPARATION,
        table_number=9,
    )
    test_db[order2.public_id] = order2

    resp_invalid = client.patch(
        f"/orders/{order2.public_id}/void",
        headers=auth_headers("admin"),
        json={"unexpectedField": "bad"},
    )
    assert resp_invalid.status_code == 422
    assert resp_invalid.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value


def test_void_order_post_alias(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """POST /orders/{orderId}/void alias functions identically to PATCH."""
    order = Order(
        public_id=uuid4(),
        status=OrderStatus.IN_PREPARATION,
        table_number=6,
    )
    test_db[order.public_id] = order

    resp = client.post(
        f"/orders/{order.public_id}/void",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "WASTED"
    assert test_db[order.public_id].status == OrderStatus.WASTED


def test_void_administrative_charges_frozen_prices(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """CT-17 & RF-27: Administrative void charges the customer with frozen dish prices."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=7,
        status=OrderStatus.IN_PREPARATION,
    )
    item1 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-tacos",
        name="Tacos al Pastor",
        quantity=3,
        unit_price=Decimal("35.00"),
        status=ItemStatus.PENDING,
    )
    item2 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-drink",
        name="Limonada",
        quantity=2,
        unit_price=Decimal("20.00"),
        status=ItemStatus.READY,
    )
    order.items.extend([item1, item2])
    order.recalculate_total()
    test_db[order_id] = order

    response = client.patch(
        f"/orders/{order_id}/void",
        headers=auth_headers("admin"),
    )
    assert response.status_code == 200

    charge_outbox = next(
        e for e in fake_session.outbox_rows if e.event_type == "ordenes.cobro.solicitado"
    )
    payload = charge_outbox.payload
    assert payload["totalAmount"] == 145.0
    items = cast(list[dict[str, Any]], payload.get("items", []))
    assert len(items) == 2
    assert items[0]["plateId"] == "dish-tacos"
    assert items[0]["unitPrice"] == 35.0
    assert items[0]["subtotal"] == 105.0
    assert items[1]["plateId"] == "dish-drink"
    assert items[1]["unitPrice"] == 20.0
    assert items[1]["subtotal"] == 40.0


# ===========================================================================
# TASK-19: Kitchen Error Void (Anulada, sin cobro)
# Satisfies: RF-28, RF-29, RF-30, RF-31, ADR-0012
# Tests: UT-BE-18, UT-BE-19, UT-BE-20, UT-BE-21, UT-BE-22
# ===========================================================================


@pytest.mark.parametrize("role", ["chef", "admin"])
def test_void_kitchen_error_allowed_for_chef_and_admin(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    role: str,
) -> None:
    """UT-BE-18: Both chef and admin roles can void an order for kitchen error."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=3,
        status=OrderStatus.IN_PREPARATION,
    )
    order.items.append(
        OrderItem(
            public_id=uuid4(),
            dish_id="dish-soup",
            name="Sopa Azteca",
            quantity=1,
            unit_price=Decimal("85.00"),
            status=ItemStatus.PENDING,
        )
    )
    test_db[order_id] = order

    response = client.patch(
        f"/orders/{order_id}/void-kitchen-error",
        headers=auth_headers(role),
        json={"note": "Error en condimentos durante la cocción"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["orderId"] == str(order_id)
    assert data["status"] == "VOIDED"
    assert test_db[order_id].status == OrderStatus.VOIDED


@pytest.mark.parametrize("role", ["waiter", "kitchen"])
def test_void_kitchen_error_by_waiter_or_kitchen_is_forbidden(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    role: str,
) -> None:
    """UT-BE-19: Waiter and kitchen staff are forbidden from kitchen error void."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=4,
        status=OrderStatus.IN_PREPARATION,
    )
    test_db[order_id] = order

    response = client.patch(
        f"/orders/{order_id}/void-kitchen-error",
        headers=auth_headers(role),
        json={"note": "Intento no autorizado"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value
    assert test_db[order_id].status == OrderStatus.IN_PREPARATION


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {},
        {"note": ""},
        {"note": "   "},
        {"reason": ""},
        {"reason": "   "},
    ],
)
def test_void_kitchen_error_without_note_raises_note_required(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    invalid_payload: dict[str, Any],
) -> None:
    """UT-BE-20: Missing, empty or whitespace-only explanation note raises NOTE_REQUIRED."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=5,
        status=OrderStatus.IN_PREPARATION,
    )
    test_db[order_id] = order

    response = client.patch(
        f"/orders/{order_id}/void-kitchen-error",
        headers=auth_headers("chef"),
        json=invalid_payload,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.NOTE_REQUIRED.value
    assert test_db[order_id].status == OrderStatus.IN_PREPARATION


def test_void_kitchen_error_affects_all_items_of_the_order(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
) -> None:
    """UT-BE-21: Kitchen error voids the entire order and includes all items in event."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=6,
        status=OrderStatus.IN_PREPARATION,
    )
    item1 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-steak",
        name="Corte Ribeye",
        quantity=2,
        unit_price=Decimal("350.00"),
        status=ItemStatus.PENDING,
    )
    item2 = OrderItem(
        public_id=uuid4(),
        dish_id="dish-salad",
        name="Ensalada César",
        quantity=1,
        unit_price=Decimal("120.00"),
        status=ItemStatus.READY,
    )
    order.items.extend([item1, item2])
    test_db[order_id] = order

    response = client.patch(
        f"/orders/{order_id}/void-kitchen-error",
        headers=auth_headers("chef"),
        json={"note": "Carne quemada y ensalada contaminada con aceite caliente"},
    )
    assert response.status_code == 200
    assert test_db[order_id].status == OrderStatus.VOIDED

    wasted_entry = next(
        e for e in fake_session.outbox_rows if e.event_type == "ordenes.comanda.mermada"
    )
    items_afectados = cast(list[dict[str, Any]], wasted_entry.payload.get("itemsAfectados", []))
    assert len(items_afectados) == 2
    offers = {it["offerId"] for it in items_afectados}
    assert "dish-steak" in offers
    assert "dish-salad" in offers


def test_void_kitchen_error_publishes_wasted_with_reason_and_no_charge(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    fake_session: FakeAsyncSession,
    spy_notifier: SpyOrderNotifier,
) -> None:
    """UT-BE-22: Publishes comanda.mermada with KITCHEN_ERROR and never publishes charge."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=8,
        status=OrderStatus.IN_PREPARATION,
    )
    item = OrderItem(
        public_id=uuid4(),
        dish_id="dish-pasta",
        name="Pasta Alfredo",
        quantity=2,
        unit_price=Decimal("140.00"),
        status=ItemStatus.PENDING,
    )
    order.items.append(item)
    test_db[order_id] = order

    response = client.patch(
        f"/orders/{order_id}/void-kitchen-error",
        headers=auth_headers("admin"),
        json={"note": "Salsa quemada en preparación"},
    )
    assert response.status_code == 200

    # 1. Exactly 1 outbox event staged (no charge event staged!)
    assert len(fake_session.outbox_rows) == 1
    assert fake_session.outbox_rows[0].event_type == "ordenes.comanda.mermada"
    wasted_payload = fake_session.outbox_rows[0].payload
    assert wasted_payload["orderId"] == str(order_id)
    assert wasted_payload["reason"] == "KITCHEN_ERROR"
    assert wasted_payload["motivo"] == "error_cocina"
    assert wasted_payload["note"] == "Salsa quemada en preparación"

    # 2. Verify strictly ZERO charge event staged (RF-31)
    charge_events = [
        e for e in fake_session.outbox_rows if e.event_type == "ordenes.cobro.solicitado"
    ]
    assert len(charge_events) == 0

    # 3. Realtime notification emitted
    assert any(o.public_id == order_id for o in spy_notifier.notified_orders)


@pytest.mark.parametrize(
    "invalid_status",
    [
        OrderStatus.CREATED,
        OrderStatus.RESERVED,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
        OrderStatus.PAID,
        OrderStatus.WASTED,
        OrderStatus.VOIDED,
        OrderStatus.EXPIRED,
    ],
)
def test_void_kitchen_error_in_non_in_preparation_status_raises_409(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
    invalid_status: OrderStatus,
) -> None:
    """Orders in any status other than IN_PREPARATION return 409 INVALID_STATE_TRANSITION."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=2,
        status=invalid_status,
    )
    test_db[order_id] = order

    response = client.patch(
        f"/orders/{order_id}/void-kitchen-error",
        headers=auth_headers("chef"),
        json={"note": "Error de cocina tardío"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION.value


def test_void_kitchen_error_not_found_404(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
) -> None:
    """Non-existent order returns 404 ORDER_NOT_FOUND."""
    missing_id = uuid4()
    response = client.patch(
        f"/orders/{missing_id}/void-kitchen-error",
        headers=auth_headers("chef"),
        json={"note": "Orden que no existe"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND.value


def test_void_kitchen_error_post_alias(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """POST alias for void-kitchen-error works identically to PATCH."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=9,
        status=OrderStatus.IN_PREPARATION,
    )
    test_db[order_id] = order

    response = client.post(
        f"/orders/{order_id}/void-kitchen-error",
        headers=auth_headers("chef"),
        json={"note": "Incidente en cocina vía POST alias"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "VOIDED"
    assert test_db[order_id].status == OrderStatus.VOIDED


def test_void_kitchen_error_rejects_extra_fields(
    client: TestClient,
    auth_headers: Callable[[str], dict[str, str]],
    test_db: dict[UUID, Order],
) -> None:
    """Payload with extra unexpected properties is rejected with 422 VALIDATION_ERROR."""
    order_id = uuid4()
    order = Order(
        public_id=order_id,
        table_number=1,
        status=OrderStatus.IN_PREPARATION,
    )
    test_db[order_id] = order

    response = client.patch(
        f"/orders/{order_id}/void-kitchen-error",
        headers=auth_headers("chef"),
        json={"note": "Error", "extraField": "forbidden"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
