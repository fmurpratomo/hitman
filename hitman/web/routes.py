"""Fragment endpoints. Every response is an HTML fragment except /export-curl."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi import Request as HttpRequest
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from hitman.core.curl_export import to_command
from hitman.core.curl_import import CurlParseError, parse_curl
from hitman.core.engines.curl_engine import CurlEngine
from hitman.core.engines.httpx_engine import HttpxEngine
from hitman.core.models import Request, Response
from hitman.web.forms import request_from_form

router = APIRouter()


def render(http_request: HttpRequest, template: str, context: dict) -> HTMLResponse:
    store = http_request.app.state.store
    full = {
        "curl_available": http_request.app.state.curl_available,
        "saved": store.list_requests(),
        "history": store.list_history(50),
        **context,
    }
    # Request-first signature: passing the context dict alone is deprecated.
    return http_request.app.state.templates.TemplateResponse(http_request, template, full)


def pretty_body(response: Response) -> str:
    """Indent JSON when it is JSON; otherwise show the body unchanged."""
    body = response.body
    if not body.strip():
        return body
    try:
        return json.dumps(json.loads(body), indent=2, ensure_ascii=False)
    except (ValueError, TypeError):
        return body


def _engine(name: str):
    return CurlEngine() if name == "curl" else HttpxEngine()


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
def index(http_request: HttpRequest):
    return render(http_request, "index.html", {"req": Request(), "warnings": []})


@router.post("/send", response_class=HTMLResponse)
async def send(http_request: HttpRequest):
    form = await http_request.form()
    outgoing = request_from_form(form)
    engine = _engine(str(form.get("engine") or "httpx"))

    if not outgoing.url:
        response = Response(engine=engine.name, error="Enter a URL first.")
    else:
        # engine.send blocks on the network; keep the event loop free.
        response = await run_in_threadpool(engine.send, outgoing)
        http_request.app.state.store.add_history(outgoing, response)

    return render(
        http_request,
        "fragments/response_with_sidebar.html",
        {"req": outgoing, "response": response, "pretty": pretty_body(response)},
    )


@router.post("/import-curl", response_class=HTMLResponse)
async def import_curl(http_request: HttpRequest):
    form = await http_request.form()
    try:
        parsed = parse_curl(str(form.get("text") or ""))
    except CurlParseError as exc:
        # 422 rather than an error-banner fragment: app.js only swaps the
        # builder on a 2xx, so the user's current form survives a bad paste.
        return PlainTextResponse(f"Could not import: {exc}", status_code=422)

    return render(
        http_request,
        "fragments/builder.html",
        {"req": parsed.request, "warnings": parsed.warnings},
    )


@router.post("/export-curl", response_class=PlainTextResponse)
async def export_curl(http_request: HttpRequest):
    form = await http_request.form()
    return PlainTextResponse(to_command(request_from_form(form)))


@router.post("/requests", response_class=HTMLResponse)
async def save_request(http_request: HttpRequest):
    form = await http_request.form()
    outgoing = request_from_form(form)
    name = str(form.get("save_name") or "").strip() or outgoing.url or "Untitled"
    http_request.app.state.store.save_request(name, outgoing)
    return render(http_request, "fragments/sidebar.html", {"req": outgoing})


@router.get("/requests/{request_id}", response_class=HTMLResponse)
def load_request(request_id: int, http_request: HttpRequest):
    saved = http_request.app.state.store.get_request(request_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved request not found")
    return render(http_request, "fragments/builder.html", {"req": saved.request, "warnings": []})


@router.put("/requests/{request_id}", response_class=HTMLResponse)
async def update_request(request_id: int, http_request: HttpRequest):
    store = http_request.app.state.store
    if store.get_request(request_id) is None:
        raise HTTPException(status_code=404, detail="Saved request not found")
    form = await http_request.form()
    outgoing = request_from_form(form)
    name = str(form.get("save_name") or "").strip() or outgoing.url or "Untitled"
    store.update_request(request_id, name, outgoing)
    return render(http_request, "fragments/sidebar.html", {"req": outgoing})


@router.delete("/requests/{request_id}", response_class=HTMLResponse)
def delete_request(request_id: int, http_request: HttpRequest):
    http_request.app.state.store.delete_request(request_id)
    return render(http_request, "fragments/sidebar.html", {"req": Request()})


@router.get("/history/{entry_id}", response_class=HTMLResponse)
def load_history(entry_id: int, http_request: HttpRequest):
    entry = http_request.app.state.store.get_history(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="History entry not found")
    return render(
        http_request,
        "fragments/replay.html",
        {
            "req": entry.request,
            "response": entry.response,
            "pretty": pretty_body(entry.response),
            "warnings": [],
        },
    )


@router.delete("/history", response_class=HTMLResponse)
def clear_history(http_request: HttpRequest):
    http_request.app.state.store.clear_history()
    return render(http_request, "fragments/sidebar.html", {"req": Request()})


@router.get("/requests", response_class=HTMLResponse)
@router.get("/history", response_class=HTMLResponse)
def sidebar(http_request: HttpRequest):
    """Re-render the sidebar on demand (both paths render the same fragment)."""
    return render(http_request, "fragments/sidebar.html", {"req": Request()})
