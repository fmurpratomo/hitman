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
from hitman.web.bodyview import pretty_lines
from hitman.web.charts import nice_ceiling
from hitman.web.dates import relative

BASE_DIR = Path(__file__).parent
DEFAULT_DB = os.environ.get("HITMAN_DB", "data/hitman.db")


def create_app(db_path: str | Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        # A performance test still running would otherwise go on sending, and
        # then try to record itself in a closed database.
        for run in app.state.live_loads.values():
            run.stop()
            run.wait(timeout=5)
        app.state.store.close()

    app = FastAPI(title="Hitman", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.store = Store(db_path or DEFAULT_DB)
    # Jinja2Templates enables autoescape for .html by default. Response bodies
    # are attacker-controlled; do not turn it off.
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    # A display helper, not markup: the run report shows a body per step and
    # cannot precompute one list per step in the route.
    app.state.templates.env.globals["pretty_lines"] = pretty_lines
    app.state.templates.env.globals["nice_ceiling"] = nice_ceiling
    # The saved list dates one kept response per row; a route cannot precompute
    # a string per row without walking the list a second time.
    app.state.templates.env.globals["relative"] = relative
    app.state.curl_available = curl_available()
    # Performance tests in flight, by token. See the performance routes.
    app.state.live_loads = {}
    # Where each of those runs was stored once it finished, by the same token,
    # so its report can offer its exports without looking the run up again.
    app.state.load_run_ids = {}
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        """Stop the browser serving a stale app.js after an update.

        Starlette's StaticFiles sends ETag and Last-Modified but no
        Cache-Control, and with no explicit directive browsers fall back to
        heuristic freshness — so a changed script can keep running from cache
        long after the app was updated. "no-cache" means revalidate, not
        "do not store": the ETag still yields a cheap 304.
        """
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    from hitman.web import routes  # imported here to avoid a circular import

    app.include_router(routes.router)
    return app
