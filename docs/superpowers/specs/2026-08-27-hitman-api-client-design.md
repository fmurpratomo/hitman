# Hitman — Local API Testing Client

**Date:** 2026-08-27
**Status:** Approved design
**Author:** design session (brainstorming)

## 1. Purpose

A local web application for testing HTTP APIs — both services running on
`localhost` and public APIs on the internet. It fills the role Postman or
Insomnia fills, but runs as a small Python app you start from your own
terminal.

Two things distinguish it from typing `curl` in a terminal:

1. Requests are saved, so you can replay them without retyping.
2. Responses are displayed readably (status, timing, size, pretty JSON)
   instead of dumped to stdout.

It interoperates with `curl` in both directions: paste a curl command to
load it into the form, and copy the current form back out as a curl
command. It can also execute requests through the real `curl` binary.

## 2. Users and deployment model

Single user, local machine only. The server binds to `127.0.0.1` and is
never exposed to a network. Consequently:

- No authentication, no accounts, no sessions.
- Calling `localhost` and private-network addresses is a feature, not a
  risk to defend against.
- No rate limiting, no SSRF filtering.

**This assumption is load-bearing.** If the app is ever served on a
network interface other than loopback, it becomes a proxy that lets any
visitor reach the host's internal network and run the `curl` binary with
attacker-chosen arguments. Changing the bind address is therefore a
security decision requiring a redesign of this section, not a config
tweak.

## 3. Scope

### In scope (v1)

- Request builder: method, URL, query parameters, headers, body.
- Two send engines, user-selectable per request: Python (`httpx`) and the
  real `curl` binary.
- Import a pasted curl command into the builder.
- Export the current request as a curl command.
- Response viewer: status, reason, elapsed time, size, response headers,
  body (pretty-printed JSON or raw text).
- Automatic history of every send, including failed sends, replayable.
- Named saved requests.

### Out of scope (v1)

Deliberately excluded to keep the first version small. The storage format
leaves room for each.

- Environments and `{{variable}}` substitution.
- Auth helper forms (Bearer / Basic / API key builders). Auth still works
  by typing the `Authorization` header directly, and `-u` in an imported
  curl command is converted to one.
- Postman collection import/export.
- Response assertions and test scripting.
- Request chaining, cookie jars, WebSocket/gRPC/GraphQL-specific UI.

## 4. Architecture

### Layering

A pure-Python core with no web dependencies, and a thin web layer on top.

```
hitman/
├── core/                   # no FastAPI, no Jinja, no uvicorn imports
│   ├── models.py           # Request, Response, KeyValue
│   ├── curl_import.py      # curl command text -> Request
│   ├── curl_export.py      # Request -> argv list / display string
│   ├── engines/
│   │   ├── base.py         # Engine protocol
│   │   ├── httpx_engine.py
│   │   └── curl_engine.py
│   └── store.py            # SQLite: history + saved requests
├── web/
│   ├── app.py              # FastAPI app factory, static/template wiring
│   ├── routes.py           # fragment endpoints driven by app.js
│   ├── forms.py            # HTML form payload <-> Request
│   ├── templates/
│   │   ├── base.html
│   │   ├── index.html
│   │   └── fragments/      # builder.html, response.html, sidebar.html
│   └── static/             # app.css, app.js (no dependencies)
├── cli.py                  # entry point: starts uvicorn on 127.0.0.1
└── tests/
```

**Dependency rule:** `core` must never import from `web`. A test that
imports `hitman.core.*` must pass without FastAPI installed. This is what
makes the curl parser — the riskiest code in the project — testable as
plain function calls.

### Why this shape

The alternative considered was a single FastAPI module. It is faster to
start but entangles curl parsing with request handlers, so testing the
parser requires a test client, and the module grows into a file too large
to edit reliably. The layered split also means a `hitman` CLI could later
reuse `core` unchanged.

## 5. Data model

`core/models.py`, plain dataclasses, no ORM.

```python
@dataclass
class KeyValue:
    key: str
    value: str
    enabled: bool = True

@dataclass
class Request:
    method: str = "GET"
    url: str = ""
    params: list[KeyValue] = field(default_factory=list)
    headers: list[KeyValue] = field(default_factory=list)
    body_type: str = "none"          # none | json | raw | form
    body: str = ""                   # used by json and raw
    form_fields: list[KeyValue] = field(default_factory=list)  # used by form
    follow_redirects: bool = True
    verify_tls: bool = True
    timeout: float = 30.0

@dataclass
class Response:
    engine: str                       # "httpx" | "curl"
    status: int | None                # None when the request never completed
    reason: str = ""
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: str = ""
    body_truncated: bool = False
    size_bytes: int = 0
    elapsed_ms: float = 0.0
    error: str | None = None          # human-readable failure message
    curl_exit_code: int | None = None
```

