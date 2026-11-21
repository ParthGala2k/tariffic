"""Work-item state machine. The only way a work item changes.

The change and its audit event are written in one DB transaction.
A refused attempt is logged too, then Denied is raised.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import log_event, now
from .perms import Decision, Denied, can

NEW = "NEW"
PROPOSED = "PROPOSED"
NEEDS_INFO = "NEEDS_INFO"
APPROVED = "APPROVED"
POSTED = "POSTED"
REJECTED = "REJECTED"
CLOSED = "CLOSED"

STATES = (NEW, PROPOSED, NEEDS_INFO, APPROVED, POSTED, REJECTED, CLOSED)

# A child is finished, one way or another.
SETTLED = (POSTED, REJECTED, CLOSED)

# (current state, action) -> next state
TRANSITIONS: dict[tuple[str, str], str] = {
    (NEW, "propose"): PROPOSED,
    (NEW, "ask"): NEEDS_INFO,
    (PROPOSED, "ask"): NEEDS_INFO,
    (NEEDS_INFO, "propose"): PROPOSED,
    (PROPOSED, "approve"): APPROVED,
    (PROPOSED, "reject"): REJECTED,
    # An approval refused by the invariant gate goes back to the seat with the reason.
    (PROPOSED, "revise"): NEEDS_INFO,
    # A seat revising its own live proposal on verified evidence; it stays proposed.
    (PROPOSED, "revise_proposal"): PROPOSED,
    (NEEDS_INFO, "reject"): REJECTED,
    (APPROVED, "post"): POSTED,
    # A parent work item closes once every child has settled. A work item that
    # carries no journal entry - a policy approval - closes instead of posting.
    (NEW, "close"): CLOSED,
    (NEEDS_INFO, "close"): CLOSED,
    (APPROVED, "close"): CLOSED,
    (PROPOSED, "close"): CLOSED,
}

# Which columns each action is allowed to stamp, besides state/updated_at.
_STAMP: dict[str, str] = {
    "propose": "proposed_by",
    "approve": "approved_by",
}

_PATCHABLE = {"payload", "evidence", "seat", "parent_id", "policy_id", "policy_version"}

# Set False only in tests that deliberately bypass the invariant gate.
INVARIANT_GATE = True


class _Refusal(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for field in ("payload", "evidence"):
        if isinstance(item.get(field), str):
            try:
                item[field] = json.loads(item[field])
            except json.JSONDecodeError:
                pass
    return item


def get_item(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone())


def next_item_id(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
    return f"WI-{n + 1:04d}"


def create_work_item(
    conn: sqlite3.Connection,
    *,
    kind: str,
    seat: str | None,
    actor: str,
    payload: dict | None = None,
    evidence: list | None = None,
    parent_id: str | None = None,
    item_id: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Create a work item in state NEW. Only `sync` may do this."""
    decision = can(actor, "create_work_item")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not decision.allowed:
            raise _Refusal(decision.reason)
        item_id = item_id or next_item_id(conn)
        stamp = now()
        conn.execute(
            "INSERT INTO work_items (id, kind, state, seat, parent_id, payload, evidence,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (item_id, kind, NEW, seat, parent_id,
             json.dumps(payload or {}), json.dumps(evidence or []), stamp, stamp),
        )
        log_event(conn, actor=actor, action="can:create_work_item", subject=item_id,
                  allowed=True, reason=decision.reason)
        log_event(conn, actor=actor, action="create_work_item", subject=item_id, allowed=True,
                  reason=reason or f"{kind} opened",
                  diff={"before": None, "after": {"id": item_id, "kind": kind, "state": NEW}},
                  evidence=evidence or [])
        conn.execute("COMMIT")
    except _Refusal as refusal:
        conn.execute("ROLLBACK")
        _log_refusal(conn, actor, "create_work_item", item_id, refusal.reason, evidence)
        raise Denied(refusal.reason, actor=actor, action="create_work_item", subject=item_id)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get_item(conn, item_id)


