import re

from hitman.core.models import DEFAULT_TIMEOUT
from hitman.web.forms import request_from_form


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_index_renders(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="request-form"' in page.text
    assert "Send" in page.text


def test_index_loads_only_local_first_party_scripts(client):
    sources = re.findall(r'<script[^>]*src="([^"]+)"', client.get("/").text)
    assert sources == ["/static/app.js"]


def test_form_parses_key_value_rows():
    form = {
        "method": "post",
        "url": "  http://localhost:3000/api  ",
        "param_key": ["page", "skipme"],
        "param_value": ["2", "x"],
        "param_enabled": ["1", "0"],
        "header_key": ["X-Key"],
        "header_value": ["abc"],
        "header_enabled": ["1"],
        "body_type": "json",
        "body": "{}",
        "timeout": "5",
        "follow_redirects": "1",
        "verify_tls": "0",
    }
    parsed = request_from_form(_MultiForm(form))
    assert parsed.method == "POST"
    assert parsed.url == "http://localhost:3000/api"
    assert [(kv.key, kv.enabled) for kv in parsed.params] == [("page", True), ("skipme", False)]
    assert parsed.verify_tls is False
    assert parsed.timeout == 5.0


def test_form_drops_completely_blank_rows():
    form = {"param_key": ["", "a"], "param_value": ["", "1"], "param_enabled": ["1", "1"]}
    assert [kv.key for kv in request_from_form(_MultiForm(form)).params] == ["a"]


def test_form_falls_back_on_a_bad_timeout():
    assert request_from_form(_MultiForm({"timeout": "abc"})).timeout == DEFAULT_TIMEOUT


def test_form_rejects_an_unknown_body_type():
    assert request_from_form(_MultiForm({"body_type": "evil"})).body_type == "none"


class _MultiForm(dict):
    """Minimal stand-in for Starlette's FormData."""

    def getlist(self, key):
        # dict.get, not self.get — self.get unwraps lists, which would
        # collapse every table to its first row.
        value = dict.get(self, key, [])
        return value if isinstance(value, list) else [value]

    def get(self, key, default=None):
        value = dict.get(self, key, default)
        return value[0] if isinstance(value, list) and value else value


def test_missing_verify_tls_field_does_not_disable_verification():
    """A dropped field must never weaken the request."""
    assert request_from_form(_MultiForm({"url": "https://x.test/"})).verify_tls is True


def test_missing_follow_redirects_field_defaults_to_following():
    assert request_from_form(_MultiForm({"url": "https://x.test/"})).follow_redirects is True


def test_explicit_zero_still_turns_them_off():
    parsed = request_from_form(_MultiForm({"verify_tls": "0", "follow_redirects": "0"}))
    assert parsed.verify_tls is False
    assert parsed.follow_redirects is False
