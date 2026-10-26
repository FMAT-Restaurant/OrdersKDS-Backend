"""Microservice Contract Stubs package for Orders & KDS (TASK-33, RNF-13).

Provides test doubles and executable contract stubs for:
- Inventory microservice (InventoryStub)
- Payments & Billing microservice (BillingStub)
- Floor & Dining Room microservice (FloorStub)
- Menu & Culinary Catalog microservice (MenuStub)
"""

from __future__ import annotations

from app.stubs.billing_stub import BillingStub
from app.stubs.config import InventoryStubMode, StubLanguageConvention, StubSettings
from app.stubs.floor_stub import FloorStub
from app.stubs.inventory_stub import InventoryStub
from app.stubs.menu_stub import MenuStub
from app.stubs.protocol import InMemoryStubPublisher, StubPublisherProtocol

__all__ = [
    "BillingStub",
    "FloorStub",
    "InMemoryStubPublisher",
    "InventoryStub",
    "InventoryStubMode",
    "MenuStub",
    "StubLanguageConvention",
    "StubPublisherProtocol",
    "StubSettings",
]
