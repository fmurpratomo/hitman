def send(client, base_form, **overrides):
    return client.post("/send", data={**base_form, **overrides})


def test_send_renders_status_and_body(client, base_form, fixture_server):
    reply = send(client, base_form, url=f"{fixture_server}/json")
    assert reply.status_code == 200
    assert "200" in reply.text
    assert "hello" in reply.text


def test_send_writes_history(client, app, base_form, fixture_server):
    send(client, base_form, url=f"{fixture_server}/json")
    entries = app.state.store.list_history()
    assert len(entries) == 1
    assert entries[0].response.status == 200


def test_send_refreshes_the_sidebar_out_of_band(client, base_form, fixture_server):
    reply = send(client, base_form, url=f"{fixture_server}/json")
    assert 'data-oob="#sidebar"' in reply.text


def test_failed_send_shows_a_friendly_message_and_is_recorded(client, app, base_form, closed_port):
    reply = send(client, base_form, url=f"http://127.0.0.1:{closed_port}/")
    assert "Connection refused" in reply.text
    assert app.state.store.list_history()[0].response.error is not None


def test_empty_url_does_not_crash(client, base_form):
    reply = send(client, base_form, url="")
    assert reply.status_code == 200
    assert "Enter a URL" in reply.text


def test_curl_engine_can_be_selected(client, base_form, fixture_server):
    reply = send(client, base_form, url=f"{fixture_server}/json", engine="curl")
    assert "via curl" in reply.text
    assert "200" in reply.text


def test_response_body_is_escaped_not_executed(client, base_form, fixture_server):
    """The single most important security test in the web layer."""
    reply = send(client, base_form, url=f"{fixture_server}/html")
    assert "<script>alert(1)</script>" not in reply.text
    assert "&lt;script&gt;" in reply.text


def test_post_with_json_body(client, base_form, fixture_server):
    reply = send(
        client, base_form, method="POST", url=f"{fixture_server}/echo",
        body_type="json", body='{"a": 1}',
    )
    assert "application/json" in reply.text
