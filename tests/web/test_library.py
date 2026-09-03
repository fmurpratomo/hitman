from hitman.core.models import KeyValue, Request, Response


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


# --- folders and duplication --------------------------------------------


def test_saving_into_a_folder(client, app, base_form):
    reply = client.post(
        "/requests",
        data={**base_form, "url": "http://x.test/", "save_name": "Get users",
              "save_folder": "Users"},
    )
    assert reply.status_code == 200
    assert "Users" in reply.text
    assert app.state.store.list_requests()[0].folder == "Users"


def test_the_sidebar_groups_by_folder(client, app):
    app.state.store.save_request("filed", Request(url="http://x.test/"), folder="Users")
    app.state.store.save_request("loose", Request(url="http://y.test/"))
    reply = client.get("/requests")
    assert "<details" in reply.text
    assert "<summary>Users" in reply.text
    assert "filed" in reply.text
    assert "loose" in reply.text


def test_no_folder_markup_when_nothing_is_filed(client, app):
    app.state.store.save_request("loose", Request(url="http://x.test/"))
    assert "<details" not in client.get("/requests").text


def test_duplicating_adds_a_copy(client, app):
    saved_id = make_saved(app, "Get users")
    reply = client.post(f"/requests/{saved_id}/duplicate")
    assert reply.status_code == 200
    assert "Get users (copy)" in reply.text
    assert len(app.state.store.list_requests()) == 2


def test_duplicating_keeps_the_request_itself(client, app):
    saved_id = make_saved(app)
    client.post(f"/requests/{saved_id}/duplicate")
    original, copy = app.state.store.list_requests()
    assert original.request == copy.request


def test_duplicating_an_unknown_request_is_a_404(client):
    assert client.post("/requests/9999/duplicate").status_code == 404


def test_loading_a_saved_request_offers_update(client, app):
    saved_id = app.state.store.save_request(
        "Get users", Request(url="http://x.test/"), folder="Users"
    )
    reply = client.get(f"/requests/{saved_id}")
    assert f'data-url="/requests/{saved_id}"' in reply.text
    assert "Update" in reply.text
    assert 'value="Get users"' in reply.text
    assert 'value="Users"' in reply.text


def test_a_fresh_builder_offers_no_update(client):
    page = client.get("/")
    assert "Update" not in page.text
    assert "Save as new" in page.text


def test_update_can_rename_and_move(client, app, base_form):
    saved_id = app.state.store.save_request(
        "old", Request(url="http://x.test/"), folder="Users"
    )
    client.put(
        f"/requests/{saved_id}",
        data={**base_form, "url": "http://x.test/", "save_name": "new", "save_folder": "Auth"},
    )
    item = app.state.store.get_request(saved_id)
    assert item.name == "new"
    assert item.folder == "Auth"


def test_the_folder_datalist_offers_existing_folders(client, app):
    app.state.store.save_request("a", Request(url="http://x.test/"), folder="Users")
    app.state.store.save_request("b", Request(url="http://x.test/"), folder="Auth")
    page = client.get("/")
    assert '<datalist id="folder-list">' in page.text
    assert '<option value="Auth">' in page.text
    assert '<option value="Users">' in page.text


# --- drafts and the checkpoint -----------------------------------------
#
# Two save methods. Editing a saved request keeps a draft automatically, so
# switching to another endpoint is not the same as discarding your work.
# Update is the deliberate one: it moves the checkpoint and clears the draft.
# Rollback throws the draft away and puts the checkpoint back.


def form_for(request, base_form):
    """The payload the builder submits for an already-stored request."""
    return {
        **base_form,
        "method": request.method,
        "url": request.url,
        "param_key": [kv.key for kv in request.params],
        "param_value": [kv.value for kv in request.params],
        "param_enabled": ["1"] * len(request.params),
        "header_key": [kv.key for kv in request.headers],
        "header_value": [kv.value for kv in request.headers],
        "header_enabled": ["1"] * len(request.headers),
        "body_type": request.body_type,
        "body": request.body,
        "timeout": str(request.timeout),
    }


