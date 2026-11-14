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
from app.api.routers import kds, orders
from app.api.websockets import routes as websocket_routes
from app.realtime.redis_bridge import RedisRealtimeBridge
from app.realtime.router import realtime_router

load_dotenv()


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


app = FastAPI(title="Orders KDS Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)


@app.get("/health")
@app.get("/api/v1/health")
@app.get("/api/v1/orders/health")
@app.get("/api/v1/kitchen/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


# Direct mounts plus Kong API Gateway mounts (strip_path: false)
for _prefix in ("", "/api/v1", "/api/v1/orders", "/api/v1/kitchen"):
    app.include_router(orders.router, prefix=_prefix)
    app.include_router(kds.router, prefix=_prefix)
    app.include_router(websocket_routes.router, prefix=_prefix)
