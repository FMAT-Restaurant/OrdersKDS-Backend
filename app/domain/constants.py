"""Shared domain constants for identifiers, field bounds, and business limits.

Satisfies: RF-14, RF-17, RF-22, RF-28, RNF-13
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

# Aligned with Inventory-Backend Recipe.plateId (@MaxLength(100)) and Menu-Backend UUID/string IDs.
MAX_CATALOG_ID_LENGTH: Final[int] = 100
MAX_NOTE_LENGTH: Final[int] = 500

# Upper bounds aligned with PostgreSQL 32-bit INTEGER / NUMERIC(12, 2) and business rules.
MAX_SQL_INT32: Final[int] = 2_147_483_647
MAX_TABLE_NUMBER: Final[int] = 10_000
MAX_ITEM_QUANTITY: Final[int] = 1_000
MAX_ORDER_ITEMS: Final[int] = 200
MAX_MONETARY_AMOUNT: Final[Decimal] = Decimal("9999999999.99")
