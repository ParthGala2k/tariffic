"""What a real human does through the API: approve, reject, send back, answer,
adopt a rule, resume a seat.

The scripted personas (desk/personas.py) play these parts in repeatable runs.
This module is the same actions taken by a person at the screen. Every one of
them goes through can() and transition(), so a human has exactly the powers the
role matrix gives them and not one more: the controller cannot approve what
they proposed, the lawyer cannot approve a rule, nobody but the gateway posts.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any

from . import gateway, policies
from .config import CFG, Config
from .db import log_event_now, now
from .evidence import load_inbox_document
from .perms import Denied, require, role_of
from .precedents import record as record_precedent
from .workflow import NEEDS_INFO, close_parent_if_settled, get_item, transition


class NotFound(Exception):
    pass


def _item(conn: sqlite3.Connection, item_id: str) -> dict[str, Any]:
    item = get_item(conn, item_id)
    if item is None:
        raise NotFound(f"no work item {item_id}")
    return item


def approve(
    conn: sqlite3.Connection,
    item_id: str,
    actor: str,
    reason: str = "",
    *,
    post: bool = True,
    cfg: Config = CFG,
) -> dict[str, Any]:
    """Approve a proposal. A journal entry is then posted by the gateway; a rule
    proposal adopts the rule and the ticket closes. Refusals raise Denied."""
    item = _item(conn, item_id)
    payload = item.get("payload") or {}
    zero_settlement = item["kind"] == "WI-CUST-002-CLOSE" and not payload.get("journal_entry")
    if zero_settlement:
        checks = (payload.get("revision") or {}).get("checks") or []
        try:
            amount = Decimal(str(payload.get("revised_amount", "-1")))
        except InvalidOperation as error:
            raise ValueError("a settlement amount must be a number") from error
        if amount != 0 or not checks or not all(c.get("passed") is True for c in checks):
            raise ValueError("a zero settlement requires a verified evidence revision")
    reviewed = transition(conn, item_id, "approve", actor,
                          reason=reason or f"{actor} approved via the API")
    result: dict[str, Any] = {"item": reviewed, "entry": None, "policy": None}

    policy_ref = (reviewed.get("payload") or {}).get("policy")
    if policy_ref:
        result["policy"] = policies.approve(
            conn, policy_ref, actor,
            reason=reason or f"{actor} approved {policy_ref} via the API")
        result["item"] = transition(conn, item_id, "close", "sync",
                                    reason=f"{policy_ref} approved; nothing to post",
                                    evidence=[policy_ref])
    elif post and (reviewed.get("payload") or {}).get("journal_entry"):
        posted = gateway.post(conn, item_id, cfg=cfg)
        result["item"], result["entry"] = posted["item"], posted["entry"]
    elif zero_settlement:
        result["item"] = transition(conn, item_id, "close", "sync",
                                    reason="controller reviewed the verified zero settlement; no entry to post")

    if item.get("parent_id") and not _run_in_progress():
        close_parent_if_settled(conn, item["parent_id"])
    return result


def _run_in_progress() -> bool:
    """A background run is still opening stages on the case; it closes the parent itself."""
    try:
        from .runs import MANAGER
    except ImportError:  # pragma: no cover - the CLI has no run manager
        return False
    return MANAGER.active() is not None


def reject(conn: sqlite3.Connection, item_id: str, actor: str, reason: str) -> dict[str, Any]:
    _item(conn, item_id)
    if not reason.strip():
        raise ValueError("a rejection needs a reason")
    item = transition(conn, item_id, "reject", actor, reason=reason)
    if item.get("parent_id") and not _run_in_progress():
        close_parent_if_settled(conn, item["parent_id"])
    return item


def send_back(conn: sqlite3.Connection, item_id: str, actor: str, reason: str) -> dict[str, Any]:
    """PROPOSED -> NEEDS_INFO with the controller's reason, for the seat to try again."""
    _item(conn, item_id)
    if not reason.strip():
        raise ValueError("say what is wrong so the seat can fix it")
    return transition(conn, item_id, "revise", actor, reason=f"sent back: {reason}")


