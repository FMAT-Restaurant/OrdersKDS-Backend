"""Configuration settings and operating modes for microservice contract stubs.

Satisfies: RNF-13, RF-33, RF-34, RF-35, OP-09, OP-11
"""

from __future__ import annotations

import os
from enum import StrEnum

from pydantic import BaseModel, Field


class InventoryStubMode(StrEnum):
    """Operating modes for the Inventory contract stub (TASK-33, RF-33..35).

    - RESERVE: Immediately confirms ingredient reservation (happy path).
    - REJECT: Emits stock insufficient rejection with missing ingredients.
    - SILENT: Emits no event, allowing Saga timeout watcher to fire (EXPIRED).
    - DELAYED: Delays response by a configured duration (e.g. 59s, 60s, 61s).
    - LATE_ON_CANCELLED: Emits a late reservation event after order cancellation.
    - ATOMIC: Decrements an internal atomic inventory counter, rejecting once exhausted.
    """

    RESERVE = "RESERVE"
    REJECT = "REJECT"
    SILENT = "SILENT"
    DELAYED = "DELAYED"
    LATE_ON_CANCELLED = "LATE_ON_CANCELLED"
    ATOMIC = "ATOMIC"


class StubLanguageConvention(StrEnum):
    """Payload language convention mode.

    - ENGLISH: Inter-team agreed convention (default).
    - HYBRID: Emits both English canonical names and Spanish legacy aliases.
    - SPANISH_LEGACY: Emits Spanish legacy field names only.
    """

    ENGLISH = "ENGLISH"
    HYBRID = "HYBRID"
    SPANISH_LEGACY = "SPANISH_LEGACY"


class StubSettings(BaseModel):
    """Environment configuration for running stubs."""

    inventory_mode: InventoryStubMode = Field(
        default_factory=lambda: InventoryStubMode(
            os.getenv("INVENTORY_STUB_MODE", InventoryStubMode.RESERVE)
        )
    )
    inventory_delay_seconds: float = Field(
        default_factory=lambda: float(os.getenv("INVENTORY_STUB_DELAY_SECONDS", "60.0"))
    )
    inventory_initial_stock: int = Field(
        default_factory=lambda: int(os.getenv("INVENTORY_STUB_INITIAL_STOCK", "1"))
    )
    billing_auto_pay: bool = Field(
        default_factory=lambda: (
            os.getenv("BILLING_STUB_AUTO_PAY", "true").lower() in ("1", "true", "yes")
        )
    )
    billing_ack_delay_seconds: float = Field(
        default_factory=lambda: float(os.getenv("BILLING_STUB_ACK_DELAY_SECONDS", "0.0"))
    )
    menu_stub_port: int = Field(default_factory=lambda: int(os.getenv("MENU_STUB_PORT", "8010")))
    menu_failure_mode: bool = Field(
        default_factory=lambda: (
            os.getenv("MENU_STUB_FAILURE_MODE", "false").lower() in ("1", "true", "yes")
        )
    )
    language_convention: StubLanguageConvention = Field(
        default_factory=lambda: StubLanguageConvention(
            os.getenv("STUB_LANGUAGE_CONVENTION", StubLanguageConvention.HYBRID)
        )
    )
