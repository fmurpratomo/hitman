# Hitman API Testing Client — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-only web app that sends HTTP requests to localhost and public APIs, imports and exports curl commands, and can execute requests through either `httpx` or the real `curl` binary.

**Architecture:** A pure-Python `hitman/core/` package (models, curl import/export, two send engines, SQLite storage) that imports nothing from the web layer, plus a thin FastAPI + Jinja2 layer in `hitman/web/` that renders HTML fragments. Interactivity is a ~50-line hand-written fetch/swap layer in `app.js` — no framework, no third-party JavaScript, no npm, no build step.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, Jinja2, httpx, SQLite (stdlib `sqlite3`), vanilla JavaScript, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-08-27-hitman-api-client-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3.11+.** The system Python 3.9 cannot parse the `X | None` syntax used throughout. Pin with `uv python pin 3.11`.
- **`hitman/core/` must never import from `hitman/web/`.** Every test under `tests/core/` must pass with FastAPI uninstalled. This is what keeps the curl parser testable without a server.
- **Never use a shell.** `subprocess` is always called with an argv list and `shell=False` (the default). No `os.system`, no `shell=True`, no string interpolation into a command line.
- **Never evaluate pasted input.** curl import uses `shlex.split` only, which tokenizes without evaluating.
- **Jinja2 autoescape stays on**, and response bodies are rendered as escaped text inside `<pre>`. Response bodies are attacker-controlled by definition; an API returning `<script>` must not execute inside the app.
- **Bind address is hardcoded `127.0.0.1`.** There is deliberately no `--host` flag.
- **All SQL uses parameter binding.** No f-strings or `%` formatting in SQL.
- **No third-party JavaScript and no CDN links.** The only script the page loads is `/static/app.js`. Do not add a framework, and do not fetch one at build or run time.
- **TDD:** write the failing test, watch it fail, implement minimally, watch it pass, commit. Every task ends on a green test run.
- **Commit style:** conventional commits (`feat:`, `test:`, `fix:`, `chore:`).

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | deps, `hitman` console script, pytest config |
| `hitman/core/models.py` | `KeyValue`, `Request`, `Response`, `normalize`, `ensure_scheme` |
| `hitman/core/curl_import.py` | `parse_curl(text) -> ParsedCurl`, `CurlParseError` |
| `hitman/core/curl_export.py` | `to_argv(request, for_execution)`, `to_command(request)` |
| `hitman/core/engines/base.py` | `Engine` protocol, `MAX_DISPLAY_BODY`, `decode_body` |
| `hitman/core/engines/httpx_engine.py` | `HttpxEngine` |
| `hitman/core/engines/curl_engine.py` | `CurlEngine`, `curl_available()` |
| `hitman/core/store.py` | `Store`, `SavedRequest`, `HistoryEntry` |
| `hitman/web/forms.py` | HTML form payload ↔ `Request` |
| `hitman/web/app.py` | FastAPI app factory, template/static wiring |
| `hitman/web/routes.py` | all fragment endpoints |
| `hitman/web/templates/` | `base.html`, `index.html`, `fragments/*.html` |
| `hitman/web/static/` | `app.css`, `app.js` (fetch/swap layer, no dependencies) |
| `hitman/cli.py` | argument parsing, starts uvicorn on 127.0.0.1 |

---

### Task 1: Project scaffolding and core data model

**Files:**
- Create: `pyproject.toml`, `.python-version`, `hitman/__init__.py`, `hitman/core/__init__.py`, `hitman/core/models.py`
- Create: `tests/__init__.py`, `tests/core/__init__.py`, `tests/core/test_models.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `KeyValue(key, value, enabled=True)`; `Request` with fields `method, url, params, headers, body_type, body, form_fields, follow_redirects, verify_tls, timeout` and methods `find_header(name) -> str | None`, `effective_headers() -> list[tuple[str,str]]`, `body_bytes() -> bytes | None`, `full_url() -> str`, `to_dict() -> dict`, `from_dict(dict) -> Request`; `Response` dataclass; module functions `ensure_scheme(url) -> str` and `normalize(request) -> Request`; constants `DEFAULT_TIMEOUT = 30.0`, `DEFAULT_CONTENT_TYPE`, `BODY_TYPES`.

- [ ] **Step 1: Create the project skeleton**

```bash
cd /Users/fmpratomo/MyProject/hitman
uv python pin 3.11
mkdir -p hitman/core/engines hitman/web/templates/fragments hitman/web/static
mkdir -p tests/core tests/web
touch hitman/__init__.py hitman/core/__init__.py hitman/core/engines/__init__.py
touch hitman/web/__init__.py tests/__init__.py tests/core/__init__.py tests/web/__init__.py
```

Write `pyproject.toml`:

```toml
[project]
name = "hitman"
version = "0.1.0"
description = "Local API testing client"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "jinja2>=3.1",
    "httpx>=0.28",
    "python-multipart>=0.0.20",
]

[project.scripts]
hitman = "hitman.cli:main"

