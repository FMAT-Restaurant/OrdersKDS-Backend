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

from app.cache.catalog import CatalogRepository, RedisHashAdapter  # noqa: E402
from app.core.config import get_redis_url  # noqa: E402
from app.db.models import Order, OrderItem, Outbox, ProcessedEvent  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.domain.order_status import ItemStatus, OrderStatus  # noqa: E402

DEFAULT_WAITER_ID = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_DINING_SESSION_ID = UUID("00000000-0000-0000-0000-000000000099")
DISH_HORCHATA_NAME = "Agua de Horchata 1L"
DISH_HORCHATA_ID = "dish-horchata"
DISH_TACOS_ID = "dish-tacos"
DISH_TACOS_NAME = "Orden de Tacos al Pastor"
DISH_ARRACHERA_ID = "dish-arrachera"
DISH_ARRACHERA_NAME = "Arrachera Norteña 300g"
DISH_GUACAMOLE_ID = "dish-guacamole"
DISH_GUACAMOLE_NAME = "Guacamole con Totopos"
DISH_HAMBURGUESA_ID = "dish-hamburguesa"
DISH_HAMBURGUESA_NAME = "Hamburguesa Clásica"
DISH_ENSALADA_ID = "dish-ensalada"
DISH_ENSALADA_NAME = "Ensalada César"

PRICE_HORCHATA = Decimal("45.00")
PRICE_TACOS = Decimal("120.00")
PRICE_ARRACHERA = Decimal("290.00")
PRICE_GUACAMOLE = Decimal("95.00")
PRICE_HAMBURGUESA = Decimal("165.00")
PRICE_ENSALADA = Decimal("110.00")

STATION_PARRILLA = "parrilla"
STATION_BARRA = "barra"
STATION_FRIA = "fria"

MODIFIER_SIN_CEBOLLA = "Sin cebolla"
MODIFIER_TERMINO_MEDIO = "Término medio"

LOCALHOST_SCHEME_PREFIX = "://localhost:"
IPV4_LOOPBACK_SCHEME_PREFIX = "://127.0.0.1:"

EXTRA_DEMO_ORDER_UUIDS: tuple[UUID, ...] = tuple(
    UUID(f"77777777-0000-0000-0000-{i:012d}") for i in range(1, 25)
)

DEMO_ORDER_IDS: tuple[UUID, ...] = (
    UUID("11111111-1111-1111-1111-111111111111"),
    UUID("22222222-2222-2222-2222-222222222222"),
    UUID("33333333-3333-3333-3333-333333333333"),
    UUID("44444444-4444-4444-4444-444444444444"),
    UUID("55555555-5555-5555-5555-555555555555"),
    UUID("66666666-6666-6666-6666-666666666666"),
    *EXTRA_DEMO_ORDER_UUIDS,
)

