from fastapi import FastAPI, Request

app = FastAPI(title="Orders KDS Backend")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
