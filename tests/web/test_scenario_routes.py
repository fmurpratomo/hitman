from hitman.core.assertions import Assertion
from hitman.core.models import KeyValue, Request
from hitman.core.scenarios import Capture, Scenario, Step


def save_scenario(app, name="Login flow", steps=None):
    return app.state.store.save_scenario(Scenario(name=name, steps=steps or []))


def scenario_form(**overrides):
    """The fields the scenario editor always submits, one step, one check."""
    form = {
        "scenario_name": "Login flow",
        "scenario_description": "",
        "on_failure": "stop",
        "step_uid": ["s0"],
        "step_enabled": ["1"],
        "step_name": ["Log in"],
        "step_request_id": ["1"],
        "assert_step": ["s0"],
        "assert_enabled": ["1"],
        "assert_kind": ["status"],
        "assert_target": [""],
        "assert_op": ["eq"],
        "assert_value": ["200"],
    }
    form.update(overrides)
    return form


# --- the editor ---------------------------------------------------------


def test_the_sidebar_offers_a_tests_tab(client):
    page = client.get("/")
    assert 'data-tab="scenarios"' in page.text
    assert "New scenario" in page.text


def test_a_new_scenario_form_renders_empty(client):
    reply = client.get("/scenarios/new")
    assert reply.status_code == 200
    assert 'id="scenario-form"' in reply.text
    assert "No steps yet" in reply.text


def test_the_editor_ships_a_template_for_adding_a_step(client):
    """app.js clones it and swaps __UID__ for a fresh uid, nested rows included."""
    reply = client.get("/scenarios/new")
    assert 'id="step-template"' in reply.text
    assert "__UID__" in reply.text


def test_the_step_picker_lists_saved_requests_by_folder(client, app):
    app.state.store.save_request("Log in", Request(url="http://x.test/login"), folder="Auth")
    reply = client.get("/scenarios/new")
    assert '<optgroup label="Auth">' in reply.text
    assert "Log in" in reply.text


def test_loading_a_scenario_fills_the_editor(client, app):
    request_id = app.state.store.save_request("Log in", Request(url="http://x.test/login"))
    scenario_id = save_scenario(
        app,
        steps=[
            Step(
                name="Log in",
                request_id=request_id,
                assertions=[Assertion(kind="status", op="eq", value="201")],
                captures=[Capture(name="token", source="json", path="token")],
            )
        ],
    )
    reply = client.get(f"/scenarios/{scenario_id}")
    assert 'name="assert_value" value="201"' in reply.text
    assert 'name="capture_name" value="token"' in reply.text
    assert f'value="{request_id}" selected' in reply.text
    assert f'data-url="/scenarios/{scenario_id}"' in reply.text  # Update, not Save as new


def test_loading_an_unknown_scenario_is_a_404(client):
    assert client.get("/scenarios/9999").status_code == 404


def test_a_blank_builder_is_reachable_again(client):
    """The editor takes over the builder pane, so there has to be a way back."""
    reply = client.get("/requests/new")
    assert reply.status_code == 200
    assert 'id="request-form"' in reply.text


# --- saving -------------------------------------------------------------


def test_saving_a_scenario_returns_an_editor_bound_to_it(client, app):
    reply = client.post("/scenarios", data=scenario_form())
    assert reply.status_code == 200
    saved = app.state.store.list_scenarios()[0]
    assert saved.scenario.name == "Login flow"
    assert len(saved.scenario.steps) == 1
    assert saved.scenario.steps[0].assertions[0].value == "200"
    # Bound to the new id, so the next click updates rather than making a copy.
    assert f'name="scenario_id" value="{saved.id}"' in reply.text
    assert 'data-oob="#sidebar"' in reply.text


def test_a_scenario_without_a_name_still_saves(client, app):
    client.post("/scenarios", data=scenario_form(scenario_name=""))
    assert app.state.store.list_scenarios()[0].name == "Untitled scenario"


def test_steps_keep_the_order_they_were_submitted_in(client, app):
    client.post(
        "/scenarios",
        data=scenario_form(
            step_uid=["s0", "s1", "s2"],
            step_enabled=["1", "1", "1"],
            step_name=["third", "first", "second"],
            step_request_id=["3", "1", "2"],
        ),
    )
    steps = app.state.store.list_scenarios()[0].scenario.steps
    assert [step.name for step in steps] == ["third", "first", "second"]
    assert [step.request_id for step in steps] == [3, 1, 2]