`enabled` on `KeyValue` lets a user toggle a header or parameter off
without deleting it — disabled entries are stored but not sent.

**`body_type` semantics**, defined explicitly because the wrong guess here
produces requests that silently differ from what curl would send:

| `body_type` | What is sent | Default `Content-Type` |
|---|---|---|
| `none` | no body | — |
| `json` | `body` verbatim | `application/json` |
| `raw` | `body` verbatim | none; whatever the user set in Headers |
| `form` | `form_fields` URL-encoded | `application/x-www-form-urlencoded` |

In every case an explicit `Content-Type` in the headers list wins over the
default. `form` means URL-encoded only; multipart is out of scope for v1
(see the multipart note in curl import below).

`Request` carries no `id` or `name`. Identity belongs to the storage
layer, so the same `Request` value can be sent, exported, or saved without
caring where it came from.

## 6. Storage

SQLite via the standard library `sqlite3`. One file, path from
`HITMAN_DB` environment variable, defaulting to `./data/hitman.db`.
`data/` is gitignored.

```sql
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
  elapsed_ms            REAL,
  size_bytes            INTEGER,
  error                 TEXT,
  response_headers_json TEXT,
  response_body         TEXT,
  created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
```

The request is stored as a single JSON column rather than spread across
typed columns. Adding a field to `Request` then needs no schema
migration.

**Retention:** on insert, delete all but the most recent 500 history rows.
Stored response bodies are capped at 256 KB (separate from the 5 MB
display cap) so the database does not grow without bound.

All queries use parameter binding. No string-formatted SQL.

## 7. Send engines

Both engines implement the same interface and return the same `Response`
shape, so the UI does not branch on which one ran:

```python
class Engine(Protocol):
    name: str
    def send(self, request: Request) -> Response: ...
```

Neither engine raises for network failures. A failure becomes a `Response`
with `error` set and `status=None`. Only programming errors propagate.

### httpx engine (default)

`httpx.Client(follow_redirects=..., timeout=..., verify=...)`. Elapsed
time measured with `time.perf_counter` around the call. Body decoded
using the response's charset, falling back to UTF-8 with
`errors="replace"`.

### curl engine

Invoked as an argv list with `shell=False`. Never a shell string.

```
curl -s -S
     -o <body_tempfile>
     -D <headers_tempfile>
     -w %{json}
     --max-time <timeout>
     [-L] [-k]
     -X <METHOD>
     -H <"K: V"> ...
     [--data-raw <body> | -F <k=v> ...]
     <url>
```

Rather than parsing curl's human-readable output, the engine reads
`-w %{json}`, which curl 8.7 emits as a JSON object on stdout containing
`http_code`, `time_total`, `size_download`, `url_effective` and more.
Headers come from the `-D` dump file; when redirects were followed the
file holds several header blocks and the **last** block is the one
reported. The body comes from the `-o` file.

Failure handling:

- `FileNotFoundError` → curl is not installed. Detected once at startup;
  the UI disables the curl option with an explanatory tooltip.
- Non-zero exit → `error` is built from curl's stderr plus a lookup of
  common exit codes (7 connection refused, 6 DNS, 28 timeout, 35/60 TLS),
  and `curl_exit_code` is recorded.
- `subprocess.TimeoutExpired` (belt-and-braces, at `timeout + 5` seconds,
  since `--max-time` should fire first) → timeout error.

Temporary files are always cleaned up.

## 8. curl import and export

### Import

