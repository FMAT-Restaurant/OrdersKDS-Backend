"""Unit tests for Inventory-aligned demo seeding and catalog registry."""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest

import app.services.catalog as catalog_service
import scripts.seed_demo as seed_demo
from app.db.models import Order, Shift
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.shift import ShiftStatus

pytestmark = pytest.mark.unit

_FRONTEND_SEED_ORDER_IDS: tuple[UUID, ...] = (
    UUID("11111111-1111-1111-1111-111111111111"),
    UUID("22222222-2222-2222-2222-222222222222"),
    UUID("33333333-3333-3333-3333-333333333333"),
    UUID("44444444-4444-4444-4444-444444444444"),
    UUID("77777777-7777-7777-7777-777777777777"),
    UUID("88888888-8888-8888-8888-888888888888"),
)

_INVENTORY_TEST_DATA_PLATES: frozenset[str] = frozenset(
    {
        "PLATE-BURGER-01",
        "PLATE-QUES-01",
        "PLATE-CESAR-01",
        "PLATE-SIM-0001",
    }
)


class _FakeRedis:
    """In-memory async Redis double capturing deletes and catalog hashes."""

    def __init__(self, initial_hashes: dict[str, dict[str, str]] | None = None) -> None:
        self.hashes: dict[str, dict[str, str]] = initial_hashes or {}
        self.deleted_keys: list[str] = []
        self.close_count: int = 0

    async def delete(self, *names: str) -> int:
        removed = 0
        for name in names:
            self.deleted_keys.append(name)
            if name in self.hashes:
                del self.hashes[name]
                removed += 1
        return removed

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self.hashes.get(name, {}))

    async def hset(self, name: str, *, mapping: Mapping[str, str]) -> int:
        bucket = self.hashes.setdefault(name, {})
        bucket.update(mapping)
        return len(mapping)

    async def aclose(self) -> None:
        self.close_count += 1


class _FakeEngine:
    """Minimal async engine double tracking disposal."""

    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _SeedSessionDouble:
    """In-memory async SQLAlchemy session double tailored to scripts/seed_demo.py."""

    def __init__(
        self,
        *,
        orders: dict[UUID, Order] | None = None,
        shifts: list[Shift] | None = None,
    ) -> None:
        self.orders: dict[UUID, Order] = orders or {}
        self.shifts: list[Shift] = shifts or []
        self.executed_deletes: list[str] = []
        self.commit_count = 0

    async def __aenter__(self) -> _SeedSessionDouble:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def _assign_shift_ids(self) -> None:
        for idx, shift in enumerate(self.shifts, start=1):
            if getattr(shift, "id", None) is None:
                shift.id = idx

    def add(self, instance: object) -> None:
        if isinstance(instance, Shift):
            if instance not in self.shifts:
                self.shifts.append(instance)
            self._assign_shift_ids()
        elif isinstance(instance, Order):
            self.orders[instance.public_id] = instance

    async def flush(self) -> None:
        self._assign_shift_ids()

    async def commit(self) -> None:
        self._assign_shift_ids()
        self.commit_count += 1

    async def execute(self, statement: Any) -> None:
        table_name = getattr(getattr(statement, "table", None), "name", None)
        if isinstance(table_name, str):
            self.executed_deletes.append(table_name)
        if table_name == "orders":
            self.orders.clear()
        elif table_name == "shifts":
            self.shifts.clear()

    async def scalar(self, statement: Any) -> Any:
        column_descs = getattr(statement, "column_descriptions", ())
        entity = column_descs[0].get("entity") if column_descs else None
        params = statement.compile().params

        if entity is Shift:
            candidates = list(self.shifts)
            valid_statuses = {status.value for status in ShiftStatus}
            status_param = next(
                (
                    val
                    for val in params.values()
                    if isinstance(val, ShiftStatus)
                    or (isinstance(val, str) and val in valid_statuses)
                ),
                None,
            )
            if status_param is not None:
                expected = ShiftStatus(status_param)
                candidates = [shift for shift in candidates if shift.status == expected]
            if not candidates:
                return None
            return max(candidates, key=lambda shift: shift.shift_number)

        if entity is Order:
            order_id = next((val for val in params.values() if isinstance(val, UUID)), None)
            if order_id is not None:
                return self.orders.get(order_id)

        return None


class _FakeSessionFactory:
    """Async session factory returning the same shared session double across runs."""

    def __init__(self, session: _SeedSessionDouble) -> None:
        self.session = session

    def __call__(self) -> _SeedSessionDouble:
        return self.session


