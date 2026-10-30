from hitman.core.loadtest import LoadResult, LoadTest
from hitman.core.models import Request
from hitman.core.scenarios import Scenario, Step


def load_form(target="", **overrides):
    """The fields the performance editor always submits."""
    form = {
        "load_name": "Ping at 2 users",
        "load_description": "",
        "load_folder": "",
        "target": target,
        "users": "2",
        "ramp_up_s": "0",
        "limit": "6",
        "stop_after": "iterations",
    }
    form.update(overrides)
    return form


def only_run(app):
    (run,) = app.state.live_loads.values()
    return run


def live_url(app):
    (token,) = app.state.live_loads
    return f"/performance/live/{token}"


# --- the editor ---------------------------------------------------------


def test_the_sidebar_offers_a_performance_tab(client):
    page = client.get("/")
    assert 'data-tab="performance"' in page.text
    assert "New performance test" in page.text


def test_the_target_picker_offers_requests_and_scenarios(client, app):
    store = app.state.store
    store.save_request("Ping", Request(url="http://x.test/ping"), folder="Health")
    store.save_scenario(Scenario(name="Checkout", steps=[Step(request_id=1)]))
    reply = client.get("/performance/new")
    assert 'value="request:1"' in reply.text
    assert "GET · Health / Ping" in reply.text
    assert 'value="scenario:1"' in reply.text
    assert "Checkout (1 steps)" in reply.text


def test_saving_binds_the_editor_to_the_new_test(client, app):
    reply = client.post("/performance", data=load_form(target="request:1"))
    assert reply.status_code == 200
    (saved,) = app.state.store.list_load_tests()
    assert saved.test.target_key == "request:1"
    assert saved.test.stop_after == "iterations"
    assert f'data-url="/performance/{saved.id}"' in reply.text  # Update, not Save as new
    assert 'data-oob="#sidebar"' in reply.text


def test_loading_a_test_fills_the_editor(client, app):
    app.state.store.save_request("Ping", Request(url="http://x.test/ping"))
    load_test_id = app.state.store.save_load_test(
        LoadTest(name="Soak", target_id=1, users=40, limit=300)
    )
    reply = client.get(f"/performance/{load_test_id}")
    assert 'name="users" value="40"' in reply.text
    assert 'name="limit" value="300"' in reply.text
    assert 'value="request:1" selected' in reply.text


def test_out_of_range_numbers_are_clamped(client, app):
    client.post(
        "/performance",
        data=load_form(target="request:1", users="100000", limit="-5", stop_after="seconds"),
    )
    (saved,) = app.state.store.list_load_tests()
    assert saved.test.users == 200
    assert saved.test.limit == 1


def test_update_and_delete(client, app):
    client.post("/performance", data=load_form(target="request:1"))
    (saved,) = app.state.store.list_load_tests()
    client.put(f"/performance/{saved.id}", data=load_form(load_name="Renamed"))
    assert app.state.store.get_load_test(saved.id).name == "Renamed"
    client.delete(f"/performance/{saved.id}")
    assert app.state.store.list_load_tests() == []


# --- running ------------------------------------------------------------


def test_a_run_needs_a_target(client):
    reply = client.post("/performance/run", data=load_form())
    assert reply.status_code == 422
    assert "Pick a saved request or a scenario" in reply.text


def test_a_run_against_a_deleted_request_says_so(client):
    reply = client.post("/performance/run", data=load_form(target="request:99"))
    assert reply.status_code == 422
    assert "no longer exists" in reply.text


def test_a_run_reports_live_then_final(client, app, fixture_server):
    app.state.store.save_request("Ping", Request(url=f"{fixture_server}/json"))

    started = client.post("/performance/run", data=load_form(target="request:1"))
    assert started.status_code == 200
    assert "Throughput" in started.text
    run = only_run(app)
    assert run.wait(timeout=10)

    final = client.get(live_url(app))
    assert "FINISHED" in final.text
    assert "data-poll" not in final.text  # the last frame ends the polling
    assert 'data-oob="#sidebar"' in final.text

    (stored,) = app.state.store.list_load_runs()
    assert stored.result.requests == 6
    assert stored.result.failed == 0
    assert stored.result.statuses == [("200", 6)]