def transition(
    conn: sqlite3.Connection,
    item: str | dict,
    action: str,
    actor: str,
    patch: dict | None = None,
    reason: str = "",
    evidence: list | dict | None = None,
    decision: Decision | None = None,
) -> dict[str, Any]:
    """Move a work item through the state machine.

    Returns the updated item. Raises Denied if permissions, separation of
    duties or the TRANSITIONS table refuse the move; the refusal is logged.

    `decision` carries a can() check the caller already made and logged - the
    tool registry and the gateway both check before they act. Passing it keeps
    one can:<action> row per action instead of two.
    """
    item_id = item if isinstance(item, str) else item["id"]
    patch = patch or {}
    bad = set(patch) - _PATCHABLE
    if bad:
        raise ValueError(f"patch may not set {sorted(bad)}")

    checked_here = decision is None
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = get_item(conn, item_id)
        if current is None:
            raise _Refusal(f"no such work item {item_id}")

        if checked_here:
            decision = can(actor, action, current)
            log_event(conn, actor=actor, action=f"can:{action}", subject=item_id,
                      allowed=decision.allowed, reason=decision.reason)
        if not decision.allowed:
            raise _Refusal(decision.reason)

        new_state = TRANSITIONS.get((current["state"], action))
        if new_state is None:
            raise _Refusal(
                f"invalid transition: {item_id} is {current['state']}, "
                f"{action!r} is not allowed from there"
            )

        # Invariants gate approval, judged by effect: a proposal is refused only
        # if posting it would create a new violation or deepen an existing one.
        if action == "approve" and INVARIANT_GATE:
            from .invariants import gate as invariant_gate

            verdict = invariant_gate(conn, current)
            log_event(conn, actor=actor, action="invariant_gate", subject=item_id,
                      allowed=verdict.allowed, reason=verdict.reason,
                      evidence={"before": [v.as_dict() for v in verdict.before],
                                "after": [v.as_dict() for v in verdict.after]})
            if not verdict.allowed:
                raise _Refusal(verdict.reason)

        if action == "close" and unsettled_children(conn, item_id):
            waiting = ", ".join(
                f"{c['id']} ({c['state']})" for c in unsettled_children(conn, item_id)
            )
            raise _Refusal(f"{item_id} still has unfinished children: {waiting}")

        sets, values = ["state = ?", "updated_at = ?"], [new_state, now()]
        for column, value in patch.items():
            sets.append(f"{column} = ?")
            values.append(json.dumps(value) if column in ("payload", "evidence") else value)
        stamp_column = _STAMP.get(action)
        if stamp_column:
            sets.append(f"{stamp_column} = ?")
            values.append(actor)
        values.append(item_id)
        conn.execute(f"UPDATE work_items SET {', '.join(sets)} WHERE id = ?", values)

        after = get_item(conn, item_id)
        log_event(
            conn,
            actor=actor,
            action=action,
            subject=item_id,
            allowed=True,
            reason=reason,
            diff={"before": _summary(current), "after": _summary(after)},
            evidence=evidence if evidence is not None else after.get("evidence"),
            policy_version=after.get("policy_version"),
        )
        conn.execute("COMMIT")
    except _Refusal as refusal:
        conn.execute("ROLLBACK")
        _log_refusal(conn, actor, action, item_id, refusal.reason, evidence,
                     log_can=checked_here)
        raise Denied(refusal.reason, actor=actor, action=action, subject=item_id)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return after


def _log_refusal(conn, actor, action, subject, reason, evidence, log_can=True) -> None:
    """Commit the refused attempt on its own, after the rollback.

    `log_can` is False when the caller already logged its own can() check, so a
    refusal from the TRANSITIONS table does not masquerade as a permission failure.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        if log_can:
            log_event(conn, actor=actor, action=f"can:{action}", subject=subject,
                      allowed=False, reason=reason)
        log_event(conn, actor=actor, action=action, subject=subject, allowed=False,
                  reason=reason, evidence=evidence)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": item["state"],
        "payload": item["payload"],
        "evidence": item["evidence"],
        "proposed_by": item["proposed_by"],
        "approved_by": item["approved_by"],
    }


def children(conn: sqlite3.Connection, parent_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM work_items WHERE parent_id = ? ORDER BY id", (parent_id,)
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def unsettled_children(conn: sqlite3.Connection, parent_id: str) -> list[dict[str, Any]]:
    return [c for c in children(conn, parent_id) if c["state"] not in SETTLED]


def close_parent_if_settled(
    conn: sqlite3.Connection, parent_id: str, actor: str = "sync"
) -> dict[str, Any] | None:
    """Close a parent work item, but only once every child has settled.

    Called by code after each successful post. A parent with an unfinished child
    is refused and the refusal is logged, so the gate is visible in the audit log.
    """
    parent = get_item(conn, parent_id)
    if parent is None or parent["state"] in SETTLED:
        return parent

    open_children = unsettled_children(conn, parent_id)
    if open_children:
        from .db import log_event_now

        waiting = ", ".join(f"{c['id']} ({c['state']})" for c in open_children)
        log_event_now(
            conn, actor=actor, action="close", subject=parent_id, allowed=False,
            reason=f"refused: {parent_id} still has unfinished children: {waiting}",
        )
        return None

    settled = children(conn, parent_id)
    names = ", ".join(c["id"] for c in settled) or "no children"
    child_ids = [c["id"] for c in settled]
    return transition(
        conn, parent_id, "close", actor,
        patch={"evidence": (parent.get("evidence") or []) + child_ids},
        reason=f"every child settled: {names}",
        evidence=child_ids,
    )


def history(conn: sqlite3.Connection, item_id: str) -> list[dict[str, Any]]:
    """The one query behind `python -m desk why <work_item_id>`."""
    rows = conn.execute(
        "SELECT event_id, at, actor, action, subject, allowed, reason, diff, evidence,"
        " policy_version FROM events WHERE subject = ? ORDER BY event_id",
        (item_id,),
    ).fetchall()
    return [dict(r) for r in rows]