def _load_fresh_catalog_module() -> ModuleType:
    """Load an isolated copy of app.services.catalog unaffected by clear_catalog_mock()."""
    catalog_path = catalog_service.__file__
    assert catalog_path is not None
    spec = importlib.util.spec_from_file_location("_isolated_catalog_probe", Path(catalog_path))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_mock_registry_includes_inventory_test_data_plates() -> None:
    fresh_catalog = _load_fresh_catalog_module()
    expected = {
        "PLATE-BURGER-01": ("Parrilla", "15 min"),
        "PLATE-QUES-01": ("Parrilla", "10 min"),
        "PLATE-CESAR-01": ("Fría", "8 min"),
        "PLATE-SIM-0001": ("Parrilla", "12 min"),
    }
    for plate_id, (station, prep_time) in expected.items():
        info = fresh_catalog.get_catalog_info_mock(plate_id)
        assert info["station"] == station
        assert info["preparation_time_text"] == prep_time

    legacy = fresh_catalog.get_catalog_info_mock("dish-tacos")
    assert legacy["station"] == "Parrilla"


def test_demo_catalog_matches_inventory_test_data() -> None:
    entries_by_id = {entry.dish_id: entry for entry in seed_demo.DEMO_CATALOG}
    assert set(entries_by_id) == _INVENTORY_TEST_DATA_PLATES | {"PLATE-AGOTADO-01"}

    for plate_id in _INVENTORY_TEST_DATA_PLATES:
        assert entries_by_id[plate_id].available is True

    assert entries_by_id["PLATE-AGOTADO-01"].available is False


def test_build_demo_orders_aligns_ids_totals_and_frontend_uuids() -> None:
    orders = seed_demo._build_demo_orders(datetime.now(UTC))
    by_id = {order.public_id: order for order in orders}

    assert len(orders) == 8
    for expected_id in _FRONTEND_SEED_ORDER_IDS:
        assert expected_id in by_id

    for order in orders:
        assert len(order.items) >= 1
        expected_total = Decimal("0.00")
        for item in order.items:
            assert item.dish_id in _INVENTORY_TEST_DATA_PLATES
            assert item.offer_id is not None
            assert item.offer_id.startswith("OFFER-")
            assert len(item.dish_id) <= 100
            assert len(item.offer_id) <= 100
            if item.status != ItemStatus.CANCELLED:
                expected_total += item.unit_price * item.quantity
        assert order.total_amount == expected_total

    rejected = by_id[UUID("77777777-7777-7777-7777-777777777777")]
    assert rejected.status == OrderStatus.REJECTED
    assert rejected.rejection_details is not None
    rejected_items = rejected.rejection_details["rejectedItems"]
    assert isinstance(rejected_items, list)
    first_rejected = rejected_items[0]
    assert first_rejected["plateId"] == "PLATE-BURGER-01"
    assert first_rejected["sourceId"] == "PLATE-BURGER-01"
    assert first_rejected["offerId"] == "OFFER-BURGER-01"

    draft_main = by_id[UUID("44444444-4444-4444-4444-444444444444")]
    draft_sim = by_id[UUID("55555555-5555-5555-5555-555555555555")]
    assert draft_main.status == OrderStatus.CREATED
    assert draft_main.confirmed_at is None
    assert draft_sim.status == OrderStatus.CREATED
    assert draft_sim.is_takeaway is True
    assert any(item.dish_id == "PLATE-SIM-0001" for item in draft_sim.items)


@pytest.mark.asyncio
async def test_seed_redis_catalog_reset_clears_legacy_and_upserts_plates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis(
        initial_hashes={
            "catalog:platillo:dish-tacos": {"available": "true"},
            "catalog:platillo:dish-agotado": {"available": "false"},
        }
    )

    def _from_url(*_: object, **__: object) -> _FakeRedis:
        return fake_redis

    monkeypatch.setattr(seed_demo.Redis, "from_url", _from_url)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    await seed_demo._seed_redis_catalog(reset=False)
    assert "catalog:platillo:dish-tacos" in fake_redis.hashes
    assert fake_redis.deleted_keys == []
    assert "catalog:platillo:PLATE-BURGER-01" in fake_redis.hashes
    assert fake_redis.close_count == 1

    await seed_demo._seed_redis_catalog(reset=True)
    assert "catalog:platillo:dish-tacos" not in fake_redis.hashes
    assert "catalog:platillo:dish-agotado" not in fake_redis.hashes
    assert "catalog:platillo:PLATE-SIM-0001" in fake_redis.hashes
    assert fake_redis.hashes["catalog:platillo:PLATE-AGOTADO-01"]["available"] == "false"
    assert fake_redis.close_count == 2


