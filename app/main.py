"""FastAPI application: routers, WebSocket endpoints and error handlers.

Satisfies: RNF-03, RNF-04
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.error_handlers import register_error_handlers
from app.api.routers import kds, orders
from app.api.websockets import routes as websocket_routes


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    database_url = os.environ.get("DATABASE_URL")
    engine = create_async_engine(database_url) if database_url else None
    if engine is not None:
        application.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield
    finally:
        if engine is not None:
            await engine.dispose()


app = FastAPI(title="Orders KDS Backend", lifespan=lifespan)

register_error_handlers(app)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(kds.router)
app.include_router(orders.router)
app.include_router(websocket_routes.router)
