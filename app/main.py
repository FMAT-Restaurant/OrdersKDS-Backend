from fastapi import FastAPI

from app.api.endpoints import kitchen

app = FastAPI(title="Orders KDS Backend")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

app.include_router(kitchen.router)  