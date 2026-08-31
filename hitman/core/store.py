"""SQLite persistence for saved requests and send history.

The request is stored as one JSON column rather than spread across typed
columns, so adding a field to :class:`Request` needs no schema migration.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from hitman.core.models import KeyValue, Request, Response, normalize
from hitman.core.scenarios import Scenario, ScenarioResult

ACTIVE_ENVIRONMENT = "active_environment"

HISTORY_LIMIT = 500
STORED_BODY_LIMIT = 256 * 1024

# A run stores a full response per step, so it is capped harder than history
# and kept for fewer entries: twenty steps of 256 KB is a 5 MB row.
SCENARIO_RUN_LIMIT = 100
RUN_BODY_LIMIT = 64 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_requests (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL,
  folder       TEXT NOT NULL DEFAULT '',
  request_json TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS environments (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  name           TEXT NOT NULL,
  variables_json TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

-- One row per remembered preference. There is no session or user, so the
-- active environment is application state and belongs in the database.
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenarios (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL,
  scenario_json TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenario_runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  scenario_id   INTEGER,
  name          TEXT NOT NULL,
  engine        TEXT NOT NULL,
  environment   TEXT NOT NULL DEFAULT '',
  passed        INTEGER NOT NULL,
  passed_count  INTEGER NOT NULL DEFAULT 0,
  failed_count  INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER NOT NULL DEFAULT 0,
  elapsed_ms    REAL,
  result_json   TEXT NOT NULL,
  created_at    TEXT NOT NULL
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
"""


@dataclass
class SavedRequest:
    id: int
    name: str
    folder: str
    request: Request
    created_at: str
    updated_at: str


@dataclass
class Environment:
    id: int
    name: str
    variables: list[KeyValue]
    created_at: str
    updated_at: str

    def as_mapping(self) -> dict[str, str]:
        """Enabled variables only — a disabled row is not defined."""
        return {row.key: row.value for row in self.variables if row.enabled and row.key}


@dataclass
class SavedScenario:
    id: int
    scenario: Scenario
    created_at: str
    updated_at: str

    @property
    def name(self) -> str:
        return self.scenario.name