def test_a_run_in_flight_polls_and_can_be_stopped(client, app, fixture_server):
    app.state.store.save_request("Slow", Request(url=f"{fixture_server}/slow"))

    started = client.post(
        "/performance/run",
        data=load_form(target="request:1", users="1", limit="60", stop_after="seconds"),
    )
    assert f'data-poll="{live_url(app)}"' in started.text
    assert "/stop" in started.text

    stopping = client.post(live_url(app) + "/stop")
    assert "STOPPING" in stopping.text
    assert only_run(app).wait(timeout=10)
    assert "STOPPED" in client.get(live_url(app)).text


def test_only_one_run_at_a_time(client, app, fixture_server):
    app.state.store.save_request("Slow", Request(url=f"{fixture_server}/slow"))
    form = load_form(target="request:1", users="1", limit="60", stop_after="seconds")

    client.post("/performance/run", data=form)
    second = client.post("/performance/run", data=form)
    assert second.status_code == 409
    assert "already running" in second.text

    run = only_run(app)
    run.stop()
    assert run.wait(timeout=10)


def test_the_sidebar_leads_back_to_a_run_in_flight(client, app, fixture_server):
    app.state.store.save_request("Slow", Request(url=f"{fixture_server}/slow"))
    client.post(
        "/performance/run",
        data=load_form(target="request:1", users="1", limit="60", stop_after="seconds"),
    )
    page = client.get("/")
    assert f'data-url="{live_url(app)}"' in page.text
    assert "RUNNING" in page.text

    run = only_run(app)
    run.stop()
    assert run.wait(timeout=10)


def test_a_saved_test_runs_from_the_sidebar(client, app, fixture_server):
    store = app.state.store
    store.save_request("Ping", Request(url=f"{fixture_server}/json"))
    load_test_id = store.save_load_test(
        LoadTest(name="Ping", target_id=1, users=2, stop_after="iterations", limit=4)
    )
    reply = client.post(f"/performance/{load_test_id}/run")
    assert reply.status_code == 200
    assert only_run(app).wait(timeout=10)
    (stored,) = store.list_load_runs()
    assert stored.load_test_id == load_test_id

    shown = client.get(f"/performance/runs/{stored.id}")
    assert "Latency" in shown.text
    assert "data-poll" not in shown.text


def test_a_scenario_run_reports_each_step(client, app, fixture_server):
    store = app.state.store
    store.save_request("Ping", Request(url=f"{fixture_server}/json"))
    store.save_request("Missing", Request(url=f"{fixture_server}/status/404"))
    store.save_scenario(
        Scenario(name="Flow", on_failure="continue", steps=[Step(request_id=1), Step(request_id=2)])
    )

    client.post("/performance/run", data=load_form(target="scenario:1", limit="3"))
    assert only_run(app).wait(timeout=10)
    final = client.get(live_url(app))
    assert "By request" in final.text
    assert "Scenario passes" in final.text

    (stored,) = store.list_load_runs()
    assert stored.result.requests == 6
    assert stored.result.failed == 3  # every 404
    assert [row.label for row in stored.result.labels] == ["Ping", "Missing"]


def test_a_response_body_never_reaches_the_report(client, app, fixture_server):
    """Errors are grouped by message, and the report never renders a body."""
    app.state.store.save_request("Html", Request(url=f"{fixture_server}/html"))
    client.post("/performance/run", data=load_form(target="request:1", limit="2"))
    assert only_run(app).wait(timeout=10)
    assert "<script>alert(1)</script>" not in client.get(live_url(app)).text


def test_clearing_runs(client, app):
    app.state.store.add_load_run(None, LoadResult(name="old"))
    client.delete("/performance/runs")
    assert app.state.store.list_load_runs() == []
