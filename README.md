# Hitman

A local API testing client. Send HTTP requests to services on `localhost` or
to public APIs, import and export curl commands, and keep a replayable
history of everything you sent.

## Running

Needs **Python 3.9 or newer** — the version already on your machine if you are
on macOS or most Linux distributions, so no CPython install is required. Any
stdlib newer than 3.9 is deliberately avoided for that reason.

### With python3 only

Nothing but the interpreter and `pip`. No `uv`, no build backend, no admin
rights, and the project itself is never installed — it runs from the checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m hitman
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
  and can be replayed with one click.

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