[dependency-groups]
dev = ["pytest>=8.3", "ruff>=0.8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["hitman"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

Then: `uv sync`

- [ ] **Step 2: Write the failing tests**

`tests/core/test_models.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hitman.core.models'`

- [ ] **Step 4: Implement the model**

`hitman/core/models.py`:

```python
"""Core data model.

Nothing in this module may import from ``hitman.web``. These types are the
contract every other module speaks in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_TIMEOUT = 30.0
MAX_DISPLAY_BODY = 5 * 1024 * 1024

BODY_TYPES = ("none", "json", "raw", "form")

# Which Content-Type is implied when the user did not set one explicitly.
# 'raw' deliberately has no default: raw means "exactly what I typed".
DEFAULT_CONTENT_TYPE: dict[str, str | None] = {
    "none": None,
    "json": "application/json",
    "form": "application/x-www-form-urlencoded",
    "raw": None,
}


def ensure_scheme(url: str) -> str:
    """Prepend ``http://`` when the user typed a bare host.

    Required, not defensive: ``urlsplit("localhost:3000/api")`` parses
    ``localhost`` as the URL *scheme* and ``8931/api`` as the path, so host
    and port extraction silently produce nonsense without this.
    """
    url = url.strip()
    if not url or "://" in url:
        return url
    return "http://" + url


@dataclass
class KeyValue:
    """One row of the params, headers or form-fields table."""

    key: str
    value: str
    enabled: bool = True


@dataclass
class Request:
    method: str = "GET"
    url: str = ""
    params: list[KeyValue] = field(default_factory=list)
    headers: list[KeyValue] = field(default_factory=list)
    body_type: str = "none"
    body: str = ""
    form_fields: list[KeyValue] = field(default_factory=list)
    follow_redirects: bool = True
    verify_tls: bool = True
    timeout: float = DEFAULT_TIMEOUT

    def find_header(self, name: str) -> str | None:
        """Case-insensitive lookup across enabled headers."""
        wanted = name.lower()
        for kv in self.headers:
            if kv.enabled and kv.key.lower() == wanted:
                return kv.value
        return None

    def effective_headers(self) -> list[tuple[str, str]]:
        """Enabled headers, plus the implied Content-Type if the user set none."""
        headers = [(kv.key, kv.value) for kv in self.headers if kv.enabled and kv.key]
        implied = DEFAULT_CONTENT_TYPE[self.body_type]
        if implied and self.find_header("content-type") is None:
            headers.append(("Content-Type", implied))
        return headers

    def body_bytes(self) -> bytes | None:
        if self.body_type == "none":
            return None
        if self.body_type == "form":
            pairs = [(kv.key, kv.value) for kv in self.form_fields if kv.enabled and kv.key]
            return urlencode(pairs).encode("utf-8")
        return self.body.encode("utf-8")

    def full_url(self) -> str:
        """The URL actually sent: base URL with enabled params appended."""
        parts = urlsplit(ensure_scheme(self.url))
        query = parse_qsl(parts.query, keep_blank_values=True)
        query += [(kv.key, kv.value) for kv in self.params if kv.enabled and kv.key]
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Request:
        def rows(items: list[dict] | None) -> list[KeyValue]:
            return [KeyValue(**item) for item in (items or [])]

        return cls(
            method=data.get("method", "GET"),
            url=data.get("url", ""),
            params=rows(data.get("params")),
            headers=rows(data.get("headers")),
            body_type=data.get("body_type", "none"),
            body=data.get("body", ""),
            form_fields=rows(data.get("form_fields")),
            follow_redirects=data.get("follow_redirects", True),
            verify_tls=data.get("verify_tls", True),
            timeout=data.get("timeout", DEFAULT_TIMEOUT),
        )


@dataclass
class Response:
    engine: str
    status: int | None = None
    reason: str = ""
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: str = ""
    body_truncated: bool = False
    size_bytes: int = 0
    elapsed_ms: float = 0.0
    content_type: str = ""
    error: str | None = None
    curl_exit_code: int | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and self.status < 400


def normalize(request: Request) -> Request:
    """Canonical form used for storage, comparison and the round-trip test.

    Three transformations, each one required to make two semantically
    identical requests compare equal:

    1. A query string embedded in ``url`` moves into ``params`` — the user can
       type ``?page=2`` in the URL bar or add a param row, and those must not
       be different requests.
    2. Disabled rows are dropped, since they are never sent.
    3. The implied Content-Type becomes an explicit header, so a request that
       relies on the default matches one that spells it out.
    """
    parts = urlsplit(ensure_scheme(request.url))
    params = [KeyValue(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    params += [KeyValue(kv.key, kv.value) for kv in request.params if kv.enabled and kv.key]

    return Request(
        method=request.method.upper(),
        url=urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment)),
        params=params,
        headers=[KeyValue(k, v) for k, v in request.effective_headers()],
        body_type=request.body_type,
        body=request.body,
        form_fields=[
            KeyValue(kv.key, kv.value) for kv in request.form_fields if kv.enabled and kv.key
        ],
        follow_redirects=request.follow_redirects,
        verify_tls=request.verify_tls,
        timeout=request.timeout,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_models.py -v`
Expected: PASS, 15 tests

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .python-version uv.lock hitman tests
git commit -m "feat: add core request/response data model"
```

---

### Task 2: curl import

The riskiest module in the project. It is pure string handling with no I/O, so it gets the heaviest test table.

**Files:**
- Create: `hitman/core/curl_import.py`
- Create: `tests/core/test_curl_import.py`

**Interfaces:**
- Consumes: `hitman.core.models` — `KeyValue`, `Request`, `normalize`, `parse_qsl` usage.
- Produces: `parse_curl(text: str) -> ParsedCurl`; `ParsedCurl(request: Request, warnings: list[str])`; `CurlParseError(ValueError)` with attributes `.token` and `.index`. `parse_curl` returns an already-`normalize`d request, which is what makes the round-trip property in Task 3 hold.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_curl_import.py`:

```python
import pytest

from hitman.core.curl_import import CurlParseError, parse_curl


def parse(text):
    return parse_curl(text).request


def headers_of(request):
    return [(kv.key, kv.value) for kv in request.headers]


def test_simple_get():
    request = parse("curl https://api.example.com/users")
    assert request.method == "GET"
    assert request.url == "https://api.example.com/users"


def test_url_query_is_split_into_params():
    request = parse("curl 'http://localhost:3000/api?page=2&limit=10'")
    assert request.url == "http://localhost:3000/api"
    assert [(kv.key, kv.value) for kv in request.params] == [("page", "2"), ("limit", "10")]


def test_multiline_chrome_style_command():
    text = """curl 'https://api.example.com/v1/items' \\
      -H 'Accept: application/json' \\
      -H 'Authorization: Bearer abc123' \\
      --compressed"""
    request = parse(text)
    assert request.url == "https://api.example.com/v1/items"
    assert ("Accept", "application/json") in headers_of(request)
    assert ("Authorization", "Bearer abc123") in headers_of(request)
    assert ("Accept-Encoding", "gzip, deflate") in headers_of(request)


def test_leading_shell_prompt_is_stripped():
    assert parse("$ curl https://example.com/").url == "https://example.com/"


def test_explicit_json_body():
    request = parse(
        "curl -X POST https://api.example.com/users "
        "-H 'Content-Type: application/json' -d '{\"name\": \"ada\"}'"
    )
    assert request.method == "POST"
    assert request.body_type == "json"
    assert request.body == '{"name": "ada"}'


def test_data_implies_post():
    assert parse("curl https://x.test/ -d 'a=1'").method == "POST"


def test_urlencoded_data_becomes_form_fields():
    request = parse("curl https://x.test/ -d 'a=1&b=2'")
    assert request.body_type == "form"
    assert [(kv.key, kv.value) for kv in request.form_fields] == [("a", "1"), ("b", "2")]
    assert ("Content-Type", "application/x-www-form-urlencoded") in headers_of(request)


def test_json_body_without_content_type_stays_raw_and_warns():
    parsed = parse_curl("curl https://x.test/ -d '{\"a\": 1}'")
    assert parsed.request.body_type == "raw"
    assert parsed.request.body == '{"a": 1}'
    assert any("x-www-form-urlencoded" in w for w in parsed.warnings)


def test_repeated_data_flags_are_joined_with_ampersand():
    request = parse("curl https://x.test/ -d 'a=1' -d 'b=2'")
    assert [(kv.key, kv.value) for kv in request.form_fields] == [("a", "1"), ("b", "2")]


def test_basic_auth_becomes_authorization_header():
    request = parse("curl -u user:pass https://x.test/")
    assert ("Authorization", "Basic dXNlcjpwYXNz") in headers_of(request)


def test_head_flag_sets_head_method():
    assert parse("curl -I https://x.test/").method == "HEAD"


def test_get_flag_moves_data_into_query_params():
    request = parse("curl -G https://x.test/search -d 'q=cats' -d 'page=2'")
    assert request.method == "GET"
    assert [(kv.key, kv.value) for kv in request.params] == [("q", "cats"), ("page", "2")]
    assert request.body_type == "none"


def test_insecure_flag_disables_tls_verification():
    assert parse("curl -k https://x.test/").verify_tls is False


def test_max_time_sets_timeout():
    assert parse("curl -m 5 https://x.test/").timeout == 5.0


def test_combined_short_flags_are_expanded():
    request = parse("curl -sSL https://x.test/")
    assert request.follow_redirects is True


def test_long_flag_with_inline_value():
    request = parse("curl --header='X-Key: v' https://x.test/")
    assert ("X-Key", "v") in headers_of(request)


def test_form_flag_converts_to_urlencoded_with_warning():
    parsed = parse_curl("curl -F 'name=ada' https://x.test/")
    assert parsed.request.body_type == "form"
    assert [(kv.key, kv.value) for kv in parsed.request.form_fields] == [("name", "ada")]
    assert any("multipart" in w for w in parsed.warnings)


def test_form_file_upload_is_dropped_with_warning():
    parsed = parse_curl("curl -F 'avatar=@photo.png' -F 'name=ada' https://x.test/")
    assert [kv.key for kv in parsed.request.form_fields] == ["name"]
    assert any("avatar" in w for w in parsed.warnings)


def test_unknown_flag_warns_but_still_parses():
    parsed = parse_curl("curl --http2 https://x.test/")
    assert parsed.request.url == "https://x.test/"
    assert any("--http2" in w for w in parsed.warnings)


def test_output_flags_are_dropped_silently():
    parsed = parse_curl("curl -s -o /dev/null https://x.test/")
    assert parsed.warnings == []
    assert parsed.request.url == "https://x.test/"


def test_missing_url_raises():
    with pytest.raises(CurlParseError, match="No URL"):
        parse_curl("curl -X POST")


def test_header_without_colon_raises():
    with pytest.raises(CurlParseError, match="colon"):
        parse_curl("curl -H 'BadHeader' https://x.test/")


def test_flag_missing_its_value_raises():
    with pytest.raises(CurlParseError, match="needs a value"):
        parse_curl("curl https://x.test/ -H")


def test_empty_input_raises():
    with pytest.raises(CurlParseError):
        parse_curl("   ")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_curl_import.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hitman.core.curl_import'`

- [ ] **Step 3: Implement the parser**

`hitman/core/curl_import.py`:

```python
"""Parse a pasted ``curl`` command into a :class:`Request`.

``shlex.split`` *tokenizes*; it does not evaluate. No shell, no subprocess
and no ``eval`` is involved anywhere in this module, so a hostile paste
cannot execute anything — the worst it can do is fail to parse.
"""

from __future__ import annotations

import re
import shlex
from base64 import b64encode
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

from hitman.core.models import KeyValue, Request, normalize


class CurlParseError(ValueError):
    def __init__(self, message: str, token: str | None = None, index: int | None = None) -> None:
        self.token = token
        self.index = index
        super().__init__(message)


@dataclass
class ParsedCurl:
    request: Request
    warnings: list[str] = field(default_factory=list)


# Flags that only control curl's own console output. They have no meaning in
# a GUI, so they are dropped silently rather than reported as warnings.
_IGNORED = {
    "-s", "--silent", "-S", "--show-error", "-v", "--verbose", "-i", "--include",
    "--no-progress-meter", "-#", "--progress-bar", "-f", "--fail",
}
_IGNORED_WITH_VALUE = {"-o", "--output", "-w", "--write-out", "--retry"}
_BOOLEAN_SHORT = set("sSviLkIGf#")


class _Tokens:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.index = 0

    def __bool__(self) -> bool:
        return self.index < len(self.tokens)

    def next(self) -> str:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def value_for(self, name: str, inline: str | None) -> str:
        if inline is not None:
            return inline
        if self.index >= len(self.tokens):
            raise CurlParseError(
                f"{name} needs a value but the command ends here.", name, self.index
            )
        return self.next()


def _clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\\\r?\n", " ", text)   # shell line continuation
    text = re.sub(r"\^\r?\n", " ", text)   # windows cmd line continuation
    text = re.sub(r"`\r?\n", " ", text)    # powershell line continuation
    return text.lstrip("$").strip()


def _expand_combined(tokens: list[str]) -> list[str]:
    """Turn ``-sSL`` into ``-s -S -L``."""
    expanded: list[str] = []
    for token in tokens:
        if re.fullmatch(r"-[a-zA-Z#]{2,}", token) and all(c in _BOOLEAN_SHORT for c in token[1:]):
            expanded.extend(f"-{c}" for c in token[1:])
        else:
            expanded.append(token)
    return expanded


def _split_inline(token: str) -> tuple[str, str | None]:
    """``--header=X: 1`` -> ``("--header", "X: 1")``."""
    if token.startswith("--") and "=" in token:
        name, _, value = token.partition("=")
        return name, value
    return token, None


def _looks_like_url(token: str) -> bool:
    return "://" in token or bool(re.match(r"^[\w.-]+(:\d+)?(/|$)", token))


def _try_pairs(data: str) -> list[tuple[str, str]] | None:
    """Key/value pairs when the body is cleanly URL-encoded form data, else None."""
    if not data or "=" not in data:
        return None
    for segment in data.split("&"):
        key, _, _ = segment.partition("=")
        if "=" not in segment or not key:
            return None
        if any(ch in segment for ch in "{}[]\"' \n\t"):
            return None
    return parse_qsl(data, keep_blank_values=True)


def parse_curl(text: str) -> ParsedCurl:
    stream = _Tokens(_expand_combined(shlex.split(_clean(text))))
    if not stream:
        raise CurlParseError("Nothing to import — paste a curl command.")
    if stream.tokens[0] == "curl":
        stream.index = 1

    request = Request()
    warnings: list[str] = []
    bare_tokens: list[str] = []
    data_parts: list[str] = []
    form_parts: list[str] = []
    explicit_method: str | None = None
    as_get = False
    as_head = False

    while stream:
        position = stream.index
        token = stream.next()
        name, inline = _split_inline(token)

        if name in _IGNORED:
            continue
        if name in _IGNORED_WITH_VALUE:
            stream.value_for(name, inline)
            continue

        if name in ("-X", "--request"):
            explicit_method = stream.value_for(name, inline).upper()
        elif name in ("-H", "--header"):
            raw = stream.value_for(name, inline)
            key, sep, value = raw.partition(":")
            if not sep:
                raise CurlParseError(
                    f"Header {raw!r} is missing a colon.", token, position
                )
            request.headers.append(KeyValue(key.strip(), value.strip()))
        elif name in ("-d", "--data", "--data-raw", "--data-ascii", "--data-binary",
                      "--data-urlencode"):
            data_parts.append(stream.value_for(name, inline))
        elif name in ("-F", "--form"):
            form_parts.append(stream.value_for(name, inline))
        elif name in ("-u", "--user"):
            encoded = b64encode(stream.value_for(name, inline).encode()).decode()
            request.headers.append(KeyValue("Authorization", f"Basic {encoded}"))
        elif name in ("-A", "--user-agent"):
            request.headers.append(KeyValue("User-Agent", stream.value_for(name, inline)))
        elif name in ("-b", "--cookie"):
            request.headers.append(KeyValue("Cookie", stream.value_for(name, inline)))
        elif name in ("-e", "--referer"):
            request.headers.append(KeyValue("Referer", stream.value_for(name, inline)))
        elif name in ("-m", "--max-time"):
            raw = stream.value_for(name, inline)
            try:
                request.timeout = float(raw)
            except ValueError:
                raise CurlParseError(f"{name} needs a number, got {raw!r}.", token, position) from None
        elif name in ("-L", "--location"):
            request.follow_redirects = True
        elif name in ("-k", "--insecure"):
            request.verify_tls = False
        elif name == "--compressed":
            request.headers.append(KeyValue("Accept-Encoding", "gzip, deflate"))
        elif name in ("-G", "--get"):
            as_get = True
        elif name in ("-I", "--head"):
            as_head = True
        elif name == "--url":
            bare_tokens.append(stream.value_for(name, inline))
        elif name.startswith("-"):
            warnings.append(f"Ignored unsupported flag {name}.")
        else:
            bare_tokens.append(token)

    urls = [t for t in bare_tokens if _looks_like_url(t)] or bare_tokens
    if not urls:
        raise CurlParseError("No URL found in the command.")
    if len(urls) > 1:
        warnings.append(f"Ignored extra URLs: {', '.join(urls[1:])}. Only the first is used.")
    request.url = urls[0]

    data = "&".join(data_parts)

    if as_head:
        request.method = "HEAD"
    elif explicit_method:
        request.method = explicit_method
    elif (data and not as_get) or form_parts:
        request.method = "POST"
    else:
        request.method = "GET"

    if as_get and data:
        request.params.extend(
            KeyValue(k, v) for k, v in parse_qsl(data, keep_blank_values=True)
        )
        data = ""

    explicit_type = request.find_header("content-type")

    if form_parts:
        request.body_type = "form"
        for part in form_parts:
            key, _, value = part.partition("=")
            if value.startswith(("@", "<")):
                warnings.append(
                    f"Dropped file upload field {key!r} — file upload is not supported yet."
                )
                continue
            request.form_fields.append(KeyValue(key, value))
        warnings.append(
            "Converted -F from multipart to URL-encoded form; v1 does not send multipart."
        )
    elif data:
        pairs = _try_pairs(data)
        if explicit_type and "json" in explicit_type.lower():
            request.body_type = "json"
            request.body = data
        elif pairs is not None and (
            explicit_type is None or "x-www-form-urlencoded" in explicit_type.lower()
        ):
            request.body_type = "form"
            request.form_fields = [KeyValue(k, v) for k, v in pairs]
        else:
            request.body_type = "raw"
            request.body = data
            if explicit_type is None:
                warnings.append(
                    "curl would send Content-Type: application/x-www-form-urlencoded for "
                    "this body; Hitman sends no Content-Type. Add one in Headers if the "
                    "server needs it."
                )

    return ParsedCurl(request=normalize(request), warnings=warnings)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_curl_import.py -v`
Expected: PASS, 24 tests

- [ ] **Step 5: Commit**

```bash
git add hitman/core/curl_import.py tests/core/test_curl_import.py
git commit -m "feat: parse pasted curl commands into requests"
```

---

### Task 3: curl export and the round-trip property

**Files:**
- Create: `hitman/core/curl_export.py`
- Create: `tests/core/test_curl_export.py`

**Interfaces:**
- Consumes: `hitman.core.models` (`DEFAULT_CONTENT_TYPE`, `DEFAULT_TIMEOUT`, `Request`, `normalize`); `hitman.core.curl_import.parse_curl` in tests only.
- Produces: `to_argv(request: Request, *, for_execution: bool = False) -> list[str]` — the argv list the curl engine in Task 6 executes; `to_command(request: Request, *, width: int = 80) -> str` — the shell-quoted display string shown by "Copy as curl".

The `for_execution` flag is the whole reason this is one function rather than two. Display output should look like something a person would type. Execution output must additionally pin `--max-time` and suppress curl's automatic `Content-Type: application/x-www-form-urlencoded` on `--data-raw` requests, so the curl engine and the httpx engine send identical bytes. Verified: `curl -H 'Content-Type:'` removes the header (curl 8.7).

- [ ] **Step 1: Write the failing tests**

`tests/core/test_curl_export.py`:

```python
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
    # `curl -X HEAD` hangs waiting for a body that never arrives.
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
    argv = to_argv(
        Request(method="POST", url="https://x.test/", body_type="raw", body="<xml/>")
    )
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_curl_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hitman.core.curl_export'`

- [ ] **Step 3: Implement the exporter**

`hitman/core/curl_export.py`:

```python
"""Render a :class:`Request` as a curl command.

Two consumers with different needs share one builder:

* the UI's "Copy as curl", which should look like something a person typed;
* the curl engine, which must send exactly what the httpx engine sends.
"""

from __future__ import annotations

import shlex

from hitman.core.models import DEFAULT_CONTENT_TYPE, DEFAULT_TIMEOUT, Request

# Flags whose value should stay on the same line when wrapping for display.
_VALUE_FLAGS = {"-X", "-H", "--data-raw", "--max-time"}


def _format_timeout(timeout: float) -> str:
    return str(int(timeout)) if float(timeout).is_integer() else str(timeout)


def to_argv(request: Request, *, for_execution: bool = False) -> list[str]:
    method = request.method.upper()
    argv = ["curl"]

    if method == "HEAD":
        # `curl -X HEAD` waits for a response body that a HEAD reply never
        # sends, and hangs until the timeout. `-I` is the correct spelling.
        argv.append("-I")
    elif method != "GET":
        argv += ["-X", method]

    if request.follow_redirects:
        argv.append("-L")
    if not request.verify_tls:
        argv.append("-k")
    if for_execution or request.timeout != DEFAULT_TIMEOUT:
        argv += ["--max-time", _format_timeout(request.timeout)]

    for key, value in request.effective_headers():
        argv += ["-H", f"{key}: {value}"]

    body = request.body_bytes()

    if (
        for_execution
        and body is not None
        and DEFAULT_CONTENT_TYPE[request.body_type] is None
        and request.find_header("content-type") is None
    ):
        # curl adds `Content-Type: application/x-www-form-urlencoded` to any
        # --data request. httpx does not. An empty-valued -H removes curl's
        # header, so both engines put identical bytes on the wire.
        argv += ["-H", "Content-Type:"]

    if body is not None:
        argv += ["--data-raw", body.decode("utf-8")]

    argv.append(request.full_url())
    return argv


def to_command(request: Request, *, width: int = 80) -> str:
    """Shell-quoted command for display and clipboard."""
    argv = to_argv(request)
    parts: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] in _VALUE_FLAGS and index + 1 < len(argv):
            parts.append(f"{argv[index]} {shlex.quote(argv[index + 1])}")
            index += 2
        else:
            parts.append(shlex.quote(argv[index]))
            index += 1

    single_line = " ".join(parts)
    if len(single_line) <= width:
        return single_line
    return " \\\n  ".join(parts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_curl_export.py -v`
Expected: PASS, 27 tests (14 unit + 13 parametrized round-trip cases)

- [ ] **Step 5: Run the whole core suite**

Run: `uv run pytest tests/core -v`
Expected: PASS. If a round-trip case fails, the bug is almost always an
ordering mismatch between `effective_headers()` and the order `parse_curl`
appends headers — fix the exporter, not the test.

- [ ] **Step 6: Commit**

```bash
git add hitman/core/curl_export.py tests/core/test_curl_export.py
git commit -m "feat: export requests as curl commands with round-trip guarantee"
```

---

### Task 4: SQLite store

**Files:**
- Create: `hitman/core/store.py`
- Create: `tests/core/test_store.py`

**Interfaces:**
- Consumes: `hitman.core.models` (`Request`, `Response`, `normalize`).
- Produces: `Store(path)` with `save_request(name, request) -> int`, `update_request(id, name, request) -> None`, `get_request(id) -> SavedRequest | None`, `list_requests() -> list[SavedRequest]`, `delete_request(id) -> None`, `add_history(request, response) -> int`, `list_history(limit=100) -> list[HistoryEntry]`, `get_history(id) -> HistoryEntry | None`, `clear_history() -> None`, `close() -> None`; dataclasses `SavedRequest(id, name, request, created_at, updated_at)` and `HistoryEntry(id, request, response, created_at)`; constants `HISTORY_LIMIT = 500`, `STORED_BODY_LIMIT = 256 * 1024`.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_store.py`:

```python
import pytest

from hitman.core.models import Request, Response
from hitman.core.store import HISTORY_LIMIT, STORED_BODY_LIMIT, Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "test.db")
    yield store
    store.close()


