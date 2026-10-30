import json

from hitman.core.engines.httpx_engine import HttpxEngine, HttpxSession
from hitman.core.models import KeyValue, Request


def send(url, **kwargs):
    return HttpxEngine().send(Request(url=url, **kwargs))


def test_get_returns_status_and_body(fixture_server):
    response = send(f"{fixture_server}/json")
    assert response.status == 200
    assert response.error is None
    assert response.body == '{"hello": "world"}'
    assert response.engine == "httpx"


def test_elapsed_and_size_are_recorded(fixture_server):
    response = send(f"{fixture_server}/json")
    assert response.elapsed_ms > 0
    assert response.size_bytes == 18


def test_response_headers_are_captured(fixture_server):
    response = send(f"{fixture_server}/json")
    assert ("x-fixture", "hitman") in [(k.lower(), v) for k, v in response.headers]


def test_error_status_is_not_an_error(fixture_server):
    response = send(f"{fixture_server}/status/404")
    assert response.status == 404
    assert response.error is None


def test_params_reach_the_server(fixture_server):
    response = send(f"{fixture_server}/echo", params=[KeyValue("page", "2")])
    assert json.loads(response.body)["path"] == "/echo?page=2"


def test_json_body_is_sent_with_content_type(fixture_server):
    response = HttpxEngine().send(
        Request(method="POST", url=f"{fixture_server}/echo", body_type="json", body='{"a": 1}')
    )
    echoed = json.loads(response.body)
    assert echoed["method"] == "POST"
    assert echoed["content_type"] == "application/json"
    assert echoed["body"] == '{"a": 1}'


def test_raw_body_sends_no_content_type(fixture_server):
    response = HttpxEngine().send(
        Request(method="POST", url=f"{fixture_server}/echo", body_type="raw", body="<xml/>")
    )
    assert json.loads(response.body)["content_type"] is None


def test_redirects_are_followed_when_enabled(fixture_server):
    response = send(f"{fixture_server}/redirect", follow_redirects=True)
    assert response.status == 200
    assert response.body == '{"hello": "world"}'


def test_redirects_are_not_followed_when_disabled(fixture_server):
    response = send(f"{fixture_server}/redirect", follow_redirects=False)
    assert response.status == 302


def test_binary_body_is_summarised_not_dumped(fixture_server):
    response = send(f"{fixture_server}/binary")
    assert "bytes" in response.body
    assert "image/png" in response.body


def test_connection_refused_is_a_friendly_message(closed_port):
    response = send(f"http://127.0.0.1:{closed_port}/")
    assert response.status is None
    assert "Connection refused" in response.error
    assert str(closed_port) in response.error


def test_timeout_is_a_friendly_message(fixture_server):
    response = send(f"{fixture_server}/slow", timeout=0.3)
    assert response.status is None
    assert "Timed out" in response.error


def test_unresolvable_host_is_a_friendly_message():
    response = send("http://no-such-host-xyz.invalid/")
    assert response.status is None
    assert "resolve" in response.error.lower()


def test_scheme_less_url_works(fixture_server):
    host_and_port = fixture_server.removeprefix("http://")
    response = send(f"{host_and_port}/json")
    assert response.status == 200


# --- the pooled session a performance test uses -------------------------


def test_a_session_answers_like_the_engine(fixture_server):
    session = HttpxSession()
    try:
        pooled = session.send(Request(url=f"{fixture_server}/json"))
    finally:
        session.close()
    single = send(f"{fixture_server}/json")
    assert (pooled.status, pooled.body, pooled.engine) == (single.status, single.body, "httpx")


def test_a_session_reports_failures_as_responses(closed_port):
    session = HttpxSession()
    try:
        response = session.send(Request(url=f"http://127.0.0.1:{closed_port}/", timeout=2))
    finally:
        session.close()
    assert response.status is None
    assert "Connection refused" in response.error


def test_a_session_does_not_carry_cookies_between_sends(fixture_server):
    """A fresh client has no cookies, so a pooled one must not grow any."""
    session = HttpxSession()
    try:
        session.send(Request(url=f"{fixture_server}/set-cookie"))
        echoed = json.loads(session.send(Request(url=f"{fixture_server}/echo")).body)
    finally:
        session.close()
    assert "cookie" not in echoed["headers"]


def test_a_session_honours_each_requests_redirect_setting(fixture_server):
    session = HttpxSession()
    try:
        followed = session.send(Request(url=f"{fixture_server}/redirect"))
        held = session.send(Request(url=f"{fixture_server}/redirect", follow_redirects=False))
    finally:
        session.close()
    assert (followed.status, held.status) == (200, 302)
