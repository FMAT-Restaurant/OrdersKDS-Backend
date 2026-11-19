"""Shared domain constants for identifiers, field bounds, and business limits.

Satisfies: RF-14, RF-17, RF-22, RF-28, RNF-13
"""

from __future__ import annotations

from typing import Final

# Aligned with Inventory-Backend Recipe.plateId (@MaxLength(100)) and Menu-Backend UUID/string IDs.
MAX_CATALOG_ID_LENGTH: Final[int] = 100
MAX_NOTE_LENGTH: Final[int] = 500
