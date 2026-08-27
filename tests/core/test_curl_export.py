import pytest

from hitman.core.curl_export import to_argv, to_command
from hitman.core.curl_import import parse_curl
from hitman.core.models import KeyValue, Request, normalize


def test_simple_get_omits_method_flag():
    argv = to_argv(Request(url="https://x.test/"))
    assert "-X" not in argv
    assert argv[0] == "curl"
    assert argv[-1] == "https://x.test/"


def test_post_includes_method_flag():
    argv = to_argv(Request(method="POST", url="https://x.test/"))
    assert argv[1:3] == ["-X", "POST"]


def test_head_uses_dash_capital_i_not_x_head():
    argv = to_argv(Request(method="HEAD", url="https://x.test/"))
    assert "-I" in argv
    assert "-X" not in argv


def test_params_are_merged_into_the_url():
    argv = to_argv(Request(url="https://x.test/api", params=[KeyValue("page", "2")]))
    assert argv[-1] == "https://x.test/api?page=2"


def test_headers_are_emitted_as_h_flags():
    argv = to_argv(Request(url="https://x.test/", headers=[KeyValue("X-Key", "abc")]))
    assert "-H" in argv
    assert "X-Key: abc" in argv


def test_disabled_headers_are_not_emitted():
    argv = to_argv(
        Request(url="https://x.test/", headers=[KeyValue("X-Key", "abc", enabled=False)])
    )
    assert "X-Key: abc" not in argv


def test_json_body_emits_content_type_and_data():
    argv = to_argv(Request(method="POST", url="https://x.test/", body_type="json", body="{}"))
    assert "Content-Type: application/json" in argv
    assert "--data-raw" in argv
    assert "{}" in argv


def test_max_time_omitted_at_default_timeout_for_display():
    assert "--max-time" not in to_argv(Request(url="https://x.test/"))


def test_max_time_always_present_for_execution():
    argv = to_argv(Request(url="https://x.test/"), for_execution=True)
    assert argv[argv.index("--max-time") + 1] == "30"


def test_execution_suppresses_curl_default_content_type_for_raw_body():
    argv = to_argv(
        Request(method="POST", url="https://x.test/", body_type="raw", body="<xml/>"),
        for_execution=True,
    )
    assert "Content-Type:" in argv


def test_display_does_not_suppress_content_type():
    argv = to_argv(Request(method="POST", url="https://x.test/", body_type="raw", body="<xml/>"))
    assert "Content-Type:" not in argv


def test_command_quotes_values_containing_spaces():
    command = to_command(Request(url="https://x.test/", headers=[KeyValue("X", "a b")]))
    assert "'X: a b'" in command


def test_short_command_is_a_single_line():
    assert "\n" not in to_command(Request(url="https://x.test/"))


def test_long_command_wraps_with_backslashes():
    request = Request(
        url="https://api.example.com/v1/some/long/path",
        headers=[KeyValue("Authorization", "Bearer " + "x" * 40), KeyValue("Accept", "*/*")],
    )
    assert " \\\n  " in to_command(request)


ROUND_TRIP_CASES = [
    Request(url="https://x.test/api"),
    Request(url="https://x.test/api", params=[KeyValue("page", "2"), KeyValue("q", "a b")]),
    Request(method="DELETE", url="https://x.test/api/1"),
    Request(method="HEAD", url="https://x.test/api"),
    Request(method="POST", url="https://x.test/", body_type="json", body='{"a": 1}'),
    Request(method="POST", url="https://x.test/", body_type="raw", body="<xml/>",
            headers=[KeyValue("Content-Type", "application/xml")]),
    Request(method="POST", url="https://x.test/", body_type="form",
            form_fields=[KeyValue("a", "1"), KeyValue("b", "2")]),
    Request(url="https://x.test/", headers=[KeyValue("Authorization", "Bearer abc")]),
    Request(url="https://x.test/", verify_tls=False),
    Request(url="https://x.test/", timeout=5.0),
    Request(url="https://x.test/", follow_redirects=False),
    Request(url="https://x.test/?a=1", params=[KeyValue("b", "2")]),
    Request(url="https://x.test/", headers=[KeyValue("X-Off", "no", enabled=False)]),
]


@pytest.mark.parametrize("request_", ROUND_TRIP_CASES, ids=lambda r: f"{r.method}-{r.body_type}")
def test_round_trip_through_a_curl_command(request_):
    """parse(export(r)) == normalize(r) — the property the spec promises."""
    rebuilt = parse_curl(to_command(request_)).request
    assert rebuilt == normalize(request_)
