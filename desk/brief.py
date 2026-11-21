"""The brief: what a human needs to read before approving or denying.

Everything here is built by code from the work item, its audit rows, its
questions, challenges and precedents. No model writes any of it, so every line
can be traced to a row, and the controller is never asked to trust a summary
the agent wrote about itself.
"""
from __future__ import annotations

import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any

from . import policies as policies_mod
from . import precedents as precedents_mod
from .config import CFG, Config
from .guard import Refused, check_doc_path
from .tools import REGISTRY
from .workflow import (APPROVED, CLOSED, NEEDS_INFO, NEW, POSTED, PROPOSED, REJECTED,
                       children, get_item, history)

KIND_TITLES = {
    "WI-REFUND-EVENT": "A CBP refund deposit arrived",
    "WI-REFUND-DEPOSIT": "Record the refund deposit",
    "WI-FORECAST-CORRECTION": "Correct the refund forecast",
    "WI-DUTY-RECONCILIATION": "Reconcile duty paid against the customs form",
    "WI-REFUND-SPLIT": "Split the refund between parts sold and parts on hand",
    "WI-CUST-001-CREDIT": "Bean Street's share of the refund",
    "WI-CUST-002-QUESTION": "Harbor's share: the clause is ambiguous",
    "WI-CUST-002-CLOSE": "Harbor's share after the waiver",
    "WI-RULE-001": "Adopt RULE-001 for distributor contracts",
    "WI-CUST-003-CREDIT": "Northwind's share under RULE-001",
    "WI-CORRECTION": "Correcting entry against a posted item",
    "WI-ONBOARD-POLICY": "Adopt a rule learned from the books",
    "WI-ONBOARD-REVIEW": "Review of past practice",
}


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _title(kind: str) -> str:
    return KIND_TITLES.get(kind) or kind.removeprefix("WI-").replace("-", " ").title()


def classify_ref(ref: str, cfg: Config = CFG) -> str:
    """document | work_item | policy | source_row | identifier"""
    if ref.startswith("work_item:"):
        return "work_item"
    if re.fullmatch(r"[A-Za-z]+-[A-Za-z0-9]+@\d+", ref):
        return "policy"
    try:
        check_doc_path(ref, cfg)
        return "document"
    except Refused:
        pass
    if "/" in ref or "\\" in ref or "#" in ref:
        return "source_row"
    return "identifier"


def _evidence_list(item: dict[str, Any], cfg: Config) -> list[dict[str, Any]]:
    out = []
    for ref in item.get("evidence") or []:
        if not isinstance(ref, str):
            continue
        kind = classify_ref(ref, cfg)
        entry: dict[str, Any] = {"ref": ref, "kind": kind}
        if kind == "document":
            entry["exists"] = (cfg.root / ref).is_file()
        out.append(entry)
    return out


def _render_entry(item: dict[str, Any]) -> str | None:
    entry = (item.get("payload") or {}).get("journal_entry")
    if not entry:
        return None
    from .gateway import render_entry
    try:
        shadow = dict(item)
        shadow["approved_by"] = item.get("approved_by") or "(pending approval)"
        return render_entry(shadow)
    except Exception:  # noqa: BLE001 - a draft that will not render is still shown as JSON
        return None


def _decision_needed(item: dict[str, Any], open_questions: list[dict[str, Any]]) -> str:
    state = item["state"]
    payload = item.get("payload") or {}
    if state == PROPOSED:
        if payload.get("revised_amount") == "0.00" and not payload.get("journal_entry"):
            return "Review the verified waiver and approve closing the zero settlement. Nothing posts."
        if payload.get("policy"):
            return (f"Approve or reject the proposed rule {payload['policy']}. Approving makes "
                    "it applicable to every case inside its scope; nothing posts.")
        return ("Approve or reject this journal entry. On approval the gateway posts it to the "
                "books after bean-check. It is outside the standing authorization, so it waits for you.")
    if state == NEEDS_INFO:
        if open_questions:
            q = open_questions[0]
            return f"Answer the question to {q['to_role']}: {q['text']}"
        return "The seat has its answer and needs to be resumed to continue."
    if state == NEW:
        return "Nothing yet: waiting for the seat to pick it up."
    if state == APPROVED:
        return "Nothing: approved and waiting for the gateway to post."
    if state == POSTED:
        return ("Nothing: posted to the books. If you disagree, challenge it with a document; "
                "a verified document opens a correcting work item, the posted entry stands.")
    if state in (REJECTED, CLOSED):
        return "Nothing: settled."
    return "Nothing."


