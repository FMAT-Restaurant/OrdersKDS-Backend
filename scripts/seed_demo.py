"""Seed local PostgreSQL and Redis with realistic demo data aligned to Inventory.

Populates:
1. Redis local catalog projection (``catalog:platillo:<dish_id>``) with the
   active recipes defined in Inventory's ``TEST_DATA.md`` (``PLATE-BURGER-01``,
   ``PLATE-QUES-01``, ``PLATE-CESAR-01``, ``PLATE-SIM-0001``) plus one
   unavailable dish (``PLATE-AGOTADO-01``) to verify RF-17 validation.
2. PostgreSQL active operational ``Shift`` (#1) plus 8 deterministic demo
   orders (with sequential ``shift_folio`` values #1-#8, ``plateId``/``dish_id``,
   and commercial ``offer_id``) covering all lifecycle states (``RESERVED``,
   ``IN_PREPARATION``, ``DELIVERED``, ``CREATED``, ``REJECTED``, and
   ``RESERVED`` expired TTL).

Usage:
    python scripts/seed_demo.py
    python scripts/seed_demo.py --reset
    python scripts/seed_demo.py --reset --catalog-only
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import load_dotenv
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure the repository root is on sys.path when invoked as `python scripts/seed_demo.py`
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.cache.catalog import CatalogRepository, RedisHashAdapter  # noqa: E402
from app.core.config import get_redis_url  # noqa: E402
from app.db.models import Order, OrderItem, Outbox, ProcessedEvent, Shift  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.domain.order_status import ItemStatus, OrderStatus  # noqa: E402
from app.domain.shift import ShiftStatus  # noqa: E402
from app.repositories.shifts import ShiftRepository  # noqa: E402

DEFAULT_WAITER_ID = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_CHEF_ID = UUID("00000000-0000-0000-0000-000000000002")
DEFAULT_DINING_SESSION_ID = UUID("00000000-0000-0000-0000-000000000099")
DEMO_SHIFT_ID = UUID("00000000-0000-0000-0000-000000000100")

# Recipe IDs aligned verbatim with Inventory TEST_DATA.md (`plateId` / `sourceId`)
PLATE_BURGER_ID = "PLATE-BURGER-01"
OFFER_BURGER_ID = "OFFER-BURGER-01"
PLATE_BURGER_NAME = "Hamburguesa clásica"
PRICE_BURGER = Decimal("165.00")

PLATE_QUES_ID = "PLATE-QUES-01"
OFFER_QUES_ID = "OFFER-QUES-01"
PLATE_QUES_NAME = "Quesadilla de Oaxaca con jamón"
PRICE_QUES = Decimal("115.00")

PLATE_CESAR_ID = "PLATE-CESAR-01"
OFFER_CESAR_ID = "OFFER-CESAR-01"
PLATE_CESAR_NAME = "Ensalada César con pollo"
PRICE_CESAR = Decimal("135.00")

PLATE_SIM_ID = "PLATE-SIM-0001"
OFFER_SIM_ID = "OFFER-SIM-0001"
PLATE_SIM_NAME = "Platillo del Simulador"
PRICE_SIM = Decimal("120.00")

PLATE_UNAVAILABLE_ID = "PLATE-AGOTADO-01"
PRICE_UNAVAILABLE = Decimal("250.00")

STATION_PARRILLA = "parrilla"
STATION_FRIA = "fria"

MODIFIER_SIN_CEBOLLA = "Sin cebolla"
MODIFIER_TERMINO_MEDIO = "Término medio"
MODIFIER_ADEREZO_APARTE = "Aderezo aparte"

LOCALHOST_SCHEME_PREFIX = "://localhost:"
IPV4_LOOPBACK_SCHEME_PREFIX = "://127.0.0.1:"

LEGACY_REDIS_DISH_IDS: tuple[str, ...] = (
    "dish-tacos",
    "dish-horchata",
    "dish-arrachera",
    "dish-guacamole",
    "dish-hamburguesa",
    "dish-ensalada",
    "dish-agotado",
)


@dataclass(frozen=True, slots=True)
class CatalogDishSpec:
    """Catalog projection entry aligned with Menu and Inventory contracts."""

    dish_id: str
    price: Decimal
    available: bool
    category: str
    station: str
    preparation_time_text: str
    offer_id: str | None = None


@dataclass(frozen=True, slots=True)
class _SeedItemSpec:
    """Declarative specification for one deterministic seeded order line."""

    public_id: UUID
    dish_id: str
    offer_id: str
    name: str
    quantity: int
    unit_price: Decimal
    modifiers: tuple[str, ...] = ()
    notes: str | None = None
    status: ItemStatus = ItemStatus.PENDING


@dataclass(frozen=True, slots=True)
class _SeedOrderSpec:
    """Declarative specification for one deterministic seeded order aggregate."""

    public_id: UUID
    table_number: int | None
    is_takeaway: bool
    status: OrderStatus
    items: tuple[_SeedItemSpec, ...]
    confirmed_ago_min: int | None = None
    reserved_ago_min: int | None = None
    in_prep_ago_min: int | None = None
    updated_ago_min: int | None = None
    rejection_details: dict[str, object] | None = None
    summary_note: str = ""


DEMO_CATALOG: tuple[CatalogDishSpec, ...] = (
    CatalogDishSpec(
        dish_id=PLATE_BURGER_ID,
        offer_id=OFFER_BURGER_ID,
        price=PRICE_BURGER,
        available=True,
        category="Platos Fuertes",
        station=STATION_PARRILLA,
        preparation_time_text="15 min",
    ),
    CatalogDishSpec(
        dish_id=PLATE_QUES_ID,
        offer_id=OFFER_QUES_ID,
        price=PRICE_QUES,
        available=True,
        category="Entradas",
        station=STATION_PARRILLA,
        preparation_time_text="10 min",
    ),
    CatalogDishSpec(
        dish_id=PLATE_CESAR_ID,
        offer_id=OFFER_CESAR_ID,
        price=PRICE_CESAR,
        available=True,
        category="Entradas",
        station=STATION_FRIA,
        preparation_time_text="8 min",
    ),
    CatalogDishSpec(
        dish_id=PLATE_SIM_ID,
        offer_id=OFFER_SIM_ID,
        price=PRICE_SIM,
        available=True,
        category="Especialidades",
        station=STATION_PARRILLA,
        preparation_time_text="12 min",
    ),
    CatalogDishSpec(
        dish_id=PLATE_UNAVAILABLE_ID,
        price=PRICE_UNAVAILABLE,
        available=False,
        category="Especialidades",
        station=STATION_PARRILLA,
        preparation_time_text="20 min",
    ),
)

_REJECTED_ORDER_DETAILS: dict[str, object] = {
    "source": "inventory",
    "reason": "Stock insuficiente de pan para preparar las hamburguesas solicitadas.",
    "missingIngredients": ["pan"],
    "rejectedItems": [
        {
            "offerId": OFFER_BURGER_ID,
            "plateId": PLATE_BURGER_ID,
            "sourceId": PLATE_BURGER_ID,
            "dishId": PLATE_BURGER_ID,
            "name": PLATE_BURGER_NAME,
            "requestedQuantity": 2,
            "availableQuantity": 0,
            "reason": "Lote activo de pan agotado en almacén",
        }
    ],
    "reservedItems": [
        {
            "offerId": OFFER_QUES_ID,
            "plateId": PLATE_QUES_ID,
            "sourceId": PLATE_QUES_ID,
            "dishId": PLATE_QUES_ID,
            "name": PLATE_QUES_NAME,
            "quantity": 1,
        }
    ],
}

_SEED_ORDER_SPECS: tuple[_SeedOrderSpec, ...] = (
    _SeedOrderSpec(
        public_id=UUID("11111111-1111-1111-1111-111111111111"),
        table_number=5,
        is_takeaway=False,
        status=OrderStatus.RESERVED,
        confirmed_ago_min=4,
        reserved_ago_min=3,
        summary_note="KDS Active (start-preparation / kitchen-reject)",
        items=(
            _SeedItemSpec(
                public_id=UUID("11111111-0000-0000-0000-000000000001"),
                dish_id=PLATE_BURGER_ID,
                offer_id=OFFER_BURGER_ID,
                name=PLATE_BURGER_NAME,
                quantity=2,
                unit_price=PRICE_BURGER,
                modifiers=(MODIFIER_SIN_CEBOLLA, MODIFIER_TERMINO_MEDIO),
                notes="Pan bien dorado",
            ),
            _SeedItemSpec(
                public_id=UUID("11111111-0000-0000-0000-000000000002"),
                dish_id=PLATE_QUES_ID,
                offer_id=OFFER_QUES_ID,
                name=PLATE_QUES_NAME,
                quantity=1,
                unit_price=PRICE_QUES,
                modifiers=("Extra queso Oaxaca",),
            ),
        ),
    ),
    _SeedOrderSpec(
        public_id=UUID("22222222-2222-2222-2222-222222222222"),
        table_number=12,
        is_takeaway=False,
        status=OrderStatus.IN_PREPARATION,
        confirmed_ago_min=5,
        reserved_ago_min=4,
        in_prep_ago_min=3,
        summary_note="KDS Active (mark item ready / admin void / chef void)",
        items=(
            _SeedItemSpec(
                public_id=UUID("22222222-0000-0000-0000-000000000001"),
                dish_id=PLATE_QUES_ID,
                offer_id=OFFER_QUES_ID,
                name=PLATE_QUES_NAME,
                quantity=2,
                unit_price=PRICE_QUES,
                modifiers=("Tortilla suave",),
                notes="Sin picante",
            ),
            _SeedItemSpec(
                public_id=UUID("22222222-0000-0000-0000-000000000002"),
                dish_id=PLATE_CESAR_ID,
                offer_id=OFFER_CESAR_ID,
                name=PLATE_CESAR_NAME,
                quantity=1,
                unit_price=PRICE_CESAR,
                modifiers=(MODIFIER_ADEREZO_APARTE,),
                status=ItemStatus.READY,
            ),
        ),
    ),
    _SeedOrderSpec(
        public_id=UUID("33333333-3333-3333-3333-333333333333"),
        table_number=3,
        is_takeaway=False,
        status=OrderStatus.DELIVERED,
        confirmed_ago_min=8,
        reserved_ago_min=7,
        in_prep_ago_min=6,
        updated_ago_min=2,
        summary_note="KDS Delivered (item redo / close-bill)",
        items=(
            _SeedItemSpec(
                public_id=UUID("33333333-0000-0000-0000-000000000001"),
                dish_id=PLATE_BURGER_ID,
                offer_id=OFFER_BURGER_ID,
                name=PLATE_BURGER_NAME,
                quantity=2,
                unit_price=PRICE_BURGER,
                modifiers=(MODIFIER_SIN_CEBOLLA,),
                notes="Corte por la mitad",
                status=ItemStatus.DELIVERED,
            ),
        ),
    ),
    _SeedOrderSpec(
        public_id=UUID("44444444-4444-4444-4444-444444444444"),
        table_number=8,
        is_takeaway=False,
        status=OrderStatus.CREATED,
        summary_note="Draft (confirm vs real Inventory: PLATE-BURGER-01 + PLATE-CESAR-01)",
        items=(
            _SeedItemSpec(
                public_id=UUID("44444444-0000-0000-0000-000000000001"),
                dish_id=PLATE_BURGER_ID,
                offer_id=OFFER_BURGER_ID,
                name=PLATE_BURGER_NAME,
                quantity=1,
                unit_price=PRICE_BURGER,
                modifiers=(MODIFIER_TERMINO_MEDIO,),
            ),
            _SeedItemSpec(
                public_id=UUID("44444444-0000-0000-0000-000000000002"),
                dish_id=PLATE_CESAR_ID,
                offer_id=OFFER_CESAR_ID,
                name=PLATE_CESAR_NAME,
                quantity=1,
                unit_price=PRICE_CESAR,
                modifiers=(MODIFIER_ADEREZO_APARTE,),
            ),
        ),
    ),
    _SeedOrderSpec(
        public_id=UUID("55555555-5555-5555-5555-555555555555"),
        table_number=None,
        is_takeaway=True,
        status=OrderStatus.CREATED,
        summary_note="Draft Takeaway (confirm vs real Inventory: pan depletion PLATE-SIM-0001)",
        items=(
            _SeedItemSpec(
                public_id=UUID("55555555-0000-0000-0000-000000000001"),
                dish_id=PLATE_QUES_ID,
                offer_id=OFFER_QUES_ID,
                name=PLATE_QUES_NAME,
                quantity=2,
                unit_price=PRICE_QUES,
                notes="Empaque para llevar",
            ),
            _SeedItemSpec(
                public_id=UUID("55555555-0000-0000-0000-000000000002"),
                dish_id=PLATE_SIM_ID,
                offer_id=OFFER_SIM_ID,
                name=PLATE_SIM_NAME,
                quantity=1,
                unit_price=PRICE_SIM,
                notes="Flujo agotamiento pan (2 kg)",
            ),
        ),
    ),
    _SeedOrderSpec(
        public_id=UUID("66666666-6666-6666-6666-666666666666"),
        table_number=15,
        is_takeaway=False,
        status=OrderStatus.RESERVED,
        confirmed_ago_min=4,
        reserved_ago_min=3,
        summary_note="KDS Active (4 recipes: Parrilla + Fría station filter)",
        items=(
            _SeedItemSpec(
                public_id=UUID("66666666-0000-0000-0000-000000000001"),
                dish_id=PLATE_BURGER_ID,
                offer_id=OFFER_BURGER_ID,
                name=PLATE_BURGER_NAME,
                quantity=2,
                unit_price=PRICE_BURGER,
            ),
            _SeedItemSpec(
                public_id=UUID("66666666-0000-0000-0000-000000000002"),
                dish_id=PLATE_QUES_ID,
                offer_id=OFFER_QUES_ID,
                name=PLATE_QUES_NAME,
                quantity=2,
                unit_price=PRICE_QUES,
            ),
            _SeedItemSpec(
                public_id=UUID("66666666-0000-0000-0000-000000000003"),
                dish_id=PLATE_CESAR_ID,
                offer_id=OFFER_CESAR_ID,
                name=PLATE_CESAR_NAME,
                quantity=1,
                unit_price=PRICE_CESAR,
            ),
            _SeedItemSpec(
                public_id=UUID("66666666-0000-0000-0000-000000000004"),
                dish_id=PLATE_SIM_ID,
                offer_id=OFFER_SIM_ID,
                name=PLATE_SIM_NAME,
                quantity=1,
                unit_price=PRICE_SIM,
            ),
        ),
    ),
    _SeedOrderSpec(
        public_id=UUID("77777777-7777-7777-7777-777777777777"),
        table_number=9,
        is_takeaway=False,
        status=OrderStatus.REJECTED,
        confirmed_ago_min=3,
        rejection_details=_REJECTED_ORDER_DETAILS,
        summary_note="Rejected (edit items / reopen draft / cancel)",
        items=(
            _SeedItemSpec(
                public_id=UUID("77777777-0000-0000-0000-000000000001"),
                dish_id=PLATE_BURGER_ID,
                offer_id=OFFER_BURGER_ID,
                name=PLATE_BURGER_NAME,
                quantity=2,
                unit_price=PRICE_BURGER,
                modifiers=(MODIFIER_SIN_CEBOLLA,),
            ),
            _SeedItemSpec(
                public_id=UUID("77777777-0000-0000-0000-000000000002"),
                dish_id=PLATE_QUES_ID,
                offer_id=OFFER_QUES_ID,
                name=PLATE_QUES_NAME,
                quantity=1,
                unit_price=PRICE_QUES,
            ),
        ),
    ),
    _SeedOrderSpec(
        public_id=UUID("88888888-8888-8888-8888-888888888888"),
        table_number=11,
        is_takeaway=False,
        status=OrderStatus.RESERVED,
        confirmed_ago_min=18,
        reserved_ago_min=18,
        summary_note="KDS Expired TTL Tab (10m grace / waiter retry-modify-cancel)",
        items=(
            _SeedItemSpec(
                public_id=UUID("88888888-0000-0000-0000-000000000001"),
                dish_id=PLATE_CESAR_ID,
                offer_id=OFFER_CESAR_ID,
                name=PLATE_CESAR_NAME,
                quantity=1,
                unit_price=PRICE_CESAR,
                modifiers=("Sin croutones",),
                notes="Cliente esperando respuesta de cocina",
            ),
        ),
    ),
)

DEMO_ORDER_IDS: tuple[UUID, ...] = tuple(spec.public_id for spec in _SEED_ORDER_SPECS)


def _normalize_localhost_url(url: str) -> str:
    """Prefer 127.0.0.1 over localhost to avoid Windows ProactorEventLoop IPv6 hangs."""
    return url.replace("@localhost:", "@127.0.0.1:").replace(
        LOCALHOST_SCHEME_PREFIX,
        IPV4_LOOPBACK_SCHEME_PREFIX,
    )


def _ago(now: datetime, minutes: int | None) -> datetime | None:
    return now - timedelta(minutes=minutes) if minutes is not None else None


def _build_order_from_spec(
    spec: _SeedOrderSpec,
    now: datetime,
    *,
    fixed_ids: bool = False,
) -> Order:
    """Materialize one ORM Order aggregate with its OrderItems from a seed spec."""
    items = [
        OrderItem(
            public_id=item.public_id if fixed_ids else uuid4(),
            dish_id=item.dish_id,
            offer_id=item.offer_id,
            name=item.name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            modifiers=list(item.modifiers),
            notes=item.notes,
            status=item.status,
        )
        for item in spec.items
    ]
    order = Order(
        public_id=spec.public_id if fixed_ids else uuid4(),
        table_number=spec.table_number,
        is_takeaway=spec.is_takeaway,
        dining_session_id=DEFAULT_DINING_SESSION_ID,
        status=spec.status,
        confirmed_at=_ago(now, spec.confirmed_ago_min),
        confirmed_by=DEFAULT_WAITER_ID if spec.confirmed_ago_min is not None else None,
        stock_reserved_at=_ago(now, spec.reserved_ago_min),
        in_preparation_at=_ago(now, spec.in_prep_ago_min),
        updated_at=_ago(now, spec.updated_ago_min) or now,
        rejection_details=spec.rejection_details,
        created_by=DEFAULT_WAITER_ID,
        responsible_waiter_id=DEFAULT_WAITER_ID,
        items=items,
    )
    order.recalculate_total()
    return order


def _build_demo_orders(now: datetime, *, fixed_ids: bool = False) -> list[Order]:
    """Create demo Order aggregates aligned to Inventory TEST_DATA.md."""
    return [_build_order_from_spec(spec, now, fixed_ids=fixed_ids) for spec in _SEED_ORDER_SPECS]


def _build_demo_shift(
    now: datetime,
    *,
    shift_number: int = 1,
    fixed_ids: bool = False,
) -> Shift:
    """Create an OPEN operational shift for seeded per-shift folio allocation."""
    return Shift(
        public_id=DEMO_SHIFT_ID if (fixed_ids and shift_number == 1) else uuid4(),
        shift_number=shift_number,
        status=ShiftStatus.OPEN,
        last_order_folio=0,
        opened_at=now - timedelta(hours=2),
        opened_by=DEFAULT_CHEF_ID,
        notes="Turno inicial semilla alineado a TEST_DATA.md (Inventario FMAT)",
    )


async def _seed_redis_catalog(*, reset: bool = False) -> None:
    """Populate Redis with catalog projections matching Inventory TEST_DATA.md."""
    redis_url = _normalize_localhost_url(get_redis_url())
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3.0)
    try:
        catalog_repo = CatalogRepository(RedisHashAdapter(redis))
        if reset:
            offer_ids = tuple(dish.offer_id for dish in DEMO_CATALOG if dish.offer_id is not None)
            keys_to_delete = [
                catalog_repo.key(dish_id)
                for dish_id in (
                    *LEGACY_REDIS_DISH_IDS,
                    *(dish.dish_id for dish in DEMO_CATALOG),
                    *offer_ids,
                )
            ]
            if keys_to_delete:
                await redis.delete(*keys_to_delete)

        for dish in DEMO_CATALOG:
            target_ids = (dish.dish_id, dish.offer_id) if dish.offer_id else (dish.dish_id,)
            for catalog_id in target_ids:
                await catalog_repo.upsert(
                    catalog_id,
                    price=dish.price,
                    available=dish.available,
                    category=dish.category,
                    station=dish.station,
                    preparation_time_text=dish.preparation_time_text,
                )
    finally:
        await redis.aclose()


async def _reset_database(session: AsyncSession) -> None:
    """Wipe PostgreSQL transactional and shift tables cleanly before re-seeding."""
    await session.execute(delete(ProcessedEvent))
    await session.execute(delete(Outbox))
    await session.execute(delete(OrderItem))
    await session.execute(delete(Order))
    await session.execute(delete(Shift))
    await session.commit()


async def _resolve_target_shift(
    session: AsyncSession,
    now: datetime,
    *,
    fixed_ids: bool = False,
) -> Shift:
    """Return the active OPEN shift or create and flush the next shift."""
    shift_repo = ShiftRepository(session)
    active = await shift_repo.get_active_shift()
    if active is not None:
        return active
    latest = await shift_repo.get_latest_shift()
    next_number = (latest.shift_number + 1) if latest is not None else 1
    shift = _build_demo_shift(now, shift_number=next_number, fixed_ids=fixed_ids)
    shift_repo.save(shift)
    await session.flush()
    return shift


async def _insert_demo_orders(
    session: AsyncSession,
    shift: Shift,
    now: datetime,
    *,
    fixed_ids: bool = False,
) -> list[tuple[Order, str]]:
    """Insert demo orders cleanly, generating unique UUIDv4 IDs unless fixed_ids is True."""
    if not fixed_ids and (shift.last_order_folio or 0) > 0:
        return []

    inserted_orders: list[tuple[Order, str]] = []
    orders = _build_demo_orders(now, fixed_ids=fixed_ids)
    for order, spec in zip(orders, _SEED_ORDER_SPECS, strict=True):
        if fixed_ids:
            existing = await session.scalar(
                select(Order).where(Order.public_id == order.public_id)
            )
            if existing is not None:
                continue
        order.shift_id = shift.id
        order.shift_folio = shift.allocate_next_folio()
        session.add(order)
        inserted_orders.append((order, spec.summary_note))
    shift_repo = ShiftRepository(session)
    shift_repo.save(shift)
    return inserted_orders


def _print_seed_summary(
    *,
    inserted_orders: list[tuple[Order, str]],
    shift: Shift,
    catalog_only: bool,
) -> None:
    """Print an actionable summary of seeded catalog dishes, shift, and orders."""
    catalog_ids = ", ".join(dish.dish_id for dish in DEMO_CATALOG)
    print(f"[OK] Redis catalog seeded with {len(DEMO_CATALOG)} entries ({catalog_ids}).")
    print(
        f"[OK] PostgreSQL active Shift #{shift.shift_number} "
        f"(status={shift.status.value}, last_order_folio={shift.last_order_folio})."
    )
    if catalog_only:
        print("[OK] Catalog-only mode: 0 demo orders inserted.")
        return

    print(
        f"[OK] PostgreSQL seeded ({len(inserted_orders)} new orders inserted; "
        "pass --reset to wipe & regenerate fresh unique UUIDs)."
    )
    if not inserted_orders:
        print("Active shift already contains demo orders (pass --reset to regenerate fresh IDs).")
        return

    print("\nDemo Orders Summary (aligned to Inventory TEST_DATA.md):")
    for order, summary_note in inserted_orders:
        location = "Para llevar" if order.is_takeaway else f"Mesa {order.table_number:<2}"
        folio = order.shift_folio or "-"
        print(
            f"  #{folio} {order.status.value:<14} | OrderId: {order.public_id} | "
            f"{location} | {summary_note}"
        )
    print("Note: Outbound events default correlationId to each unique OrderId when confirmed.")


async def seed_demo_data(
    *,
    reset: bool = False,
    catalog_only: bool = False,
    fixed_ids: bool = False,
) -> tuple[int, int]:
    """Insert Inventory-aligned catalog entries in Redis and shift/orders in PostgreSQL."""
    load_dotenv()
    await _seed_redis_catalog(reset=reset)

    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        os.environ["DATABASE_URL"] = _normalize_localhost_url(db_url)

    session_factory = get_session_factory()
    now = datetime.now(UTC)

    try:
        async with session_factory() as session:
            if reset:
                await _reset_database(session)

            shift = await _resolve_target_shift(session, now, fixed_ids=fixed_ids)
            inserted_orders = (
                []
                if catalog_only
                else await _insert_demo_orders(session, shift, now, fixed_ids=fixed_ids)
            )
            shift_number = shift.shift_number
            await session.commit()
    finally:
        await get_engine().dispose()

    _print_seed_summary(
        inserted_orders=inserted_orders,
        shift=shift,
        catalog_only=catalog_only,
    )
    return len(inserted_orders), shift_number


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seed local PostgreSQL and Redis with operational shift, catalog items, "
            "and demo orders aligned to Inventory TEST_DATA.md."
        )
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Wipe existing PostgreSQL orders/shifts/outbox/processed_events and "
            "legacy/demo Redis catalog keys before seeding."
        ),
    )
    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="Seed Redis catalog and ensure an active Shift exists without demo orders.",
    )
    parser.add_argument(
        "--fixed-ids",
        action="store_true",
        help="Use deterministic legacy UUIDs instead of generating fresh unique UUIDv4 values.",
    )
    return parser.parse_args(argv)


def main() -> None:
    """CLI entrypoint for seeding demo data."""
    args = _parse_args()
    asyncio.run(
        seed_demo_data(
            reset=args.reset,
            catalog_only=args.catalog_only,
            fixed_ids=args.fixed_ids,
        )
    )


if __name__ == "__main__":
    main()
