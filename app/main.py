"""FastAPI application: routers, WebSocket endpoints and error handlers.

Satisfies: RNF-03, RNF-04
"""

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_error_handlers
from app.api.routers import kds, orders
from app.api.websockets import routes as websocket_routes

load_dotenv()

app = FastAPI(title="Orders KDS Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(orders.router)
app.include_router(kds.router)
app.include_router(websocket_routes.router)

# Mounts for Kong API Gateway (strip_path: false)
app.include_router(orders.router, prefix="/api/v1")
app.include_router(kds.router, prefix="/api/v1")
app.include_router(websocket_routes.router, prefix="/api/v1")

app.include_router(orders.router, prefix="/api/v1/orders")
app.include_router(kds.router, prefix="/api/v1/orders")
app.include_router(websocket_routes.router, prefix="/api/v1/orders")
