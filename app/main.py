"""FastAPI application: routers, WebSocket endpoints and error handlers.

Satisfies: RNF-03, RNF-04
"""

from fastapi import FastAPI

from app.api.error_handlers import register_error_handlers
from app.api.routers import kds, orders
from app.api.websockets import routes as websocket_routes

app = FastAPI(title="Orders KDS Backend")

register_error_handlers(app)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(orders.router)
app.include_router(kds.router)
app.include_router(websocket_routes.router)