Input is normalized (line continuations `\` + newline collapsed to
spaces) and tokenized with `shlex.split`. **`shlex.split` tokenizes; it
does not evaluate.** No `eval`, no shell, no subprocess is involved in
parsing, so a hostile paste cannot execute anything.

Flags translated into the `Request`:

| Flag | Effect |
|---|---|
| `-X`, `--request` | method |
| `-H`, `--header` | header entry |
| `-d`, `--data`, `--data-raw`, `--data-binary`, `--data-ascii` | body; implies POST when no `-X` |
| `-F`, `--form` | `form_fields` entry (see multipart note below) |
| `-u`, `--user` | encoded into an `Authorization: Basic` header |
| `-L`, `--location` | `follow_redirects = True` |
| `-k`, `--insecure` | `verify_tls = False` |
| `-A`, `--user-agent` | `User-Agent` header |
| `-b`, `--cookie` | `Cookie` header |
| `-e`, `--referer` | `Referer` header |
| `-m`, `--max-time` | timeout |
| `--compressed` | `Accept-Encoding: gzip, deflate` header |
| `-G`, `--get` | data is moved into query parameters |
| `-I`, `--head` | method `HEAD` |
| bare token | URL |

Query parameters present in the pasted URL are split out into the params
list so they are individually editable.

Curl defaults are reproduced: `-d` without an explicit `Content-Type`
yields `application/x-www-form-urlencoded`.

**Multipart limitation.** Real `-F` is multipart, which v1 does not send.
An imported `-F k=v` becomes a URL-encoded `form_fields` entry plus a
warning saying the request was converted from multipart. An `-F k=@file`
file upload cannot be represented at all: it is dropped with a warning
naming the field. This is a deliberate v1 cut, not an oversight — file
upload needs a file picker in the builder, which is future work.

Flags that only control curl's own output (`-o`, `-O`, `-s`, `-v`, `-i`,
`-w`, `--retry`) are ignored silently — they have no meaning in the UI.
Any **unrecognized** flag is collected into a `warnings` list shown above
the builder. Parsing never fails on an unknown flag.

A genuinely malformed command (a flag whose required value is missing, or
no URL at all) raises `CurlParseError` carrying the offending token and
its index. The UI shows the message and leaves the existing form
untouched.

### Export

`curl_export` produces the argv list (reused by the curl engine) and a
display string built with `shlex.quote` on each argument. Displayed
commands are multi-line with `\` continuations when longer than roughly
80 characters.

`-X` is emitted only when the method is not `GET`; `-L` only when
following redirects; `--max-time` only when the timeout differs from the
default. The goal is a command a person would plausibly have typed.

**Round-trip property:** `parse(export(r)) == normalize(r)`, where
`normalize` splits any query string embedded in `url` out into `params`
and drops disabled entries.

The normalization step is necessary rather than a fudge: a user may type
`http://localhost:3000/api?page=2` straight into the URL bar, so `params`
and the URL can express the same thing two ways. `export` and `parse` both
converge on the params list, so plain equality would fail on requests that
are semantically identical. `normalize` is a real function in
`core/models.py`, applied by the store before saving so history entries
are comparable too.

## 9. Web layer

### Routes

All non-`GET /` responses are HTML fragments that `app.js` swaps into the
page, except `/export-curl` which is `text/plain`.

| Method | Path | Returns |
|---|---|---|
| GET | `/` | full page |
| POST | `/send` | response pane + out-of-band sidebar refresh |
| POST | `/import-curl` | prefilled builder fragment (+ warnings) |
| POST | `/export-curl` | curl command as `text/plain` |
| GET | `/requests` | sidebar saved-requests fragment |
| POST | `/requests` | save current request under a name |
| GET | `/requests/{id}` | builder fragment loaded from a saved request |
| PUT | `/requests/{id}` | update a saved request |
| DELETE | `/requests/{id}` | sidebar fragment |
| GET | `/history` | sidebar history fragment |
| GET | `/history/{id}` | builder + response fragments for a past send |
| DELETE | `/history` | clear history |
| GET | `/healthz` | liveness check |

### UI

Three panes, Postman-shaped:

- **Left sidebar:** tabs for Saved and History. History rows show method,
  a shortened URL, status badge and elapsed time. Clicking loads the
  request into the builder.
- **Center:** method dropdown, URL bar, `Send` button with an adjacent
  dropdown for `Send with curl`. Below it, tabs for Params, Headers, Body
  and Options. Params and Headers are editable key/value rows with an
  enable checkbox and add/remove. Body has a type selector and a textarea;
  JSON mode offers format and validate, and `form` type shows key/value
  rows instead of a textarea. **Options** holds the three per-request
  settings that have no other home: follow redirects, verify TLS
  certificates, and timeout in seconds. Its tab label shows a dot when any
  option differs from its default, so a request behaving oddly because TLS
  verification was left off is not a mystery.
- **Response pane:** status badge (colored by class), elapsed ms, size,
  and which engine ran. Tabs for Pretty, Raw and Headers. When the curl
  engine ran, an extra line shows the exit code and stderr on failure.
- **Toolbar:** `Import curl` (opens a paste box) and `Copy as curl`.

Editing the URL keeps the params table in sync and vice versa.

## 10. Error handling

Failures are the normal case for this tool — you will frequently point it
at a `localhost` server that is not running yet. Every failure produces a
readable message, a history entry, and never a traceback in the browser.

| Condition | Behavior |
|---|---|
| Connection refused | "Connection refused — is anything listening on `<host>:<port>`?" |
| DNS failure | "Could not resolve host `<host>`." |
| Timeout | "Timed out after `<n>`s." with elapsed time shown |
| TLS error | Message plus a hint that TLS verification can be disabled |
| Invalid URL | Caught before sending; inline message on the URL field |
| Malformed curl paste | Offending token and position; form left untouched |
| `curl` binary absent | Option disabled at startup with a tooltip |
| Response > 5 MB | Body truncated for display, flagged in the UI |
| Binary content type | Content type and size shown instead of raw bytes |
| Unexpected exception | Logged to the terminal; UI shows a generic message |

## 11. Security

Local-only does not mean the app renders untrusted data carelessly.

- **Bind address:** uvicorn is started with `host="127.0.0.1"`,
  hardcoded, not configurable through the UI.
- **No shell:** the curl engine passes an argv list with `shell=False`.
  No user input is ever concatenated into a shell string.
- **No evaluation of pasted input:** curl import uses `shlex.split`
  only.
- **Response rendering:** Jinja2 autoescape stays enabled and response
  bodies are rendered as escaped text inside `<pre>`. An API returning
  `<script>...</script>` must not execute inside the app. This is the
  single most important rule in the web layer, because response bodies
  are attacker-controlled content by definition.
- **SQL:** parameter binding everywhere.
- **Secrets:** headers such as `Authorization` are stored in plain text in
  the local SQLite file, exactly as a `.env` file would be. `data/` is
  gitignored. This is documented in the README so it is a known
  trade-off rather than a surprise.

## 12. Testing strategy

Test-driven: tests are written before the implementation of each unit.
`pytest` throughout.

| File | Covers |
|---|---|
| `test_curl_import.py` | table-driven over real commands: Chrome "Copy as cURL", multi-line with `\`, repeated `-H`, `--data-raw`, `-u`, `-F`, `--compressed`, `-G`, unknown flags produce warnings, malformed input raises `CurlParseError` |
| `test_curl_export.py` | argv correctness, quoting of values containing spaces/quotes, conditional `-X`/`-L`/`--max-time` |
| `test_roundtrip.py` | `parse(export(r)) == r` across a corpus of requests |
| `test_engines.py` | both engines against a local fixture server: status codes, headers, JSON and binary bodies, redirects, timeout, connection refused on a closed port |
| `test_store.py` | save/load/update/delete, history insert and 500-row trim, body cap |
| `test_routes.py` | FastAPI `TestClient`: send, import, export, save, replay; and an escaping test asserting a `<script>` response body is escaped in the rendered fragment |

The fixture server is a real threaded `http.server` on an ephemeral port,
not a mock — the curl engine is a subprocess and cannot be intercepted by
a Python-level mock. This has the useful side effect of proving that
localhost calls, the app's primary use case, actually work.

## 13. Tech stack and commands

- Python 3.11, managed with `uv` (3.9 is the macOS system Python and is
  too old for the `X | None` syntax used throughout).
- `fastapi`, `uvicorn[standard]`, `jinja2`, `httpx`, `python-multipart`.
- `pytest` for tests, `ruff` for lint.
- No third-party JavaScript at all. Interactivity is a ~50-line fetch/swap
  layer in `web/static/app.js`, so the app has no supply chain and works
  with no internet connection.
- No Node.js, no bundler, no build step.

```
uv sync                 # install
uv run hitman           # serve http://127.0.0.1:8765
uv run pytest           # tests
```

`hitman` is a console-script entry point defined in `pyproject.toml`,
pointing at `hitman.cli:main`. `--port` and `--db` flags are accepted;
there is deliberately no `--host` flag.

## 14. Future work

Enabled by, but not part of, this design:

- Environments and `{{variable}}` substitution — a third SQLite table plus
  a substitution pass in `core` before the engine runs.
- Auth helper forms — pure UI sugar over the existing headers list.
- Postman collection import/export — a converter module in `core`.
- A `hitman send` CLI reusing `core` with no changes.
