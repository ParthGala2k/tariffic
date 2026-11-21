"""SQLite connections, desk schema, ERP rebuild."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import CFG, Config

SCHEMA = Path(__file__).with_name("schema.sql")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # explicit transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS leaves an existing
# table alone, so each is added here when missing; nothing is ever dropped.
LATER_COLUMNS = {
    "challenges": {"reply": "TEXT", "replied_by": "TEXT"},
}


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, columns in LATER_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def desk_conn(cfg: Config = CFG) -> sqlite3.Connection:
    """Open (creating if needed) the desk DB with its schema applied."""
    conn = _connect(cfg.desk_db)
    conn.executescript(SCHEMA.read_text())
    _add_missing_columns(conn)
    return conn


def desk_conn_ro(cfg: Config = CFG) -> sqlite3.Connection:
    """Read-only handle on the desk DB, used by run_sql."""
    uri = f"file:{cfg.desk_db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
  request_hash TEXT PRIMARY KEY,
  provider     TEXT NOT NULL,
  model        TEXT NOT NULL,
  response     TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
"""


def cache_conn(cfg: Config = CFG) -> sqlite3.Connection:
    """The LLM response cache, in its own gitignored file outside the desk DB.

    Kept across resets so repeated runs stay cheap; `reset --clear-cache` wipes it.
    """
    conn = _connect(cfg.cache_db)
    conn.executescript(CACHE_SCHEMA)
    return conn


def _remove_db(path: Path) -> None:
    """Delete a SQLite file and its sidecars.

    Windows refuses to delete a file another handle still has open. Connections
    that have simply gone out of scope are closed by a collection pass; if the
    file is still held after that, the database is emptied in place instead, so
    a reset is a reset on every platform.
    """
    import gc
    import time

    targets = [path] + [Path(str(path) + sidecar) for sidecar in ("-wal", "-shm")]
    for attempt in range(5):
        try:
            for target in targets:
                target.unlink(missing_ok=True)
            return
        except PermissionError:
            gc.collect()
            time.sleep(0.05 * (attempt + 1))
    if not path.exists():
        return
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for kind in ("trigger", "view", "table"):
            for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'", (kind,)).fetchall():
                conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
        conn.execute("VACUUM")
    finally:
        conn.close()


def clear_cache(cfg: Config = CFG) -> None:
    _remove_db(cfg.cache_db)


def erp_conn(cfg: Config = CFG) -> sqlite3.Connection:
    return _connect(cfg.erp_db)


def rebuild_erp(cfg: Config = CFG) -> Path:
    """Drop the ERP DB and rebuild it from schema.sql + seed.sql."""
    _remove_db(cfg.erp_db)
    conn = _connect(cfg.erp_db)
    try:
        conn.executescript(cfg.erp_schema.read_text())
        conn.executescript(cfg.erp_seed.read_text())
    finally:
        conn.close()
    return cfg.erp_db


def drop_desk_db(cfg: Config = CFG) -> None:
    _remove_db(cfg.desk_db)


def log_event(
    conn: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    subject: str | None,
    allowed: bool,
    reason: str = "",
    diff: dict | None = None,
    evidence: list | dict | None = None,
    policy_version: str | None = None,
) -> int:
    """Append one audit row. Caller controls the surrounding transaction."""
    cur = conn.execute(
        "INSERT INTO events (at, actor, action, subject, allowed, reason, diff, evidence, policy_version)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            now(),
            actor,
            action,
            subject,
            1 if allowed else 0,
            reason,
            json.dumps(diff) if diff is not None else None,
            json.dumps(evidence) if evidence is not None else None,
            policy_version,
        ),
    )
    return int(cur.lastrowid)


def log_event_now(conn: sqlite3.Connection, **kw) -> int:
    """Append one audit row and commit it immediately.

    Used for refusals: the refused attempt must survive the rollback of
    whatever transaction was attempting the change.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        event_id = log_event(conn, **kw)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return event_id