def make_request(url="http://localhost:3000/api"):
    return Request(method="POST", url=url, body_type="json", body='{"a": 1}')


def make_response(**kwargs):
    defaults = dict(
        engine="httpx", status=200, reason="OK",
        headers=[("Content-Type", "application/json")],
        body='{"ok": true}', size_bytes=12, elapsed_ms=42.5,
    )
    return Response(**{**defaults, **kwargs})


def test_saved_request_survives_a_round_trip(store):
    saved_id = store.save_request("Create user", make_request())
    loaded = store.get_request(saved_id)
    assert loaded.name == "Create user"
    assert loaded.request.method == "POST"
    assert loaded.request.body == '{"a": 1}'


def test_get_request_returns_none_for_unknown_id(store):
    assert store.get_request(9999) is None


def test_list_requests_is_newest_first(store):
    store.save_request("first", make_request())
    store.save_request("second", make_request())
    assert [item.name for item in store.list_requests()] == ["second", "first"]


def test_update_request_changes_name_and_payload(store):
    saved_id = store.save_request("old", make_request())
    store.update_request(saved_id, "new", Request(method="GET", url="http://x.test/"))
    loaded = store.get_request(saved_id)
    assert loaded.name == "new"
    assert loaded.request.method == "GET"


def test_delete_request_removes_it(store):
    saved_id = store.save_request("gone", make_request())
    store.delete_request(saved_id)
    assert store.get_request(saved_id) is None


def test_saved_requests_are_normalised(store):
    # Query in the URL must come back as editable params.
    saved_id = store.save_request("q", Request(url="http://x.test/api?page=2"))
    loaded = store.get_request(saved_id)
    assert loaded.request.url == "http://x.test/api"
    assert [(kv.key, kv.value) for kv in loaded.request.params] == [("page", "2")]


def test_history_round_trip_preserves_response_headers_as_tuples(store):
    entry_id = store.add_history(make_request(), make_response())
    entry = store.get_history(entry_id)
    assert entry.response.headers == [("Content-Type", "application/json")]
    assert entry.response.status == 200
    assert entry.response.elapsed_ms == 42.5


def test_failed_sends_are_recorded_too(store):
    store.add_history(
        make_request(),
        Response(engine="curl", status=None, error="Connection refused", curl_exit_code=7),
    )
    entry = store.list_history()[0]
    assert entry.response.error == "Connection refused"
    assert entry.response.status is None
    assert entry.response.curl_exit_code == 7


def test_history_is_newest_first(store):
    store.add_history(Request(url="http://a.test/"), make_response())
    store.add_history(Request(url="http://b.test/"), make_response())
    assert store.list_history()[0].request.url == "http://b.test/"


def test_history_is_trimmed_to_the_limit(store):
    for index in range(HISTORY_LIMIT + 10):
        store.add_history(Request(url=f"http://x.test/{index}"), make_response())
    assert len(store.list_history(limit=10_000)) == HISTORY_LIMIT
    # The oldest entries are the ones dropped.
    assert store.list_history(limit=1)[0].request.url.endswith(str(HISTORY_LIMIT + 9))


def test_stored_response_body_is_capped(store):
    entry_id = store.add_history(make_request(), make_response(body="x" * (STORED_BODY_LIMIT + 500)))
    assert len(store.get_history(entry_id).response.body) == STORED_BODY_LIMIT


def test_clear_history_empties_it(store):
    store.add_history(make_request(), make_response())
    store.clear_history()
    assert store.list_history() == []


def test_store_creates_missing_parent_directory(tmp_path):
    store = Store(tmp_path / "nested" / "deeper" / "hitman.db")
    store.save_request("x", make_request())
    store.close()
    assert (tmp_path / "nested" / "deeper" / "hitman.db").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hitman.core.store'`

- [ ] **Step 3: Implement the store**

`hitman/core/store.py`:

```python
"""SQLite persistence for saved requests and send history.

The request is stored as one JSON column rather than spread across typed
columns, so adding a field to :class:`Request` needs no schema migration.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from hitman.core.models import Request, Response, normalize

HISTORY_LIMIT = 500
STORED_BODY_LIMIT = 256 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_requests (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL,
  request_json TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  request_json          TEXT NOT NULL,
  engine                TEXT NOT NULL,
  status                INTEGER,
  reason                TEXT,
  elapsed_ms            REAL,
  size_bytes            INTEGER,
  content_type          TEXT,
  error                 TEXT,
  curl_exit_code        INTEGER,
  body_truncated        INTEGER NOT NULL DEFAULT 0,
  response_headers_json TEXT,
  response_body         TEXT,
  created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
"""


@dataclass
class SavedRequest:
    id: int
    name: str
    request: Request
    created_at: str
    updated_at: str


@dataclass
class HistoryEntry:
    id: int
    request: Request
    response: Response
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False because FastAPI runs sync endpoints on a
        # worker threadpool; the lock below serialises every access.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- saved requests -------------------------------------------------

    def save_request(self, name: str, request: Request) -> int:
        payload = json.dumps(normalize(request).to_dict())
        stamp = _now()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO saved_requests (name, request_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (name, payload, stamp, stamp),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def update_request(self, request_id: int, name: str, request: Request) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE saved_requests SET name = ?, request_json = ?, updated_at = ?"
                " WHERE id = ?",
                (name, json.dumps(normalize(request).to_dict()), _now(), request_id),
            )
            self._conn.commit()

    def get_request(self, request_id: int) -> SavedRequest | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM saved_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return _row_to_saved(row) if row else None

    def list_requests(self) -> list[SavedRequest]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM saved_requests ORDER BY id DESC"
            ).fetchall()
        return [_row_to_saved(row) for row in rows]

    def delete_request(self, request_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM saved_requests WHERE id = ?", (request_id,))
            self._conn.commit()

    # --- history --------------------------------------------------------

    def add_history(self, request: Request, response: Response) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO history (request_json, engine, status, reason, elapsed_ms,"
                " size_bytes, content_type, error, curl_exit_code, body_truncated,"
                " response_headers_json, response_body, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    json.dumps(normalize(request).to_dict()),
                    response.engine,
                    response.status,
                    response.reason,
                    response.elapsed_ms,
                    response.size_bytes,
                    response.content_type,
                    response.error,
                    response.curl_exit_code,
                    int(response.body_truncated),
                    json.dumps([list(pair) for pair in response.headers]),
                    response.body[:STORED_BODY_LIMIT],
                    _now(),
                ),
            )
            entry_id = int(cursor.lastrowid)
            # Keep the database from growing without bound.
            self._conn.execute(
                "DELETE FROM history WHERE id NOT IN"
                " (SELECT id FROM history ORDER BY id DESC LIMIT ?)",
                (HISTORY_LIMIT,),
            )
            self._conn.commit()
            return entry_id

    def list_history(self, limit: int = 100) -> list[HistoryEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_history(row) for row in rows]

    def get_history(self, entry_id: int) -> HistoryEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM history WHERE id = ?", (entry_id,)
            ).fetchone()
        return _row_to_history(row) if row else None

    def clear_history(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history")
            self._conn.commit()


def _row_to_saved(row: sqlite3.Row) -> SavedRequest:
    return SavedRequest(
        id=row["id"],
        name=row["name"],
        request=Request.from_dict(json.loads(row["request_json"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_history(row: sqlite3.Row) -> HistoryEntry:
    return HistoryEntry(
        id=row["id"],
        request=Request.from_dict(json.loads(row["request_json"])),
        response=Response(
            engine=row["engine"],
            status=row["status"],
            reason=row["reason"] or "",
            # JSON has no tuples; restore them so Response compares equal.
            headers=[tuple(pair) for pair in json.loads(row["response_headers_json"] or "[]")],
            body=row["response_body"] or "",
            body_truncated=bool(row["body_truncated"]),
            size_bytes=row["size_bytes"] or 0,
            elapsed_ms=row["elapsed_ms"] or 0.0,
            content_type=row["content_type"] or "",
            error=row["error"],
            curl_exit_code=row["curl_exit_code"],
        ),
        created_at=row["created_at"],
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_store.py -v`
Expected: PASS, 13 tests

