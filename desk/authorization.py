"""Standing authorization: the CFO decides once which routine entries the desk may post
on its own, and the desk only asks when it is genuinely stuck.

The authorization is a policy like any other (kind `standing_authorization`, seeded as
POL-A1 and adopted by the CFO). It names the work-item kinds it covers, a cap per kind,
and the conditions every covered proposal must meet. Code checks all of it; the model
never decides whether a review is needed.

A proposal posts without a person when ALL of these hold:
  - its kind is covered and its amount is under that kind's cap
  - every figure in it came out of a compute tool (the brief has computations)
  - the invariant gate passes (no new or worse violation)
  - no question is open on it and nobody has pushed back on it
  - it was never sent back and never revised on a document
  - for a customer credit: the contract clause is explicit

Anything else waits for the CFO, and the reason is logged so the screen can say why.
"""
from __future__ import annotations

import fnmatch
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from . import invariants, policies
from .config import CFG, Config
from .db import log_event_now
from .workflow import PROPOSED, get_item

ACTOR = "standing_authorization"
KIND = "standing_authorization"
APPLIED, NEEDS_HUMAN = "authorization:applied", "authorization:needs_human"

# Why a kind outside the authorization needs a person, in the person's words.
_OUTSIDE = {
    "-QUESTION": "a contract clause is open to more than one reading; a person decides",
    "-CLOSE": "a document changed what the customer is owed; a person reviews the settlement",
    "WI-DUTY-RECONCILIATION": "the books and Customs disagree; a person decides how to explain it",
    "WI-CORRECTION": "a correcting entry against a posted decision always gets a review",
    "WI-RULE-": "adopting a rule is the CFO's call",
    "WI-ONBOARD-": "adopting a rule is the CFO's call",
}


@dataclass(frozen=True)
class Verdict:
    covered: bool
    reason: str
    policy_ref: str | None = None
    cap: str | None = None
    amount: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"covered": self.covered, "reason": self.reason, "policy": self.policy_ref,
                "cap": self.cap, "amount": self.amount}


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _fmt(value: Decimal) -> str:
    return f"{value:,.2f}"


def in_force(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The adopted standing authorization, if any (the latest approved version)."""
    found = [p for p in policies.listing(conn, policies.APPROVED)
             if (p.get("rule") or {}).get("kind") == KIND]
    return sorted(found, key=lambda p: int(p["version"]))[-1] if found else None


def _cover_for(rule: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for pattern, cover in (rule.get("covers") or {}).items():
        if fnmatch.fnmatchcase(kind, pattern):
            return dict(cover)
    return None


def _why_outside(kind: str) -> str:
    for key, why in _OUTSIDE.items():
        if (key.startswith("-") and kind.endswith(key)) or (not key.startswith("-") and kind.startswith(key)):
            return why
    return "this kind of work is not covered by the standing authorization"


def check(conn: sqlite3.Connection, item_id: str, cfg: Config = CFG) -> Verdict:
    """Decide, from the record alone, whether the proposal may post without a person."""
    from .brief import brief as make_brief

    policy = in_force(conn)
    if policy is None:
        return Verdict(False, "no standing authorization is in force; every entry waits for the CFO")
    ref = policy["ref"]
    item = get_item(conn, item_id)
    if item is None:
        return Verdict(False, f"no work item {item_id}", ref)
    if item["state"] != PROPOSED:
        return Verdict(False, f"nothing to authorize: the item is {item['state']}", ref)

    kind, payload = item["kind"], item.get("payload") or {}
    cover = _cover_for(policy["rule"], kind)
    if cover is None:
        return Verdict(False, _why_outside(kind), ref)

    if payload.get("revision") or payload.get("revised_amount") is not None:
        return Verdict(False, "the proposal was revised on a document; a person reviews it", ref)
    sent_back = conn.execute(
        "SELECT COUNT(*) FROM events WHERE subject = ? AND action = 'revise' AND allowed = 1",
        (item_id,)).fetchone()[0]
    if sent_back:
        return Verdict(False, "it was sent back once already; a person reviews the new proposal", ref)
    pushed = conn.execute("SELECT COUNT(*) FROM challenges WHERE work_item_id = ?", (item_id,)).fetchone()[0]
    if pushed:
        return Verdict(False, "someone pushed back on it; a person decides", ref)
    open_q = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE work_item_id = ? AND state = 'OPEN'", (item_id,)).fetchone()[0]
    if open_q:
        return Verdict(False, "a question is still open on it", ref)

    b = make_brief(conn, item_id, cfg) or {}
    postings = (b.get("proposal") or {}).get("postings") or []
    if not postings:
        return Verdict(False, "there is no entry to post; a person settles it", ref)
    if not b.get("computations"):
        return Verdict(False, "not every figure came out of a compute tool; a person checks it", ref)
    amount = sum((_money(p["amount"]) for p in postings if p.get("side") == "debit"), Decimal("0.00"))
    cap = _money(cover.get("cap", "0"))
    if amount > cap:
        return Verdict(False, f"{_fmt(amount)} is above the {_fmt(cap)} cap for this kind of entry",
                       ref, _fmt(cap), _fmt(amount))

    if cover.get("clause"):
        customer_id = payload.get("customer_id")
        row = conn.execute("SELECT passthrough_clause FROM golden_billing_customers WHERE customer_id = ?",
                           (customer_id,)).fetchone() if customer_id else None
        clause = (row["passthrough_clause"] if row else "") or "unknown"
        if clause.lower() != str(cover["clause"]).lower():
            return Verdict(False, f"the contract clause is {clause}; only {cover['clause']} clauses post on their own",
                           ref, _fmt(cap), _fmt(amount))

    gate = invariants.gate(conn, item, cfg)
    if not gate.allowed:
        return Verdict(False, f"a check would fail: {gate.reason}", ref, _fmt(cap), _fmt(amount))

    return Verdict(True, f"routine: {_fmt(amount)} is under the {_fmt(cap)} cap, every figure computed, "
                         "all checks pass, nothing open on it", ref, _fmt(cap), _fmt(amount))


def apply(conn: sqlite3.Connection, item_id: str, cfg: Config = CFG) -> tuple[Verdict, dict[str, Any] | None]:
    """Log the verdict; if covered, approve as the standing authorization and let the gateway post."""
    from . import humans

    verdict = check(conn, item_id, cfg)
    log_event_now(
        conn, actor="code", action=APPLIED if verdict.covered else NEEDS_HUMAN, subject=item_id,
        allowed=True, reason=verdict.reason, evidence=verdict.as_dict(),
        policy_version=verdict.policy_ref,
    )
    if not verdict.covered:
        return verdict, None
    result = humans.approve(conn, item_id, ACTOR,
                            reason=f"standing authorization {verdict.policy_ref}: {verdict.reason}", cfg=cfg)
    return verdict, result


def last_verdict(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    """The most recent authorization verdict logged for an item, for the brief and the screen."""
    row = conn.execute(
        "SELECT action, reason, evidence FROM events WHERE subject = ? AND action IN (?, ?)"
        " ORDER BY event_id DESC LIMIT 1", (item_id, APPLIED, NEEDS_HUMAN)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["evidence"]) if row["evidence"] else {}
    except json.JSONDecodeError:
        data = {}
    return {"covered": row["action"] == APPLIED, "reason": row["reason"], **{k: v for k, v in data.items() if k != "reason"}}
