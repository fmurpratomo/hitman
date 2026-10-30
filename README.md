# Hitman

A local API testing client. Send HTTP requests to services on `localhost` or
to public APIs, import and export curl commands, and keep a replayable
history of everything you sent.

## Installing Python

Hitman needs **Python 3.9 or newer**. Confirm your installed version with:

```bash
python3 --version
```

If it is missing or older than 3.9, install it for your platform:

- **macOS:** Install with [Homebrew](https://brew.sh/):

  ```bash
  brew install python
  ```

  Or download the installer from [python.org](https://www.python.org/downloads/macos/).

- **Linux:** Use your distribution's package manager. For Debian or Ubuntu:

  ```bash
  sudo apt update
  sudo apt install python3 python3-venv python3-pip
  ```

  Other distributions may provide equivalent packages such as `python3`,
  `python3-pip`, and `python3-virtualenv`.

- **Windows:** In PowerShell, install Python with:

  ```powershell
  winget install Python.Python.3.13
  ```

  Or download it from [python.org](https://www.python.org/downloads/windows/).
  When using the installer, select **Add Python to PATH**. Open a new terminal,
  then confirm the installation with `py --version`.

## Running

### With python3 only

Nothing but the interpreter and `pip`. No `uv`, no build backend, no admin
rights, and the project itself is never installed — it runs from the checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m hitman
```

For a shorter command, add a shell alias from the project root:

```bash
alias hitman='.venv/bin/python -m hitman'
```

Then run:

```bash
hitman
hitman --port 9000 --no-browser
```

Every later run is just the last line. Flags work the same:
`.venv/bin/python -m hitman --port 9000 --no-browser`.

### With uv

```bash
uv sync
uv run hitman
```

Opens <http://127.0.0.1:8765>. Use `--port` for a different port, `--db` for a
different database file, and `--no-browser` to stay in the terminal.

## What it does

- **Request builder** — method, URL, query params, headers, and a body
  (`none`, `json`, `raw`, or `form`).
- **Two engines** — `Send` uses Python's httpx. `Send with curl` runs the real
  curl binary, which is what you want when a request works in your terminal
  but not in the app. A parity test suite asserts both send identical bytes.
- **Import curl** — paste anything from Chrome's "Copy as cURL" and it becomes
  an editable request.
- **Copy as curl** — the reverse, so you can paste into a terminal or a ticket.
- **History and saved requests** — every send is recorded, including failures,
  and can be replayed with one click. History lists a send under the name of
  the request you saved rather than its URL, with the URL still on the row as
  a tooltip.

  The list is grouped into collapsible days — **Today**, **Yesterday**, then
  the date — using your machine's timezone, not the UTC the timestamps are
  stored in, so a request sent at 02:00 is filed under the day you sent it.

  A send made from a loaded saved request is linked to it, so the name follows
  a rename. Anything else — typed by hand, imported from curl, replayed, or
  sent before this existed — is matched on content instead, comparing the
  *resolved* form so a saved `{{base_url}}/users` matches the
  `http://localhost:3000/users` that actually went out. That makes the match
  depend on the active environment: switch environments and an old entry goes
  back to showing its URL, because under this environment it is no longer that
  request.

## Security notes

- The server binds to `127.0.0.1` only and has no authentication, because it is
  a single-user local tool. There is no `--host` flag: serving it on a network
  interface would let anyone reach your internal services and run curl with
  arguments they choose.
- **Saved headers are stored in plain text** in `data/hitman.db`, the same
  trade-off as a `.env` file. `data/` is gitignored. Delete the file to wipe
  everything.
- Pasted curl commands are tokenized with `shlex`, never executed through a
  shell. The curl engine passes an argv list with `shell=False`.
- Response bodies are rendered as escaped text inside `<pre>`, so an API that
  returns `<script>` cannot execute anything in the app.
- No third-party JavaScript. The front end is one 160-line `app.js`.

## Saved requests

Give a request a name and, optionally, a folder — the folder box autocompletes
from the folders you already have, and typing a new name creates it. Folders
are a single flat level, which is what an API client of this size needs.

Loading a saved request fills the builder and offers **Update** (rename, move
folder, or overwrite with the current request) alongside **Save as new**. The
duplicate button copies a request into the same folder as `… (copy)`.

### Drafts and the checkpoint

A saved request has two states, so that editing one is never a commitment:

- The **draft** is your working copy. It is written automatically as you type,
  a moment after you stop, and it is what you get back when you load the
  request. Switching to another endpoint to check something is therefore not
  the same as throwing your edits away — every request keeps its own draft, and
  the sidebar marks the ones holding unsaved work with a dot.
- The **checkpoint** is the state as of the last time you pressed **Update**.
  That is the deliberate save. There is exactly one, and moving it discards
  the draft, which is now redundant.

**Roll back to checkpoint** appears whenever a draft exists and does the
obvious thing: throws it away and puts the last updated version back in the
builder. Only that one checkpoint is kept, so it undoes your unsaved edits, not
a previous Update.

Two details that follow from this split:

- A draft is stored **verbatim**, unlike the checkpoint, which is canonicalised
  the way `Copy as curl` is. Canonicalising a draft would drop the rows you had
  just toggled off and rewrite a half-typed query string — it would edit the
  one thing whose job is to come back exactly as you left it. A draft that ends
  up identical to the checkpoint is dropped instead of stored, so typing a
  character and deleting it does not leave a request looking permanently
  unsaved.
- **Scenarios run the checkpoint**, never the draft, so a run means the same
  thing whether or not you happen to have a builder open on one of its steps.

## Environments

Variables are resolved just before the request is sent, so:

- **Saved requests keep the template.** A request saved as
  `{{base_url}}/users` works against every environment.
- **History records what was actually sent.** You are looking at a log of real
  requests, not of intentions.
- **"Copy as curl" resolves too** — a curl command containing `{{base_url}}`
  is not something you can paste into a terminal.
- **An unset variable is left in place**, not blanked, and the response pane
  names it. Sending to `{{base_url}}/users` fails in an obvious way; sending
  to `/users` fails in a baffling one.
- Substitution is a **single pass** — a variable's value is never rescanned,
  so cycles are impossible.

Environment values live in the same database as your headers, in plain text.

## Scenarios

A scenario runs saved requests **in order** and checks each response — the
**Tests** tab in the sidebar. Each step picks a saved request and carries its
own list of checks:

| Check | Target | Example |
|---|---|---|
| `status` | — | `status eq 200`, `status lt 300` |
| `json` | a path into the body | `json user.id exists`, `json roles.0 eq admin` |
| `header` | a header name | `header Content-Type contains json` |
| `body` | — | `body not contains error`, `body matches ^\{` |
| `time_ms` | — | `time_ms lt 500` |

JSON paths are dotted, with numeric indices for arrays: `data.items.0.id`, or
`data.items[0].id` — the same thing. A leading `$` is allowed and ignored.

Comparison is forgiving about types, because the expected value is typed into
a text box and the actual one comes back from `json.loads`: `eq 200` matches
the integer, `eq true` matches the boolean, and `contains` looks inside a JSON
array rather than its text.

### Chaining

Sequence only matters if a step can use what the last one returned. **Capture
into variables** binds a value out of a response to `{{name}}`, and every later
step sees it:

1. `POST {{base_url}}/login` — capture `token` from the JSON path `token`.
2. `GET {{base_url}}/me` with a header `Authorization: Bearer {{token}}`.

Captures use the same machinery as environments, so a captured value and an
environment variable are referenced identically, and a capture shadows an
environment variable of the same name for the rest of the run. A capture that
finds nothing **fails its step** rather than warning: left as a warning, the
next request sends the literal text `{{token}}` and fails somewhere far away
from the actual cause.

Scenarios take a folder the same way saved requests do — the box beside the
description autocompletes from the folders you already have, and typing a new
name creates it. The **Tests** list groups by folder, one flat level, with
unfiled scenarios last. A folder is only a label on its scenarios: move the
last one out and the folder is gone.

### Running

- **Run** runs what is on screen, saved or not, so you can iterate. The **&#9658;**
  button in the sidebar runs the *stored* scenario instead.
- A failing step **stops the run** by default and the rest are marked skipped,
  since a chained step usually cannot work without the one before it. Switch to
  *Run every step* when the steps are independent.
- Runs go through either engine, use the active environment, and are kept —
  the last 100 — with the full request and response of every step.
- Scenario sends are **not** written to the send history. The run report
  already holds every request and response, and a twenty-step scenario would
  otherwise flush the history you were keeping by hand.

## Performance tests

The **Performance** tab puts a saved request or a scenario under load and
measures how it holds up. A test has:

- **Target** — one saved request, or a whole scenario. With a scenario, every
  pass runs all of its steps, checks and captures included, so a flow that logs
  in and then fetches a page is measured as that flow rather than as loose
  requests that would fail without the token.
- **Users** — how many virtual users send at once, up to 200. Each one repeats
  the target back to back, with no pause, until the test stops.
- **Ramp-up** — users start evenly spread over this many seconds instead of
  all at once.
- **Stop after** — a number of **seconds**, or a number of **iterations**. One
  iteration is one send of the request, or one full pass through the scenario.

Pressing **Run** starts the test and the report updates every second while it
runs: requests, throughput, failure rate, latency percentiles (min, mean, p50,
p90, p95, p99, max), a requests-per-second chart, and a count per status code.
A scenario run adds a row per step and how many passes failed. **Stop** ends a
run early, and the users first finish the request they are on. Only one test
runs at a time, because two load tests on one machine would each be measuring
the other.

What to know when reading the numbers:

- A request **fails** when it gets no response, or a 4xx or 5xx status. A
  scenario pass fails the way a scenario run does, so a failed check counts
  even when every response was a 200.
- Requests always go out through **httpx**, and each user **keeps its
  connection open** between sends, as a browser or an app would. Opening a
  fresh connection per request would mostly measure TCP and TLS handshakes.
  Cookies are still cleared after every send, so a request means the same thing
  under load as it does in the builder.
- Percentiles are accurate to within 1%. Responses are summarised as they
  arrive rather than kept, which is what lets an hour-long test run in constant
  memory.
- The test runs **on your machine, in Python**, so at high throughput the
  client itself can become the bottleneck, and its own queueing shows up as
  latency. When numbers at that scale matter, confirm them with a dedicated
  load-testing tool.
- Performance sends are **not** written to the send history. The last 100 runs
  are kept under **Recent runs**, as their summaries only.

Only load-test servers you run or have permission to test.

## Not supported yet

Auth helper forms, Postman collection import/export, multipart file upload,
running a scenario from the command line.

## Development

```bash
uv run pytest
uv run ruff check .
```

`hitman/core/` is pure Python with no web dependencies — the curl parser, both
engines, and the store are all testable without starting a server.