@dataclass
class ScenarioRun:
    id: int
    scenario_id: int | None
    result: ScenarioResult
    created_at: str

    @property
    def passed(self) -> bool:
        return self.result.passed


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
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Bring an existing database up to the current schema.

        CREATE TABLE IF NOT EXISTS is a no-op on a table that already exists,
        so a column added after someone started using the app has to be
        applied here or their saved requests break on read.
        """
        columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(saved_requests)")
        }
        if "folder" not in columns:
            self._conn.execute(
                "ALTER TABLE saved_requests ADD COLUMN folder TEXT NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- saved requests -------------------------------------------------

    def save_request(self, name: str, request: Request, folder: str = "") -> int:
        payload = json.dumps(normalize(request).to_dict())
        stamp = _now()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO saved_requests (name, folder, request_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, folder.strip(), payload, stamp, stamp),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def update_request(
        self, request_id: int, name: str, request: Request, folder: str = ""
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE saved_requests SET name = ?, folder = ?, request_json = ?,"
                " updated_at = ? WHERE id = ?",
                (
                    name,
                    folder.strip(),
                    json.dumps(normalize(request).to_dict()),
                    _now(),
                    request_id,
                ),
            )
            self._conn.commit()

    def duplicate_request(self, request_id: int) -> int | None:
        """Copy a saved request, into the same folder, under a free name."""
        original = self.get_request(request_id)
        if original is None:
            return None
        taken = {item.name for item in self.list_requests() if item.folder == original.folder}
        name = f"{original.name} (copy)"
        suffix = 2
        while name in taken:
            name = f"{original.name} (copy {suffix})"
            suffix += 1
        return self.save_request(name, original.request, original.folder)

    def list_folders(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT folder FROM saved_requests WHERE folder <> ''"
                " ORDER BY folder"
            ).fetchall()
        return [row["folder"] for row in rows]

    def grouped_requests(self) -> list[tuple[str, list[SavedRequest]]]:
        """Saved requests as (folder, items), folders first and unfiled last."""
        groups: dict[str, list[SavedRequest]] = {}
        for item in self.list_requests():
            groups.setdefault(item.folder, []).append(item)
        return sorted(groups.items(), key=lambda pair: (pair[0] == "", pair[0]))

    def get_request(self, request_id: int) -> SavedRequest | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM saved_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return _row_to_saved(row) if row else None

    def list_requests(self) -> list[SavedRequest]:
        with self._lock:
            rows = self._conn.execute(
                # Folders first, then unfiled; alphabetical within each.
                "SELECT * FROM saved_requests"
                " ORDER BY (folder = '') ASC, folder ASC, name ASC"
            ).fetchall()
        return [_row_to_saved(row) for row in rows]

    def delete_request(self, request_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM saved_requests WHERE id = ?", (request_id,))
            self._conn.commit()

    # --- environments ---------------------------------------------------

    def save_environment(self, name: str, variables: list[KeyValue]) -> int:
        stamp = _now()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO environments (name, variables_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (name, _dump_vars(variables), stamp, stamp),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def update_environment(self, env_id: int, name: str, variables: list[KeyValue]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE environments SET name = ?, variables_json = ?, updated_at = ?"
                " WHERE id = ?",
                (name, _dump_vars(variables), _now(), env_id),
            )
            self._conn.commit()

    def get_environment(self, env_id: int) -> Environment | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM environments WHERE id = ?", (env_id,)
            ).fetchone()
        return _row_to_env(row) if row else None

    def list_environments(self) -> list[Environment]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM environments ORDER BY name").fetchall()
        return [_row_to_env(row) for row in rows]

    def delete_environment(self, env_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM environments WHERE id = ?", (env_id,))
            # Deleting the active environment must not leave a dangling id.
            self._conn.execute(
                "DELETE FROM settings WHERE key = ? AND value = ?",
                (ACTIVE_ENVIRONMENT, str(env_id)),
            )
            self._conn.commit()

    def set_active_environment(self, env_id: int | None) -> None:
        with self._lock:
            if env_id is None:
                self._conn.execute("DELETE FROM settings WHERE key = ?", (ACTIVE_ENVIRONMENT,))
            else:
                self._conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (ACTIVE_ENVIRONMENT, str(env_id)),
                )
            self._conn.commit()

    def active_environment(self) -> Environment | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (ACTIVE_ENVIRONMENT,)
            ).fetchone()
        if row is None:
            return None
        # The row may point at an environment deleted by another route.
        return self.get_environment(int(row["value"]))

    # --- scenarios ------------------------------------------------------

    def save_scenario(self, scenario: Scenario) -> int:
        stamp = _now()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO scenarios (name, scenario_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (scenario.name, json.dumps(scenario.to_dict()), stamp, stamp),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def update_scenario(self, scenario_id: int, scenario: Scenario) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE scenarios SET name = ?, scenario_json = ?, updated_at = ?"
                " WHERE id = ?",
                (scenario.name, json.dumps(scenario.to_dict()), _now(), scenario_id),
            )
            self._conn.commit()

    def get_scenario(self, scenario_id: int) -> SavedScenario | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scenarios WHERE id = ?", (scenario_id,)
            ).fetchone()
        return _row_to_scenario(row) if row else None

    def list_scenarios(self) -> list[SavedScenario]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM scenarios ORDER BY name").fetchall()
        return [_row_to_scenario(row) for row in rows]

    def delete_scenario(self, scenario_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,))
            # Past runs outlive the scenario: they are a record of what
            # happened, and losing them would rewrite history. Only the link
            # back to a scenario that no longer exists is cleared.
            self._conn.execute(
                "UPDATE scenario_runs SET scenario_id = NULL WHERE scenario_id = ?",
                (scenario_id,),
            )
            self._conn.commit()

    def duplicate_scenario(self, scenario_id: int) -> int | None:
        original = self.get_scenario(scenario_id)
        if original is None:
            return None
        taken = {item.scenario.name for item in self.list_scenarios()}
        name = f"{original.scenario.name} (copy)"
        suffix = 2
        while name in taken:
            name = f"{original.scenario.name} (copy {suffix})"
            suffix += 1
        return self.save_scenario(replace(original.scenario, name=name))

    # --- scenario runs --------------------------------------------------

    def add_scenario_run(self, scenario_id: int | None, result: ScenarioResult) -> int:
        payload = json.dumps(_trim_run(result).to_dict())
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO scenario_runs (scenario_id, name, engine, environment, passed,"
                " passed_count, failed_count, skipped_count, elapsed_ms, result_json,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scenario_id,
                    result.name,
                    result.engine,
                    result.environment,
                    int(result.passed),
                    result.count("passed"),
                    result.count("failed"),
                    result.count("skipped"),
                    result.elapsed_ms,
                    payload,
                    _now(),
                ),
            )
            run_id = int(cursor.lastrowid)
            self._conn.execute(
                "DELETE FROM scenario_runs WHERE id NOT IN"
                " (SELECT id FROM scenario_runs ORDER BY id DESC LIMIT ?)",
                (SCENARIO_RUN_LIMIT,),
            )
            self._conn.commit()
            return run_id

    def list_scenario_runs(self, limit: int = 20) -> list[ScenarioRun]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scenario_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_run(row) for row in rows]

    def get_scenario_run(self, run_id: int) -> ScenarioRun | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scenario_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return _row_to_run(row) if row else None

    def clear_scenario_runs(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM scenario_runs")
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


def _trim_run(result: ScenarioResult) -> ScenarioResult:
    """Cap the stored body of every step without touching the caller's copy."""
    steps = []
    for step in result.steps:
        if step.response is not None and len(step.response.body) > RUN_BODY_LIMIT:
            step = replace(
                step,
                response=replace(
                    step.response,
                    body=step.response.body[:RUN_BODY_LIMIT],
                    body_truncated=True,
                ),
            )
        steps.append(step)
    return replace(result, steps=steps)


def _row_to_scenario(row: sqlite3.Row) -> SavedScenario:
    return SavedScenario(
        id=row["id"],
        scenario=Scenario.from_dict(json.loads(row["scenario_json"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_run(row: sqlite3.Row) -> ScenarioRun:
    return ScenarioRun(
        id=row["id"],
        scenario_id=row["scenario_id"],
        result=ScenarioResult.from_dict(json.loads(row["result_json"])),
        created_at=row["created_at"],
    )


def _dump_vars(variables: list[KeyValue]) -> str:
    return json.dumps([{"key": v.key, "value": v.value, "enabled": v.enabled} for v in variables])


def _row_to_env(row: sqlite3.Row) -> Environment:
    return Environment(
        id=row["id"],
        name=row["name"],
        variables=[KeyValue(**item) for item in json.loads(row["variables_json"])],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_saved(row: sqlite3.Row) -> SavedRequest:
    return SavedRequest(
        id=row["id"],
        name=row["name"],
        folder=row["folder"],
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