- [ ] **Step 5: Commit**

```bash
git add hitman/core/store.py tests/core/test_store.py
git commit -m "feat: add SQLite store for saved requests and history"
```

---

### Task 5: Engine protocol, httpx engine, and the test fixture server

**Files:**
- Create: `hitman/core/engines/base.py`, `hitman/core/engines/httpx_engine.py`
- Create: `tests/conftest.py`, `tests/core/test_httpx_engine.py`

**Interfaces:**
- Consumes: `hitman.core.models`.
- Produces: `Engine` protocol with `name: str` and `send(request) -> Response`; `decode_body(raw: bytes, content_type: str) -> tuple[str, bool, int]` returning `(text, truncated, size)`; `is_textual(content_type) -> bool`; `HttpxEngine()` with `name = "httpx"`. Pytest fixtures `fixture_server` (base URL string) and `closed_port` (int) usable by every later task.

The fixture server is a real threaded `http.server`, not a mock. The curl engine in Task 6 is a subprocess and cannot be intercepted by any Python-level patching, and using the same real server for both engines is what makes the parity test in Task 6 meaningful.

- [ ] **Step 1: Write the shared fixture server**

`tests/conftest.py`:

```python
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep pytest output clean
        pass

    def _respond(self, status, payload: bytes, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Fixture", "hitman")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _echo(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        payload = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "content_type": self.headers.get("Content-Type"),
                "body": body,
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
        ).encode()
        self._respond(200, payload)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/json":
            self._respond(200, b'{"hello": "world"}')
        elif path == "/slow":
            time.sleep(2)
            self._respond(200, b'{"slow": true}')
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/json")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/binary":
            self._respond(200, bytes(range(256)) * 4, "image/png")
        elif path == "/html":
            self._respond(200, b"<script>alert(1)</script>", "text/html")
        elif path.startswith("/status/"):
            self._respond(int(path.rsplit("/", 1)[1]), b"{}")
        else:
            self._echo()

    do_HEAD = do_GET
    do_POST = do_PUT = do_PATCH = do_DELETE = _echo


@pytest.fixture(scope="session")
def fixture_server():
    """A real HTTP server on a random loopback port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def closed_port():
    """A port number with nothing listening on it."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port
```

- [ ] **Step 2: Write the failing engine tests**

`tests/core/test_httpx_engine.py`:

```python
import json

from hitman.core.engines.httpx_engine import HttpxEngine
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
    assert response.ok is False


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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_httpx_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hitman.core.engines.httpx_engine'`

- [ ] **Step 4: Implement the base module**

`hitman/core/engines/base.py`:

```python
"""Shared engine contract and response-body decoding."""

from __future__ import annotations

from typing import Protocol

from hitman.core.models import MAX_DISPLAY_BODY, Request, Response

_TEXTUAL_HINTS = (
    "json", "text", "xml", "javascript", "html", "csv", "yaml",
    "x-www-form-urlencoded", "graphql",
)


class Engine(Protocol):
    name: str

    def send(self, request: Request) -> Response: ...


def is_textual(content_type: str) -> bool:
    lowered = (content_type or "").lower()
    if not lowered:
        return True  # no Content-Type: assume text and let decoding cope
    return any(hint in lowered for hint in _TEXTUAL_HINTS)


def decode_body(raw: bytes, content_type: str) -> tuple[str, bool, int]:
    """Return ``(display_text, truncated, size_in_bytes)``.

    Binary payloads are summarised rather than decoded — dumping megabytes of
    PNG into the DOM helps nobody.
    """
    size = len(raw)
    if not is_textual(content_type):
        label = content_type or "binary data"
        return f"[{size} bytes of {label} — not shown]", False, size

    truncated = size > MAX_DISPLAY_BODY
    data = raw[:MAX_DISPLAY_BODY] if truncated else raw

    charset = "utf-8"
    if "charset=" in (content_type or "").lower():
        charset = content_type.lower().split("charset=")[-1].split(";")[0].strip() or "utf-8"
    try:
        text = data.decode(charset, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    return text, truncated, size
```

- [ ] **Step 5: Implement the httpx engine**

`hitman/core/engines/httpx_engine.py`:

```python
"""Default send engine: structured, in-process, fast."""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx

from hitman.core.engines.base import decode_body
from hitman.core.models import Request, Response, ensure_scheme


def _host_label(request: Request) -> str:
    return urlsplit(ensure_scheme(request.url)).netloc or request.url


def _format_timeout(timeout: float) -> str:
    return str(int(timeout)) if float(timeout).is_integer() else str(timeout)


def describe_error(exc: Exception, request: Request) -> str:
    """Turn an httpx exception into something a person can act on."""
    host = _host_label(request)
    if isinstance(exc, httpx.TooManyRedirects):
        return "Too many redirects."
    if isinstance(exc, httpx.TimeoutException):
        return f"Timed out after {_format_timeout(request.timeout)}s."
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return f"Invalid URL: {exc}"
    if isinstance(exc, httpx.ConnectError):
        text = str(exc).lower()
        if "refused" in text:
            return f"Connection refused — is anything listening on {host}?"
        if any(
            hint in text
            for hint in ("name or service not known", "nodename nor servname",
                         "getaddrinfo", "name resolution", "no address associated")
        ):
            return f"Could not resolve host {host}."
        if "certificate" in text or "ssl" in text:
            return (
                f"TLS error talking to {host}: {exc}. "
                "Turn off 'Verify TLS' in Options to bypass."
            )
        return f"Could not connect to {host}: {exc}"
    return f"{type(exc).__name__}: {exc}"


class HttpxEngine:
    name = "httpx"

    def send(self, request: Request) -> Response:
        started = time.perf_counter()
        try:
            with httpx.Client(
                follow_redirects=request.follow_redirects,
                verify=request.verify_tls,
                timeout=request.timeout,
            ) as client:
                reply = client.request(
                    request.method.upper(),
                    request.full_url(),
                    headers=request.effective_headers(),
                    content=request.body_bytes(),
                )
        except Exception as exc:  # noqa: BLE001 - every failure becomes a Response
            return Response(
                engine=self.name,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                error=describe_error(exc, request),
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        content_type = reply.headers.get("content-type", "")
        body, truncated, size = decode_body(reply.content, content_type)
        return Response(
            engine=self.name,
            status=reply.status_code,
            reason=reply.reason_phrase,
            headers=list(reply.headers.items()),
            body=body,
            body_truncated=truncated,
            size_bytes=size,
            elapsed_ms=elapsed_ms,
            content_type=content_type,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_httpx_engine.py -v`
Expected: PASS, 14 tests

- [ ] **Step 7: Commit**

```bash
git add hitman/core/engines tests/conftest.py tests/core/test_httpx_engine.py
git commit -m "feat: add httpx send engine with readable network errors"
```

---

### Task 6: curl engine and engine parity

**Files:**
- Create: `hitman/core/engines/curl_engine.py`
- Create: `tests/core/test_curl_engine.py`, `tests/core/test_engine_parity.py`

**Interfaces:**
- Consumes: `hitman.core.curl_export.to_argv`, `hitman.core.engines.base.decode_body`, `hitman.core.models`.
- Produces: `CurlEngine()` with `name = "curl"`; `curl_available() -> bool` (cached), consumed by the web layer in Task 7 to disable the toggle.

Verified against curl 8.7: `-w %{json}` emits an object containing `response_code`, `http_code`, `time_total`, `size_download`, `content_type`, `url_effective`, `exitcode` and `errormsg`, so failure details come from curl's own structured output rather than scraped stderr. Verified exit codes: closed port → 7, unresolvable host → 6.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_curl_engine.py`:

```python
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
    # use, so setting TMPDIR here would silently do nothing and the test
    # would pass without proving anything.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    send(f"{fixture_server}/json")
    assert list(tmp_path.glob("hitman-*")) == []
```

`tests/core/test_engine_parity.py` — the test that justifies having two engines at all:

```python
import json

import pytest

from hitman.core.engines.curl_engine import CurlEngine
from hitman.core.engines.httpx_engine import HttpxEngine
from hitman.core.models import KeyValue, Request


@pytest.fixture(params=["httpx", "curl"])
def engine(request):
    return HttpxEngine() if request.param == "httpx" else CurlEngine()


def test_same_status_for_the_same_request(engine, fixture_server):
    assert engine.send(Request(url=f"{fixture_server}/status/404")).status == 404


def test_same_body_for_the_same_request(engine, fixture_server):
    assert engine.send(Request(url=f"{fixture_server}/json")).body == '{"hello": "world"}'


def test_same_content_type_sent_for_a_json_body(engine, fixture_server):
    response = engine.send(
        Request(method="POST", url=f"{fixture_server}/echo", body_type="json", body="{}")
    )
    assert json.loads(response.body)["content_type"] == "application/json"


def test_neither_engine_sends_a_content_type_for_a_raw_body(engine, fixture_server):
    """curl adds urlencoded to --data by default; the engine must suppress it."""
    response = engine.send(
        Request(method="POST", url=f"{fixture_server}/echo", body_type="raw", body="<xml/>")
    )
    assert json.loads(response.body)["content_type"] is None


def test_same_form_encoding(engine, fixture_server):
    response = engine.send(
        Request(
            method="POST", url=f"{fixture_server}/echo", body_type="form",
            form_fields=[KeyValue("a", "1"), KeyValue("b", "x y")],
        )
    )
    echoed = json.loads(response.body)
    assert echoed["body"] == "a=1&b=x+y"
    assert echoed["content_type"] == "application/x-www-form-urlencoded"


def test_same_params_in_the_url(engine, fixture_server):
    response = engine.send(
        Request(url=f"{fixture_server}/echo", params=[KeyValue("page", "2")])
    )
    assert json.loads(response.body)["path"] == "/echo?page=2"


def test_both_report_failure_without_raising(engine, closed_port):
    response = engine.send(Request(url=f"http://127.0.0.1:{closed_port}/"))
    assert response.status is None
    assert "Connection refused" in response.error
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_curl_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hitman.core.engines.curl_engine'`

- [ ] **Step 3: Implement the curl engine**

`hitman/core/engines/curl_engine.py`:

