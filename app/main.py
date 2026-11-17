"""HTTP dashboard."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import HEADER_HINTS, PRESETS, Settings
from app.engine import Engine

STATIC = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = Engine()
        app.state.engine = engine
        engine.start()
        try:
            yield
        finally:
            engine.stop()

    app = FastAPI(title="Rampage Control", version=__version__, lifespan=lifespan)

    token = os.environ.get("RAMPAGE_TOKEN", "").strip()

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if token and request.url.path.startswith("/api") and request.url.path != "/api/health":
            supplied = request.headers.get("x-rampage-token", "")
            if supplied != token:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/status")
    def status(request: Request) -> dict:
        return request.app.state.engine.snapshot()

    @app.get("/api/config")
    def get_config(request: Request) -> dict:
        settings = request.app.state.engine.get_settings()
        return settings.model_dump()

    @app.put("/api/config")
    def put_config(settings: Settings, request: Request) -> dict:
        saved = request.app.state.engine.replace_settings(settings)
        return saved.model_dump()

    @app.post("/api/armed")
    def set_armed(body: dict, request: Request) -> dict:
        if "armed" not in body or not isinstance(body["armed"], bool):
            raise HTTPException(status_code=422, detail="armed must be true or false")
        saved = request.app.state.engine.set_armed(body["armed"])
        return saved.model_dump()

    @app.post("/api/fans/{fan_id:path}/pulse")
    def pulse(fan_id: str, request: Request) -> dict:
        try:
            request.app.state.engine.pulse(fan_id)
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown fan header") from exc
        return {"ok": True}

    @app.get("/api/meta")
    def meta() -> dict:
        return {"headers": HEADER_HINTS, "presets": PRESETS, "version": __version__}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
