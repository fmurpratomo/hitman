def test_import_fills_the_builder(client):
    reply = client.post(
        "/import-curl",
        data={"text": "curl https://api.example.com/users -H 'Accept: application/json'"},
    )
    assert reply.status_code == 200
    assert 'value="https://api.example.com/users"' in reply.text
    assert "Accept" in reply.text


def test_import_sets_the_method(client):
    reply = client.post("/import-curl", data={"text": "curl -X DELETE https://x.test/1"})
    assert '<option value="DELETE" selected>' in reply.text


def test_import_surfaces_warnings(client):
    reply = client.post("/import-curl", data={"text": "curl --http2 https://x.test/"})
    assert "--http2" in reply.text
    assert "warnings" in reply.text


def test_import_rejects_a_malformed_command_without_a_builder(client):
    reply = client.post("/import-curl", data={"text": "curl -X POST"})
    assert reply.status_code == 422
    assert "No URL" in reply.text
    assert "request-form" not in reply.text


def test_import_rejects_empty_input(client):
    assert client.post("/import-curl", data={"text": "   "}).status_code == 422


def test_export_returns_a_plain_text_curl_command(client, base_form):
    reply = client.post("/export-curl", data={**base_form, "url": "http://localhost:3000/api"})
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/plain")
    assert reply.text.startswith("curl ")
    assert "http://localhost:3000/api" in reply.text


def test_export_includes_headers_and_body(client, base_form):
    reply = client.post(
        "/export-curl",
        data={
            **base_form, "method": "POST", "url": "http://localhost:3000/api",
            "body_type": "json", "body": '{"a": 1}',
            "header_key": ["X-Key"], "header_value": ["abc"], "header_enabled": ["1"],
        },
    )
    assert "-X POST" in reply.text
    assert "'X-Key: abc'" in reply.text
    assert "application/json" in reply.text


def test_export_then_import_survives_the_round_trip(client, base_form):
    exported = client.post(
        "/export-curl",
        data={**base_form, "url": "http://localhost:3000/api", "param_key": ["page"],
              "param_value": ["2"], "param_enabled": ["1"]},
    ).text
    reimported = client.post("/import-curl", data={"text": exported})
    assert reimported.status_code == 200
    assert 'value="page"' in reimported.text
