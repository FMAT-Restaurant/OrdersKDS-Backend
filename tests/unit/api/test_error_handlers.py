"""Unit tests for global API error handlers.

Satisfies: RNF-03, RNF-09, RNF-10
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.error_handlers import register_error_handlers
from app.core.error_codes import ErrorCode
from app.domain.errors import DishUnavailableError

pytestmark = [pytest.mark.unit, pytest.mark.requirement("RNF-03", "RNF-09", "RNF-10")]


def _build_test_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/domain-error")
    def trigger_domain() -> None:
        raise DishUnavailableError(dish_id="dish-999")

    @app.get("/unauthorized")
    def trigger_unauthorized() -> None:
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    @app.get("/forbidden")
    def trigger_forbidden() -> None:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    @app.get("/not-found")
    def trigger_not_found() -> None:
        raise HTTPException(status_code=404, detail="Order not found")

    @app.get("/custom-http")
    def trigger_custom_http() -> None:
        raise HTTPException(status_code=418, detail="I am a teapot")

    @app.get("/unexpected")
    def trigger_unexpected() -> None:
        raise RuntimeError("Something exploded")

    return app


def test_domain_error_handling() -> None:
    client = TestClient(_build_test_app(), raise_server_exceptions=False)
    resp = client.get("/domain-error")
    assert resp.status_code == 422
    data = resp.json()["error"]
    assert data["code"] == ErrorCode.DISH_UNAVAILABLE
    assert "dish-999" in data["message"]


def test_http_exceptions() -> None:
    client = TestClient(_build_test_app(), raise_server_exceptions=False)

    resp_401 = client.get("/unauthorized")
    assert resp_401.status_code == 401
    assert resp_401.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED

    resp_403 = client.get("/forbidden")
    assert resp_403.status_code == 403
    assert resp_403.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE

    resp_404 = client.get("/not-found")
    assert resp_404.status_code == 404
    assert resp_404.json()["error"]["code"] == ErrorCode.ORDER_NOT_FOUND

    resp_418 = client.get("/custom-http")
    assert resp_418.status_code == 418
    assert resp_418.json()["error"]["code"] == ErrorCode.INTERNAL_ERROR
    assert resp_418.json()["error"]["message"] == "I am a teapot"


def test_unexpected_exception() -> None:
    client = TestClient(_build_test_app(), raise_server_exceptions=False)
    resp = client.get("/unexpected")
    assert resp.status_code == 500
    data = resp.json()["error"]
    assert data["code"] == ErrorCode.INTERNAL_ERROR
    assert data["message"] == "An internal server error occurred."