def test_checks_follow_their_own_step_not_their_position(client, app):
    """Rows are matched to steps by uid, which is what survives a reorder."""
    client.post(
        "/scenarios",
        data=scenario_form(
            step_uid=["s0", "s1"],
            step_enabled=["1", "1"],
            step_name=["first", "second"],
            step_request_id=["1", "2"],
            assert_step=["s1", "s0"],
            assert_enabled=["1", "1"],
            assert_kind=["status", "status"],
            assert_target=["", ""],
            assert_op=["eq", "eq"],
            assert_value=["404", "200"],
        ),
    )
    steps = app.state.store.list_scenarios()[0].scenario.steps
    assert steps[0].assertions[0].value == "200"
    assert steps[1].assertions[0].value == "404"


def test_an_abandoned_blank_check_row_is_not_stored(client, app):
    client.post(
        "/scenarios",
        data=scenario_form(
            assert_step=["s0", "s0"],
            assert_enabled=["1", "1"],
            assert_kind=["status", "status"],
            assert_target=["", ""],
            assert_op=["eq", "eq"],
            assert_value=["200", ""],
        ),
    )
    assert len(app.state.store.list_scenarios()[0].scenario.steps[0].assertions) == 1


def test_a_capture_without_a_variable_name_is_dropped(client, app):
    client.post(
        "/scenarios",
        data=scenario_form(
            capture_step=["s0"],
            capture_enabled=["1"],
            capture_name=[""],
            capture_source=["json"],
            capture_path=["token"],
        ),
    )
    assert app.state.store.list_scenarios()[0].scenario.steps[0].captures == []


def test_a_crafted_check_kind_falls_back_instead_of_being_stored(client, app):
    client.post("/scenarios", data=scenario_form(assert_kind=["evil"], assert_op=["evil"]))
    stored = app.state.store.list_scenarios()[0].scenario.steps[0].assertions[0]
    assert stored.kind == "status"
    assert stored.op == "eq"


def test_update_replaces_the_stored_scenario(client, app):
    scenario_id = save_scenario(app)
    client.put(f"/scenarios/{scenario_id}", data=scenario_form(scenario_name="Renamed"))
    assert app.state.store.get_scenario(scenario_id).name == "Renamed"


def test_updating_an_unknown_scenario_is_a_404(client):
    assert client.put("/scenarios/9999", data=scenario_form()).status_code == 404


def test_duplicate_and_delete(client, app):
    scenario_id = save_scenario(app)
    client.post(f"/scenarios/{scenario_id}/duplicate")
    assert len(app.state.store.list_scenarios()) == 2
    reply = client.delete(f"/scenarios/{scenario_id}")
    assert reply.status_code == 200
    assert app.state.store.get_scenario(scenario_id) is None


def test_duplicating_an_unknown_scenario_is_a_404(client):
    assert client.post("/scenarios/9999/duplicate").status_code == 404


# --- running ------------------------------------------------------------


def test_running_reports_each_check(client, app, fixture_server):
    request_id = app.state.store.save_request("JSON", Request(url=f"{fixture_server}/json"))
    reply = client.post(
        "/scenarios/run",
        data=scenario_form(
            step_request_id=[str(request_id)],
            assert_step=["s0", "s0"],
            assert_enabled=["1", "1"],
            assert_kind=["status", "json"],
            assert_target=["", "hello"],
            assert_op=["eq", "eq"],
            assert_value=["200", "world"],
        ),
    )
    assert reply.status_code == 200
    assert "PASSED" in reply.text
    assert "json hello eq world" in reply.text
    assert 'data-oob="#sidebar"' in reply.text


def test_a_failing_check_names_what_it_got(client, app, fixture_server):
    request_id = app.state.store.save_request("JSON", Request(url=f"{fixture_server}/json"))
    reply = client.post(
        "/scenarios/run",
        data=scenario_form(step_request_id=[str(request_id)], assert_value=["404"]),
    )
    assert "FAILED" in reply.text
    assert "status eq 404" in reply.text
    assert app.state.store.list_scenario_runs()[0].passed is False


def test_a_captured_value_reaches_the_next_step(client, app, fixture_server):
    """The reason scenarios run in sequence: step 2 uses what step 1 returned."""
    first = app.state.store.save_request("Get it", Request(url=f"{fixture_server}/json"))
    second = app.state.store.save_request(
        "Use it",
        Request(url=f"{fixture_server}/echo", headers=[KeyValue("X-Chained", "{{hello}}")]),
    )
    reply = client.post(
        "/scenarios/run",
        data=scenario_form(
            step_uid=["s0", "s1"],
            step_enabled=["1", "1"],
            step_name=["Get it", "Use it"],
            step_request_id=[str(first), str(second)],
            capture_step=["s0"],
            capture_enabled=["1"],
            capture_name=["hello"],
            capture_source=["json"],
            capture_path=["hello"],
            assert_step=["s1"],
            assert_enabled=["1"],
            assert_kind=["json"],
            assert_target=["headers.x-chained"],
            assert_op=["eq"],
            assert_value=["world"],
        ),
    )
    assert "PASSED" in reply.text
    run = app.state.store.list_scenario_runs()[0]
    assert run.result.steps[0].captured == [("hello", "world")]


