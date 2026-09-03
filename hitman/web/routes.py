"""Fragment endpoints. Every response is an HTML fragment except /export-curl."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi import Request as HttpRequest
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response as BareResponse

from hitman.core.assertions import KINDS, OPS, blank_assertion
from hitman.core.curl_export import to_command
from hitman.core.curl_import import CurlParseError, parse_curl
from hitman.core.engines.curl_engine import CurlEngine
from hitman.core.engines.httpx_engine import HttpxEngine
from hitman.core.models import KeyValue, Request, Response
from hitman.core.scenarios import (
    CAPTURE_SOURCES,
    Capture,
    Scenario,
    Step,
    run_scenario,
)
from hitman.core.variables import substitute
from hitman.web.bodyview import pretty_lines
from hitman.web.forms import request_from_form, scenario_from_form

log = logging.getLogger(__name__)
router = APIRouter()


def render(http_request: HttpRequest, template: str, context: dict) -> HTMLResponse:
    store = http_request.app.state.store
    full = {
        "curl_available": http_request.app.state.curl_available,
        "saved_groups": store.grouped_requests(),
        "folders": store.list_folders(),
        "history": store.list_history(50),
        "environments": store.list_environments(),
        "active_env": store.active_environment(),
        "scenario_groups": store.grouped_scenarios(),
        "scenario_folders": store.list_scenario_folders(),
        "runs": store.list_scenario_runs(20),
        # Vocabulary and blank rows the scenario editor's macros need. They are
        # constants, but the editor is assembled from macros that cannot import
        # from core, so they arrive as context like everything else.
        "assertion_kinds": KINDS,
        "assertion_ops": OPS,
        "capture_sources": CAPTURE_SOURCES,
        "blank_assertion": blank_assertion(),
        "blank_capture": Capture(),
        "blank_step": Step(assertions=[blank_assertion()]),
        **context,
    }
    # Request-first signature: passing the context dict alone is deprecated.
    return http_request.app.state.templates.TemplateResponse(http_request, template, full)


def _engine(name: str):
    return CurlEngine() if name == "curl" else HttpxEngine()


def resolve(store, request: Request) -> tuple[Request, list[str]]:
    """Apply the active environment, if any, to a request about to be sent."""
    environment = store.active_environment()
    if environment is None:
        return request, []
    outcome = substitute(request, environment.as_mapping())
    return outcome.request, outcome.unresolved


def _variables_from_form(form) -> list[KeyValue]:
    keys = form.getlist("var_key")
    values = form.getlist("var_value")
    flags = form.getlist("var_enabled")
    rows = []
    for index, key in enumerate(keys):
        value = values[index] if index < len(values) else ""
        flag = flags[index] if index < len(flags) else "1"
        if not key.strip() and not str(value).strip():
            continue
        rows.append(KeyValue(key.strip(), str(value), flag == "1"))
    return rows


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

    store = http_request.app.state.store
    notes = []

    # Resolve {{variables}} before the engine runs, so both engines and the
    # history record all see the same fully-resolved request.
    resolved, unresolved = resolve(store, outgoing)
    if unresolved:
        notes.append(
            "No value for " + ", ".join(f"{{{{{name}}}}}" for name in unresolved)
            + " — sent as written. Set it in the environment editor."
        )

    if not resolved.url:
        response = Response(engine=engine.name, error="Enter a URL first.")
    else:
        # engine.send blocks on the network; keep the event loop free.
        response = await run_in_threadpool(engine.send, resolved)
        try:
            # History records what actually went out, placeholders resolved.
            store.add_history(resolved, response)
        except Exception as exc:  # noqa: BLE001 - history is strictly secondary
            # The request already went out and came back. Losing the history
            # row must not cost the user the response they were waiting for,
            # so this degrades to a visible note instead of a 500.
            log.warning("Could not write history: %s", exc)
            notes.append(f"Response not saved to history: {exc}")

    return render(
        http_request,
        "fragments/response_with_sidebar.html",
        {
            # The builder keeps the unresolved form the user typed.
            "req": outgoing,
            "response": response,
            "pretty": pretty_lines(response.body),
            "notes": notes,
        },
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
    # Resolved, not templated: a curl command containing {{base_url}} is not a
    # command anyone can paste into a terminal and run.
    resolved, _ = resolve(http_request.app.state.store, request_from_form(form))
    return PlainTextResponse(to_command(resolved))


@router.post("/requests", response_class=HTMLResponse)
async def save_request(http_request: HttpRequest):
    form = await http_request.form()
    outgoing = request_from_form(form)
    name = str(form.get("save_name") or "").strip() or outgoing.url or "Untitled"
    folder = str(form.get("save_folder") or "")
    http_request.app.state.store.save_request(name, outgoing, folder)
    return render(http_request, "fragments/sidebar.html", {"req": outgoing})


@router.get("/requests/new", response_class=HTMLResponse)
def new_request(http_request: HttpRequest):
    """An empty builder — the way back after the scenario editor took the pane."""
    return render(http_request, "fragments/builder.html", {"req": Request(), "warnings": []})


@router.get("/requests/{request_id}", response_class=HTMLResponse)
def load_request(request_id: int, http_request: HttpRequest):
    saved = http_request.app.state.store.get_request(request_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved request not found")
    # editing, not request: loading gives back what you were doing.
    return render(
        http_request,
        "fragments/builder_with_sidebar.html",
        {"req": saved.editing, "warnings": [], "current": saved},
    )


@router.put("/requests/{request_id}/draft", response_class=BareResponse)
async def save_draft(request_id: int, http_request: HttpRequest):
    """Autosave point. Called while you type, so it answers with nothing.

    A draft is a convenience, never the record: a request that never arrives
    costs the last few seconds of typing and must not interrupt anything, so
    there is no fragment to swap and no error banner to render.
    """
    store = http_request.app.state.store
    if store.get_request(request_id) is None:
        raise HTTPException(status_code=404, detail="Saved request not found")
    form = await http_request.form()
    kept = store.save_draft(request_id, request_from_form(form))
    # 204 with the answer in a header: the caller only needs to know whether
    # the edit left anything unsaved, so it can show or hide the marker.
    return BareResponse(status_code=204, headers={"X-Draft": "1" if kept else "0"})


@router.post("/requests/{request_id}/rollback", response_class=HTMLResponse)
def rollback_request(request_id: int, http_request: HttpRequest):
    """Throw the draft away and put the checkpoint back in the builder."""
    store = http_request.app.state.store
    if store.get_request(request_id) is None:
        raise HTTPException(status_code=404, detail="Saved request not found")
    store.clear_draft(request_id)
    saved = store.get_request(request_id)
    return render(
        http_request,
        "fragments/builder_with_sidebar.html",
        {"req": saved.request, "warnings": [], "current": saved},
    )


@router.put("/requests/{request_id}", response_class=HTMLResponse)
async def update_request(request_id: int, http_request: HttpRequest):
    """Move the checkpoint to what is on screen. This is the deliberate save."""
    store = http_request.app.state.store
    if store.get_request(request_id) is None:
        raise HTTPException(status_code=404, detail="Saved request not found")
    form = await http_request.form()
    outgoing = request_from_form(form)
    name = str(form.get("save_name") or "").strip() or outgoing.url or "Untitled"
    folder = str(form.get("save_folder") or "")
    store.update_request(request_id, name, outgoing, folder)
    # The builder comes back too, so the unsaved marker clears itself.
    saved = store.get_request(request_id)
    return render(
        http_request,
        "fragments/builder_with_sidebar.html",
        {"req": saved.request, "warnings": [], "current": saved},
    )


@router.post("/requests/{request_id}/duplicate", response_class=HTMLResponse)
def duplicate_request(request_id: int, http_request: HttpRequest):
    if http_request.app.state.store.duplicate_request(request_id) is None:
        raise HTTPException(status_code=404, detail="Saved request not found")
    return render(http_request, "fragments/sidebar.html", {"req": Request()})


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
            "pretty": pretty_lines(entry.response.body),
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


# --- environments -------------------------------------------------------


@router.get("/environments", response_class=HTMLResponse)
def environment_bar(http_request: HttpRequest):
    return render(http_request, "fragments/envbar.html", {})


@router.get("/environments/new", response_class=HTMLResponse)
def new_environment_form(http_request: HttpRequest):
    return render(http_request, "fragments/env_editor.html", {"env": None})


@router.get("/environments/{env_id}/edit", response_class=HTMLResponse)
def edit_environment_form(env_id: int, http_request: HttpRequest):
    environment = http_request.app.state.store.get_environment(env_id)
    if environment is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    return render(http_request, "fragments/env_editor.html", {"env": environment})


@router.post("/environments", response_class=HTMLResponse)
async def create_environment(http_request: HttpRequest):
    form = await http_request.form()
    store = http_request.app.state.store
    name = str(form.get("env_name") or "").strip() or "Untitled"
    env_id = store.save_environment(name, _variables_from_form(form))
    # A newly created environment is almost always the one you want active.
    store.set_active_environment(env_id)
    return render(http_request, "fragments/envbar.html", {})


@router.put("/environments/{env_id}", response_class=HTMLResponse)
async def update_environment(env_id: int, http_request: HttpRequest):
    store = http_request.app.state.store
    if store.get_environment(env_id) is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    form = await http_request.form()
    name = str(form.get("env_name") or "").strip() or "Untitled"
    store.update_environment(env_id, name, _variables_from_form(form))
    return render(http_request, "fragments/envbar.html", {})


@router.delete("/environments/{env_id}", response_class=HTMLResponse)
def delete_environment(env_id: int, http_request: HttpRequest):
    http_request.app.state.store.delete_environment(env_id)
    return render(http_request, "fragments/envbar.html", {})


@router.post("/environments/active", response_class=HTMLResponse)
async def set_active_environment(http_request: HttpRequest):
    form = await http_request.form()
    raw = str(form.get("env_id") or "").strip()
    http_request.app.state.store.set_active_environment(int(raw) if raw.isdigit() else None)
    return render(http_request, "fragments/envbar.html", {})


# --- scenarios ----------------------------------------------------------
#
# Route order matters twice here: "/scenarios/new" and "/scenarios/runs" would
# both be swallowed by "/scenarios/{scenario_id}", which is typed int and so
# would 422 rather than fall through to the right handler.


def _run(http_request: HttpRequest, scenario: Scenario, scenario_id, engine_name: str):
    """Run a scenario and record it. Blocking — call through a threadpool."""
    store = http_request.app.state.store
    engine = _engine(engine_name)
    environment = store.active_environment()

    result = run_scenario(
        scenario,
        # get_request returns a SavedRequest, which carries both the name the
        # report labels the step with and the request itself.
        lookup=store.get_request,
        send=engine.send,
        variables=environment.as_mapping() if environment else {},
        engine_name=engine.name,
        environment=environment.name if environment else "",
    )

    notes = []
    run_id = None
    try:
        run_id = store.add_scenario_run(scenario_id, result)
    except Exception as exc:  # noqa: BLE001 - the run itself is what matters
        # The requests have already gone out. Losing the stored record must not
        # cost the user the report they waited for.
        log.warning("Could not write scenario run: %s", exc)
        notes.append(f"Run not saved: {exc}")
    return result, run_id, notes


def _report(http_request: HttpRequest, result, run_id, notes) -> HTMLResponse:
    return render(
        http_request,
        "fragments/run_with_sidebar.html",
        {"result": result, "run_id": run_id, "notes": notes},
    )


@router.get("/scenarios", response_class=HTMLResponse)
def scenario_list(http_request: HttpRequest):
    return render(http_request, "fragments/sidebar.html", {"req": Request()})


@router.get("/scenarios/new", response_class=HTMLResponse)
def new_scenario_form(http_request: HttpRequest):
    return render(
        http_request,
        "fragments/scenario_editor.html",
        {"scenario": Scenario(), "current": None},
    )


@router.get("/scenarios/runs/{run_id}", response_class=HTMLResponse)
def load_run(run_id: int, http_request: HttpRequest):
    run = http_request.app.state.store.get_scenario_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return render(
        http_request,
        "fragments/run_report.html",
        {"result": run.result, "run_id": run.id, "notes": []},
    )


@router.delete("/scenarios/runs", response_class=HTMLResponse)
def clear_runs(http_request: HttpRequest):
    http_request.app.state.store.clear_scenario_runs()
    return render(http_request, "fragments/sidebar.html", {"req": Request()})


@router.get("/scenarios/{scenario_id}", response_class=HTMLResponse)
def load_scenario(scenario_id: int, http_request: HttpRequest):
    saved = http_request.app.state.store.get_scenario(scenario_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return render(
        http_request,
        "fragments/scenario_editor.html",
        {"scenario": saved.scenario, "current": saved},
    )


@router.post("/scenarios", response_class=HTMLResponse)
async def create_scenario(http_request: HttpRequest):
    store = http_request.app.state.store
    scenario = scenario_from_form(await http_request.form())
    scenario_id = store.save_scenario(scenario)
    # The editor comes back bound to the new id, so the next click is Update
    # rather than a second copy.
    return render(
        http_request,
        "fragments/scenario_with_sidebar.html",
        {"scenario": scenario, "current": store.get_scenario(scenario_id)},
    )


@router.put("/scenarios/{scenario_id}", response_class=HTMLResponse)
async def update_scenario(scenario_id: int, http_request: HttpRequest):
    store = http_request.app.state.store
    if store.get_scenario(scenario_id) is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    scenario = scenario_from_form(await http_request.form())
    store.update_scenario(scenario_id, scenario)
    return render(
        http_request,
        "fragments/scenario_with_sidebar.html",
        {"scenario": scenario, "current": store.get_scenario(scenario_id)},
    )


@router.post("/scenarios/{scenario_id}/duplicate", response_class=HTMLResponse)
def duplicate_scenario(scenario_id: int, http_request: HttpRequest):
    if http_request.app.state.store.duplicate_scenario(scenario_id) is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return render(http_request, "fragments/sidebar.html", {"req": Request()})


@router.delete("/scenarios/{scenario_id}", response_class=HTMLResponse)
def delete_scenario(scenario_id: int, http_request: HttpRequest):
    http_request.app.state.store.delete_scenario(scenario_id)
    return render(http_request, "fragments/sidebar.html", {"req": Request()})


@router.post("/scenarios/run", response_class=HTMLResponse)
async def run_from_form(http_request: HttpRequest):
    """Run exactly what is on screen, saved or not, so editing can iterate."""
    form = await http_request.form()
    scenario = scenario_from_form(form)
    raw_id = str(form.get("scenario_id") or "").strip()
    scenario_id = int(raw_id) if raw_id.isdigit() else None
    engine_name = str(form.get("engine") or "httpx")

    result, run_id, notes = await run_in_threadpool(
        _run, http_request, scenario, scenario_id, engine_name
    )
    return _report(http_request, result, run_id, notes)


@router.post("/scenarios/{scenario_id}/run", response_class=HTMLResponse)
async def run_saved_scenario(scenario_id: int, http_request: HttpRequest):
    """Run the stored scenario, for the play button in the sidebar."""
    form = await http_request.form()
    saved = http_request.app.state.store.get_scenario(scenario_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    result, run_id, notes = await run_in_threadpool(
        _run,
        http_request,
        saved.scenario,
        scenario_id,
        str(form.get("engine") or "httpx"),
    )
    return _report(http_request, result, run_id, notes)
