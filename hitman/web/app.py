"""FastAPI application factory."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from hitman.core.engines.curl_engine import curl_available
from hitman.core.store import Store

BASE_DIR = Path(__file__).parent
DEFAULT_DB = os.environ.get("HITMAN_DB", "data/hitman.db")


def create_app(db_path: str | Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.store.close()

    app = FastAPI(title="Hitman", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.store = Store(db_path or DEFAULT_DB)
    # Jinja2Templates enables autoescape for .html by default. Response bodies
    # are attacker-controlled; do not turn it off.
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    app.state.curl_available = curl_available()
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    from hitman.web import routes  # imported here to avoid a circular import

    app.include_router(routes.router)
    return app