def test_editing_a_saved_request_keeps_a_draft(client, app, base_form):
    saved_id = make_saved(app)
    reply = client.put(
        f"/requests/{saved_id}/draft", data={**base_form, "url": "http://x.test/edited"}
    )
    assert reply.status_code == 204
    assert reply.headers["X-Draft"] == "1"
    saved = app.state.store.get_request(saved_id)
    assert saved.dirty is True
    assert saved.editing.url == "http://x.test/edited"


def test_a_draft_survives_moving_to_another_request_and_back(client, app, base_form):
    """The whole point: checking another endpoint must not cost you your edits."""
    first = make_saved(app, "First")
    second = make_saved(app, "Second")

    client.put(f"/requests/{first}/draft", data={**base_form, "url": "http://x.test/wip"})
    client.get(f"/requests/{second}")  # wander off
    reply = client.get(f"/requests/{first}")  # and come back

    assert 'value="http://x.test/wip"' in reply.text
    assert "Roll back to checkpoint" in reply.text


def test_loading_a_clean_request_hides_the_unsaved_marker(client, app):
    reply = client.get(f"/requests/{make_saved(app)}")
    assert 'id="draft-state"' in reply.text
    assert "hidden" in reply.text.split('id="draft-state"')[1].split(">")[0]


def test_the_sidebar_marks_which_requests_have_unsaved_work(client, app, base_form):
    saved_id = make_saved(app, "Get users")
    assert "Unsaved changes" not in client.get("/requests").text
    client.put(f"/requests/{saved_id}/draft", data={**base_form, "url": "http://x.test/wip"})
    assert "Unsaved changes" in client.get("/requests").text


def test_resubmitting_an_unchanged_form_does_not_look_unsaved(client, app, base_form):
    """Type a character, delete it again: nothing was actually changed."""
    saved_id = app.state.store.save_request(
        "Get users",
        Request(
            url="http://localhost:3000/users",
            params=[KeyValue("page", "2")],
            headers=[KeyValue("Accept", "application/json")],
        ),
    )
    stored = app.state.store.get_request(saved_id).request
    reply = client.put(f"/requests/{saved_id}/draft", data=form_for(stored, base_form))
    assert reply.headers["X-Draft"] == "0"
    assert app.state.store.get_request(saved_id).dirty is False


def test_update_moves_the_checkpoint_and_clears_the_draft(client, app, base_form):
    saved_id = make_saved(app)
    client.put(f"/requests/{saved_id}/draft", data={**base_form, "url": "http://x.test/wip"})
    reply = client.put(
        f"/requests/{saved_id}",
        data={**base_form, "url": "http://x.test/wip", "save_name": "Get users"},
    )
    saved = app.state.store.get_request(saved_id)
    assert saved.request.url == "http://x.test/wip"
    assert saved.dirty is False
    # The builder comes back so the marker clears without a reload.
    assert 'id="request-form"' in reply.text
    assert 'data-oob="#sidebar"' in reply.text


def test_rollback_restores_the_checkpoint(client, app, base_form):
    saved_id = make_saved(app)
    client.put(f"/requests/{saved_id}/draft", data={**base_form, "url": "http://x.test/wip"})
    reply = client.post(f"/requests/{saved_id}/rollback")
    assert reply.status_code == 200
    assert 'value="http://localhost:3000/users"' in reply.text
    assert "http://x.test/wip" not in reply.text
    assert app.state.store.get_request(saved_id).dirty is False


def test_rollback_on_a_clean_request_changes_nothing(client, app):
    saved_id = make_saved(app)
    assert client.post(f"/requests/{saved_id}/rollback").status_code == 200
    assert app.state.store.get_request(saved_id).request.url == "http://localhost:3000/users"


def test_draft_and_rollback_on_an_unknown_request_are_404s(client, base_form):
    assert client.put("/requests/9999/draft", data=base_form).status_code == 404
    assert client.post("/requests/9999/rollback").status_code == 404


