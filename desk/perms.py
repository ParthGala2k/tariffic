"""Actors x actions x resources, enforced in code (CLAUDE.md section 6.2).

Every can() check is logged, allowed or denied.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

from .db import log_event, log_event_now


class Denied(Exception):
    """A permission or separation-of-duties check refused the action."""

    def __init__(self, reason: str, *, actor: str = "", action: str = "", subject: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.actor = actor
        self.action = action
        self.subject = subject


# role -> actions that role may perform
ROLES: dict[str, set[str]] = {
    "sync": {"create_work_item", "close"},
    "ai_agent": {
        # work
        "propose",
        "ask",
        "revise_proposal",
        "propose_policy",
        "apply_policy",
        # read
        "run_sql",
        "read_doc",
        "search_precedents",
        "get_policies",
        # compute
        "match_deposit",
        "sum_duties",
        "reconcile_duty_paid",
        "breakdown_receivable",
        "split_refund",
        "surcharge_on_invoices",
    },
    # The two people at the screen. The CFO approves, declines, sends back and adopts
    # rules, and may push back; the lawyer answers contract questions, proposes rules
    # and may ask why. human_controller is the scripted stand-in for the CFO in
    # recorded runs; human_auditor is kept for the scripted auditor persona.
    "human_cfo": {"approve", "reject", "approve_policy", "ask", "answer", "revise",
                  "challenge"},
    "human_lawyer": {"answer", "propose_policy", "challenge"},
    "human_controller": {"approve", "reject", "approve_policy", "ask", "answer", "revise",
                         "challenge"},
    "human_auditor": {"answer", "challenge"},
    # The CFO's standing authorization (desk/authorization.py): code approves a routine
    # entry on the CFO's behalf, inside the caps and conditions the CFO adopted.
    "standing_authorization": {"approve"},
    "gateway": {"post"},
}

# Actions no role other than the gateway may ever perform.
GATEWAY_ONLY = {"post"}

# Actions subject to separation of duties: the actor must not be the proposer.
SEPARATION_OF_DUTIES = {"approve", "approve_policy"}


def role_of(actor: str) -> str:
    """Actors are named `<role>` or `<role>.<instance>`, e.g. ai_agent.seat1."""
    return actor.split(".", 1)[0]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def can(actor: str, action: str, resource: Mapping[str, Any] | None = None) -> Decision:
    """Pure decision function. No logging, no side effects."""
    role = role_of(actor)
    if role not in ROLES:
        return Decision(False, f"unknown role {role!r} for actor {actor!r}")

    if action in GATEWAY_ONLY and role != "gateway":
        return Decision(False, f"only the gateway may {action}; {actor} is {role}")

    if action not in ROLES[role]:
        return Decision(False, f"role {role} may not {action}")

    if action in SEPARATION_OF_DUTIES and resource is not None:
        proposer = resource.get("proposed_by")
        if proposer and proposer == actor:
            return Decision(
                False,
                f"separation of duties: {actor} proposed {resource.get('id')} and may not {action} it",
            )

    return Decision(True, f"{role} may {action}")


def check(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    resource: Mapping[str, Any] | None = None,
    *,
    subject: str | None = None,
    evidence: list | dict | None = None,
    in_transaction: bool = False,
) -> Decision:
    """can() plus the audit row. Returns the decision; never raises."""
    decision = can(actor, action, resource)
    subject = subject or (resource.get("id") if resource else None)
    write = log_event if in_transaction else log_event_now
    write(
        conn,
        actor=actor,
        action=f"can:{action}",
        subject=subject,
        allowed=decision.allowed,
        reason=decision.reason,
        evidence=evidence,
    )
    return decision


def require(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    resource: Mapping[str, Any] | None = None,
    *,
    subject: str | None = None,
    evidence: list | dict | None = None,
    in_transaction: bool = False,
) -> Decision:
    """check() that raises Denied when refused. The refusal is already logged."""
    decision = check(
        conn, actor, action, resource,
        subject=subject, evidence=evidence, in_transaction=in_transaction,
    )
    if not decision.allowed:
        raise Denied(decision.reason, actor=actor, action=action,
                     subject=subject or (resource.get("id") if resource else None))
    return decision
