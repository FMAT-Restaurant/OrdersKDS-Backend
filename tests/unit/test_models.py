"""ORM mapping tests for orders and order items.

Verifies table registration, UUID/Decimal/UTC column contracts and
the optimistic-concurrency wiring (``version_id_col``).

Satisfies: RNF-09
"""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal

import pytest
from sqlalchemy import Enum, Numeric, Uuid

from app.db.base import Base
from app.db.models import Order, OrderItem, _utcnow
from app.domain.order_status import ItemStatus, OrderStatus


@pytest.mark.unit
def test_tables_are_registered_on_base_metadata() -> None:
    assert set(Base.metadata.tables.keys()) >= {"orders", "order_items"}


@pytest.mark.unit
def test_utcnow_returns_timezone_aware_utc() -> None:
    assert _utcnow().tzinfo is UTC


@pytest.mark.unit
def test_order_column_contracts() -> None:
    table = Order.__table__
    assert isinstance(table.c.public_id.type, Uuid)
    assert table.c.public_id.unique
    status_type = table.c.status.type
    assert isinstance(status_type, Enum)
    assert status_type.name == "order_status"
    assert table.c.status.default is not None
    assert table.c.status.default.arg == OrderStatus.CREATED
    assert table.c.version.default is not None
    assert table.c.version.default.arg == 1
    total_type = table.c.total_amount.type
    assert isinstance(total_type, Numeric)
    assert (total_type.precision, total_type.scale) == (12, 2)
    assert table.c.total_amount.default is not None
    assert table.c.total_amount.default.arg == Decimal("0.00")
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True


@pytest.mark.unit
def test_order_item_column_contracts() -> None:
    table = OrderItem.__table__
    assert table.c.order_id.references(Order.__table__.c.id)
    assert isinstance(table.c.public_id.type, Uuid)
    assert table.c.public_id.unique
    status_type = table.c.status.type
    assert isinstance(status_type, Enum)
    assert status_type.name == "item_status"
    assert table.c.status.default is not None
    assert table.c.status.default.arg == ItemStatus.PENDING
    assert table.c.version.default is not None
    assert table.c.version.default.arg == 1


@pytest.mark.unit
def test_version_id_col_enables_optimistic_concurrency() -> None:
    assert Order.__mapper__.version_id_col is Order.__table__.c.version
    assert OrderItem.__mapper__.version_id_col is OrderItem.__table__.c.version
