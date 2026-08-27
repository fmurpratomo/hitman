"""SQLite persistence for saved requests and send history.

The request is stored as one JSON column rather than spread across typed
columns, so adding a field to :class:`Request` needs no schema migration.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
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
    return datetime.now(UTC).isoformat(timespec="seconds")


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
                "UPDATE saved_requests SET name = ?, request_json = ?, updated_at = ? WHERE id = ?",
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
            rows = self._conn.execute("SELECT * FROM saved_requests ORDER BY id DESC").fetchall()
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
            row = self._conn.execute("SELECT * FROM history WHERE id = ?", (entry_id,)).fetchone()
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