def test_a_run_stops_at_the_first_failure_by_default(client, app, fixture_server):
    first = app.state.store.save_request("Boom", Request(url=f"{fixture_server}/status/500"))
    second = app.state.store.save_request("Never", Request(url=f"{fixture_server}/json"))
    client.post(
        "/scenarios/run",
        data=scenario_form(
            step_uid=["s0", "s1"],
            step_enabled=["1", "1"],
            step_name=["Boom", "Never"],
            step_request_id=[str(first), str(second)],
        ),
    )
    outcomes = [step.outcome for step in app.state.store.list_scenario_runs()[0].result.steps]
    assert outcomes == ["failed", "skipped"]


def test_running_from_the_sidebar_uses_the_stored_scenario(client, app, fixture_server):
    request_id = app.state.store.save_request("JSON", Request(url=f"{fixture_server}/json"))
    scenario_id = save_scenario(
        app,
        steps=[
            Step(
                name="Check",
                request_id=request_id,
                assertions=[Assertion(kind="status", op="eq", value="200")],
            )
        ],
    )
    reply = client.post(f"/scenarios/{scenario_id}/run")
    assert reply.status_code == 200
    assert "PASSED" in reply.text
    assert app.state.store.list_scenario_runs()[0].scenario_id == scenario_id


def test_running_an_unknown_scenario_is_a_404(client):
    assert client.post("/scenarios/9999/run").status_code == 404


def test_a_connection_failure_is_reported_not_raised(client, app, closed_port):
    request_id = app.state.store.save_request(
        "Dead", Request(url=f"http://127.0.0.1:{closed_port}/")
    )
    reply = client.post("/scenarios/run", data=scenario_form(step_request_id=[str(request_id)]))
    assert reply.status_code == 200
    assert "Connection refused" in reply.text


def test_the_active_environment_supplies_variables(client, app, fixture_server):
    env_id = app.state.store.save_environment("Local", [KeyValue("base", fixture_server)])
    app.state.store.set_active_environment(env_id)
    request_id = app.state.store.save_request("Templated", Request(url="{{base}}/json"))
    reply = client.post("/scenarios/run", data=scenario_form(step_request_id=[str(request_id)]))
    assert "PASSED" in reply.text
    assert "env Local" in reply.text


def test_a_run_does_not_flood_the_send_history(client, app, fixture_server):
    """The report already holds every request and response; history stays yours."""
    request_id = app.state.store.save_request("JSON", Request(url=f"{fixture_server}/json"))
    client.post("/scenarios/run", data=scenario_form(step_request_id=[str(request_id)]))
    assert app.state.store.list_history() == []


def test_a_step_response_body_is_escaped_not_executed(client, app, fixture_server):
    """Same rule as the response pane: a body is attacker-controlled content."""
    request_id = app.state.store.save_request("HTML", Request(url=f"{fixture_server}/html"))
    reply = client.post(
        "/scenarios/run",
        data=scenario_form(step_request_id=[str(request_id)], assert_value=["200"]),
    )
    assert "<script>alert(1)</script>" not in reply.text
    assert "&lt;script&gt;" in reply.text


def test_a_captured_value_is_escaped_in_the_report(client, app, fixture_server):
    """A capture puts response text straight into the report."""
    request_id = app.state.store.save_request("Base64", Request(url=f"{fixture_server}/base64"))
    reply = client.post(
        "/scenarios/run",
        data=scenario_form(
            step_request_id=[str(request_id)],
            capture_step=["s0"],
            capture_enabled=["1"],
            capture_name=["note"],
            capture_source=["json"],
            capture_path=["note"],
        ),
    )
    assert "<script>alert(1)</script>" not in reply.text


# --- past runs ----------------------------------------------------------


def test_recent_runs_appear_in_the_sidebar(client, app, fixture_server):
    request_id = app.state.store.save_request("JSON", Request(url=f"{fixture_server}/json"))
    client.post("/scenarios/run", data=scenario_form(step_request_id=[str(request_id)]))
    sidebar = client.get("/scenarios").text
    assert "Recent runs" in sidebar
    assert "PASS" in sidebar