DEMO_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "dish_id": DISH_TACOS_ID,
        "price": PRICE_TACOS,
        "available": True,
        "category": "Platos Fuertes",
        "station": STATION_PARRILLA,
        "preparation_time_text": "12 min",
    },
    {
        "dish_id": DISH_HORCHATA_ID,
        "price": PRICE_HORCHATA,
        "available": True,
        "category": "Bebidas",
        "station": STATION_BARRA,
        "preparation_time_text": "3 min",
    },
    {
        "dish_id": DISH_ARRACHERA_ID,
        "price": PRICE_ARRACHERA,
        "available": True,
        "category": "Cortes",
        "station": STATION_PARRILLA,
        "preparation_time_text": "18 min",
    },
    {
        "dish_id": DISH_GUACAMOLE_ID,
        "price": PRICE_GUACAMOLE,
        "available": True,
        "category": "Entradas",
        "station": STATION_FRIA,
        "preparation_time_text": "5 min",
    },
    {
        "dish_id": DISH_HAMBURGUESA_ID,
        "price": PRICE_HAMBURGUESA,
        "available": True,
        "category": "Platos Fuertes",
        "station": STATION_PARRILLA,
        "preparation_time_text": "15 min",
    },
    {
        "dish_id": DISH_ENSALADA_ID,
        "price": PRICE_ENSALADA,
        "available": True,
        "category": "Entradas",
        "station": STATION_FRIA,
        "preparation_time_text": "8 min",
    },
    {
        "dish_id": "dish-agotado",
        "price": Decimal("320.00"),
        "available": False,
        "category": "Especialidades",
        "station": STATION_PARRILLA,
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
        confirmed_at=now - timedelta(minutes=4),
        confirmed_by=DEFAULT_WAITER_ID,
        stock_reserved_at=now - timedelta(minutes=3),
        created_by=DEFAULT_WAITER_ID,
        responsible_waiter_id=DEFAULT_WAITER_ID,
        items=[
            OrderItem(
                public_id=UUID("11111111-0000-0000-0000-000000000001"),
                dish_id=DISH_TACOS_ID,
                name=DISH_TACOS_NAME,
                quantity=2,
                unit_price=PRICE_TACOS,
                modifiers=[MODIFIER_SIN_CEBOLLA, "Con todo"],
                notes="Tortilla bien dorada",
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("11111111-0000-0000-0000-000000000002"),
                dish_id=DISH_HORCHATA_ID,
                name=DISH_HORCHATA_NAME,
                quantity=1,
                unit_price=PRICE_HORCHATA,
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
        confirmed_at=now - timedelta(minutes=5),
        confirmed_by=DEFAULT_WAITER_ID,
        stock_reserved_at=now - timedelta(minutes=4),
        in_preparation_at=now - timedelta(minutes=3),
        created_by=DEFAULT_WAITER_ID,
        responsible_waiter_id=DEFAULT_WAITER_ID,
        items=[
            OrderItem(
                public_id=UUID("22222222-0000-0000-0000-000000000001"),
                dish_id=DISH_ARRACHERA_ID,
                name=DISH_ARRACHERA_NAME,
                quantity=1,
                unit_price=PRICE_ARRACHERA,
                modifiers=[MODIFIER_TERMINO_MEDIO],
                notes="Sin sal extra",
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("22222222-0000-0000-0000-000000000002"),
                dish_id=DISH_GUACAMOLE_ID,
                name=DISH_GUACAMOLE_NAME,
                quantity=1,
                unit_price=PRICE_GUACAMOLE,
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
        confirmed_at=now - timedelta(minutes=8),
        confirmed_by=DEFAULT_WAITER_ID,
        stock_reserved_at=now - timedelta(minutes=7),
        in_preparation_at=now - timedelta(minutes=6),
        updated_at=now - timedelta(minutes=2),
        created_by=DEFAULT_WAITER_ID,
        responsible_waiter_id=DEFAULT_WAITER_ID,
        items=[
            OrderItem(
                public_id=UUID("33333333-0000-0000-0000-000000000001"),
                dish_id=DISH_HAMBURGUESA_ID,
                name=DISH_HAMBURGUESA_NAME,
                quantity=2,
                unit_price=PRICE_HAMBURGUESA,
                modifiers=["Con tocino"],
                notes=None,
                status=ItemStatus.DELIVERED,
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
                dish_id=DISH_ENSALADA_ID,
                name=DISH_ENSALADA_NAME,
                quantity=1,
                unit_price=PRICE_ENSALADA,
                modifiers=["Aderezo aparte"],
                notes=None,
                status=ItemStatus.PENDING,
            ),
        ],
    )
    order_created.recalculate_total()

    order_large_prep = Order(
        public_id=UUID("55555555-5555-5555-5555-555555555555"),
        table_number=5,
        is_takeaway=False,
        dining_session_id=DEFAULT_DINING_SESSION_ID,
        status=OrderStatus.IN_PREPARATION,
        confirmed_at=now - timedelta(minutes=10),
        confirmed_by=DEFAULT_WAITER_ID,
        stock_reserved_at=now - timedelta(minutes=9),
        in_preparation_at=now - timedelta(minutes=8),
        created_by=DEFAULT_WAITER_ID,
        responsible_waiter_id=DEFAULT_WAITER_ID,
        items=[
            OrderItem(
                public_id=UUID("55555555-0000-0000-0000-000000000001"),
                dish_id=DISH_HAMBURGUESA_ID,
                name=DISH_HAMBURGUESA_NAME,
                quantity=2,
                unit_price=PRICE_HAMBURGUESA,
                modifiers=[MODIFIER_SIN_CEBOLLA, "Queso extra"],
                notes="Término 3/4",
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("55555555-0000-0000-0000-000000000002"),
                dish_id=DISH_TACOS_ID,
                name=DISH_TACOS_NAME,
                quantity=1,
                unit_price=PRICE_TACOS,
                modifiers=["Cebolla asada"],
                notes=None,
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("55555555-0000-0000-0000-000000000003"),
                dish_id=DISH_GUACAMOLE_ID,
                name=DISH_GUACAMOLE_NAME,
                quantity=1,
                unit_price=PRICE_GUACAMOLE,
                modifiers=["Sin picante"],
                notes=None,
                status=ItemStatus.READY,
            ),
            OrderItem(
                public_id=UUID("55555555-0000-0000-0000-000000000004"),
                dish_id=DISH_ENSALADA_ID,
                name=DISH_ENSALADA_NAME,
                quantity=1,
                unit_price=PRICE_ENSALADA,
                modifiers=["Aderezo aparte"],
                notes=None,
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("55555555-0000-0000-0000-000000000005"),
                dish_id=DISH_HORCHATA_ID,
                name=DISH_HORCHATA_NAME,
                quantity=2,
                unit_price=PRICE_HORCHATA,
                modifiers=["Poco hielo"],
                notes=None,
                status=ItemStatus.READY,
            ),
            OrderItem(
                public_id=UUID("55555555-0000-0000-0000-000000000006"),
                dish_id=DISH_ARRACHERA_ID,
                name=DISH_ARRACHERA_NAME,
                quantity=1,
                unit_price=PRICE_ARRACHERA,
                modifiers=["Bien cocida"],
                notes="Papas cambray",
                status=ItemStatus.PENDING,
            ),
        ],
    )
    order_large_prep.recalculate_total()

    order_huge_reserved = Order(
        public_id=UUID("66666666-6666-6666-6666-666666666666"),
        table_number=15,
        is_takeaway=False,
        dining_session_id=DEFAULT_DINING_SESSION_ID,
        status=OrderStatus.RESERVED,
        confirmed_at=now - timedelta(minutes=4),
        confirmed_by=DEFAULT_WAITER_ID,
        stock_reserved_at=now - timedelta(minutes=3),
        created_by=DEFAULT_WAITER_ID,
        responsible_waiter_id=DEFAULT_WAITER_ID,
        items=[
            OrderItem(
                public_id=UUID("66666666-0000-0000-0000-000000000001"),
                dish_id=DISH_TACOS_ID,
                name=DISH_TACOS_NAME,
                quantity=3,
                unit_price=PRICE_TACOS,
                modifiers=["Salsa habanera aparte"],
                notes=None,
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("66666666-0000-0000-0000-000000000002"),
                dish_id=DISH_HAMBURGUESA_ID,
                name=DISH_HAMBURGUESA_NAME,
                quantity=2,
                unit_price=PRICE_HAMBURGUESA,
                modifiers=["Papas gajo"],
                notes="Sin mostaza",
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("66666666-0000-0000-0000-000000000003"),
                dish_id=DISH_ARRACHERA_ID,
                name=DISH_ARRACHERA_NAME,
                quantity=1,
                unit_price=PRICE_ARRACHERA,
                modifiers=[MODIFIER_TERMINO_MEDIO],
                notes="Tortillas de maíz",
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("66666666-0000-0000-0000-000000000004"),
                dish_id=DISH_GUACAMOLE_ID,
                name=DISH_GUACAMOLE_NAME,
                quantity=2,
                unit_price=PRICE_GUACAMOLE,
                modifiers=["Extra totopos"],
                notes=None,
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("66666666-0000-0000-0000-000000000005"),
                dish_id=DISH_ENSALADA_ID,
                name=DISH_ENSALADA_NAME,
                quantity=1,
                unit_price=PRICE_ENSALADA,
                modifiers=[],
                notes="Sin aceitunas",
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("66666666-0000-0000-0000-000000000006"),
                dish_id=DISH_HORCHATA_ID,
                name=DISH_HORCHATA_NAME,
                quantity=3,
                unit_price=PRICE_HORCHATA,
                modifiers=[],
                notes=None,
                status=ItemStatus.PENDING,
            ),
            OrderItem(
                public_id=UUID("66666666-0000-0000-0000-000000000007"),
                dish_id=DISH_HAMBURGUESA_ID,
                name=DISH_HAMBURGUESA_NAME,
                quantity=1,
                unit_price=PRICE_HAMBURGUESA,
                modifiers=["Pepinillos extra"],
                notes=None,
                status=ItemStatus.PENDING,
            ),
        ],
    )
    order_huge_reserved.recalculate_total()

    return [
        order_reserved,
        order_in_prep,
        order_large_prep,
        order_huge_reserved,
        order_delivered,
        order_created,
        *_build_extra_demo_orders(now),
    ]


EXTRA_ORDER_CONFIGS: tuple[dict[str, Any], ...] = (
    {
        "table": 1,
        "is_takeaway": False,
        "status": OrderStatus.IN_PREPARATION,
        "age_min": 3,
        "items": [
            (DISH_TACOS_ID, DISH_TACOS_NAME, PRICE_TACOS, 2, ["Con piña"], ItemStatus.PENDING),
            (DISH_HORCHATA_ID, DISH_HORCHATA_NAME, PRICE_HORCHATA, 1, [], ItemStatus.READY),
        ],
    },
    {
        "table": 2,
        "is_takeaway": False,
        "status": OrderStatus.RESERVED,
        "age_min": 5,
        "items": [
            (
                DISH_HAMBURGUESA_ID,
                DISH_HAMBURGUESA_NAME,
                PRICE_HAMBURGUESA,
                1,
                [MODIFIER_SIN_CEBOLLA],
                ItemStatus.PENDING,
            ),
        ],
    },
    {
        "table": None,
        "is_takeaway": True,
        "status": OrderStatus.IN_PREPARATION,
        "age_min": 7,
        "items": [
            (
                DISH_ARRACHERA_ID,
                DISH_ARRACHERA_NAME,
                PRICE_ARRACHERA,
                1,
                [MODIFIER_TERMINO_MEDIO],
                ItemStatus.PENDING,
            ),
            (DISH_GUACAMOLE_ID, DISH_GUACAMOLE_NAME, PRICE_GUACAMOLE, 1, [], ItemStatus.PENDING),
        ],
    },
    {
        "table": 4,
        "is_takeaway": False,
        "status": OrderStatus.IN_PREPARATION,
        "age_min": 9,
        "items": [
            (
                DISH_ENSALADA_ID,
                DISH_ENSALADA_NAME,
                PRICE_ENSALADA,
                2,
                ["Pollo extra"],
                ItemStatus.READY,
            ),
            (DISH_HORCHATA_ID, DISH_HORCHATA_NAME, PRICE_HORCHATA, 2, [], ItemStatus.READY),
        ],
    },
    {
        "table": 6,
        "is_takeaway": False,
        "status": OrderStatus.RESERVED,
        "age_min": 4,
        "items": [
            (DISH_TACOS_ID, DISH_TACOS_NAME, PRICE_TACOS, 3, ["Salsa verde"], ItemStatus.PENDING),
        ],
    },
    {
        "table": 7,
        "is_takeaway": False,
        "status": OrderStatus.IN_PREPARATION,
        "age_min": 12,
        "items": [
            (
                DISH_HAMBURGUESA_ID,
                DISH_HAMBURGUESA_NAME,
                PRICE_HAMBURGUESA,
                2,
                ["Tocino extra"],
                ItemStatus.PENDING,
            ),
            (DISH_GUACAMOLE_ID, DISH_GUACAMOLE_NAME, PRICE_GUACAMOLE, 1, [], ItemStatus.PENDING),
        ],
    },
    {
        "table": 9,
        "is_takeaway": False,
        "status": OrderStatus.IN_PREPARATION,
        "age_min": 16,
        "items": [
            (
                DISH_ARRACHERA_ID,
                DISH_ARRACHERA_NAME,
                PRICE_ARRACHERA,
                2,
                ["Bien cocida"],
                ItemStatus.PENDING,
            ),
            (DISH_HORCHATA_ID, DISH_HORCHATA_NAME, PRICE_HORCHATA, 2, [], ItemStatus.PENDING),
        ],
    },
    {
        "table": 10,
        "is_takeaway": False,
        "status": OrderStatus.RESERVED,
        "age_min": 6,
        "items": [
            (DISH_TACOS_ID, DISH_TACOS_NAME, PRICE_TACOS, 1, [], ItemStatus.PENDING),
            (DISH_ENSALADA_ID, DISH_ENSALADA_NAME, PRICE_ENSALADA, 1, [], ItemStatus.PENDING),
        ],
    },
    {
        "table": 11,
        "is_takeaway": False,
        "status": OrderStatus.IN_PREPARATION,
        "age_min": 8,
        "items": [
            (
                DISH_HAMBURGUESA_ID,
                DISH_HAMBURGUESA_NAME,
                PRICE_HAMBURGUESA,
                1,
                [],
                ItemStatus.READY,
            ),
            (DISH_HORCHATA_ID, DISH_HORCHATA_NAME, PRICE_HORCHATA, 1, [], ItemStatus.PENDING),
        ],
    },
    {
        "table": None,
        "is_takeaway": True,
        "status": OrderStatus.IN_PREPARATION,
        "age_min": 11,
        "items": [
            (
                DISH_TACOS_ID,
                DISH_TACOS_NAME,
                PRICE_TACOS,
                2,
                ["Cebolla asada"],
                ItemStatus.PENDING,
            ),
        ],
    },
    {
        "table": 13,
        "is_takeaway": False,
        "status": OrderStatus.RESERVED,
        "age_min": 2,
        "items": [
            (
                DISH_ARRACHERA_ID,
                DISH_ARRACHERA_NAME,
                PRICE_ARRACHERA,
                1,
                ["Término 3/4"],
                ItemStatus.PENDING,
            ),
        ],
    },
    {
        "table": 14,
        "is_takeaway": False,
        "status": OrderStatus.IN_PREPARATION,
        "age_min": 14,
        "items": [
            (DISH_GUACAMOLE_ID, DISH_GUACAMOLE_NAME, PRICE_GUACAMOLE, 2, [], ItemStatus.READY),
            (DISH_HORCHATA_ID, DISH_HORCHATA_NAME, PRICE_HORCHATA, 2, [], ItemStatus.READY),
        ],
    },
    {
        "table": 16,
        "is_takeaway": False,
        "status": OrderStatus.IN_PREPARATION,
        "age_min": 18,
        "items": [
            (
                DISH_HAMBURGUESA_ID,
                DISH_HAMBURGUESA_NAME,
                PRICE_HAMBURGUESA,
                2,
                [],
                ItemStatus.PENDING,
            ),
        ],
    },
    {
        "table": 17,
        "is_takeaway": False,
        "status": OrderStatus.RESERVED,
        "age_min": 3,
        "items": [
            (DISH_ENSALADA_ID, DISH_ENSALADA_NAME, PRICE_ENSALADA, 1, [], ItemStatus.PENDING),
            (DISH_HORCHATA_ID, DISH_HORCHATA_NAME, PRICE_HORCHATA, 1, [], ItemStatus.PENDING),
        ],
    },
    {
        "table": 18,
        "is_takeaway": False,
        "status": OrderStatus.DELIVERED,
        "age_min": 25,
        "items": [(DISH_TACOS_ID, DISH_TACOS_NAME, PRICE_TACOS, 2, [], ItemStatus.READY)],
    },
    {
        "table": 19,
        "is_takeaway": False,
        "status": OrderStatus.DELIVERED,
        "age_min": 30,
        "items": [
            (
                DISH_HAMBURGUESA_ID,
                DISH_HAMBURGUESA_NAME,
                PRICE_HAMBURGUESA,
                1,
                [],
                ItemStatus.READY,
            )
        ],
    },
    {
        "table": 20,
        "is_takeaway": False,
        "status": OrderStatus.DELIVERED,
        "age_min": 35,
        "items": [
            (DISH_ARRACHERA_ID, DISH_ARRACHERA_NAME, PRICE_ARRACHERA, 1, [], ItemStatus.READY)
        ],
    },
    {
        "table": None,
        "is_takeaway": True,
        "status": OrderStatus.DELIVERED,
        "age_min": 40,
        "items": [
            (DISH_GUACAMOLE_ID, DISH_GUACAMOLE_NAME, PRICE_GUACAMOLE, 1, [], ItemStatus.READY)
        ],
    },
    {
        "table": 21,
        "is_takeaway": False,
        "status": OrderStatus.DELIVERED,
        "age_min": 45,
        "items": [(DISH_ENSALADA_ID, DISH_ENSALADA_NAME, PRICE_ENSALADA, 1, [], ItemStatus.READY)],
    },
    {
        "table": 22,
        "is_takeaway": False,
        "status": OrderStatus.DELIVERED,
        "age_min": 50,
        "items": [(DISH_HORCHATA_ID, DISH_HORCHATA_NAME, PRICE_HORCHATA, 2, [], ItemStatus.READY)],
    },
    {
        "table": 23,
        "is_takeaway": False,
        "status": OrderStatus.DELIVERED,
        "age_min": 55,
        "items": [(DISH_TACOS_ID, DISH_TACOS_NAME, PRICE_TACOS, 1, [], ItemStatus.READY)],
    },
    {
        "table": 24,
        "is_takeaway": False,
        "status": OrderStatus.DELIVERED,
        "age_min": 60,
        "items": [
            (
                DISH_HAMBURGUESA_ID,
                DISH_HAMBURGUESA_NAME,
                PRICE_HAMBURGUESA,
                2,
                [],
                ItemStatus.READY,
            )
        ],
    },
    {
        "table": 25,
        "is_takeaway": False,
        "status": OrderStatus.CREATED,
        "age_min": 1,
        "items": [(DISH_TACOS_ID, DISH_TACOS_NAME, PRICE_TACOS, 1, [], ItemStatus.PENDING)],
    },
    {
        "table": None,
        "is_takeaway": True,
        "status": OrderStatus.CREATED,
        "age_min": 1,
        "items": [
            (DISH_HORCHATA_ID, DISH_HORCHATA_NAME, PRICE_HORCHATA, 1, [], ItemStatus.PENDING)
        ],
    },
)


def _build_extra_demo_orders(now: datetime) -> list[Order]:
    orders: list[Order] = []
    for idx, cfg in enumerate(EXTRA_ORDER_CONFIGS, start=1):
        order_uuid = EXTRA_DEMO_ORDER_UUIDS[idx - 1]
        age = timedelta(minutes=cfg["age_min"])
        status = cfg["status"]
        items = [
            OrderItem(
                public_id=UUID(f"77777777-{idx:04d}-0000-0000-{j:012d}"),
                dish_id=item_tuple[0],
                name=item_tuple[1],
                unit_price=item_tuple[2],
                quantity=item_tuple[3],
                modifiers=item_tuple[4],
                status=item_tuple[5],
            )
            for j, item_tuple in enumerate(cfg["items"], start=1)
        ]
        order = Order(
            public_id=order_uuid,
            table_number=cfg["table"],
            is_takeaway=cfg["is_takeaway"],
            dining_session_id=DEFAULT_DINING_SESSION_ID,
            status=status,
            confirmed_at=now - age if status != OrderStatus.CREATED else None,
            confirmed_by=DEFAULT_WAITER_ID if status != OrderStatus.CREATED else None,
            stock_reserved_at=now - age if status != OrderStatus.CREATED else None,
            in_preparation_at=now - age
            if status in (OrderStatus.IN_PREPARATION, OrderStatus.DELIVERED)
            else None,
            updated_at=now - age if status == OrderStatus.DELIVERED else None,
            created_by=DEFAULT_WAITER_ID,
            responsible_waiter_id=DEFAULT_WAITER_ID,
            items=items,
        )
        order.recalculate_total()
        orders.append(order)
    return orders


async def _seed_redis_catalog() -> None:
    """Populate Redis with demo dish availability and metadata."""
    # On Windows + Python 3.14 ProactorEventLoop, connecting to "localhost" tries IPv6 (::1)
    # first, which can hang on Docker Desktop port forwarding. Prefer 127.0.0.1 for local URLs.
    redis_url = get_redis_url().replace(LOCALHOST_SCHEME_PREFIX, IPV4_LOOPBACK_SCHEME_PREFIX)
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3.0)
    try:
        catalog_repo = CatalogRepository(RedisHashAdapter(redis))
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
    if LOCALHOST_SCHEME_PREFIX in db_url or "@localhost:" in db_url:
        os.environ["DATABASE_URL"] = db_url.replace("@localhost:", "@127.0.0.1:").replace(
            LOCALHOST_SCHEME_PREFIX, IPV4_LOOPBACK_SCHEME_PREFIX
        )

    session_factory = get_session_factory()
    now = datetime.now(UTC)
    demo_orders = _build_demo_orders(now)

    try:
        async with session_factory() as session:
            if reset:
                await session.execute(
                    delete(ProcessedEvent).where(ProcessedEvent.order_id.in_(DEMO_ORDER_IDS))
                )
                await session.execute(delete(Outbox))
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
