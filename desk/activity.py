"""Live activity for the UI, read straight off the audit log.

There is no second channel. A seat's "thinking mode" is a reading of the last
events it logged, so what the screen shows is exactly what the log holds, and
anything the screen shows can be answered with `python -m desk why <id>`.

Modes a seat can be in:

  idle              nothing picked up
  picking_up        agent:start
  thinking          a model turn that produced text and/or chose tools
  investigating     read_doc, run_sql, search_precedents
  consulting_rules  get_policies, apply_policy
  computing         any compute tool (match_deposit, split_refund, ...)
  proposing         propose, propose_policy
  asking            ask
  revising          revise_proposal
  refused           the last tool call was refused by code
  waiting_approval  the seat finished with the item PROPOSED
  waiting_answer    the seat finished with the item NEEDS_INFO
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .tools import REGISTRY, SEAT_TOOLS

SEATS = tuple(SEAT_TOOLS)

SEAT_NAMES = {
    "seat1": "Trade compliance",
    "seat2": "Cost accounting",
    "seat3": "Revenue accounting",
}
ACTOR_NAMES = {
    "standing_authorization": "Standing authorization",
    "human_controller": "CFO (scripted)",
    "human_lawyer": "Lawyer",
    "human_cfo": "CFO",
    "human_auditor": "Auditor",
    "gateway": "Gateway",
    "sync": "Sync",
    "code": "Code",
}

_READ = {n for n, t in REGISTRY.items() if t.kind == "read"}
_COMPUTE = {n for n, t in REGISTRY.items() if t.kind == "compute"}
_RULES = {"get_policies", "apply_policy"}
_MODE_BY_TOOL = {
    **{n: "investigating" for n in _READ - _RULES},
    **{n: "consulting_rules" for n in _RULES},
    **{n: "computing" for n in _COMPUTE},
    "propose": "proposing",
    "propose_policy": "proposing",
    "ask": "asking",
    "revise_proposal": "revising",
}


def who(actor: str) -> str:
    if actor.startswith("ai_agent."):
        return SEAT_NAMES.get(actor.split(".", 1)[1], actor)
    return ACTOR_NAMES.get(actor, actor)


def seat_of(actor: str) -> str | None:
    return actor.split(".", 1)[1] if actor.startswith("ai_agent.") else None


# --------------------------------------------------------------------------- #
# the plain reading: the same row, with ids and paths replaced by names
# --------------------------------------------------------------------------- #

# Rows only a developer reads: the run harness, sync, and duplicates of a tool
# call that is already narrated (the policies-table row behind propose_policy).
TECHNICAL_ONLY = {"run:step", "run:tie_outs", "run:run", "run:reset", "sync", "match_question",
                  "propose_policy", "approve_policy", "ask"}

_ITEM_ID = re.compile(r"\bWI-\d{4}\b")
_POLICY_REF = re.compile(r"\b(POL-L\d|POL-\d{3}|RULE-\d{3}|BAD-[A-Z0-9-]+)(@\d+)?\b")
_PATH = re.compile(r"\b(?:data|personas|systems)/[\w./-]+")
_SEAT_ID = re.compile(r"\bseat([123])\b")
_PERSON_ID = re.compile(r"\bhuman_(controller|lawyer|cfo|auditor)\b")
_RECORD_ID = re.compile(r"\b(?:PRE-|Q-)[A-Z0-9-]+(?:[+T]\d+)*:\s*")

# What a compute tool does, in the words of the person reading the feed.
COMPUTE_PLAIN = {
    "sum_duties": "is adding up the duties on every entry",
    "match_deposit": "is matching the deposit to the refund notice",
    "breakdown_receivable": "is breaking the refund receivable down by programme",
    "split_refund": "is splitting the refund between parts sold and parts on hand",
    "surcharge_on_invoices": "is adding up the surcharge billed on the invoices",
}


def names_for(conn: sqlite3.Connection) -> dict[str, str]:
    """Plain names for the ids the log carries: work items and policies."""
    from .brief import KIND_TITLES

    names: dict[str, str] = {}
    for r in conn.execute("SELECT id, kind, payload FROM work_items"):
        payload = _json(r["payload"]) or {}
        if r["kind"] == "WI-ONBOARD-POLICY":
            names[r["id"]] = f"the rule \u2018{payload.get('name') or 'learned from the books'}\u2019"
        elif r["kind"] == "WI-ONBOARD-REVIEW":
            names[r["id"]] = "the review of past practice"
        elif r["kind"] == "WI-REFUND-EVENT":
            names[r["id"]] = "the refund case"
        else:
            names[r["id"]] = f"\u2018{KIND_TITLES.get(r['kind']) or r['kind']}\u2019"
    for r in conn.execute("SELECT policy_id, version, name FROM policies"):
        label = f"the rule \u2018{r['name']}\u2019"
        names[f"{r['policy_id']}@{r['version']}"] = label
        names.setdefault(r["policy_id"], label)
    return names


def humanize(text: str, names: dict[str, str]) -> str:
    """Replace ids, policy refs, seat codes and file paths with their plain names."""
    from .plain import document_title

    def doc(m: re.Match) -> str:
        path = m.group(0)
        return document_title(path) if path.startswith("data/") else ""

    text = _PATH.sub(doc, text)
    text = text.replace("scripted controller: ", "")
    text = _POLICY_REF.sub(lambda m: names.get(m.group(0)) or names.get(m.group(1)) or "a rule", text)
    text = _ITEM_ID.sub(lambda m: names.get(m.group(0), "the item"), text)
    text = _SEAT_ID.sub(lambda m: SEAT_NAMES["seat" + m.group(1)], text)
    text = _PERSON_ID.sub(lambda m: "the " + ("CFO" if m.group(1) == "cfo" else m.group(1)), text)
    text = _RECORD_ID.sub("", text)
    text = text.replace("golden tables", "the records").replace("a journal entry", "an entry")
    text = re.sub(r"\(\s*\)", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def plain_label(event: dict[str, Any], label: str, names: dict[str, str]) -> str:
    """One line a finance person reads: no ids, no paths, no tool names."""
    action = event["action"]
    name = who(event["actor"])
    allowed = bool(event["allowed"])
    reason = humanize((event.get("reason") or "").replace("\n", " "), names)
    subject = event.get("subject")
    item = names.get(subject, "the item") if subject else "the item"
    evidence = _json(event.get("evidence")) or {}
    args = evidence.get("arguments", {}) if isinstance(evidence, dict) else {}

    if action == "create_work_item":
        if reason.startswith("onboarding:"):
            if item == "the review of past practice":
                return "Reviewing past practice: the September forecast method"
            return f"Learning {item} from the policy and the books"
        if subject and subject in names and names[subject] == "the refund case":
            return "A tariff refund arrived; the desk opened the case"
        return f"New: {_cap(item)}"
    if action == "tool:propose_policy" and allowed:
        rule = names.get(str(args.get("policy_id") or ""), "a rule")
        return f"{name} proposed {rule}"
    if action == "tool:run_sql" and allowed:
        return f"{name} is querying the records"
    if action.startswith("tool:") and allowed and action[5:] in COMPUTE_PLAIN:
        return f"{name} {COMPUTE_PLAIN[action[5:]]}"
    if action == "propose":
        return f"{_cap(item)} is ready for the controller"
    if action == "agent:start":
        return f"{name} picked up {item}"
    if action == "approve":
        return f"{name} approved {item}" if allowed else f"{name} may not approve {item}: {reason[:120]}"
    if action == "reject":
        return f"{name} declined {item}: {reason[:120]}"
    if action == "revise":
        return f"{name} sent {item} back: {reason[:120]}"
    if action == "post":
        return f"Posted to the books: {item}" if allowed else f"The books refused {item}: {reason[:120]}"
    if action == "close":
        if not allowed:
            return f"{_cap(item)} could not be closed: {reason[:120]}"
        if "nothing to adopt" in reason:
            return f"{_cap(item)} ended with nothing to adopt"
        if "nothing to post" in reason:
            return f"{_cap(item)} is in force"
        return f"{_cap(item)} settled: {reason[:120]}" if reason else f"{_cap(item)} settled"
    if action == "invariant_gate":
        return f"Checks passed on {item}" if allowed else f"A check stopped {item}: {reason[:140]}"
    if action == "invariant":
        return f"A check failed: {reason[:140]}"
    if action == "propose_policy":
        return f"{name} proposed {item}"
    if action == "approve_policy":
        return f"{name} adopted {reason.split(':')[0][:80]}" if allowed else f"{name} may not adopt a rule: {reason[:120]}"
    if action == "challenge":
        return f"{name} pushed back on {item}: {reason[:140]}"
    if action == "ask":
        return f"{name} asked a question on {item}"
    if action == "authorization:applied":
        return f"{_cap(item)} is routine and posts on the CFO's standing authorization: {reason[:140]}"
    if action == "authorization:needs_human":
        return f"{_cap(item)} needs the CFO: {reason[:160]}"
    if action == "challenge_reply":
        return f"{name} answered the pushback on {item}: {reason[:200]}"
    if action == "challenge_outcome":
        return f"Pushback on {item}: {reason.split(': ', 1)[-1][:160]}"
    if action == "answer":
        return f"{name} answered: {reason[:160]}" if allowed else f"{name} could not answer: {reason[:120]}"
    if action == "record_precedent":
        return f"Precedent kept: {reason[:140]}"
    if action.startswith("tool:") and not allowed:
        return f"A check stopped {name}: {reason[:160]}"
    if action.startswith("run:") or action == "sync":
        return humanize(label, names)
    return humanize(label, names)


def _technical_only(event: dict[str, Any]) -> bool:
    """True for rows only a developer reads. A model turn that only chose tools is one:
    the tool rows that follow say the same thing in plain words."""
    if event["action"] in TECHNICAL_ONLY:
        return True
    if event["action"] == "agent:turn":
        reason = event.get("reason") or ""
        return not reason or reason.startswith("turn ")
    return False


def enrich(event: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    mode, label = classify(event)
    return {
        "event_id": event["event_id"],
        "at": event["at"],
        "actor": event["actor"],
        "who": who(event["actor"]),
        "seat": seat_of(event["actor"]),
        "action": event["action"],
        "subject": event.get("subject"),
        "allowed": bool(event["allowed"]),
        "mode": mode,
        "label": label,
        "plain": plain_label(event, label, names or {}),
        "technical_only": _technical_only(event),
        "reason": event.get("reason"),
        "evidence": _json(event.get("evidence")),
        "diff": _json(event.get("diff")),
        "policy_version": event.get("policy_version"),
        "noise": event["action"].startswith("can:"),
    }


def _json(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def classify(event: dict[str, Any]) -> tuple[str, str]:
    """(mode, one-line label) for one audit row."""
    action = event["action"]
    actor = event["actor"]
    name = who(actor)
    allowed = bool(event["allowed"])
    reason = (event.get("reason") or "").replace("\n", " ")
    evidence = _json(event.get("evidence")) or {}
    args = evidence.get("arguments", {}) if isinstance(evidence, dict) else {}

    if action.startswith("tool:"):
        tool = action[5:]
        if not allowed:
            return "refused", f"{name} was refused {tool}: {reason[:160]}"
        mode = _MODE_BY_TOOL.get(tool, "working")
        if tool == "read_doc":
            return mode, f"{name} is reading {args.get('path', 'a document')}"
        if tool == "run_sql":
            return mode, f"{name} is querying the golden tables"
        if tool == "search_precedents":
            return mode, f"{name} is looking for precedents" + (
                f" on '{args['query']}'" if args.get("query") else "")
        if tool == "get_policies":
            return mode, f"{name} is checking which rules are approved"
        if tool == "apply_policy":
            return mode, f"{name} is applying {args.get('policy_ref', 'a rule')}"
        if tool == "propose":
            return mode, f"{name} proposed a journal entry"
        if tool == "propose_policy":
            return mode, f"{name} proposed a rule: {args.get('policy_id', '')}".rstrip(": ")
        if tool == "ask":
            return mode, f"{name} is asking {who(args.get('to_role', 'a human'))}"
        if tool == "revise_proposal":
            return mode, f"{name} revised the proposal on {args.get('evidence_doc', 'a document')}"
        return mode, f"{name} ran {tool}"

    if action == "agent:start":
        return "picking_up", f"{name} picked up {event.get('subject')}"
    if action == "agent:turn":
        calls = evidence.get("tool_calls", []) if isinstance(evidence, dict) else []
        if reason and not reason.startswith("turn "):
            return "thinking", f"{name}: {reason[:200]}"
        return "thinking", f"{name} is calling {', '.join(c.get('name', '?') for c in calls) or 'no tools'}"
    if action == "agent:finish":
        if "PROPOSED" in reason:
            return "waiting_approval", f"{name} finished; waiting for the controller"
        if "NEEDS_INFO" in reason:
            return "waiting_answer", f"{name} finished; waiting for an answer"
        return "idle", f"{name} finished"

    if action == "create_work_item":
        return "opened", f"{name} opened {event.get('subject')}: {reason[:120]}"
    if action == "approve":
        return ("approved" if allowed else "refused",
                f"{name} approved {event.get('subject')}" if allowed
                else f"{name} was refused approval of {event.get('subject')}: {reason[:120]}")
    if action == "reject":
        return "rejected", f"{name} rejected {event.get('subject')}: {reason[:120]}"
    if action == "revise":
        return "sent_back", f"{name} sent {event.get('subject')} back: {reason[:120]}"
    if action == "post":
        return ("posted" if allowed else "refused",
                f"Gateway posted {event.get('subject')} to the books" if allowed
                else f"Gateway refused to post {event.get('subject')}: {reason[:120]}")
    if action == "close":
        if not allowed:
            return "refused", f"closing {event.get('subject')} was refused: {reason[:120]}"
        return "closed", f"{event.get('subject')} closed: {reason[:120]}"
    if action == "invariant_gate":
        return ("gate_passed" if allowed else "gate_refused",
                f"Invariant gate on {event.get('subject')}: {reason[:140]}")
    if action == "invariant":
        return "violation", f"Invariant violated: {reason[:140]}"
    if action == "challenge":
        return "challenged", f"{name} pushed back on {event.get('subject')}: {reason[:140]}"
    if action in ("challenge_outcome", "challenge_acted_on", "challenge_not_verified"):
        return "challenge_settled", reason[:200]
    if action == "escalate":
        return "escalated", reason[:160]
    if action == "answer":
        return ("answered" if allowed else "unanswered"), f"{name}: {reason[:160]}"
    if action == "record_precedent":
        return "precedent", f"Precedent recorded: {reason[:140]}"
    if action == "propose_policy":
        return "rule_proposed", reason[:160]
    if action == "approve_policy":
        return ("rule_approved" if allowed else "refused"), f"{name}: {reason[:140]}"
    if action == "inbox:upload":
        return "evidence_uploaded", f"{name} uploaded {reason[:140]}"
    if action.startswith("run:"):
        return action.replace(":", "_"), reason[:160]
    if action.startswith("can:"):
        return "permission", f"{name} {'may' if allowed else 'may NOT'} {action[4:]}"
    return action, reason[:160] or action


def feed(
    conn: sqlite3.Connection,
    *,
    after: int = 0,
    limit: int = 200,
    subject: str | None = None,
    actor: str | None = None,
    include_permission_checks: bool = False,
) -> list[dict[str, Any]]:
    """Events after `after`, oldest first, each with its mode and label."""
    sql = "SELECT * FROM events WHERE event_id > ?"
    params: list[Any] = [after]
    if subject:
        sql += " AND subject = ?"
        params.append(subject)
    if actor:
        sql += " AND actor = ?"
        params.append(actor)
    if not include_permission_checks:
        sql += " AND action NOT LIKE 'can:%'"
    sql += " ORDER BY event_id LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))
    rows = conn.execute(sql, params).fetchall()
    names = names_for(conn) if rows else {}
    return [enrich(dict(r), names) for r in rows]


def last_event_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(event_id), 0) FROM events").fetchone()
    return int(row[0]) if row else 0


def seat_status(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """What each seat is doing right now, from its own last logged event."""
    status: dict[str, dict[str, Any]] = {}
    names = names_for(conn)
    for seat in SEATS:
        actor = f"ai_agent.{seat}"
        row = conn.execute(
            "SELECT * FROM events WHERE actor = ? AND action NOT LIKE 'can:%'"
            " ORDER BY event_id DESC LIMIT 1", (actor,),
        ).fetchone()
        if row is None:
            status[seat] = {"seat": seat, "name": SEAT_NAMES[seat], "mode": "idle",
                            "label": f"{SEAT_NAMES[seat]} is idle", "plain": "Free", "work_item": None,
                            "work_item_state": None, "since": None, "last_event_id": None,
                            "turns": 0, "tools": list(SEAT_TOOLS[seat])}
            continue
        event = enrich(dict(row), names)
        turns = conn.execute(
            "SELECT COUNT(*) FROM events WHERE actor = ? AND subject = ? AND action = 'agent:turn'",
            (actor, event["subject"]),
        ).fetchone()[0]
        mode, label, plain = event["mode"], event["label"], event["plain"]
        # The seat's own last event says what it did; the item's current state says
        # whether anyone has acted since. A posted or closed item means the seat is free.
        item_state = None
        if event["subject"]:
            found = conn.execute("SELECT state FROM work_items WHERE id = ?",
                                 (event["subject"],)).fetchone()
            item_state = found["state"] if found else None
        item_name = names.get(event["subject"] or "", "its last item")
        if mode in ("waiting_approval", "waiting_answer", "thinking", "proposing") and item_state in (
                "APPROVED", "POSTED", "REJECTED", "CLOSED"):
            mode = "idle"
            label = f"{SEAT_NAMES[seat]} is idle; {event['subject']} is {item_state}"
            plain = {"POSTED": f"Done: {item_name} is on the books",
                     "CLOSED": f"Done: {item_name} is settled",
                     "REJECTED": f"{_cap(item_name)} was declined",
                     "APPROVED": f"{_cap(item_name)} is approved; the gateway is posting it"}[item_state]
        elif mode == "waiting_answer" and item_state == "NEEDS_INFO":
            open_q = conn.execute(
                "SELECT COUNT(*) FROM questions WHERE work_item_id = ? AND state = 'OPEN'",
                (event["subject"],)).fetchone()[0]
            if not open_q:
                mode = "answered"
                label = f"{SEAT_NAMES[seat]} has its answer on {event['subject']}; waiting to resume"
                plain = f"Has the lawyer's answer on {item_name}; picking it back up"
        status[seat] = {
            "seat": seat, "name": SEAT_NAMES[seat], "mode": mode, "label": label, "plain": plain,
            "work_item": event["subject"], "work_item_state": item_state, "since": event["at"],
            "last_event_id": event["event_id"], "turns": turns,
            "tools": list(SEAT_TOOLS[seat]),
        }
    return status


def snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """One call for the dashboard header."""
    by_state = {
        r["state"]: r["n"] for r in conn.execute(
            "SELECT state, COUNT(*) AS n FROM work_items GROUP BY state")
    }
    policies = {
        r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM policies GROUP BY status")
    }
    return {
        "seats": seat_status(conn),
        "work_items": by_state,
        "awaiting_controller": [
            r[0] for r in conn.execute(
                "SELECT id FROM work_items WHERE state = 'PROPOSED' ORDER BY id")],
        "open_questions": conn.execute(
            "SELECT COUNT(*) FROM questions WHERE state = 'OPEN'").fetchone()[0],
        "policies": policies,
        "precedents": conn.execute("SELECT COUNT(*) FROM precedents").fetchone()[0],
        "challenges": conn.execute("SELECT COUNT(*) FROM challenges").fetchone()[0],
        "events": last_event_id(conn),
        "refused_attempts": conn.execute(
            "SELECT COUNT(*) FROM events WHERE allowed = 0").fetchone()[0],
    }
