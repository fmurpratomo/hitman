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


def test_a_failed_history_write_still_returns_the_response(client, app, base_form, fixture_server):
    """The request already went out; bookkeeping must not cost the payload."""
    def explode(*args, **kwargs):
        raise RuntimeError("disk gone")

    app.state.store.add_history = explode
    reply = send(client, base_form, url=f"{fixture_server}/json")
    assert reply.status_code == 200
    assert "200" in reply.text
    assert "hello" in reply.text
    assert "not saved to history" in reply.text


def test_a_long_base64_field_is_clipped_with_a_toggle(client, base_form, fixture_server):
    reply = send(client, base_form, url=f"{fixture_server}/base64")
    assert reply.status_code == 200
    # The clipped half, the full half, and the control are all present.
    assert 'class="clip-short"' in reply.text
    assert 'class="clip-full"' in reply.text
    assert "show all" in reply.text and "chars" in reply.text
    # The whole blob is still delivered so "show all" has something to show.
    assert "A" * 4000 in reply.text


def test_short_fields_alongside_a_long_one_are_not_clipped(client, base_form, fixture_server):
    reply = send(client, base_form, url=f"{fixture_server}/base64")
    assert reply.text.count('class="clip-short"') == 1


def test_clipping_does_not_defeat_escaping(client, base_form, fixture_server):
    """A body is attacker-controlled in both the clipped and expanded halves."""
    reply = send(client, base_form, url=f"{fixture_server}/base64")
    assert "<script>alert(1)</script>" not in reply.text
    assert "&lt;script&gt;" in reply.text