def test_a_brand_new_request_is_not_drafted(client):
    """There is nothing for it to be a draft of until it has been saved once."""
    reply = client.get("/requests/new")
    assert "data-request-id" not in reply.text
    assert 'id="draft-state"' not in reply.text


# --- history shows the saved name ---------------------------------------


def test_sending_a_loaded_saved_request_names_it_in_the_history(client, app, base_form,
                                                                fixture_server):
    saved_id = app.state.store.save_request("Get users", Request(url=f"{fixture_server}/json"))
    reply = client.post(
        "/send",
        data={**base_form, "url": f"{fixture_server}/json", "request_id": str(saved_id)},
    )
    assert app.state.store.list_history()[0].saved_request_id == saved_id
    # The reply carries the refreshed sidebar, so the name is visible at once.
    assert "Get users" in reply.text


def test_an_ad_hoc_send_still_shows_its_url(client, app, base_form, fixture_server):
    reply = client.post("/send", data={**base_form, "url": f"{fixture_server}/json"})
    assert app.state.store.list_history()[0].saved_request_id is None
    assert f"{fixture_server}/json" in reply.text


def test_the_builder_carries_the_id_of_the_request_it_loaded(client, app):
    saved_id = make_saved(app)
    reply = client.get(f"/requests/{saved_id}")
    assert f'name="request_id" value="{saved_id}"' in reply.text


def test_a_new_builder_carries_no_id_to_attribute_a_send_to(client):
    assert 'name="request_id"' not in client.get("/requests/new").text


def test_a_garbage_request_id_is_ignored_rather_than_failing_the_send(client, app, base_form,
                                                                     fixture_server):
    reply = client.post(
        "/send",
        data={**base_form, "url": f"{fixture_server}/json", "request_id": "not-a-number"},
    )
    assert reply.status_code == 200
    assert app.state.store.list_history()[0].saved_request_id is None


def test_the_history_row_keeps_the_url_in_reach_when_it_shows_a_name(client, app,
                                                                    fixture_server):
    """Showing the name must not hide what was actually sent."""
    saved_id = app.state.store.save_request("Get users", Request(url=f"{fixture_server}/json"))
    app.state.store.add_history(
        Request(url=f"{fixture_server}/json"), Response(engine="httpx", status=200), saved_id
    )
    sidebar = client.get("/history").text
    assert "Get users" in sidebar
    assert f'title="{fixture_server}/json"' in sidebar


def test_replaying_from_history_does_not_bind_the_builder_to_the_saved_request(client, app,
                                                                              fixture_server):
    """History holds the resolved request; updating a template with it would be wrong.

    The name is a label on the list, not a claim that the two are the same
    thing, so replay stays an unbound builder offering only Save as new.
    """
    saved_id = app.state.store.save_request("Templated", Request(url="{{base_url}}/json"))
    entry_id = app.state.store.add_history(
        Request(url=f"{fixture_server}/json"), Response(engine="httpx", status=200), saved_id
    )
    reply = client.get(f"/history/{entry_id}")
    assert 'name="request_id"' not in reply.text
    assert "Roll back to checkpoint" not in reply.text


def test_the_history_list_is_grouped_under_day_headings(client, app):
    app.state.store.add_history(
        Request(url="http://x.test/"), Response(engine="httpx", status=200)
    )
    sidebar = client.get("/history").text
    assert "<summary>Today" in sidebar
    assert "http://x.test/" in sidebar


def test_an_empty_history_says_so_instead_of_showing_an_empty_day(client):
    sidebar = client.get("/history").text
    assert "Nothing sent yet." in sidebar
    assert "<summary>Today" not in sidebar


def test_a_send_lands_under_today(client, base_form, fixture_server):
    reply = client.post("/send", data={**base_form, "url": f"{fixture_server}/json"})
    assert "<summary>Today" in reply.text