def _patch_seed_infra(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fake_redis: _FakeRedis,
    session: _SeedSessionDouble,
    engine: _FakeEngine,
) -> None:
    def _from_url(*_: object, **__: object) -> _FakeRedis:
        return fake_redis

    factory = _FakeSessionFactory(session)
    monkeypatch.setattr(seed_demo, "load_dotenv", lambda: False)
    monkeypatch.setattr(seed_demo.Redis, "from_url", _from_url)
    monkeypatch.setattr(seed_demo, "get_session_factory", lambda: factory)
    monkeypatch.setattr(seed_demo, "get_engine", lambda: engine)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://testuser@localhost:5433/ordenes",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")


@pytest.mark.asyncio
async def test_seed_demo_data_reset_and_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    stale_order = Order(public_id=UUID("99999999-9999-9999-9999-999999999999"))
    stale_shift = Shift(shift_number=4, status=ShiftStatus.CLOSED)
    session = _SeedSessionDouble(
        orders={stale_order.public_id: stale_order},
        shifts=[stale_shift],
    )
    fake_redis = _FakeRedis(initial_hashes={"catalog:platillo:dish-tacos": {"available": "true"}})
    engine = _FakeEngine()
    _patch_seed_infra(monkeypatch, fake_redis=fake_redis, session=session, engine=engine)

    inserted, shift_number = await seed_demo.seed_demo_data(reset=True)

    assert inserted == 8
    assert shift_number == 1
    assert session.executed_deletes == [
        "processed_events",
        "outbox",
        "order_items",
        "orders",
        "shifts",
    ]
    assert stale_order.public_id not in session.orders
    assert len(session.shifts) == 1
    active_shift = session.shifts[0]
    assert active_shift.status == ShiftStatus.OPEN
    assert active_shift.last_order_folio == 8
    folios = sorted(
        order.shift_folio for order in session.orders.values() if order.shift_folio is not None
    )
    assert folios == [1, 2, 3, 4, 5, 6, 7, 8]
    assert all(order.shift_id == active_shift.id for order in session.orders.values())
    assert engine.disposed is True

    engine.disposed = False
    inserted_second, shift_second = await seed_demo.seed_demo_data(reset=False)
    assert inserted_second == 0
    assert shift_second == 1
    assert active_shift.last_order_folio == 8
    assert len(session.shifts) == 1


@pytest.mark.asyncio
async def test_seed_demo_data_opens_next_shift_when_previous_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed_shift = Shift(
        id=1,
        shift_number=3,
        status=ShiftStatus.CLOSED,
        last_order_folio=14,
    )
    session = _SeedSessionDouble(shifts=[closed_shift])
    fake_redis = _FakeRedis()
    engine = _FakeEngine()
    _patch_seed_infra(monkeypatch, fake_redis=fake_redis, session=session, engine=engine)

    inserted, shift_number = await seed_demo.seed_demo_data(reset=False)

    assert inserted == 8
    assert shift_number == 4
    assert len(session.shifts) == 2
    new_shift = session.shifts[-1]
    assert new_shift.status == ShiftStatus.OPEN
    assert new_shift.last_order_folio == 8


@pytest.mark.asyncio
async def test_seed_demo_data_catalog_only_opens_shift_without_orders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SeedSessionDouble()
    fake_redis = _FakeRedis()
    engine = _FakeEngine()
    _patch_seed_infra(monkeypatch, fake_redis=fake_redis, session=session, engine=engine)

    inserted, shift_number = await seed_demo.seed_demo_data(reset=True, catalog_only=True)

    assert inserted == 0
    assert shift_number == 1
    assert session.orders == {}
    assert len(session.shifts) == 1
    assert session.shifts[0].status == ShiftStatus.OPEN
    assert session.shifts[0].last_order_folio == 0
    assert "catalog:platillo:PLATE-BURGER-01" in fake_redis.hashes


def test_parse_args_and_main(monkeypatch: pytest.MonkeyPatch) -> None:
    default_args = seed_demo._parse_args([])
    assert default_args.reset is False
    assert default_args.catalog_only is False

    custom_args = seed_demo._parse_args(["--reset", "--catalog-only"])
    assert custom_args.reset is True
    assert custom_args.catalog_only is True

    called: dict[str, bool] = {}

    async def _fake_seed(*, reset: bool = False, catalog_only: bool = False) -> tuple[int, int]:
        called["reset"] = reset
        called["catalog_only"] = catalog_only
        return 0, 1

    monkeypatch.setattr(seed_demo, "seed_demo_data", _fake_seed)
    monkeypatch.setattr(seed_demo, "_parse_args", lambda: custom_args)

    seed_demo.main()
    assert called == {"reset": True, "catalog_only": True}
