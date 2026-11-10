"""Seed local PostgreSQL and Redis with realistic demo data for manual testing.

Populates:
1. Redis local catalog projection (``catalog:platillo:<dish_id>``) with available
   dishes and one unavailable dish (``dish-agotado``) to test RF-17 validation.
2. PostgreSQL ``orders`` and ``order_items`` tables with 4 deterministic orders
   covering the main lifecycle states (``RESERVED``, ``IN_PREPARATION``,
   ``DELIVERED``, and ``CREATED``) so that both Swagger (``/docs``) and the KDS
   frontend can be tested immediately without running external microservices.

Usage:
    python scripts/seed_demo.py
    python scripts/seed_demo.py --reset
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from redis.asyncio import Redis
from sqlalchemy import delete, select

# Ensure the repository root is on sys.path when invoked as `python scripts/seed_demo.py`
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.cache.catalog import CatalogRepository  # noqa: E402
from app.core.config import get_redis_url  # noqa: E402
from app.db.models import Order, OrderItem  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.domain.order_status import ItemStatus, OrderStatus  # noqa: E402
from app.services.catalog import _RedisHashAdapter  # noqa: E402

DEFAULT_WAITER_ID = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_DINING_SESSION_ID = UUID("00000000-0000-0000-0000-000000000099")

DEMO_ORDER_IDS: tuple[UUID, ...] = (
    UUID("11111111-1111-1111-1111-111111111111"),
    UUID("22222222-2222-2222-2222-222222222222"),
    UUID("33333333-3333-3333-3333-333333333333"),
    UUID("44444444-4444-4444-4444-444444444444"),
)

DEMO_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "dish_id": "dish-tacos",
        "price": Decimal("120.00"),
        "available": True,
        "category": "Platos Fuertes",
        "station": "parrilla",
        "preparation_time_text": "12 min",
    },
    {
        "dish_id": "dish-horchata",
        "price": Decimal("45.00"),
        "available": True,
        "category": "Bebidas",
        "station": "barra",
        "preparation_time_text": "3 min",
    },
    {
        "dish_id": "dish-arrachera",
        "price": Decimal("290.00"),
        "available": True,
        "category": "Cortes",
        "station": "parrilla",
        "preparation_time_text": "18 min",
    },
    {
        "dish_id": "dish-guacamole",
        "price": Decimal("95.00"),
        "available": True,
        "category": "Entradas",
        "station": "fria",
        "preparation_time_text": "5 min",
    },
    {
        "dish_id": "dish-hamburguesa",
        "price": Decimal("165.00"),
        "available": True,
        "category": "Platos Fuertes",
        "station": "parrilla",
        "preparation_time_text": "15 min",
    },
    {
        "dish_id": "dish-ensalada",
        "price": Decimal("110.00"),
        "available": True,
        "category": "Entradas",
        "station": "fria",
        "preparation_time_text": "8 min",
    },
    {
        "dish_id": "dish-agotado",
        "price": Decimal("320.00"),
        "available": False,
        "category": "Especialidades",
        "station": "parrilla",
        "preparation_time_text": "20 min",
    },
)


def _build_demo_orders(now: datetime) -> list[Order]:
    """Create the four demo Order aggregates covering key lifecycle states."""
    order_reserved = Order(
        public_id=UUID("11111111-1111-1111-1111-111111111111"),
        table_number=5,
        is_takeaway=False,
        dining_session_id=DEFAULT_DINING_SESSION_ID,
        status=OrderStatus.RESERVED,
        confirmed_at=now - timedelta(minutes=8),
        confirmed_by=DEFAULT_WAITER_ID,
        stock_reserved_at=now - timedelta(minutes=7),
        created_by=DEFAULT_WAITER_ID,
        responsible_waiter_id=DEFAULT_WAITER_ID,
        items=[
            OrderItem(
                public_id=UUID("11111111-0000-0000-0000-000000000001"),
                dish_id="dish-tacos",
                name="Orden de Tacos al Pastor",
                quantity=2,
                unit_price=Decimal("120.00"),
                modifiers=["Sin cebolla", "Con todo"],
                notes="Tortilla bien dorada",
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("11111111-0000-0000-0000-000000000002"),
                dish_id="dish-horchata",
                name="Agua de Horchata 1L",
                quantity=1,
                unit_price=Decimal("45.00"),
                modifiers=[],
                notes=None,
                status=ItemStatus.PENDING,
            ),
        ],
    )
    order_reserved.recalculate_total()

    order_in_prep = Order(
        public_id=UUID("22222222-2222-2222-2222-222222222222"),
        table_number=12,
        is_takeaway=False,
        dining_session_id=DEFAULT_DINING_SESSION_ID,
        status=OrderStatus.IN_PREPARATION,
        confirmed_at=now - timedelta(minutes=15),
        confirmed_by=DEFAULT_WAITER_ID,
        stock_reserved_at=now - timedelta(minutes=14),
        in_preparation_at=now - timedelta(minutes=10),
        created_by=DEFAULT_WAITER_ID,
        responsible_waiter_id=DEFAULT_WAITER_ID,
        items=[
            OrderItem(
                public_id=UUID("22222222-0000-0000-0000-000000000001"),
                dish_id="dish-arrachera",
                name="Arrachera Norteña 300g",
                quantity=1,
                unit_price=Decimal("290.00"),
                modifiers=["Término medio"],
                notes="Sin sal extra",
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("22222222-0000-0000-0000-000000000002"),
                dish_id="dish-guacamole",
                name="Guacamole con Totopos",
                quantity=1,
                unit_price=Decimal("95.00"),
                modifiers=["Poco picante"],
                notes=None,
                status=ItemStatus.READY,
            ),
        ],
    )
    order_in_prep.recalculate_total()

    order_delivered = Order(
        public_id=UUID("33333333-3333-3333-3333-333333333333"),
        table_number=3,
        is_takeaway=False,
        dining_session_id=DEFAULT_DINING_SESSION_ID,
        status=OrderStatus.DELIVERED,
        confirmed_at=now - timedelta(minutes=30),
        confirmed_by=DEFAULT_WAITER_ID,
        stock_reserved_at=now - timedelta(minutes=29),
        in_preparation_at=now - timedelta(minutes=25),
        created_by=DEFAULT_WAITER_ID,
        responsible_waiter_id=DEFAULT_WAITER_ID,
        items=[
            OrderItem(
                public_id=UUID("33333333-0000-0000-0000-000000000001"),
                dish_id="dish-hamburguesa",
                name="Hamburguesa Clásica",
                quantity=2,
                unit_price=Decimal("165.00"),
                modifiers=["Con tocino"],
                notes=None,
                status=ItemStatus.READY,
            ),
        ],
    )
    order_delivered.recalculate_total()

    order_created = Order(
        public_id=UUID("44444444-4444-4444-4444-444444444444"),
        table_number=8,
        is_takeaway=False,
        dining_session_id=DEFAULT_DINING_SESSION_ID,
        status=OrderStatus.CREATED,
        created_by=DEFAULT_WAITER_ID,
        responsible_waiter_id=DEFAULT_WAITER_ID,
        items=[
            OrderItem(
                public_id=UUID("44444444-0000-0000-0000-000000000001"),
                dish_id="dish-ensalada",
                name="Ensalada César",
                quantity=1,
                unit_price=Decimal("110.00"),
                modifiers=["Aderezo aparte"],
                notes=None,
                status=ItemStatus.PENDING,
            ),
        ],
    )
    order_created.recalculate_total()

    return [order_reserved, order_in_prep, order_delivered, order_created]


async def _seed_redis_catalog() -> None:
    """Populate Redis with demo dish availability and metadata."""
    # On Windows + Python 3.14 ProactorEventLoop, connecting to "localhost" tries IPv6 (::1)
    # first, which can hang on Docker Desktop port forwarding. Prefer 127.0.0.1 for local URLs.
    redis_url = get_redis_url().replace("://localhost:", "://127.0.0.1:")
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3.0)
    try:
        catalog_repo = CatalogRepository(_RedisHashAdapter(redis))
        for dish in DEMO_CATALOG:
            await catalog_repo.upsert(
                dish["dish_id"],
                price=dish["price"],
                available=dish["available"],
                category=dish["category"],
                station=dish["station"],
                preparation_time_text=dish["preparation_time_text"],
            )
    finally:
        await redis.aclose()


async def seed_demo_data(*, reset: bool = False) -> None:
    """Insert demo catalog entries in Redis and demo orders in PostgreSQL."""
    load_dotenv()
    await _seed_redis_catalog()

    # Also normalize localhost -> 127.0.0.1 in DATABASE_URL to avoid IPv6 timeouts on Windows
    db_url = os.environ.get("DATABASE_URL", "")
    if "://localhost:" in db_url or "@localhost:" in db_url:
        os.environ["DATABASE_URL"] = db_url.replace("@localhost:", "@127.0.0.1:").replace(
            "://localhost:", "://127.0.0.1:"
        )

    session_factory = get_session_factory()
    now = datetime.now(UTC)
    demo_orders = _build_demo_orders(now)

    try:
        async with session_factory() as session:
            if reset:
                await session.execute(delete(Order).where(Order.public_id.in_(DEMO_ORDER_IDS)))
                await session.commit()

            inserted = 0
            for order in demo_orders:
                existing = await session.scalar(
                    select(Order).where(Order.public_id == order.public_id)
                )
                if existing is None:
                    session.add(order)
                    inserted += 1

            await session.commit()
    finally:
        await get_engine().dispose()

    print(f"[OK] Redis catalog seeded with {len(DEMO_CATALOG)} dishes.")
    print(
        f"[OK] PostgreSQL seeded ({inserted} new orders inserted; "
        "pass --reset to recreate existing demo orders)."
    )
    print("\nDemo Orders Summary:")
    print(
        "  1. RESERVED       | OrderId: 11111111-1111-1111-1111-111111111111 | "
        "Mesa 5  | KDS Active (start-preparation / kitchen-reject)"
    )
    print(
        "  2. IN_PREPARATION | OrderId: 22222222-2222-2222-2222-222222222222 | "
        "Mesa 12 | KDS Active (mark item ready / admin void / chef void)"
    )
    print(
        "  3. DELIVERED      | OrderId: 33333333-3333-3333-3333-333333333333 | "
        "Mesa 3  | KDS Delivered (item redo / close-bill)"
    )
    print(
        "  4. CREATED        | OrderId: 44444444-4444-4444-4444-444444444444 | "
        "Mesa 8  | Draft (update items / transfer / confirm / cancel)"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed local PostgreSQL and Redis with demo orders and catalog items."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete and recreate the 4 demo orders in their initial statuses.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint for seeding demo data."""
    args = _parse_args()
    asyncio.run(seed_demo_data(reset=args.reset))


if __name__ == "__main__":
    main()
