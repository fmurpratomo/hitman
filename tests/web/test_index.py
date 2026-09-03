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


def test_static_assets_are_revalidated_not_heuristically_cached(client):
    """Without this the browser can keep running a stale app.js after an update.

    Starlette sends ETag/Last-Modified but no Cache-Control, and browsers then
    guess a freshness lifetime. This was not hypothetical: a JS fix appeared to
    have no effect because the page was still running the cached old file.
    """
    reply = client.get("/static/app.js")
    assert reply.status_code == 200
    assert reply.headers.get("cache-control") == "no-cache"


def test_page_responses_are_not_given_that_header(client):
    assert client.get("/").headers.get("cache-control") != "no-cache"


def test_the_in_flight_indicator_is_wired_up_at_both_ends():
    """Regression guard for a bug no server-side test can see.

    The waiting state is a name agreed between app.js, which sets it, and
    app.css, which draws it. Rename one and the Send button silently goes back
    to just greying out — the markup stays correct and only the rendering is
    wrong, which is exactly the class of bug the stylesheet test in
    test_bodyview.py exists for.
    """
    from pathlib import Path

    script = Path("hitman/web/static/app.js").read_text()
    css = Path("hitman/web/static/app.css").read_text()

    assert "aria-busy" in script and 'button[aria-busy="true"]::after' in css
    assert "'is-loading'" in script and ".is-loading" in css
    # Shown on a delay, or a localhost reply makes it strobe.
    assert "WAIT_DELAY_MS" in script


def test_the_draft_autosave_is_wired_up_at_both_ends():
    """Same class of bug as the indicator guard above: a silent front-end break.

    The autosave is a contract between builder.html, which marks the form with
    the id to save against, app.js, which reads it, and the route it calls. Any
    one of the three renamed and edits stop being kept, with every server-side
    test still green.
    """
    from pathlib import Path

    script = Path("hitman/web/static/app.js").read_text()
    builder = Path("hitman/web/templates/fragments/builder.html").read_text()

    assert "data-request-id" in builder
    assert "form[data-request-id]" in script
    assert "/draft" in script and "draft-state" in script
    # Debounced, not one request per keystroke.
    assert "DRAFT_DELAY_MS" in script
    # Settled before any action that could swap the form away.
    assert "await flushDraft();" in script

