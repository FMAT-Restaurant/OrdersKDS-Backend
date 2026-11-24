"""Root test fixtures configuring deterministic JWT verification for all test suites."""

from __future__ import annotations

import pytest

TEST_JWT_SECRET = "unit-test-key-not-a-secret-0123456789"


@pytest.fixture(autouse=True)
def _configure_test_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a deterministic test HMAC key and fail-closed gateway defaults."""
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.delenv("JWT_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("JWT_ALGORITHMS", raising=False)
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "false")
    monkeypatch.delenv("GATEWAY_SHARED_SECRET", raising=False)
