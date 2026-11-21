"""Simulated humans. Phase 1 and 2 run them in scripted mode.

Scripted personas make runs repeatable for scoring; live LLM personas are a
demo-only concern (CLAUDE.md section 5).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

import yaml

from .config import CFG, Config
from .db import log_event_now, now
from .perms import Denied
from .policies import PROPOSED as POLICY_PROPOSED, approve as approve_policy, listing
from .workflow import get_item, transition

CONTROLLER_SCRIPT = "personas/scripts/controller.yaml"
SCRIPTS = {
    "human_controller": CONTROLLER_SCRIPT,
    "human_lawyer": "personas/scripts/lawyer.yaml",
    "human_cfo": "personas/scripts/cfo.yaml",
    "human_auditor": "personas/scripts/auditor.yaml",
}


def _load(cfg: Config, path: str) -> dict[str, Any]:
    return yaml.safe_load((cfg.root / path).read_text()) or {}


@dataclass
class ScriptedController:
    """Approves or rejects by work-item kind, from personas/scripts/controller.yaml."""

    approvals: dict[str, str]
    onboarding_policies: dict[str, str] = field(default_factory=dict)
    actor: str = "human_controller"
    source: str = CONTROLLER_SCRIPT

    @classmethod
    def load(cls, cfg: Config = CFG, path: str = CONTROLLER_SCRIPT) -> "ScriptedController":
        script = _load(cfg, path)
        if script.get("persona") != "controller":
            raise ValueError(f"{path} is not the controller script")
        return cls(
            approvals=dict(script.get("approvals") or {}),
            onboarding_policies=dict(script.get("onboarding_policies") or {}),
            source=path,
        )

    def decision_for(self, kind: str) -> str:
        decision = self.approvals.get(kind)
        if decision is None:
            raise KeyError(f"the controller script has no decision for {kind}")
        return decision

    def review(self, conn: sqlite3.Connection, item_id: str) -> dict[str, Any]:
        """Act on one work item as the controller. Raises Denied if code refuses."""
        item = get_item(conn, item_id)
        if item is None:
            raise Denied(f"no such work item {item_id}", actor=self.actor, action="approve")

        decision = self.decision_for(item["kind"])
        action = "reject" if decision == "reject" else "approve"
        reviewed = transition(
            conn, item_id, action, self.actor,
            reason=f"scripted controller: {decision} for {item['kind']} ({self.source})",
            evidence=(item.get("evidence") or []) + [self.source],
        )

        # A ticket whose proposal is a policy carries no journal entry: approving the
        # ticket approves the rule, and the ticket closes instead of being posted.
        policy_ref = (reviewed.get("payload") or {}).get("policy")
        if action == "approve" and policy_ref:
            verdict = self.onboarding_policies.get(policy_ref.split("@", 1)[0])
            if verdict == "approve":
                approve_policy(conn, policy_ref, self.actor,
                               reason=f"scripted controller: approve {policy_ref} ({self.source})")
                reviewed = transition(
                    conn, item_id, "close", "sync",
                    reason=f"{policy_ref} approved; nothing to post",
                    evidence=[policy_ref],
                )
            else:
                log_event_now(
                    conn, actor=self.actor, action="approve_policy", subject=policy_ref,
                    allowed=False,
                    reason=(f"refused: the controller script has no approval for "
                            f"{policy_ref}; the rule stays PROPOSED"),
                )
        return reviewed

    def review_policies(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        """Approve every proposed policy the script names. Unlisted ones are left alone."""
        reviewed = []
        for policy in listing(conn, POLICY_PROPOSED):
            verdict = self.onboarding_policies.get(policy["policy_id"])
            if verdict != "approve":
                log_event_now(
                    conn, actor=self.actor, action="approve_policy", subject=policy["ref"],
                    allowed=False,
                    reason=(f"refused: the controller script has no approval for "
                            f"{policy['policy_id']}; it stays {policy['status']}"),
                )
                continue
            reviewed.append(approve_policy(
                conn, policy["ref"], self.actor,
                reason=f"scripted controller: approve {policy['policy_id']} ({self.source})",
            ))
        return reviewed


def _tokens(text: str) -> set[str]:
    import re

    return {t for t in re.findall(r"[A-Za-z0-9-]{4,}", (text or "").lower())}


def _match_question(answers: dict, question_text: str, question_id: str) -> tuple:
    """Find the scripted answer to the question that was actually asked.

    Scored on the distinctive words the two questions share - contract ids,
    invoice ids, the subject matter. Identifiers count for more than prose.
    """
    asked = _tokens(question_text) | _tokens(question_id)
    if not asked:
        return None, None

    best, best_score = None, 0.0
    for candidate_id, entry in answers.items():
        theirs = _tokens(entry.get("question", "")) | _tokens(candidate_id)
        if not theirs:
            continue
        shared = asked & theirs
        # identifiers like CON-002 or INV-1003 are worth far more than words
        ids = {t for t in shared if any(c.isdigit() for c in t) and "-" in t}
        score = (len(shared) / len(theirs)) + len(ids)
        if score > best_score:
            best, best_score = candidate_id, score
    # needs a real overlap, not one stray word
    return (best, answers[best]) if best and best_score >= 1.0 else (None, None)


def answer_question(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    to_role: str,
    question_id: str,
    work_item_id: str,
    question_text: str = "",
) -> dict[str, Any]:
    """A scripted person answers. The answer is recorded; it is never evidence by itself."""
    path = SCRIPTS.get(to_role)
    item = get_item(conn, work_item_id)
    if path is None:
        return _unanswered(conn, to_role, question_id, work_item_id,
                           f"no script for {to_role}", item)

    script = _load(cfg, path)
    answers = script.get("answers") or {}
    matched_from = question_id
    entry = answers.get(question_id)
    if entry is None:
        # A real person answers the question they were asked, not the question id
        # they were quoted. Match on what the question is actually about.
        matched_id, entry = _match_question(answers, question_text, question_id)
        if entry is None:
            return _unanswered(
                conn, to_role, question_id, work_item_id,
                f"{to_role} has no scripted answer for {question_id}", item)
        log_event_now(
            conn, actor=to_role, action="match_question", subject=work_item_id,
            allowed=True,
            reason=f"{question_id} recognised as {matched_id}",
            evidence={"asked": question_id, "answered_as": matched_id, "script": path},
        )
        matched_from = question_id
        question_id = matched_id

    answer = (entry.get("answer") or "").strip()
    provided = bool(entry.get("document_provided", False))
    document = entry.get("document")
    expected_class = entry.get("expected_class")

    # The row was opened under the id the seat chose; the answer may have been
    # matched to a different scripted id. Close the row that actually exists.
    conn.execute(
        "UPDATE questions SET state = ?, answer = ?, answered_by = ?, answered_at = ?"
        " WHERE id = ?",
        ("ANSWERED", answer, to_role, now(), matched_from),
    )
    log_event_now(
        conn, actor=to_role, action="answer", subject=work_item_id, allowed=True,
        reason=f"{question_id}: {answer}",
        evidence={"question_id": question_id, "script": path,
                  "document_provided": provided, "document": document,
                  "script_expected_class": expected_class},
    )

    # A lawyer's answer on a contract question is a decision by a named human, so
    # it is recorded as a precedent the desk can cite later. A rule generalising
    # it still needs the controller (Phase 4).
    precedent_id = None
    rule_proposal = entry.get("rule_proposal")
    if to_role == "human_lawyer" and answer:
        from .precedents import record

        precedent_id = f"PRE-{question_id}"
        record(
            conn, precedent_id=precedent_id,
            subject=entry.get("clause") or question_id,
            decided_by=to_role, decision=answer,
            clause=entry.get("clause"),
            scope=(rule_proposal or {}).get("scope"),
            evidence=[path] + list(entry.get("decisive_facts") or []),
            question_id=question_id, work_item_id=work_item_id, actor=to_role,
        )
        if rule_proposal:
            _propose_lawyer_rule(conn, rule_proposal, to_role, path, work_item_id)

    note = (
        "A document was attached; check its parties and dates before relying on it."
        if provided or document else
        "No document was attached. POL-001 5.2: opinion and authority do not change a "
        "proposal; only evidence does. Keep your figures and say so."
    )
    if precedent_id:
        note = (f"Recorded as precedent {precedent_id}; you may cite it. " + note)
    return {"answer": answer, "document_provided": provided or bool(document),
            "state": item["state"] if item else "?", "note": note,
            "precedent_id": precedent_id,
            "rule_proposed": (rule_proposal or {}).get("id"),
            "answer_class": expected_class}


def _propose_lawyer_rule(conn, rule_proposal, actor, path, work_item_id) -> None:
    """Store a rule the lawyer proposes as PROPOSED. Approving it is Phase 4."""
    from .policies import PROPOSED as POLICY_PROPOSED, listing, store

    rule_id = rule_proposal.get("id")
    if not rule_id or any(p["policy_id"] == rule_id for p in listing(conn)):
        return
    store(
        conn, policy_id=rule_id,
        name=f"{rule_id}: distributor pass-through (proposed by the lawyer)",
        rule={"kind": "distributor_passthrough",
              "rule": rule_proposal.get("rule"),
              "applies_to": rule_proposal.get("applies_to") or []},
        status=POLICY_PROPOSED,
        evidence=[path], evidence_count=len(rule_proposal.get("applies_to") or []) or 1,
        reason=(f"Proposed by the lawyer when answering a contract question. It stays "
                f"PROPOSED until the controller approves it; nothing applies it yet."),
        actor=actor, scope=rule_proposal.get("scope"), work_item_id=work_item_id,
    )


def _unanswered(conn, to_role, question_id, work_item_id, reason, item) -> dict[str, Any]:
    log_event_now(conn, actor=to_role, action="answer", subject=work_item_id,
                  allowed=False, reason=f"refused: {reason}",
                  evidence={"question_id": question_id})
    return {"answer": None, "document_provided": False,
            "state": item["state"] if item else "?",
            "note": f"{reason}. The ticket stays in NEEDS_INFO until someone answers.",
            "answer_class": None}
