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

## Not supported yet

Auth helper forms, Postman collection import/export, multipart file upload.

## Development

```bash
uv run pytest
uv run ruff check .
```

`hitman/core/` is pure Python with no web dependencies — the curl parser, both
engines, and the store are all testable without starting a server.