def test_a_past_run_can_be_reopened(client, app, fixture_server):
    request_id = app.state.store.save_request("JSON", Request(url=f"{fixture_server}/json"))
    client.post("/scenarios/run", data=scenario_form(step_request_id=[str(request_id)]))
    run_id = app.state.store.list_scenario_runs()[0].id
    reply = client.get(f"/scenarios/runs/{run_id}")
    assert reply.status_code == 200
    assert "PASSED" in reply.text


def test_reopening_an_unknown_run_is_a_404(client):
    assert client.get("/scenarios/runs/9999").status_code == 404


def test_runs_can_be_cleared(client, app, fixture_server):
    request_id = app.state.store.save_request("JSON", Request(url=f"{fixture_server}/json"))
    client.post("/scenarios/run", data=scenario_form(step_request_id=[str(request_id)]))
    client.delete("/scenarios/runs")
    assert app.state.store.list_scenario_runs() == []


def test_a_scenario_runs_the_checkpoint_not_your_unsaved_edits(client, app, fixture_server):
    """A test suite has to run the committed request, or it tests your scratchpad.

    Steps name saved requests, and a saved request has two states while you are
    editing it. The scenario takes the checkpoint, so a run means the same thing
    whether or not a builder happens to be open on one of its steps.
    """
    request_id = app.state.store.save_request("JSON", Request(url=f"{fixture_server}/json"))
    app.state.store.save_draft(request_id, Request(url=f"{fixture_server}/status/500"))

    client.post("/scenarios/run", data=scenario_form(step_request_id=[str(request_id)]))
    step = app.state.store.list_scenario_runs()[0].result.steps[0]
    assert step.request.url == f"{fixture_server}/json"
    assert step.outcome == "passed"


# --- folders ------------------------------------------------------------


def test_the_editor_offers_a_folder_box_that_autocompletes(client, app):
    app.state.store.save_scenario(Scenario(name="Existing", folder="Checkout"))
    reply = client.get("/scenarios/new")
    assert 'name="scenario_folder"' in reply.text
    assert '<datalist id="scenario-folder-list">' in reply.text
    assert '<option value="Checkout">' in reply.text


def test_saving_into_a_folder(client, app):
    client.post("/scenarios", data=scenario_form(scenario_folder="Checkout"))
    assert app.state.store.list_scenarios()[0].folder == "Checkout"


def test_the_folder_comes_back_in_the_editor(client, app):
    scenario_id = app.state.store.save_scenario(Scenario(name="Login", folder="Auth"))
    reply = client.get(f"/scenarios/{scenario_id}")
    assert 'name="scenario_folder" value="Auth"' in reply.text


def test_update_moves_a_scenario_between_folders(client, app):
    scenario_id = app.state.store.save_scenario(Scenario(name="Login", folder="Auth"))
    client.put(f"/scenarios/{scenario_id}", data=scenario_form(scenario_folder="Checkout"))
    assert app.state.store.get_scenario(scenario_id).folder == "Checkout"


def test_the_sidebar_groups_scenarios_under_their_folder(client, app):
    app.state.store.save_scenario(Scenario(name="Login", folder="Auth"))
    app.state.store.save_scenario(Scenario(name="Ad hoc"))
    sidebar = client.get("/scenarios").text
    assert '<details class="folder" open>' in sidebar
    assert "<summary>Auth" in sidebar
    assert "Login" in sidebar and "Ad hoc" in sidebar


def test_an_unfiled_scenario_is_not_wrapped_in_a_folder(client, app):
    app.state.store.save_scenario(Scenario(name="Ad hoc"))
    assert "<summary>" not in client.get("/scenarios").text


def test_running_and_deleting_still_work_from_inside_a_folder(client, app, fixture_server):
    request_id = app.state.store.save_request("JSON", Request(url=f"{fixture_server}/json"))
    scenario_id = app.state.store.save_scenario(
        Scenario(
            name="Smoke",
            folder="Checkout",
            steps=[Step(request_id=request_id,
                        assertions=[Assertion(kind="status", op="eq", value="200")])],
        )
    )
    assert "PASSED" in client.post(f"/scenarios/{scenario_id}/run").text
    client.delete(f"/scenarios/{scenario_id}")
    assert app.state.store.get_scenario(scenario_id) is None


def test_a_folder_with_only_deleted_scenarios_disappears(client, app):
    """Folders exist only as a label on their scenarios, as with saved requests."""
    scenario_id = app.state.store.save_scenario(Scenario(name="Login", folder="Auth"))
    client.delete(f"/scenarios/{scenario_id}")
    assert app.state.store.list_scenario_folders() == []
    assert "<summary>Auth" not in client.get("/scenarios").text