def answer(
    conn: sqlite3.Connection,
    question_id: str,
    actor: str,
    text: str,
    *,
    document: str | None = None,
    rule_proposal: dict[str, Any] | None = None,
    cfg: Config = CFG,
) -> dict[str, Any]:
    """A human answers a seat's question.

    The answer is recorded and the ticket stays NEEDS_INFO until the seat is
    resumed. An answer is never evidence by itself; a document attached must be
    a real inbox document. A lawyer's answer on a contract becomes a precedent,
    and a rule the lawyer proposes with it is stored PROPOSED for the controller.
    """
    row = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if row is None:
        raise NotFound(f"no question {question_id}")
    question = dict(row)
    if question["state"] != "OPEN":
        raise Denied(f"refused: {question_id} is already {question['state']}",
                     actor=actor, action="answer", subject=question["work_item_id"])
    if not text or not text.strip():
        raise ValueError("an answer needs text")
    require(conn, actor, "answer", subject=question["work_item_id"],
            evidence={"question_id": question_id})
    if question["to_role"] not in (role_of(actor), actor):
        reason = f"refused: {question_id} was put to {question['to_role']}, not {actor}"
        log_event_now(conn, actor=actor, action="answer", subject=question["work_item_id"],
                      allowed=False, reason=reason)
        raise Denied(reason, actor=actor, action="answer", subject=question["work_item_id"])
    if document:
        load_inbox_document(document, cfg)      # EvidenceRejected if it is not citable

    conn.execute(
        "UPDATE questions SET state = 'ANSWERED', answer = ?, answered_by = ?, answered_at = ?"
        " WHERE id = ?", (text.strip(), actor, now(), question_id))
    log_event_now(
        conn, actor=actor, action="answer", subject=question["work_item_id"], allowed=True,
        reason=f"{question_id}: {text.strip()[:300]}",
        evidence={"question_id": question_id, "document": document, "via": "api",
                  "document_provided": bool(document)},
    )

    precedent_id = rule_ref = None
    if role_of(actor) == "human_lawyer":
        precedent_id = f"PRE-{question_id}"
        record_precedent(
            conn, precedent_id=precedent_id,
            subject=(rule_proposal or {}).get("subject") or question["text"][:120],
            decided_by=actor, decision=text.strip(),
            clause=(rule_proposal or {}).get("clause"),
            scope=(rule_proposal or {}).get("scope"),
            evidence=[document] if document else [],
            question_id=question_id, work_item_id=question["work_item_id"], actor=actor,
        )
        if rule_proposal:
            rule_ref = _propose_rule(conn, actor, rule_proposal, question["work_item_id"],
                                     document)

    return {"question": dict(conn.execute("SELECT * FROM questions WHERE id = ?",
                                          (question_id,)).fetchone()),
            "precedent_id": precedent_id, "rule_proposed": rule_ref,
            "note": ("Recorded. The seat continues when the work item is resumed; an answer "
                     "alone never changes a figure, only a verified document does.")}


def _propose_rule(conn, actor, rule_proposal, work_item_id, document) -> str:
    require(conn, actor, "propose_policy", subject=work_item_id)
    rule_id = rule_proposal.get("id")
    rule_text = rule_proposal.get("rule")
    if not rule_id or not rule_text:
        raise ValueError("a rule proposal needs an id and the rule in words")
    kind = rule_proposal.get("kind") or "distributor_passthrough"
    if kind not in policies.EXECUTORS or kind == "document":
        raise ValueError(f"rule kind {kind!r} has no executor; use one of "
                         f"{', '.join(sorted(k for k in policies.EXECUTORS if k != 'document'))}")
    extra = {k: v for k, v in rule_proposal.items()
             if k not in ("id", "rule", "kind", "name", "scope", "applies_to", "subject", "clause")}
    stored = policies.store(
        conn, policy_id=rule_id,
        name=rule_proposal.get("name") or f"{rule_id}: proposed by {actor}",
        rule={"kind": kind, "rule": rule_text,
              "applies_to": rule_proposal.get("applies_to") or [], **extra},
        status=policies.PROPOSED,
        evidence=[document] if document else [],
        evidence_count=len(rule_proposal.get("applies_to") or []) or 1,
        reason=(f"Proposed by {actor} when answering a question. It stays PROPOSED until "
                f"the controller approves it; nothing applies it yet."),
        actor=actor, scope=rule_proposal.get("scope"), work_item_id=work_item_id,
    )
    return stored["ref"]


def approve_policy(conn: sqlite3.Connection, policy_ref: str, actor: str,
                   reason: str = "") -> dict[str, Any]:
    return policies.approve(conn, policy_ref, actor,
                            reason=reason or f"{actor} approved {policy_ref} via the API")


def resume_task(conn: sqlite3.Connection, item: dict[str, Any]) -> str:
    """The prompt that hands a seat its answered question(s) and asks it to go on."""
    rows = conn.execute(
        "SELECT id, to_role, text, answer, answered_by FROM questions WHERE work_item_id = ?"
        " ORDER BY created_at", (item["id"],)).fetchall()
    lines = [f"You asked about {item['id']} ({item['kind']}) and have been answered.", ""]
    for q in rows:
        lines.append(f"- You asked {q['to_role']}: {q['text']}")
        lines.append(f"  {q['answered_by'] or q['to_role']} answered: "
                     f"{q['answer'] or '(no answer yet)'}")
    lines += ["",
              "Use search_precedents to see whether the answer was recorded as a precedent, and "
              "get_policies to see whether a rule now covers this case. Then either propose "
              "(citing the precedent or approved rule and a source document) or, if the answer "
              "still does not decide it, ask again. An answer with no document attached is "
              "opinion and does not change any figure."]
    return "\n".join(lines)


def resume(conn: sqlite3.Connection, item_id: str, llm, *, cfg: Config = CFG):
    """Run the seat again on a NEEDS_INFO item whose question has been answered."""
    from . import agent

    item = _item(conn, item_id)
    if item["state"] != NEEDS_INFO:
        raise Denied(f"refused: {item_id} is {item['state']}, only NEEDS_INFO items are resumed",
                     actor="api", action="resume", subject=item_id)
    if not item.get("seat"):
        raise Denied(f"refused: {item_id} has no seat to resume", actor="api",
                     action="resume", subject=item_id)
    open_q = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE work_item_id = ? AND state = 'OPEN'", (item_id,)
    ).fetchone()[0]
    if open_q:
        raise Denied(f"refused: {item_id} still has {open_q} unanswered question(s)",
                     actor="api", action="resume", subject=item_id)
    return agent.run_seat(conn, item["seat"], item_id, llm, resume_task(conn, item), cfg=cfg)
