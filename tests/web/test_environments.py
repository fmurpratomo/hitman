from hitman.core.models import KeyValue, Request


def make_env(app, name="Local", url="http://localhost:3000", active=True):
    env_id = app.state.store.save_environment(
        name, [KeyValue("base_url", url), KeyValue("token", "abc123")]
    )
    if active:
        app.state.store.set_active_environment(env_id)
    return env_id


def test_index_shows_the_environment_picker(client):
    page = client.get("/")
    assert 'id="env-select"' in page.text
    assert "No environment" in page.text


def test_creating_an_environment_makes_it_active(client, app):
    reply = client.post(
        "/environments",
        data={"env_name": "Local", "var_key": ["base_url"],
              "var_value": ["http://localhost:3000"], "var_enabled": ["1"]},
    )
    assert reply.status_code == 200
    assert "Local" in reply.text
    assert app.state.store.active_environment().name == "Local"


def test_switching_the_active_environment(client, app):
    first = make_env(app, "Local")
    second = make_env(app, "Prod", "https://api.live", active=False)
    assert app.state.store.active_environment().id == first
    client.post("/environments/active", data={"env_id": str(second)})
    assert app.state.store.active_environment().id == second


def test_selecting_no_environment_clears_it(client, app):
    make_env(app)
    client.post("/environments/active", data={"env_id": ""})
    assert app.state.store.active_environment() is None


def test_editor_loads_existing_variables(client, app):
    env_id = make_env(app)
    reply = client.get(f"/environments/{env_id}/edit")
    assert reply.status_code == 200
    assert 'value="base_url"' in reply.text
    assert 'value="http://localhost:3000"' in reply.text


def test_editor_for_a_new_environment_is_blank(client):
    reply = client.get("/environments/new")
    assert reply.status_code == 200
    assert "New environment" in reply.text


def test_editing_an_unknown_environment_is_a_404(client):
    assert client.get("/environments/9999/edit").status_code == 404


def test_updating_replaces_name_and_variables(client, app):
    env_id = make_env(app)
    client.put(
        f"/environments/{env_id}",
        data={"env_name": "Renamed", "var_key": ["base_url"],
              "var_value": ["https://x.test"], "var_enabled": ["1"]},
    )
    env = app.state.store.get_environment(env_id)
    assert env.name == "Renamed"
    assert env.as_mapping() == {"base_url": "https://x.test"}


def test_deleting_removes_it_from_the_picker(client, app):
    env_id = make_env(app)
    reply = client.delete(f"/environments/{env_id}")
    assert "Local" not in reply.text
    assert app.state.store.get_environment(env_id) is None


# --- substitution through the real send path ----------------------------


def test_send_resolves_variables_in_the_url(client, app, base_form, fixture_server):
    app.state.store.set_active_environment(
        app.state.store.save_environment("Local", [KeyValue("base_url", fixture_server)])
    )
    reply = client.post("/send", data={**base_form, "url": "{{base_url}}/json"})
    assert "200" in reply.text
    assert "hello" in reply.text


def test_send_resolves_variables_in_headers(client, app, base_form, fixture_server):
    app.state.store.set_active_environment(
        app.state.store.save_environment("Local", [KeyValue("token", "s3cret")])
    )
    reply = client.post(
        "/send",
        data={**base_form, "url": f"{fixture_server}/echo",
              "header_key": ["Authorization"], "header_value": ["Bearer {{token}}"],
              "header_enabled": ["1"]},
    )
    assert "Bearer s3cret" in reply.text


def test_history_records_the_resolved_request(client, app, base_form, fixture_server):
    """History is the record of what actually went out, not what was typed."""
    app.state.store.set_active_environment(
        app.state.store.save_environment("Local", [KeyValue("base_url", fixture_server)])
    )
    client.post("/send", data={**base_form, "url": "{{base_url}}/json"})
    assert app.state.store.list_history()[0].request.url == f"{fixture_server}/json"


def test_an_unset_variable_is_reported_and_sent_as_written(client, app, base_form):
    app.state.store.set_active_environment(app.state.store.save_environment("Local", []))
    reply = client.post("/send", data={**base_form, "url": "{{nope}}/users"})
    assert reply.status_code == 200
    assert "No value for" in reply.text
    assert "nope" in reply.text


def test_no_active_environment_leaves_the_request_untouched(client, base_form, fixture_server):
    reply = client.post("/send", data={**base_form, "url": f"{fixture_server}/json"})
    assert "200" in reply.text


def test_export_curl_resolves_variables(client, app, base_form):
    """A curl command containing {{base_url}} is not runnable."""
    app.state.store.set_active_environment(
        app.state.store.save_environment("Local", [KeyValue("base_url", "http://localhost:3000")])
    )
    reply = client.post("/export-curl", data={**base_form, "url": "{{base_url}}/api"})
    assert "http://localhost:3000/api" in reply.text
    assert "{{" not in reply.text


def test_the_builder_keeps_the_template_after_sending(client, app, base_form, fixture_server):
    """Saved/typed requests stay portable across environments."""
    app.state.store.set_active_environment(
        app.state.store.save_environment("Local", [KeyValue("base_url", fixture_server)])
    )
    client.post("/send", data={**base_form, "url": "{{base_url}}/json"})
    saved_id = app.state.store.save_request("tpl", Request(url="{{base_url}}/json"))
    assert "{{base_url}}" in client.get(f"/requests/{saved_id}").text