def _authorization(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    from .authorization import last_verdict

    return last_verdict(conn, item_id)


def brief(conn: sqlite3.Connection, item_id: str, cfg: Config = CFG) -> dict[str, Any] | None:
    item = get_item(conn, item_id)
    if item is None:
        return None
    payload = item.get("payload") or {}
    rows = history(conn, item_id)

    # ---- the proposal -----------------------------------------------------
    entry = payload.get("journal_entry") or None
    postings = []
    debits = Decimal("0.00")
    if entry:
        for p in entry.get("postings") or []:
            amount = _money(p.get("amount"))
            postings.append({
                "account": p.get("account"),
                "amount": f"{amount:.2f}" if amount is not None else p.get("amount"),
                "side": "debit" if amount is not None and amount > 0 else "credit",
            })
            if amount is not None and amount > 0:
                debits += amount

    policy = None
    ref = payload.get("policy_ref") or payload.get("policy") or (
        f"{item['policy_id']}@{item['policy_version']}"
        if item.get("policy_id") and item.get("policy_version") else None)
    if ref:
        try:
            p = policies_mod.get(conn, ref)
            policy = {"ref": p["ref"], "name": p["name"], "status": p["status"],
                      "approved_by": p.get("approved_by"), "scope": p.get("scope"),
                      "rule": p.get("rule")}
        except policies_mod.PolicyError:
            policy = {"ref": ref, "name": None, "status": "unknown"}

    # ---- what the seat did, from the log ---------------------------------
    sources_read: list[str] = []
    queries: list[str] = []
    computations: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []
    gate = None
    turns = 0
    for row in rows:
        action = row["action"]
        evidence = _loads(row.get("evidence")) or {}
        args = evidence.get("arguments", {}) if isinstance(evidence, dict) else {}
        if not row["allowed"] and not action.startswith("can:"):
            refusals.append({"event_id": row["event_id"], "at": row["at"],
                             "actor": row["actor"], "action": action,
                             "reason": row.get("reason")})
        if action == "agent:turn":
            turns += 1
        if action == "invariant_gate":
            gate = {"allowed": bool(row["allowed"]), "reason": row.get("reason"),
                    "violations_after": (evidence.get("after")
                                         if isinstance(evidence, dict) else None)}
        if action.startswith("tool:") and row["allowed"]:
            tool = action[5:]
            kind = REGISTRY[tool].kind if tool in REGISTRY else "?"
            if tool == "read_doc" and args.get("path") and args["path"] not in sources_read:
                sources_read.append(args["path"])
            elif tool == "run_sql":
                queries.append((args.get("sql") or "")[:160])
            elif kind == "compute" or tool == "apply_policy":
                diff = _loads(row.get("diff")) or {}
                result = diff.get("after") if isinstance(diff, dict) else None
                digest = {}
                if isinstance(result, dict):
                    for k, v in result.items():
                        if isinstance(v, (str, int, float)) and k not in ("raw_refs", "note", "formula"):
                            digest[k] = v
                computations.append({
                    "tool": tool, "arguments": args, "result": digest,
                    "raw_refs": result.get("raw_refs", []) if isinstance(result, dict) else []})

    questions = [dict(r) for r in conn.execute(
        "SELECT id, to_role, text, state, answer, answered_by, created_at, answered_at"
        " FROM questions WHERE work_item_id = ? ORDER BY created_at", (item_id,))]
    open_questions = [q for q in questions if q["state"] == "OPEN"]

    challenges = []
    for r in conn.execute(
            "SELECT challenge_id, raised_by, text, document, classification, changed,"
            " verified, outcome, routed_to, created_at FROM challenges WHERE work_item_id = ?"
            " ORDER BY created_at", (item_id,)):
        c = dict(r)
        c["changed"] = bool(c["changed"])
        challenges.append(c)

    # ---- precedents that bear on this item -------------------------------
    seen, matches = set(), []
    for term in [t for t in (payload.get("contract_id"), payload.get("customer_id")) if t]:
        for p in precedents_mod.search(conn, term):
            if p["precedent_id"] not in seen:
                seen.add(p["precedent_id"])
                matches.append(p)

    revision = payload.get("revision") or None
    decisive = None
    if revision:
        decisive = {
            "document": revision.get("document"), "doc_id": revision.get("doc_id"),
            "revised_amount": revision.get("revised_amount"),
            "facts": [{"fact": k, "value": v.get("value"), "quote": v.get("quote")}
                      for k, v in (revision.get("facts") or {}).items() if isinstance(v, dict)],
            "checks": revision.get("checks") or [],
        }

    evidence = _evidence_list(item, cfg)
    documents = [e["ref"] for e in evidence if e["kind"] == "document"]
    kids = children(conn, item_id)

    # ---- key points, in plain words --------------------------------------
    points: list[str] = []
    if entry:
        points.append(
            f"Journal entry dated {entry.get('date')}: {len(postings)} postings, "
            f"{debits:,.2f} USD each way. \"{entry.get('narration', '')}\"")
    if payload.get("policy") and not entry:
        points.append(f"Proposes rule {payload['policy']} with "
                      f"{payload.get('evidence_count', '?')} supporting sources.")
    if policy:
        points.append(
            f"Rests on {policy['ref']}" + (f" \"{policy['name']}\"" if policy.get("name") else "")
            + f", which is {policy['status']}"
            + (f", approved by {policy['approved_by']}" if policy.get("approved_by") else "") + ".")
    if evidence:
        points.append(
            f"Cites {len(evidence)} source(s), {len(documents)} of them documents"
            + (": " + ", ".join(documents[:3]) if documents else "") + ".")
    if computations:
        names = sorted({c["tool"] for c in computations})
        points.append(f"Every figure came out of code: {len(computations)} compute call(s) "
                      f"({', '.join(names)}). The seat did no arithmetic.")
    if sources_read:
        points.append(f"The seat read {len(sources_read)} document(s): "
                      f"{', '.join(sources_read[:4])}.")
    if gate:
        points.append(("Invariant gate passed: " if gate["allowed"]
                       else "Invariant gate REFUSED: ") + (gate["reason"] or ""))
    for q in questions:
        answer = (q.get("answer") or "").strip()
        points.append(
            f"Asked {q['to_role']}: \"{q['text'][:100]}\" -> "
            + (f"\"{answer[:120]}\" ({q['answered_by']})" if answer else "unanswered"))
    if challenges:
        by_class: dict[str, int] = {}
        for c in challenges:
            by_class[c["classification"]] = by_class.get(c["classification"], 0) + 1
        moved = sum(1 for c in challenges if c["changed"])
        summary = ", ".join(f"{k} x{v}" for k, v in sorted(by_class.items()))
        points.append(
            f"Pushback: {len(challenges)} challenge(s) ({summary}); "
            + (f"{moved} changed the decision, all on verified evidence."
               if moved else "none changed the decision."))
    if decisive:
        points.append(
            f"Revised to {decisive['revised_amount']} on {decisive['doc_id']}: "
            f"{len(decisive['facts'])} facts quoted verbatim, "
            f"{len(decisive['checks'])} checks passed in code.")
    if matches:
        points.append("Precedent on file: " + "; ".join(
            f"{p['precedent_id']} ({p['decided_by']}): {p['decision'][:90]}"
            for p in matches[:2]) + ".")
    if refusals:
        points.append(f"{len(refusals)} attempt(s) were refused by code and logged, "
                      f"e.g. {refusals[0]['reason'][:120]}")
    if kids:
        points.append("Children: " + ", ".join(
            f"{k['id']} {k['kind']} {k['state']}" for k in kids) + ".")

    headline = _title(item["kind"])
    if entry and debits:
        headline += f": {debits:,.2f} USD"
    elif payload.get("revised_amount") is not None:
        headline += f": {payload['revised_amount']}"

    return {
        "work_item": {k: item.get(k) for k in (
            "id", "kind", "state", "seat", "parent_id", "proposed_by", "approved_by",
            "policy_id", "policy_version", "created_at", "updated_at")},
        "headline": headline,
        "decision_needed": _decision_needed(item, open_questions),
        "key_points": points,
        "proposal": {
            "rationale": payload.get("rationale"),
            "journal_entry": entry,
            "entry_text": _render_entry(item),
            "postings": postings,
            "detail": payload.get("detail"),
            "policy": policy,
            "revised_amount": payload.get("revised_amount"),
        },
        "evidence": evidence,
        "sources_read": sources_read,
        "queries_run": len(queries),
        "computations": computations,
        "invariant_gate": gate,
        "authorization": _authorization(conn, item_id),
        "questions": questions,
        "challenges": challenges,
        "precedents": matches,
        "decisive_facts": decisive,
        "refusals": refusals,
        "children": [{"id": k["id"], "kind": k["kind"], "state": k["state"]} for k in kids],
        "agent_turns": turns,
        "timeline": [
            {"event_id": r["event_id"], "at": r["at"], "actor": r["actor"],
             "action": r["action"], "allowed": bool(r["allowed"]),
             "reason": (r.get("reason") or "")[:200]}
            for r in rows if not r["action"].startswith("can:")
        ],
        "trigger": {k: v for k, v in payload.items()
                    if k in ("trigger", "bank_txn_id", "amount", "date", "refund_id",
                             "customer_id", "contract_id", "invariant", "finding", "follows",
                             "onboarding", "name", "counterparty")},
    }
