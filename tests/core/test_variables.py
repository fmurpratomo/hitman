from hitman.core.models import KeyValue, Request
from hitman.core.variables import find_names, substitute

ENV = {"base_url": "http://localhost:3000", "token": "abc123", "api.key": "k-1"}


def test_url_placeholder_is_replaced():
    result = substitute(Request(url="{{base_url}}/users"), ENV)
    assert result.request.url == "http://localhost:3000/users"
    assert result.unresolved == []


def test_whitespace_inside_the_braces_is_tolerated():
    assert substitute(Request(url="{{ base_url }}/x"), ENV).request.url.endswith("/x")
    assert substitute(Request(url="{{ base_url }}/x"), ENV).request.url.startswith("http://")


def test_dotted_names_work():
    result = substitute(Request(url="http://x.test/?k={{api.key}}"), ENV)
    assert result.request.url.endswith("k-1")


def test_headers_are_substituted():
    request = Request(url="http://x.test/", headers=[KeyValue("Authorization", "Bearer {{token}}")])
    resolved = substitute(request, ENV).request
    assert resolved.headers[0].value == "Bearer abc123"


def test_params_are_substituted():
    request = Request(url="http://x.test/", params=[KeyValue("key", "{{api.key}}")])
    assert substitute(request, ENV).request.params[0].value == "k-1"


def test_json_body_is_substituted():
    request = Request(url="http://x.test/", body_type="json", body='{"t": "{{token}}"}')
    assert substitute(request, ENV).request.body == '{"t": "abc123"}'


def test_form_fields_are_substituted():
    request = Request(
        url="http://x.test/", body_type="form", form_fields=[KeyValue("t", "{{token}}")]
    )
    assert substitute(request, ENV).request.form_fields[0].value == "abc123"


def test_an_unknown_name_is_left_in_place_and_reported():
    """Replacing with '' would turn a clear failure into a baffling one."""
    result = substitute(Request(url="{{missing}}/users"), ENV)
    assert result.request.url == "{{missing}}/users"
    assert result.unresolved == ["missing"]


def test_each_missing_name_is_reported_once():
    result = substitute(Request(url="{{a}}/{{a}}/{{b}}"), ENV)
    assert result.unresolved == ["a", "b"]


def test_substitution_is_a_single_pass():
    """A value is never rescanned, so it cannot inject another placeholder."""
    result = substitute(Request(url="{{outer}}"), {"outer": "{{inner}}", "inner": "boom"})
    assert result.request.url == "{{inner}}"
    assert result.unresolved == []


def test_disabled_rows_are_left_alone_and_do_not_report_missing():
    request = Request(
        url="http://x.test/",
        headers=[KeyValue("X-Off", "{{nope}}", enabled=False)],
    )
    result = substitute(request, ENV)
    assert result.request.headers[0].value == "{{nope}}"
    assert result.unresolved == []


def test_the_original_request_is_not_mutated():
    request = Request(url="{{base_url}}/users")
    substitute(request, ENV)
    assert request.url == "{{base_url}}/users"


def test_no_placeholders_is_a_no_op():
    request = Request(url="http://x.test/", headers=[KeyValue("A", "1")])
    assert substitute(request, ENV).request == request


def test_find_names_lists_references_in_order():
    assert find_names("{{a}}/{{b}}?x={{a}}") == ["a", "b", "a"]
