import json
import tempfile

from hitman.core.engines.curl_engine import CurlEngine, curl_available
from hitman.core.models import Request


def send(url, **kwargs):
    return CurlEngine().send(Request(url=url, **kwargs))


def test_curl_is_available_on_this_machine():
    assert curl_available() is True


def test_get_returns_status_and_body(fixture_server):
    response = send(f"{fixture_server}/json")
    assert response.status == 200
    assert response.body == '{"hello": "world"}'
    assert response.engine == "curl"
    assert response.error is None


def test_response_headers_are_read_from_the_dump_file(fixture_server):
    response = send(f"{fixture_server}/json")
    assert ("x-fixture", "hitman") in [(k.lower(), v) for k, v in response.headers]


def test_reason_phrase_is_parsed(fixture_server):
    assert send(f"{fixture_server}/json").reason == "OK"


def test_only_the_final_header_block_is_kept_after_a_redirect(fixture_server):
    response = send(f"{fixture_server}/redirect", follow_redirects=True)
    assert response.status == 200
    assert not any(k.lower() == "location" for k, _ in response.headers)


def test_post_body_reaches_the_server(fixture_server):
    response = CurlEngine().send(
        Request(method="POST", url=f"{fixture_server}/echo", body_type="json", body='{"a": 1}')
    )
    echoed = json.loads(response.body)
    assert echoed["method"] == "POST"
    assert echoed["body"] == '{"a": 1}'


def test_connection_refused_reports_exit_code_seven(closed_port):
    response = send(f"http://127.0.0.1:{closed_port}/")
    assert response.status is None
    assert response.curl_exit_code == 7
    assert "Connection refused" in response.error


def test_unresolvable_host_reports_exit_code_six():
    response = send("http://no-such-host-xyz.invalid/")
    assert response.curl_exit_code == 6
    assert "resolve" in response.error.lower()


def test_timeout_reports_exit_code_twenty_eight(fixture_server):
    response = send(f"{fixture_server}/slow", timeout=0.3)
    assert response.curl_exit_code == 28
    assert "Timed out" in response.error


def test_binary_body_is_summarised(fixture_server):
    assert "image/png" in send(f"{fixture_server}/binary").body


def test_temporary_files_are_cleaned_up(fixture_server, tmp_path, monkeypatch):
    # Patch tempfile.tempdir directly: tempfile caches gettempdir() on first
    # use, so setting TMPDIR here would silently do nothing.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    send(f"{fixture_server}/json")
    assert list(tmp_path.glob("hitman-*")) == []