```python
"""Send engine that shells out to the real curl binary.

Always invoked as an argv list with ``shell=False``. No part of the request
is ever interpolated into a shell string.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from urllib.parse import urlsplit

from hitman.core.curl_export import to_argv
from hitman.core.engines.base import decode_body
from hitman.core.models import Request, Response, ensure_scheme

# curl's documented exit codes, phrased the way a person debugging would want.
_FRIENDLY = {
    3: "Malformed URL.",
    5: "Could not resolve proxy.",
    6: "Could not resolve host {host}.",
    7: "Connection refused — is anything listening on {host}?",
    28: "Timed out after {timeout}s.",
    35: "TLS handshake failed with {host}.",
    47: "Too many redirects.",
    52: "Empty reply from {host}.",
    56: "Connection reset by {host}.",
    60: "TLS certificate could not be verified. Turn off 'Verify TLS' in Options to bypass.",
}


@lru_cache(maxsize=1)
def curl_available() -> bool:
    return shutil.which("curl") is not None


def _format_timeout(timeout: float) -> str:
    return str(int(timeout)) if float(timeout).is_integer() else str(timeout)


def _parse_header_dump(raw: str) -> tuple[int | None, str, list[tuple[str, str]]]:
    """Read the last header block; earlier blocks are redirect hops."""
    blocks = [block for block in re.split(r"\r?\n\r?\n", raw) if block.strip()]
    if not blocks:
        return None, "", []
    lines = blocks[-1].strip().splitlines()
    status, reason = None, ""
    match = re.match(r"HTTP/[\d.]+\s+(\d{3})\s*(.*)", lines[0])
    if match:
        status = int(match.group(1))
        reason = match.group(2).strip()
    headers = []
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers.append((key.strip(), value.strip()))
    return status, reason, headers


class CurlEngine:
    name = "curl"

    def send(self, request: Request) -> Response:
        host = urlsplit(ensure_scheme(request.url)).netloc or request.url
        body_fd, body_path = tempfile.mkstemp(prefix="hitman-body-")
        head_fd, head_path = tempfile.mkstemp(prefix="hitman-head-")
        os.close(body_fd)
        os.close(head_fd)

        argv = to_argv(request, for_execution=True)
        # -w %{json} puts a machine-readable summary on stdout while the body
        # and headers go to files, so nothing needs to be scraped from text.
        argv[1:1] = ["-s", "-S", "-o", body_path, "-D", head_path, "-w", "%{json}"]

        started = time.perf_counter()
        try:
            try:
                completed = subprocess.run(
                    argv, capture_output=True, timeout=request.timeout + 5
                )
            except FileNotFoundError:
                return Response(
                    engine=self.name,
                    error="The curl binary was not found on this system.",
                )
            except subprocess.TimeoutExpired:
                return Response(
                    engine=self.name,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    error=f"Timed out after {_format_timeout(request.timeout)}s.",
                    curl_exit_code=28,
                )

            try:
                stats = json.loads(completed.stdout or b"{}")
            except (ValueError, UnicodeDecodeError):
                stats = {}

            elapsed_ms = float(stats.get("time_total") or 0) * 1000 or (
                time.perf_counter() - started
            ) * 1000

            if completed.returncode != 0:
                template = _FRIENDLY.get(completed.returncode)
                if template:
                    error = template.format(
                        host=host, timeout=_format_timeout(request.timeout)
                    )
                else:
                    detail = (
                        stats.get("errormsg")
                        or completed.stderr.decode("utf-8", "replace").strip()
                        or "no further detail"
                    )
                    error = f"curl failed (exit {completed.returncode}): {detail}"
                return Response(
                    engine=self.name,
                    elapsed_ms=elapsed_ms,
                    error=error,
                    curl_exit_code=completed.returncode,
                )

            with open(head_path, "rb") as handle:
                status, reason, headers = _parse_header_dump(
                    handle.read().decode("utf-8", "replace")
                )
            with open(body_path, "rb") as handle:
                raw_body = handle.read()

            content_type = stats.get("content_type") or ""
            for key, value in headers:
                if key.lower() == "content-type":
                    content_type = value
                    break

            body, truncated, size = decode_body(raw_body, content_type)
            return Response(
                engine=self.name,
                status=status if status is not None else stats.get("response_code") or None,
                reason=reason,
                headers=headers,
                body=body,
                body_truncated=truncated,
                size_bytes=size,
                elapsed_ms=elapsed_ms,
                content_type=content_type,
                curl_exit_code=0,
            )
        finally:
            for path in (body_path, head_path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_curl_engine.py tests/core/test_engine_parity.py -v`
Expected: PASS, 11 + 14 tests (7 parity tests × 2 engines)

- [ ] **Step 5: Run the whole core suite**

Run: `uv run pytest tests/core -v`
Expected: PASS. The core package is now complete and has no web dependency.

- [ ] **Step 6: Commit**

```bash
git add hitman/core/engines/curl_engine.py tests/core/test_curl_engine.py tests/core/test_engine_parity.py
git commit -m "feat: add curl subprocess engine with parity tests"
```

---

### Task 7: Web shell — app, templates, static assets, index page

**Files:**
- Create: `hitman/web/app.py`, `hitman/web/forms.py`, `hitman/web/routes.py`
- Create: `hitman/web/templates/base.html`, `index.html`, `fragments/_macros.html`, `fragments/builder.html`, `fragments/sidebar.html`, `fragments/response.html`
- Create: `hitman/web/static/app.css`, `hitman/web/static/app.js`
- Create: `tests/web/conftest.py`, `tests/web/test_index.py`

**Interfaces:**
- Consumes: `hitman.core.store.Store`, `hitman.core.engines.curl_engine.curl_available`, `hitman.core.models`.
- Produces: `create_app(db_path=None) -> FastAPI` with `app.state.store`, `app.state.templates`, `app.state.curl_available`; `request_from_form(form) -> Request`; `router` in `routes.py`; pytest fixtures `app` and `client`.

There is no front-end framework. `app.js` implements a small declarative
fetch/swap layer — about 50 lines — that covers everything this UI needs:

| Attribute | Meaning |
|---|---|
| `data-url` | endpoint to call (its presence is what makes an element a trigger) |
| `data-action` | HTTP method, default `get` |
| `data-target` | selector whose `innerHTML` is replaced with the reply |
| `data-form` | selector of a form to serialise into the request body |
| `data-vals` | JSON object of extra fields to add to the body |
| `data-confirm` | text to confirm before firing |

A returned fragment may contain `<div data-oob="#selector">…</div>` elements.
Their contents are swapped into that selector, and the remainder goes to
`data-target` — the same idea as an out-of-band swap. Because every trigger
is bound by one delegated listener on `document`, swapped-in markup is live
immediately with no re-initialisation step.

- [ ] **Step 1: Write the failing tests**

`tests/web/conftest.py`:

```python
import pytest
from fastapi.testclient import TestClient

from hitman.web.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "test.db")


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def base_form():
    """The fields the request form always submits."""
    return {
        "method": "GET",
        "url": "",
        "body_type": "none",
        "body": "",
        "timeout": "30",
        "follow_redirects": "1",
        "verify_tls": "1",
        "engine": "httpx",
    }
```

`tests/web/test_index.py`:

```python
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
    form = {
        "param_key": ["", "a"],
        "param_value": ["", "1"],
        "param_enabled": ["1", "1"],
    }
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/web -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hitman.web.app'`

- [ ] **Step 3: Implement the form translator**

`hitman/web/forms.py`:

```python
"""Translate the browser form payload into a :class:`Request`."""

from __future__ import annotations

from hitman.core.models import BODY_TYPES, DEFAULT_TIMEOUT, KeyValue, Request

MIN_TIMEOUT = 0.1
MAX_TIMEOUT = 600.0


def _rows(form, prefix: str) -> list[KeyValue]:
    """Read the parallel key/value/enabled arrays one table submits.

    The three arrays stay aligned because the template always emits all
    three inputs per row — including a hidden ``_enabled`` field, since an
    unchecked checkbox submits nothing and would shift the array.
    """
    keys = form.getlist(f"{prefix}_key")
    values = form.getlist(f"{prefix}_value")
    flags = form.getlist(f"{prefix}_enabled")

    rows = []
    for index, key in enumerate(keys):
        value = values[index] if index < len(values) else ""
        flag = flags[index] if index < len(flags) else "1"
        if not key.strip() and not str(value).strip():
            continue
        rows.append(KeyValue(key.strip(), str(value), flag == "1"))
    return rows


def request_from_form(form) -> Request:
    body_type = str(form.get("body_type") or "none")
    if body_type not in BODY_TYPES:
        body_type = "none"

    try:
        timeout = float(form.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    return Request(
        method=str(form.get("method") or "GET").upper(),
        url=str(form.get("url") or "").strip(),
        params=_rows(form, "param"),
        headers=_rows(form, "header"),
        body_type=body_type,
        body=str(form.get("body") or ""),
        form_fields=_rows(form, "field"),
        follow_redirects=form.get("follow_redirects") == "1",
        verify_tls=form.get("verify_tls") == "1",
        timeout=max(MIN_TIMEOUT, min(timeout, MAX_TIMEOUT)),
    )
```

- [ ] **Step 4: Implement the app factory**

`hitman/web/app.py`:

```python
"""FastAPI application factory."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from hitman.core.engines.curl_engine import curl_available
from hitman.core.store import Store

BASE_DIR = Path(__file__).parent
DEFAULT_DB = os.environ.get("HITMAN_DB", "data/hitman.db")


def create_app(db_path: str | Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.store.close()

    app = FastAPI(title="Hitman", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.store = Store(db_path or DEFAULT_DB)
    # Jinja2Templates enables autoescape for .html by default. Response bodies
    # are attacker-controlled; do not turn it off.
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    app.state.curl_available = curl_available()
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    from hitman.web import routes  # imported here to avoid a circular import

    app.include_router(routes.router)
    return app
```

- [ ] **Step 5: Implement routes (index and health only for now)**

`hitman/web/routes.py`:

```python
"""Fragment endpoints. Every response is an HTML fragment except /export-curl."""

from __future__ import annotations

from fastapi import APIRouter, Request as HttpRequest
from fastapi.responses import HTMLResponse

from hitman.core.models import Request

router = APIRouter()


def render(http_request: HttpRequest, template: str, context: dict) -> HTMLResponse:
    store = http_request.app.state.store
    full = {
        "curl_available": http_request.app.state.curl_available,
        "saved": store.list_requests(),
        "history": store.list_history(50),
        **context,
    }
    # Request-first signature: passing the context dict alone is deprecated
    # in the Starlette version FastAPI >= 0.115 depends on.
    return http_request.app.state.templates.TemplateResponse(http_request, template, full)


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
def index(http_request: HttpRequest):
    return render(http_request, "index.html", {"req": Request(), "warnings": []})
```

Note the context key is `req`, not `request`: Starlette injects the HTTP
request under `request`, and shadowing it breaks `url_for` inside templates.

- [ ] **Step 6: Write the templates**

`hitman/web/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hitman — API testing</title>
  <link rel="stylesheet" href="/static/app.css">
  <script src="/static/app.js" defer></script>
</head>
<body>
{% block content %}{% endblock %}
<div id="toast" hidden></div>
</body>
</html>
```

`hitman/web/templates/fragments/_macros.html`:

```html
{% macro kv_table(prefix, rows, key_hint, value_hint) %}
<div class="kv" data-prefix="{{ prefix }}">
  <div class="rows">
    {% for row in rows %}
    <div class="row">
      <input type="hidden" name="{{ prefix }}_enabled" value="{{ '1' if row.enabled else '0' }}">
      <input type="checkbox" class="toggle" {% if row.enabled %}checked{% endif %} aria-label="Enabled">
      <input type="text" name="{{ prefix }}_key" value="{{ row.key }}" placeholder="{{ key_hint }}">
      <input type="text" name="{{ prefix }}_value" value="{{ row.value }}" placeholder="{{ value_hint }}">
      <button type="button" class="remove" aria-label="Remove row">&times;</button>
    </div>
    {% endfor %}
  </div>
  <button type="button" class="add-row">+ Add</button>
  <template class="row-template">
    <div class="row">
      <input type="hidden" name="{{ prefix }}_enabled" value="1">
      <input type="checkbox" class="toggle" checked aria-label="Enabled">
      <input type="text" name="{{ prefix }}_key" placeholder="{{ key_hint }}">
      <input type="text" name="{{ prefix }}_value" placeholder="{{ value_hint }}">
      <button type="button" class="remove" aria-label="Remove row">&times;</button>
    </div>
  </template>
</div>
{% endmacro %}
```

`hitman/web/templates/index.html`:

```html
{% extends "base.html" %}
{% block content %}
<header class="topbar">
  <h1>Hitman</h1>
  <div class="toolbar">
    <button type="button" id="import-curl-open">Import curl</button>
    <button type="button" id="copy-curl">Copy as curl</button>
  </div>
</header>

<main class="layout">
  <aside id="sidebar">{% include "fragments/sidebar.html" %}</aside>
  <section class="centre">
    <div id="builder">{% include "fragments/builder.html" %}</div>
    <div id="response">
      <p class="empty">No response yet. Fill in a URL and press Send.</p>
    </div>
  </section>
</main>

<dialog id="import-dialog">
  <form method="dialog">
    <h2>Import a curl command</h2>
    <textarea id="curl-text" rows="8" placeholder="curl https://api.example.com/users -H 'Accept: application/json'"></textarea>
    <menu>
      <button value="cancel">Cancel</button>
      <button type="button" id="import-curl-submit">Import</button>
    </menu>
  </form>
</dialog>
{% endblock %}
```

`hitman/web/templates/fragments/builder.html`:

```html
{% from "fragments/_macros.html" import kv_table %}
{% if warnings %}
<ul class="warnings">
  {% for warning in warnings %}<li>{{ warning }}</li>{% endfor %}
</ul>
{% endif %}
<form id="request-form">
  <div class="urlbar">
    <select name="method" aria-label="HTTP method">
      {% for verb in ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] %}
      <option value="{{ verb }}" {% if req.method == verb %}selected{% endif %}>{{ verb }}</option>
      {% endfor %}
    </select>
    <input type="text" name="url" value="{{ req.url }}" placeholder="http://localhost:3000/api/users" autocomplete="off">
    <button type="button" class="primary" data-action="post" data-url="/send"
            data-form="#request-form" data-vals='{"engine": "httpx"}'
            data-target="#response">Send</button>
    <button type="button" data-action="post" data-url="/send"
            data-form="#request-form" data-vals='{"engine": "curl"}'
            data-target="#response"
            {% if not curl_available %}disabled title="The curl binary was not found on this system"{% endif %}>
      Send with curl
    </button>
  </div>

  <nav class="tabs" data-tabs="builder">
    <button type="button" data-tab="params" class="active">Params</button>
    <button type="button" data-tab="headers">Headers</button>
    <button type="button" data-tab="body">Body</button>
    <button type="button" data-tab="options">
      Options{% if not req.follow_redirects or not req.verify_tls or req.timeout != 30.0 %} <span class="dot">&bull;</span>{% endif %}
    </button>
  </nav>

  <div data-panel="params" class="panel">{{ kv_table("param", req.params, "name", "value") }}</div>
  <div data-panel="headers" class="panel" hidden>{{ kv_table("header", req.headers, "Header", "Value") }}</div>

  <div data-panel="body" class="panel" hidden>
    <select name="body_type" id="body-type">
      {% for kind in ["none", "json", "raw", "form"] %}
      <option value="{{ kind }}" {% if req.body_type == kind %}selected{% endif %}>{{ kind }}</option>
      {% endfor %}
    </select>
    <button type="button" id="format-json">Format JSON</button>
    <div id="body-text" {% if req.body_type in ("none", "form") %}hidden{% endif %}>
      <textarea name="body" rows="12" spellcheck="false">{{ req.body }}</textarea>
    </div>
    <div id="body-form" {% if req.body_type != "form" %}hidden{% endif %}>
      {{ kv_table("field", req.form_fields, "field", "value") }}
    </div>
  </div>

  <div data-panel="options" class="panel" hidden>
    <label>
      <input type="hidden" name="follow_redirects" value="{{ '1' if req.follow_redirects else '0' }}">
      <input type="checkbox" class="toggle" {% if req.follow_redirects %}checked{% endif %}> Follow redirects
    </label>
    <label>
      <input type="hidden" name="verify_tls" value="{{ '1' if req.verify_tls else '0' }}">
      <input type="checkbox" class="toggle" {% if req.verify_tls %}checked{% endif %}> Verify TLS certificates
    </label>
    <label>Timeout (seconds)
      <input type="number" name="timeout" value="{{ req.timeout }}" min="0.1" max="600" step="0.1">
    </label>
  </div>

  <div class="save-row">
    <input type="text" id="save-name" name="save_name" placeholder="Name this request" autocomplete="off">
    <button type="button" id="save-request" data-action="post" data-url="/requests"
            data-form="#request-form" data-target="#sidebar">Save</button>
  </div>
</form>
```

`hitman/web/templates/fragments/sidebar.html`:

```html
<nav class="tabs" data-tabs="sidebar">
  <button type="button" data-tab="history" class="active">History</button>
  <button type="button" data-tab="saved">Saved</button>
</nav>

<div data-panel="history" class="panel list">
  {% for entry in history %}
  <button type="button" class="entry" data-action="get" data-url="/history/{{ entry.id }}" data-target="#builder">
    <span class="verb">{{ entry.request.method }}</span>
    <span class="url">{{ entry.request.url }}</span>
    {% if entry.response.error %}
    <span class="badge err">ERR</span>
    {% else %}
    <span class="badge s{{ (entry.response.status or 0) // 100 }}">{{ entry.response.status }}</span>
    <span class="ms">{{ entry.response.elapsed_ms | round | int }}ms</span>
    {% endif %}
  </button>
  {% else %}
  <p class="empty">Nothing sent yet.</p>
  {% endfor %}
  {% if history %}
  <button type="button" class="danger" data-action="delete" data-url="/history"
          data-target="#sidebar" data-confirm="Clear all history?">Clear history</button>
  {% endif %}
</div>

<div data-panel="saved" class="panel list" hidden>
  {% for item in saved %}
  <div class="entry-row">
    <button type="button" class="entry" data-action="get" data-url="/requests/{{ item.id }}" data-target="#builder">
      <span class="verb">{{ item.request.method }}</span>
      <span class="name">{{ item.name }}</span>
    </button>
    <button type="button" class="delete" data-action="delete" data-url="/requests/{{ item.id }}"
            data-target="#sidebar" aria-label="Delete {{ item.name }}">&times;</button>
  </div>
  {% else %}
  <p class="empty">No saved requests.</p>
  {% endfor %}
</div>
```

`hitman/web/templates/fragments/response.html`:

```html
{% if response.error %}
<div class="status-line error">
  <span class="badge err">Failed</span>
  <span class="message">{{ response.error }}</span>
  {% if response.curl_exit_code %}<span class="ms">curl exit {{ response.curl_exit_code }}</span>{% endif %}
  <span class="ms">{{ response.elapsed_ms | round | int }}ms</span>
  <span class="engine">via {{ response.engine }}</span>
</div>
{% else %}
<div class="status-line">
  <span class="badge s{{ (response.status or 0) // 100 }}">{{ response.status }} {{ response.reason }}</span>
  <span class="ms">{{ response.elapsed_ms | round | int }}ms</span>
  <span class="size">{{ response.size_bytes }} B</span>
  <span class="engine">via {{ response.engine }}</span>
  {% if response.body_truncated %}<span class="warn">body truncated for display</span>{% endif %}
</div>

<nav class="tabs" data-tabs="response">
  <button type="button" data-tab="pretty" class="active">Pretty</button>
  <button type="button" data-tab="raw">Raw</button>
  <button type="button" data-tab="resp-headers">Headers ({{ response.headers | length }})</button>
</nav>

<div data-panel="pretty" class="panel"><pre>{{ pretty }}</pre></div>
<div data-panel="raw" class="panel" hidden><pre>{{ response.body }}</pre></div>
<div data-panel="resp-headers" class="panel" hidden>
  <table class="headers">
    {% for key, value in response.headers %}
    <tr><th>{{ key }}</th><td>{{ value }}</td></tr>
    {% endfor %}
  </table>
</div>
{% endif %}
```

This fragment deliberately contains **no** out-of-band marker. Task 8 wraps
it for the send route and Task 10 wraps it for history replay; a fragment
that carried its own `data-oob` would fight whichever wrapper included it.

`{{ pretty }}` and `{{ response.body }}` are autoescaped by Jinja2 and sit inside `<pre>`, which is exactly the rule from the Global Constraints. Never change these to `| safe`.

- [ ] **Step 7: Write the static assets**

`hitman/web/static/app.css`:

```css
:root {
  --bg: #16181d; --panel: #1e2127; --line: #2c313a; --text: #e6e8eb;
  --muted: #9aa4b2; --accent: #4c8dff; --ok: #3fb950; --warn: #d29922; --err: #f85149;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font-size: 14px; }
.topbar { display: flex; justify-content: space-between; align-items: center;
  padding: 10px 16px; border-bottom: 1px solid var(--line); }
.topbar h1 { font-size: 16px; margin: 0; letter-spacing: .04em; }
.layout { display: grid; grid-template-columns: 280px 1fr; height: calc(100vh - 49px); }
#sidebar { border-right: 1px solid var(--line); overflow-y: auto; padding: 8px; }
.centre { display: grid; grid-template-rows: auto 1fr; overflow: hidden; }
#builder { padding: 12px; border-bottom: 1px solid var(--line); overflow-y: auto; max-height: 55vh; }
#response { padding: 12px; overflow-y: auto; }
.urlbar { display: flex; gap: 8px; margin-bottom: 10px; }
.urlbar input[name="url"] { flex: 1; }
input, select, textarea, button { font: inherit; color: var(--text);
  background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 7px 9px; }
textarea { width: 100%; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
button { cursor: pointer; }
button:disabled { opacity: .45; cursor: not-allowed; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.danger { color: var(--err); }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); margin: 10px 0; }
.tabs button { background: none; border: none; border-bottom: 2px solid transparent;
  border-radius: 0; color: var(--muted); }
.tabs button.active { color: var(--text); border-bottom-color: var(--accent); }
.panel { padding: 4px 0; }
.row { display: grid; grid-template-columns: auto 1fr 1fr auto; gap: 6px; margin-bottom: 6px; align-items: center; }
.kv .add-row { margin-top: 4px; }
.status-line { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.badge { padding: 2px 8px; border-radius: 999px; font-weight: 600; background: var(--line); }
.badge.s2 { background: rgba(63,185,80,.18); color: var(--ok); }
.badge.s3 { background: rgba(210,153,34,.18); color: var(--warn); }
.badge.s4, .badge.s5, .badge.err { background: rgba(248,81,73,.18); color: var(--err); }
.ms, .size, .engine, .muted { color: var(--muted); }
.warn { color: var(--warn); }
pre { background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
  padding: 10px; overflow-x: auto; white-space: pre-wrap; word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.headers { width: 100%; border-collapse: collapse; }
.headers th { text-align: left; color: var(--muted); font-weight: 500;
  padding: 4px 10px 4px 0; vertical-align: top; white-space: nowrap; }
.headers td { padding: 4px 0; word-break: break-all; }
.list .entry { display: flex; gap: 8px; align-items: center; width: 100%;
  text-align: left; background: none; border: none; padding: 6px; border-radius: 6px; }
.list .entry:hover { background: var(--panel); }
.entry-row { display: flex; align-items: center; }
.entry-row .delete { background: none; border: none; color: var(--muted); padding: 6px 8px; }
.entry-row .delete:hover { color: var(--err); }
.entry .verb { color: var(--accent); font-weight: 600; font-size: 11px; min-width: 46px; }
.entry .url, .entry .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty { color: var(--muted); padding: 8px; }
.warnings { background: rgba(210,153,34,.12); border: 1px solid var(--warn);
  border-radius: 6px; padding: 8px 8px 8px 26px; margin: 0 0 10px; color: var(--warn); }
.dot { color: var(--accent); }
#toast { position: fixed; bottom: 20px; right: 20px; background: var(--panel);
  border: 1px solid var(--line); border-radius: 6px; padding: 10px 14px; }
dialog { background: var(--panel); color: var(--text); border: 1px solid var(--line);
  border-radius: 10px; width: min(680px, 90vw); }
dialog textarea { margin: 10px 0; }
dialog menu { display: flex; gap: 8px; justify-content: flex-end; padding: 0; }
```

`hitman/web/static/app.js`:

```js
// A small declarative fetch/swap layer. This is the whole front-end
// framework: no dependencies, no build step, no third-party code.
//
//   data-url      endpoint to call (its presence makes an element a trigger)
//   data-action   HTTP method, default "get"
//   data-target   selector whose innerHTML is replaced with the reply
//   data-form     selector of a form to serialise into the request body
//   data-vals     JSON object of extra body fields
//   data-confirm  text to confirm before firing
//
// A reply may contain <div data-oob="#selector">...</div> elements; their
// contents go to that selector and the rest goes to data-target.

function swap(html, targetSelector) {
  const holder = document.createElement('div');
  holder.innerHTML = html;

  holder.querySelectorAll('[data-oob]').forEach((piece) => {
    const destination = document.querySelector(piece.dataset.oob);
    if (destination) destination.innerHTML = piece.innerHTML;
    piece.remove();
  });

  if (targetSelector) {
    const target = document.querySelector(targetSelector);
    if (target) target.innerHTML = holder.innerHTML;
  }
}

async function fire(trigger) {
  if (trigger.dataset.confirm && !window.confirm(trigger.dataset.confirm)) return;

  const method = (trigger.dataset.action || 'get').toUpperCase();
  const options = { method };

  if (method !== 'GET' && method !== 'DELETE') {
    const form = trigger.dataset.form && document.querySelector(trigger.dataset.form);
    const body = form ? new FormData(form) : new FormData();
    if (trigger.dataset.vals) {
      for (const [key, value] of Object.entries(JSON.parse(trigger.dataset.vals))) {
        body.set(key, value);
      }
    }
    options.body = body;
  }

  trigger.disabled = true;
  try {
    const reply = await fetch(trigger.dataset.url, options);
    const text = await reply.text();
    if (!reply.ok) {
      toast(text);
      return;
    }
    swap(text, trigger.dataset.target);
  } catch (error) {
    toast('Request failed: ' + error.message);
  } finally {
    trigger.disabled = false;
  }
}

// Keep the hidden "_enabled" field in step with its checkbox. An unchecked
// checkbox submits nothing, which would shift the parallel arrays the server
// reads, so the hidden field carries the real value.
document.addEventListener('change', (event) => {
  const box = event.target;
  if (box.classList.contains('toggle')) {
    box.previousElementSibling.value = box.checked ? '1' : '0';
  }
  if (box.id === 'body-type') {
    document.getElementById('body-text').hidden = box.value === 'none' || box.value === 'form';
    document.getElementById('body-form').hidden = box.value !== 'form';
  }
});

document.addEventListener('click', (event) => {
  const target = event.target;

  // Checked first: every fetch/swap trigger is identified by data-url, and
  // one delegated listener means swapped-in markup is live immediately.
  const trigger = target.closest('[data-url]');
  if (trigger) {
    event.preventDefault();
    fire(trigger);
    return;
  }

  if (target.dataset.tab) {
    const bar = target.closest('.tabs');
    bar.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
    target.classList.add('active');
    const scope = bar.parentElement;
    scope.querySelectorAll(':scope > [data-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.panel !== target.dataset.tab;
    });
    return;
  }

  if (target.classList.contains('add-row')) {
    const table = target.closest('.kv');
    table.querySelector('.rows').appendChild(
      table.querySelector('.row-template').content.cloneNode(true)
    );
    return;
  }

  if (target.classList.contains('remove') && target.closest('.row')) {
    target.closest('.row').remove();
    return;
  }

  if (target.id === 'import-curl-open') {
    document.getElementById('import-dialog').showModal();
  }

  if (target.id === 'format-json') {
    const area = document.querySelector('textarea[name="body"]');
    try {
      area.value = JSON.stringify(JSON.parse(area.value), null, 2);
    } catch (error) {
      toast('Not valid JSON: ' + error.message);
    }
  }

  if (target.id === 'copy-curl') copyAsCurl();
  if (target.id === 'import-curl-submit') importCurl();
});

async function copyAsCurl() {
  const body = new FormData(document.getElementById('request-form'));
  const reply = await fetch('/export-curl', { method: 'POST', body });
  const command = await reply.text();
  // navigator.clipboard needs a secure context; http://localhost qualifies.
  await navigator.clipboard.writeText(command);
  toast('curl command copied');
}

async function importCurl() {
  const body = new FormData();
  body.append('text', document.getElementById('curl-text').value);
  const reply = await fetch('/import-curl', { method: 'POST', body });
  const text = await reply.text();
  if (!reply.ok) {
    // Spec: a bad paste must leave the existing form untouched.
    toast(text);
    return;
  }
  swap(text, '#builder');
  document.getElementById('import-dialog').close();
}

let toastTimer;
function toast(message) {
  const element = document.getElementById('toast');
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 2500);
}
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/web -v`
Expected: PASS, 7 tests

- [ ] **Step 9: Commit**

```bash
git add hitman/web tests/web
git commit -m "feat: add web shell with request builder UI"
```

---

### Task 8: Send route

**Files:**
- Modify: `hitman/web/routes.py` (add `/send` and the `pretty_body` helper)
- Create: `tests/web/test_send.py`

**Interfaces:**
- Consumes: `request_from_form`, `HttpxEngine`, `CurlEngine`, `Store.add_history`, `fragments/response.html`.
- Produces: `POST /send` returning the response fragment plus an out-of-band `#sidebar` swap; `pretty_body(response) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_send.py`:

```python
import json


def send(client, base_form, **overrides):
    return client.post("/send", data={**base_form, **overrides})


def test_send_renders_status_and_body(client, base_form, fixture_server):
    reply = send(client, base_form, url=f"{fixture_server}/json")
    assert reply.status_code == 200
    assert "200" in reply.text
    assert "hello" in reply.text


def test_send_pretty_prints_json(client, base_form, fixture_server):
    reply = send(client, base_form, url=f"{fixture_server}/json")
    assert '&#34;hello&#34;: &#34;world&#34;' in reply.text or '"hello": "world"' in reply.text


def test_send_writes_history(client, app, base_form, fixture_server):
    send(client, base_form, url=f"{fixture_server}/json")
    entries = app.state.store.list_history()
    assert len(entries) == 1
    assert entries[0].response.status == 200


def test_send_refreshes_the_sidebar_out_of_band(client, base_form, fixture_server):
    reply = send(client, base_form, url=f"{fixture_server}/json")
    assert 'data-oob="#sidebar"' in reply.text


def test_failed_send_shows_a_friendly_message_and_is_recorded(
    client, app, base_form, closed_port
):
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


def test_non_json_body_pretty_falls_back_to_raw(client, base_form, fixture_server):
    reply = send(client, base_form, url=f"{fixture_server}/html")
    assert reply.status_code == 200


def test_post_with_json_body(client, base_form, fixture_server):
    reply = send(
        client, base_form, method="POST", url=f"{fixture_server}/echo",
        body_type="json", body='{"a": 1}',
    )
    assert "application/json" in reply.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/web/test_send.py -v`
Expected: FAIL — 404, because `/send` does not exist yet

- [ ] **Step 3: Add the route**

Append to `hitman/web/routes.py`. Merge these into the **existing** import
block at the top of the file, and do **not** re-declare `router` — the
decorators below attach to the one Task 7 created. `Request`, `HttpRequest`
and `HTMLResponse` are already imported there.

```python
import json

from starlette.concurrency import run_in_threadpool

from hitman.core.engines.curl_engine import CurlEngine
from hitman.core.engines.httpx_engine import HttpxEngine
from hitman.core.models import Response
from hitman.web.forms import request_from_form


def pretty_body(response: Response) -> str:
    """Indent JSON when it is JSON; otherwise show the body unchanged."""
    body = response.body
    if not body.strip():
        return body
    try:
        return json.dumps(json.loads(body), indent=2, ensure_ascii=False)
    except (ValueError, TypeError):
        return body


def _engine(name: str):
    return CurlEngine() if name == "curl" else HttpxEngine()


@router.post("/send", response_class=HTMLResponse)
async def send(http_request: HttpRequest):
    form = await http_request.form()
    outgoing = request_from_form(form)
    engine = _engine(str(form.get("engine") or "httpx"))

    if not outgoing.url:
        response = Response(engine=engine.name, error="Enter a URL first.")
    else:
        # engine.send blocks on the network; keep the event loop free.
        response = await run_in_threadpool(engine.send, outgoing)
        http_request.app.state.store.add_history(outgoing, response)

    return render(
        http_request,
        "fragments/response_with_sidebar.html",
        {"req": outgoing, "response": response, "pretty": pretty_body(response)},
    )
```

- [ ] **Step 4: Add the wrapper template**

`hitman/web/templates/fragments/response_with_sidebar.html`:

```html
{% include "fragments/response.html" %}
<div data-oob="#sidebar">{% include "fragments/sidebar.html" %}</div>
```

Sending is the only action that changes history, so it is the only one that
needs to refresh the sidebar out of band.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/web/test_send.py -v`
Expected: PASS, 10 tests

- [ ] **Step 6: Commit**

```bash
git add hitman/web/routes.py hitman/web/templates/fragments/response_with_sidebar.html tests/web/test_send.py
git commit -m "feat: send requests and render the response pane"
```

---

### Task 9: Import and export curl over HTTP

**Files:**
- Modify: `hitman/web/routes.py`
- Create: `tests/web/test_curl_routes.py`

**Interfaces:**
- Consumes: `hitman.core.curl_import.parse_curl`, `CurlParseError`, `hitman.core.curl_export.to_command`, `request_from_form`.
- Produces: `POST /import-curl` (form field `text`) returning the prefilled builder fragment, or **HTTP 422 with a plain-text message** when the paste is malformed; `POST /export-curl` returning `text/plain`.

The 422 matters: `app.js` only replaces the builder when the reply is `ok`, which is how the spec's "form left untouched on a bad paste" is actually delivered. Returning a fresh empty builder with an error banner would wipe whatever the user had typed.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_curl_routes.py`:

```python
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
    # Nothing that would overwrite the user's current form.
    assert "request-form" not in reply.text


def test_import_rejects_empty_input(client):
    assert client.post("/import-curl", data={"text": "   "}).status_code == 422


def test_export_returns_a_plain_text_curl_command(client, base_form):
    reply = client.post(
        "/export-curl", data={**base_form, "url": "http://localhost:3000/api"}
    )
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/plain")
    assert reply.text.startswith("curl ")
    assert "http://localhost:3000/api" in reply.text


def test_export_includes_headers_and_body(client, base_form):
    reply = client.post(
        "/export-curl",
        data={
            **base_form,
            "method": "POST",
            "url": "http://localhost:3000/api",
            "body_type": "json",
            "body": '{"a": 1}',
            "header_key": ["X-Key"],
            "header_value": ["abc"],
            "header_enabled": ["1"],
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/web/test_curl_routes.py -v`
Expected: FAIL — 404 on both routes

- [ ] **Step 3: Add the routes**

Append to `hitman/web/routes.py` (extend the imports):

```python
from fastapi.responses import PlainTextResponse

from hitman.core.curl_export import to_command
from hitman.core.curl_import import CurlParseError, parse_curl


@router.post("/import-curl", response_class=HTMLResponse)
async def import_curl(http_request: HttpRequest):
    form = await http_request.form()
    try:
        parsed = parse_curl(str(form.get("text") or ""))
    except CurlParseError as exc:
        # 422 rather than an error-banner fragment: app.js only swaps the
        # builder on a 2xx, so the user's current form survives a bad paste.
        return PlainTextResponse(f"Could not import: {exc}", status_code=422)

    return render(
        http_request,
        "fragments/builder.html",
        {"req": parsed.request, "warnings": parsed.warnings},
    )


@router.post("/export-curl", response_class=PlainTextResponse)
async def export_curl(http_request: HttpRequest):
    form = await http_request.form()
    return PlainTextResponse(to_command(request_from_form(form)))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/web/test_curl_routes.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add hitman/web/routes.py tests/web/test_curl_routes.py
git commit -m "feat: import and export curl commands from the UI"
```

---

### Task 10: Saved requests and history replay

**Files:**
- Modify: `hitman/web/routes.py`
- Create: `hitman/web/templates/fragments/replay.html`
- Create: `tests/web/test_library.py`

**Interfaces:**
- Consumes: `Store` CRUD methods, `pretty_body`, `fragments/builder.html`, `fragments/sidebar.html`, `fragments/response.html`.
- Produces: `POST /requests` (form field `save_name` plus the builder fields), `GET /requests`, `GET /requests/{id}`, `PUT /requests/{id}`, `DELETE /requests/{id}`, `GET /history`, `GET /history/{id}`, `DELETE /history`.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_library.py`:

```python
from hitman.core.models import Request, Response


