from hitman.core.models import (
    DEFAULT_TIMEOUT,
    KeyValue,
    Request,
    ensure_scheme,
    normalize,
)


def test_ensure_scheme_adds_http_to_bare_host():
    assert ensure_scheme("localhost:3000/api") == "http://localhost:3000/api"


def test_ensure_scheme_leaves_explicit_scheme_alone():
    assert ensure_scheme("https://example.com") == "https://example.com"


def test_ensure_scheme_prefixes_url_with_an_embedded_url_in_the_query():
    assert ensure_scheme("api.example.com/cb?to=http://callback.test/hook") == (
        "http://api.example.com/cb?to=http://callback.test/hook"
    )


def test_ensure_scheme_leaves_a_leading_placeholder_alone():
    """The variable may carry the scheme, so prefixing here doubles it up."""
    assert ensure_scheme("{{base_url}}/users") == "{{base_url}}/users"


def test_ensure_scheme_still_prefixes_a_placeholder_used_as_a_path():
    assert ensure_scheme("localhost:3000/{{path}}") == "http://localhost:3000/{{path}}"


def test_normalize_keeps_a_templated_url_sendable():
    """Saving is what applies normalize, and a saved request must still run.

    Stored as http://{{base_url}}/users, this resolved to
    http://http://localhost:3000/users the moment an environment was applied.
    """
    assert normalize(Request(url="{{base_url}}/users")).url == "{{base_url}}/users"


def test_full_url_merges_enabled_params_only():
    request = Request(
        url="http://localhost:3000/api",
        params=[KeyValue("page", "2"), KeyValue("debug", "1", enabled=False)],
    )
    assert request.full_url() == "http://localhost:3000/api?page=2"


def test_full_url_keeps_query_already_in_the_url():
    request = Request(url="http://localhost:3000/api?a=1", params=[KeyValue("b", "2")])
    assert request.full_url() == "http://localhost:3000/api?a=1&b=2"


def test_full_url_repairs_scheme_less_url():
    # urlsplit() parses 'localhost' as a scheme, so this would otherwise break.
    request = Request(url="localhost:3000/api", params=[KeyValue("a", "1")])
    assert request.full_url() == "http://localhost:3000/api?a=1"


def test_full_url_extracts_the_real_host_despite_an_embedded_url():
    request = Request(url="api.example.com/cb?to=http://callback.test/hook")
    assert request.full_url().startswith("http://api.example.com/cb")


def test_effective_headers_materialises_json_content_type():
    request = Request(body_type="json", body='{"a": 1}')
    assert ("Content-Type", "application/json") in request.effective_headers()


def test_effective_headers_respects_explicit_content_type():
    request = Request(
        body_type="json",
        body='{"a": 1}',
        headers=[KeyValue("Content-Type", "application/vnd.api+json")],
    )
    types = [v for k, v in request.effective_headers() if k.lower() == "content-type"]
    assert types == ["application/vnd.api+json"]


def test_effective_headers_adds_nothing_for_raw_body():
    # Spec: 'raw' means whatever the user set, with no default Content-Type.
    request = Request(body_type="raw", body="<xml/>")
    assert request.effective_headers() == []


def test_body_bytes_urlencodes_form_fields():
    request = Request(body_type="form", form_fields=[KeyValue("a", "1"), KeyValue("b", "x y")])
    assert request.body_bytes() == b"a=1&b=x+y"


def test_body_bytes_is_none_when_body_type_is_none():
    assert Request().body_bytes() is None


def test_normalize_moves_url_query_into_params():
    normalized = normalize(Request(url="http://localhost:3000/api?a=1&b=2"))
    assert normalized.url == "http://localhost:3000/api"
    assert [(kv.key, kv.value) for kv in normalized.params] == [("a", "1"), ("b", "2")]


def test_normalize_drops_disabled_entries():
    request = Request(
        url="http://x.test/",
        headers=[KeyValue("A", "1"), KeyValue("B", "2", enabled=False)],
    )
    assert [kv.key for kv in normalize(request).headers] == ["A"]


def test_normalize_is_idempotent():
    request = Request(url="http://x.test/api?a=1", body_type="json", body="{}")
    once = normalize(request)
    assert normalize(once) == once


def test_request_survives_dict_round_trip():
    request = Request(
        method="POST",
        url="http://localhost:3000/api",
        headers=[KeyValue("X-Test", "1", enabled=False)],
        body_type="json",
        body='{"a": 1}',
        timeout=5.0,
    )
    assert Request.from_dict(request.to_dict()) == request


def test_default_timeout_is_thirty_seconds():
    assert Request().timeout == DEFAULT_TIMEOUT
