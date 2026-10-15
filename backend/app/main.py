from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIST, settings
from .db import init_db, session_scope
from .logging_setup import redact, setup_logging
from .security import rate_limiter

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    from .layout.text import register_fonts
    from .providers.registry import seed_builtins
    register_fonts()
    with session_scope() as s:
        seed_builtins(s)
    from .pipeline import worker as wmod
    if settings.start_worker:
        wmod.worker = wmod.Worker()
        wmod.worker.start()
    log.info("Bookmaker started (env=%s, data=%s)", settings.env, settings.data_dir)
    yield
    if wmod.worker:
        wmod.worker.stop()


app = FastAPI(title="Bookmaker", version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs" if not settings.is_production else None, openapi_url="/api/openapi.json")

if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")], allow_methods=["*"],
                       allow_headers=["*"], allow_credentials=False)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        client = request.client.host if request.client else "?"
        if not rate_limiter.allow(client):
            return JSONResponse({"detail": "Too many requests"}, status_code=429)
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > (settings.max_upload_mb + 1) * 1024 * 1024:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    msg = "Internal error" if settings.is_production else redact(f"{exc.__class__.__name__}: {exc}")[:300]
    return JSONResponse({"detail": msg}, status_code=500)


@app.get("/api/health")
def health():
    return {"ok": True}


from .api.auth import router as auth_router  # noqa: E402
from .api.routes import router  # noqa: E402

app.include_router(auth_router)
app.include_router(router)

if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and FRONTEND_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
