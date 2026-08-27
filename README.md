# Hitman

A local API testing client. Send HTTP requests to services on `localhost` or
to public APIs, import and export curl commands, and keep a replayable
history of everything you sent.

## Running

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

## Not supported yet

Environments and `{{variable}}` substitution, auth helper forms, Postman
collection import/export, multipart file upload.

## Development

```bash
uv run pytest
uv run ruff check .
```

`hitman/core/` is pure Python with no web dependencies — the curl parser, both
engines, and the store are all testable without starting a server.