def make_saved(app, name="Get users"):
    return app.state.store.save_request(name, Request(url="http://localhost:3000/users"))


def test_save_returns_the_sidebar_with_the_new_entry(client, base_form):
    reply = client.post(
        "/requests",
        data={**base_form, "url": "http://localhost:3000/users", "save_name": "Get users"},
    )
    assert reply.status_code == 200
    assert "Get users" in reply.text


def test_save_without_a_name_uses_the_url(client, base_form):
    reply = client.post(
        "/requests", data={**base_form, "url": "http://localhost:3000/users", "save_name": ""}
    )
    assert "http://localhost:3000/users" in reply.text


def test_saved_request_loads_into_the_builder(client, app):
    saved_id = make_saved(app)
    reply = client.get(f"/requests/{saved_id}")
    assert reply.status_code == 200
    assert 'value="http://localhost:3000/users"' in reply.text
    assert 'id="request-form"' in reply.text


def test_loading_an_unknown_saved_request_is_a_404(client):
    assert client.get("/requests/9999").status_code == 404


def test_update_replaces_the_saved_request(client, app, base_form):
    saved_id = make_saved(app)
    client.put(
        f"/requests/{saved_id}",
        data={**base_form, "method": "POST", "url": "http://x.test/", "save_name": "Renamed"},
    )
    loaded = app.state.store.get_request(saved_id)
    assert loaded.name == "Renamed"
    assert loaded.request.method == "POST"


def test_delete_removes_it_from_the_sidebar(client, app):
    saved_id = make_saved(app)
    reply = client.delete(f"/requests/{saved_id}")
    assert "Get users" not in reply.text
    assert app.state.store.get_request(saved_id) is None


def test_history_entry_reloads_request_and_response(client, app):
    entry_id = app.state.store.add_history(
        Request(method="POST", url="http://localhost:3000/users"),
        Response(engine="httpx", status=201, reason="Created", body='{"id": 1}'),
    )
    reply = client.get(f"/history/{entry_id}")
    assert reply.status_code == 200
    assert 'value="http://localhost:3000/users"' in reply.text
    assert '<option value="POST" selected>' in reply.text
    # The response comes back too, swapped out of band into #response.
    assert 'data-oob="#response"' in reply.text
    assert "201" in reply.text


def test_replay_of_a_failed_send_shows_the_error(client, app):
    entry_id = app.state.store.add_history(
        Request(url="http://127.0.0.1:1/"),
        Response(engine="curl", error="Connection refused", curl_exit_code=7),
    )
    reply = client.get(f"/history/{entry_id}")
    assert "Connection refused" in reply.text


def test_unknown_history_entry_is_a_404(client):
    assert client.get("/history/9999").status_code == 404


def test_clear_history_empties_the_sidebar(client, app):
    app.state.store.add_history(Request(url="http://x.test/"), Response(engine="httpx", status=200))
    reply = client.delete("/history")
    assert "Nothing sent yet." in reply.text
    assert app.state.store.list_history() == []


def test_get_requests_renders_the_sidebar(client, app):
    make_saved(app, "Get users")
    reply = client.get("/requests")
    assert reply.status_code == 200
    assert "Get users" in reply.text


def test_get_history_renders_the_sidebar(client, app):
    app.state.store.add_history(
        Request(url="http://x.test/one"), Response(engine="httpx", status=200)
    )
    reply = client.get("/history")
    assert reply.status_code == 200
    assert "http://x.test/one" in reply.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/web/test_library.py -v`
Expected: FAIL — 404 on every new route

- [ ] **Step 3: Add the replay template**

`hitman/web/templates/fragments/replay.html`:

```html
{% include "fragments/builder.html" %}
<div data-oob="#response">{% include "fragments/response.html" %}</div>
```

- [ ] **Step 4: Add the routes**

Append to `hitman/web/routes.py` (extend the imports with `HTTPException`):

```python
from fastapi import HTTPException


@router.post("/requests", response_class=HTMLResponse)
async def save_request(http_request: HttpRequest):
    form = await http_request.form()
    outgoing = request_from_form(form)
    name = str(form.get("save_name") or "").strip() or outgoing.url or "Untitled"
    http_request.app.state.store.save_request(name, outgoing)
    return render(http_request, "fragments/sidebar.html", {"req": outgoing})


@router.get("/requests/{request_id}", response_class=HTMLResponse)
def load_request(request_id: int, http_request: HttpRequest):
    saved = http_request.app.state.store.get_request(request_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved request not found")
    return render(http_request, "fragments/builder.html", {"req": saved.request, "warnings": []})


@router.put("/requests/{request_id}", response_class=HTMLResponse)
async def update_request(request_id: int, http_request: HttpRequest):
    store = http_request.app.state.store
    if store.get_request(request_id) is None:
        raise HTTPException(status_code=404, detail="Saved request not found")
    form = await http_request.form()
    outgoing = request_from_form(form)
    name = str(form.get("save_name") or "").strip() or outgoing.url or "Untitled"
    store.update_request(request_id, name, outgoing)
    return render(http_request, "fragments/sidebar.html", {"req": outgoing})


@router.delete("/requests/{request_id}", response_class=HTMLResponse)
def delete_request(request_id: int, http_request: HttpRequest):
    http_request.app.state.store.delete_request(request_id)
    return render(http_request, "fragments/sidebar.html", {"req": Request()})


@router.get("/history/{entry_id}", response_class=HTMLResponse)
def load_history(entry_id: int, http_request: HttpRequest):
    entry = http_request.app.state.store.get_history(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="History entry not found")
    return render(
        http_request,
        "fragments/replay.html",
        {
            "req": entry.request,
            "response": entry.response,
            "pretty": pretty_body(entry.response),
            "warnings": [],
        },
    )


@router.delete("/history", response_class=HTMLResponse)
def clear_history(http_request: HttpRequest):
    http_request.app.state.store.clear_history()
    return render(http_request, "fragments/sidebar.html", {"req": Request()})


@router.get("/requests", response_class=HTMLResponse)
@router.get("/history", response_class=HTMLResponse)
def sidebar(http_request: HttpRequest):
    """Re-render the sidebar on demand.

    Both paths render the same fragment, which holds the Saved and History
    lists side by side. Useful for a manual refresh and for recovering after
    an out-of-band swap is missed.
    """
    return render(http_request, "fragments/sidebar.html", {"req": Request()})
```

`GET /requests` must be registered *after* `GET /requests/{request_id}` is
declared in the file, but ordering does not actually matter here: the typed
`int` path parameter means `/requests` cannot match the parameterised route.

Note that `render` always reloads `saved` and `history` from the store, so
every one of these fragments is rendered against fresh data.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/web/test_library.py -v`
Expected: PASS, 12 tests

- [ ] **Step 6: Commit**

```bash
git add hitman/web/routes.py hitman/web/templates/fragments/replay.html tests/web/test_library.py
git commit -m "feat: save, load, replay and delete requests"
```

---

### Task 11: CLI entry point, README, and end-to-end verification

**Files:**
- Create: `hitman/cli.py`, `README.md`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `hitman.web.app.create_app`.
- Produces: `main()` and `build_parser() -> argparse.ArgumentParser`; the `hitman` console script.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
import pytest

from hitman.cli import HOST, build_parser


def test_defaults():
    args = build_parser().parse_args([])
    assert args.port == 8765
    assert args.db is None


def test_port_and_db_are_configurable():
    args = build_parser().parse_args(["--port", "9000", "--db", "/tmp/x.db"])
    assert args.port == 9000
    assert args.db == "/tmp/x.db"


def test_bind_address_is_loopback_only():
    assert HOST == "127.0.0.1"


def test_there_is_deliberately_no_host_flag():
    """Exposing this app on a network turns it into an open proxy."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--host", "0.0.0.0"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hitman.cli'`

- [ ] **Step 3: Implement the CLI**

`hitman/cli.py`:

```python
"""Command line entry point: ``uv run hitman``."""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from hitman.web.app import create_app

# Deliberately not configurable. Serving this app on any other interface would
# let anyone on the network reach the host's internal services and run the
# curl binary with arguments of their choosing.
HOST = "127.0.0.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hitman", description="Local API testing client."
    )
    parser.add_argument("--port", type=int, default=8765, help="port to listen on")
    parser.add_argument("--db", default=None, help="SQLite file (default: data/hitman.db)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    app = create_app(args.db)
    url = f"http://{HOST}:{args.port}"

    print(f"Hitman is running at {url} (loopback only — press Ctrl+C to stop)")
    if not args.no_browser:
        # Give uvicorn a moment to bind before the browser asks for the page.
        threading.Timer(1.0, webbrowser.open, [url]).start()

    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Write the README**

`README.md`:

````markdown
# Hitman

A local API testing client. Send HTTP requests to services on `localhost` or
to public APIs, import and export curl commands, and keep a replayable
history of everything you sent.

## Running

```bash
uv sync
uv run hitman
```

Opens <http://127.0.0.1:8765>. Use `--port` for a different port,
`--db` for a different database file, and `--no-browser` to stay in the
terminal.

## What it does

- **Request builder** — method, URL, query params, headers, and a body
  (`none`, `json`, `raw`, or `form`).
- **Two engines** — `Send` uses Python's httpx. `Send with curl` runs the
  real curl binary, which is what you want when a request works in your
  terminal but not in the app. Both are tested to send identical bytes.
- **Import curl** — paste anything from Chrome's "Copy as cURL" and it
  becomes an editable request.
- **Copy as curl** — the reverse, so you can paste into a terminal or a
  ticket.
- **History and saved requests** — every send is recorded, including
  failures, and can be replayed with one click.

## Security notes

- The server binds to `127.0.0.1` only and has no authentication, because it
  is a single-user local tool. There is no `--host` flag: serving it on a
  network interface would let anyone reach your internal services and run
  curl with arguments they choose.
- **Saved headers are stored in plain text** in `data/hitman.db`, the same
  trade-off as a `.env` file. `data/` is gitignored. Delete the file to wipe
  everything.
- Pasted curl commands are tokenized, never executed through a shell.

## Not supported yet

Environments and `{{variable}}` substitution, auth helper forms, Postman
collection import/export, multipart file upload.

## Development

```bash
uv run pytest
uv run ruff check .
```

`hitman/core/` is pure Python with no web dependencies — the curl parser,
both engines, and the store are all testable without starting a server.
````

- [ ] **Step 6: Run the entire suite and the linter**

```bash
uv run pytest -v
```

Expected: PASS, roughly 160 tests, zero failures.

```bash
uv run ruff check .
```

Expected: no findings. Fix anything reported.

- [ ] **Step 7: Smoke-test the real application**

This is the step that catches what unit tests cannot — template syntax
errors, broken data-url wiring, dead JavaScript.

In one terminal, start a throwaway API to point at:

```bash
python3 -m http.server 3000
```

In another, start the app:

```bash
uv run hitman
```

Then, in the browser, confirm each of these by hand:

1. `GET http://localhost:3000/` → `Send` returns 200 and shows an HTML body.
2. The same request with `Send with curl` → same status, `via curl` shown.
3. Stop the `http.server` and send again → "Connection refused", no traceback.
4. `Import curl` with `curl 'https://api.github.com/repos/python/cpython' -H 'Accept: application/vnd.github+json'` → the form fills in; Send returns pretty-printed JSON.
5. `Copy as curl` → paste into a terminal and confirm it runs.
6. Add a param row, disable it with the checkbox, and confirm it vanishes from `Copy as curl`.
7. Save the request with a name → it appears under **Saved**; reload the page and click it → the form repopulates.
8. Click a **History** entry → both the request and its old response come back.
9. Switch to the **Options** tab, change the timeout → the tab shows its dot.

- [ ] **Step 8: Commit**

```bash
git add hitman/cli.py README.md tests/test_cli.py
git commit -m "feat: add CLI entry point and README"
```

- [ ] **Step 9: Final check**

```bash
git status --short
```

Expected: clean tree. `data/` and `.venv/` must not appear — if they do,
`.gitignore` is wrong and secrets are about to be committed.
