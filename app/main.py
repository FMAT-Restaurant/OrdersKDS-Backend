"""FastAPI application: routers, WebSocket endpoints and error handlers.

Satisfies: RNF-03, RNF-04
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.error_handlers import register_error_handlers
from app.api.middleware import CorrelationIdMiddleware
from app.api.routers import kds, orders, shifts
from app.api.websockets import routes as websocket_routes
from app.core.config import get_cors_allowed_origins
from app.core.logging import configure_structured_logging
from app.realtime.redis_bridge import RedisRealtimeBridge
from app.realtime.router import realtime_router

load_dotenv()
configure_structured_logging(service="orders-backend")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Start Redis Pub/Sub listener so worker updates reach live WebSockets."""
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        yield
        return

    redis_client = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
    )
    bridge = RedisRealtimeBridge(redis_client, router=realtime_router)
    stop_event = asyncio.Event()
    subscriber_task = asyncio.create_task(bridge.run_subscriber(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        subscriber_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await subscriber_task
        with contextlib.suppress(Exception):
            await redis_client.aclose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    application = FastAPI(title="Orders KDS Backend", lifespan=lifespan)

    application.add_middleware(CorrelationIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "Origin",
            "If-Match",
            "Idempotency-Key",
            "X-Correlation-ID",
            "X-Request-ID",
        ],
        expose_headers=["X-Correlation-ID", "ETag"],
    )

    register_error_handlers(application)

    @application.get("/health")
    @application.get("/api/v1/health")
    @application.get("/api/v1/orders/health")
    @application.get("/api/v1/kitchen/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    # Direct mounts plus Kong API Gateway mounts (strip_path: false).
    # Register specific gateway prefixes before "/api/v1" so static "/orders" routes
    # (e.g. GET /api/v1/orders/orders) win over "/api/v1/orders/{orderId}".
    for _prefix in ("/api/v1/orders", "/api/v1/kitchen", "/api/v1", ""):
        application.include_router(orders.router, prefix=_prefix)
        application.include_router(kds.router, prefix=_prefix)
        application.include_router(shifts.router, prefix=_prefix)
        application.include_router(websocket_routes.router, prefix=_prefix)

    return application


app = create_app()
