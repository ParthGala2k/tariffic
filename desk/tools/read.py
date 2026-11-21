"""Read tools: run_sql over the golden tables, read_doc over the allowed folders."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..db import desk_conn_ro
from ..guard import check_doc_path, check_sql

MAX_ROWS = 200
MAX_DOC_CHARS = 20_000

RUN_SQL_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {
            "type": "string",
            "description": (
                "A single read-only SELECT over the golden_* tables: "
                "golden_bank_txns, golden_cbp_entries, golden_cbp_duty_lines, "
                "golden_cbp_refunds, golden_cbp_refund_entries, golden_ledger_postings. "
                "Every golden row has a raw_ref column citing the source row."
            ),
        },
        "limit": {"type": "integer", "description": f"Max rows to return (default {MAX_ROWS})."},
    },
    "required": ["sql"],
    "additionalProperties": False,
}

READ_DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Repo-relative path under data/documents/ or data/reference/, "
                "e.g. data/documents/inbox/2025-10-14-cbp-refund-notice.md. "
                "Give a directory instead (e.g. data/documents/inbox) to list what is "
                "in it, then read the file you want."
            ),
        }
    },
    "required": ["path"],
    "additionalProperties": False,
}


class BadQuery(ValueError):
    """The SQL was allowed but SQLite could not run it. The seat is told why."""


def _columns(conn: sqlite3.Connection, statement: str) -> str:
    """Name the real columns of the tables the query mentions, so a typo is fixable."""
    tables = sorted({
        t for t in re.findall(r"\b(?:from|join)\s+(golden_\w+)", statement.lower())
    })
    described = []
    for table in tables:
        try:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        except sqlite3.Error:
            continue
        if cols:
            described.append(f"{table}({', '.join(cols)})")
    return "; ".join(described)


def run_sql(ctx, sql: str, limit: int | None = None) -> dict[str, Any]:
    statement = check_sql(sql)
    limit = min(int(limit or MAX_ROWS), MAX_ROWS)
    conn = desk_conn_ro(ctx.cfg)
    try:
        try:
            rows = conn.execute(statement).fetchmany(limit)
        except sqlite3.Error as error:
            available = _columns(conn, statement)
            raise BadQuery(
                f"the query did not run: {error}."
                + (f" Columns available: {available}" if available else "")
            ) from error
    finally:
        conn.close()
    records = [dict(row) for row in rows]
    return {
        "sql": statement,
        "row_count": len(records),
        "rows": records,
        "raw_refs": sorted({r["raw_ref"] for r in records if r.get("raw_ref")}),
    }


def read_doc(ctx, path: str) -> dict[str, Any]:
    """Read one document, or list a directory so the seat can pick a file.

    The guard runs first either way: data/truth and data/answer_key are refused
    whether they are asked for as a file or as a directory.
    """
    resolved = check_doc_path(path, ctx.cfg)
    relative = resolved.relative_to(ctx.cfg.root).as_posix()

    if resolved.is_dir():
        entries = sorted(
            (child for child in resolved.iterdir() if not child.name.startswith(".")),
            key=lambda c: c.name,
        )
        listing = [
            {"name": child.name,
             "path": child.relative_to(ctx.cfg.root).as_posix(),
             "kind": "directory" if child.is_dir() else "file",
             "bytes": child.stat().st_size if child.is_file() else None}
            for child in entries
        ]
        return {
            "path": relative,
            "kind": "directory",
            "file_count": sum(1 for e in listing if e["kind"] == "file"),
            "entries": listing,
            "note": "A directory, not a document. Call read_doc again with one of these paths.",
            "raw_refs": [relative],
        }

    if not resolved.exists():
        raise FileNotFoundError(
            f"{relative} does not exist. Call read_doc on its directory to see what is there."
        )

    text = resolved.read_text(encoding="utf-8")
    truncated = len(text) > MAX_DOC_CHARS
    return {
        "path": relative,
        "kind": "file",
        "truncated": truncated,
        "text": text[:MAX_DOC_CHARS],
        "raw_refs": [relative],
    }


SEARCH_PRECEDENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "What to look for: a contract id, a clause, a customer. Leave empty to "
                "list every precedent on file."
            ),
        }
    },
    "additionalProperties": False,
}


def search_precedents(ctx, query: str = "") -> dict[str, Any]:
    from .. import precedents

    found = precedents.search(ctx.conn, query)
    return {
        "query": query,
        "count": len(found),
        "precedents": [
            {"precedent_id": p["precedent_id"], "subject": p["subject"],
             "decided_by": p["decided_by"], "decision": p["decision"],
             "clause": p["clause"], "scope": p["scope"], "evidence": p["evidence"],
             "decided_at": p["decided_at"]}
            for p in found
        ],
        "note": ("A precedent is what a human decided in a past case, not a rule. It "
                 "binds only a case that genuinely matches; a rule that generalises it "
                 "needs the controller's approval."),
    }


def _json(value: Any) -> str:
    return json.dumps(value, default=str)
