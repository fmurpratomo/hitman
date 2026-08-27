from hitman.core.models import Request, Response


def make_saved(app, name="Get users"):
    return app.state.store.save_request(name, Request(url="http://localhost:3000/users"))


def test_save_returns_the_sidebar_with_the_new_entry(client, base_form):
    reply = client.post(
        "/requests",
        data={**base_form, "url": "http://localhost:3000/users", "save_name": "Get users"},
    )
    assert reply.status_code == 200
    assert "Get users" in reply.text


def test_save_without_a_name_uses_the_url(client, base_form):
    reply = client.post(
        "/requests", data={**base_form, "url": "http://localhost:3000/users", "save_name": ""}
    )
    assert "http://localhost:3000/users" in reply.text


def test_saved_request_loads_into_the_builder(client, app):
    saved_id = make_saved(app)
    reply = client.get(f"/requests/{saved_id}")
    assert reply.status_code == 200
    assert 'value="http://localhost:3000/users"' in reply.text
    assert 'id="request-form"' in reply.text


def test_loading_an_unknown_saved_request_is_a_404(client):
    assert client.get("/requests/9999").status_code == 404


def test_update_replaces_the_saved_request(client, app, base_form):
    saved_id = make_saved(app)
    client.put(
        f"/requests/{saved_id}",
        data={**base_form, "method": "POST", "url": "http://x.test/", "save_name": "Renamed"},
    )
    loaded = app.state.store.get_request(saved_id)
    assert loaded.name == "Renamed"
    assert loaded.request.method == "POST"


def test_delete_removes_it_from_the_sidebar(client, app):
    saved_id = make_saved(app)
    reply = client.delete(f"/requests/{saved_id}")
    assert "Get users" not in reply.text
    assert app.state.store.get_request(saved_id) is None


def test_history_entry_reloads_request_and_response(client, app):
    entry_id = app.state.store.add_history(
        Request(method="POST", url="http://localhost:3000/users"),
        Response(engine="httpx", status=201, reason="Created", body='{"id": 1}'),
    )
    reply = client.get(f"/history/{entry_id}")
    assert reply.status_code == 200
    assert 'value="http://localhost:3000/users"' in reply.text
    assert '<option value="POST" selected>' in reply.text
    assert 'data-oob="#response"' in reply.text
    assert "201" in reply.text


def test_replay_of_a_failed_send_shows_the_error(client, app):
    entry_id = app.state.store.add_history(
        Request(url="http://127.0.0.1:1/"),
        Response(engine="curl", error="Connection refused", curl_exit_code=7),
    )
    assert "Connection refused" in client.get(f"/history/{entry_id}").text


def test_unknown_history_entry_is_a_404(client):
    assert client.get("/history/9999").status_code == 404


def test_clear_history_empties_the_sidebar(client, app):
    app.state.store.add_history(Request(url="http://x.test/"), Response(engine="httpx", status=200))
    reply = client.delete("/history")
    assert "Nothing sent yet." in reply.text
    assert app.state.store.list_history() == []


def test_get_requests_renders_the_sidebar(client, app):
    make_saved(app, "Get users")
    reply = client.get("/requests")
    assert reply.status_code == 200
    assert "Get users" in reply.text


def test_get_history_renders_the_sidebar(client, app):
    app.state.store.add_history(
        Request(url="http://x.test/one"), Response(engine="httpx", status=200)
    )
    reply = client.get("/history")
    assert reply.status_code == 200
    assert "http://x.test/one" in reply.text
