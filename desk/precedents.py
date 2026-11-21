"""Precedents: past human decisions the desk can cite in a matching case.

A precedent is a record, not a rule. It says what a named human decided, about
what, and on what grounds. A rule generalises a precedent and needs the
controller's approval (that is Phase 4); citing a precedent needs only that the
case actually matches.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import log_event_now, now


def record(
    conn: sqlite3.Connection,
    *,
    precedent_id: str,
    subject: str,
    decided_by: str,
    decision: str,
    clause: str | None = None,
    scope: str | None = None,
    evidence: list[str] | None = None,
    question_id: str | None = None,
    work_item_id: str | None = None,
    actor: str = "sync",
) -> dict[str, Any]:
    conn.execute(
        "INSERT OR REPLACE INTO precedents (precedent_id, question_id, work_item_id,"
        " subject, decided_by, decision, clause, scope, evidence, decided_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (precedent_id, question_id, work_item_id, subject, decided_by, decision,
         clause, scope, json.dumps(evidence or []), now()),
    )
    log_event_now(
        conn, actor=actor, action="record_precedent", subject=work_item_id, allowed=True,
        reason=f"{precedent_id}: {decided_by} decided {subject}",
        evidence={"precedent_id": precedent_id, "clause": clause,
                  "sources": evidence or []},
    )
    return get(conn, precedent_id)


def get(conn: sqlite3.Connection, precedent_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM precedents WHERE precedent_id = ?", (precedent_id,)
    ).fetchone()
    if row is None:
        return None
    precedent = dict(row)
    precedent["evidence"] = json.loads(precedent["evidence"] or "[]")
    return precedent


def search(conn: sqlite3.Connection, query: str = "") -> list[dict[str, Any]]:
    """Find precedents whose subject, decision, clause or scope mentions the query."""
    rows = conn.execute("SELECT * FROM precedents ORDER BY decided_at").fetchall()
    found = []
    for row in rows:
        precedent = dict(row)
        precedent["evidence"] = json.loads(precedent["evidence"] or "[]")
        haystack = " ".join(
            str(precedent.get(field) or "")
            for field in ("subject", "decision", "clause", "scope", "precedent_id")
        ).lower()
        if not query or query.lower() in haystack:
            found.append(precedent)
    return found
