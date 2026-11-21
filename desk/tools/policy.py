"""Policy tools: get_policies, propose_policy, apply_policy."""
from __future__ import annotations

from typing import Any

from .. import policies
from ..policies import PolicyError  # re-exported for the dispatcher

GET_POLICIES_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["PROPOSED", "APPROVED", "REJECTED", "FLAGGED_BAD"],
            "description": "Filter by status. Omit to list every policy.",
        }
    },
    "additionalProperties": False,
}

PROPOSE_POLICY_SCHEMA = {
    "type": "object",
    "properties": {
        "policy_id": {
            "type": "string",
            "description": "The policy id, e.g. POL-L1. A new version is created automatically.",
        },
        "name": {"type": "string", "description": "A short name for the rule."},
        "verdict": {
            "type": "string",
            "enum": ["adopt", "reject"],
            "description": (
                "adopt = propose this as a rule to follow. "
                "reject = flag an existing practice as a bad habit NOT to learn."
            ),
        },
        "rule": {
            "type": "object",
            "description": (
                "The executable rule. Must have a 'kind' the desk can run: "
                "'program_refundability' (fields: refundable[], not_refundable[]), "
                "'refund_split' (fields: numerator, denominator - one of qty_consumed, "
                "qty_received, qty_on_hand), or 'explicit_clause_credit'. "
                "For verdict=reject, describe the bad practice instead."
            ),
        },
        "scope": {"type": "string", "description": "Where this rule applies."},
        "reason": {
            "type": "string",
            "description": "Why adopt it, or why it is a bad habit. Required.",
        },
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Every source behind the rule: document paths, CSV paths, entry numbers, "
                "raw_ref values. At least one must be a document under data/documents/ "
                "or data/reference/."
            ),
        },
        "evidence_count": {
            "type": "integer",
            "description": "How many source rows or documents support this rule.",
        },
    },
    "required": ["policy_id", "name", "verdict", "rule", "reason", "evidence", "evidence_count"],
    "additionalProperties": False,
}

APPLY_POLICY_SCHEMA = {
    "type": "object",
    "properties": {
        "policy_ref": {
            "type": "string",
            "description": "Which approved policy to run, as <id>@<version>, e.g. POL-L2@1.",
        },
        "inputs": {
            "type": "object",
            "description": (
                "Inputs for the rule. program_refundability needs {\"entry_nos\": [...]}; "
                "refund_split needs {\"refund_id\": \"RFD-001\"}."
            ),
        },
    },
    "required": ["policy_ref", "inputs"],
    "additionalProperties": False,
}


def get_policies(ctx, status: str | None = None) -> dict[str, Any]:
    found = policies.listing(ctx.conn, status)
    return {
        "count": len(found),
        "policies": [
            {"ref": p["ref"], "name": p["name"], "status": p["status"], "rule": p["rule"],
             "scope": p["scope"], "evidence_count": p["evidence_count"], "reason": p["reason"],
             "proposed_by": p["proposed_by"], "approved_by": p["approved_by"]}
            for p in found
        ],
    }


def propose_policy(
    ctx,
    policy_id: str,
    name: str,
    verdict: str,
    rule: dict[str, Any],
    reason: str,
    evidence: list[str],
    evidence_count: int,
    scope: str | None = None,
) -> dict[str, Any]:
    from .work import require_source_document

    if verdict not in ("adopt", "reject"):
        raise PolicyError("verdict must be 'adopt' or 'reject'")
    if not reason.strip():
        raise PolicyError("reason is required: say why this is a rule, or why it is a bad habit")
    require_source_document(ctx, evidence)
    if evidence_count < 1:
        raise PolicyError("evidence_count must say how many sources support the rule")

    if verdict == "adopt":
        kind = (rule or {}).get("kind")
        if kind not in policies.EXECUTORS:
            raise PolicyError(
                f"rule kind {kind!r} cannot be executed. Use one of: "
                f"{', '.join(sorted(k for k in policies.EXECUTORS if k != 'document'))}"
            )
        status = policies.PROPOSED
    else:
        status = policies.FLAGGED_BAD

    stored = policies.store(
        ctx.conn, policy_id=policy_id, name=name, rule=rule or {}, status=status,
        evidence=evidence, evidence_count=evidence_count, reason=reason, actor=ctx.actor,
        scope=scope, work_item_id=ctx.work_item_id,
    )

    # A policy the seat wants adopted becomes the proposal on its work item, so the
    # controller has exactly one thing to approve.
    state = None
    if status == policies.PROPOSED and ctx.work_item_id:
        from ..workflow import get_item, transition

        item = get_item(ctx.conn, ctx.work_item_id)
        if item and item["state"] in ("NEW", "NEEDS_INFO"):
            payload = dict(item["payload"] or {})
            payload.update({"policy": stored["ref"], "rule": stored["rule"],
                            "rationale": reason, "evidence_count": evidence_count})
            updated = transition(
                ctx.conn, ctx.work_item_id, "propose", ctx.actor,
                patch={"payload": payload, "evidence": evidence,
                       "policy_id": stored["policy_id"], "policy_version": stored["version"]},
                reason=f"proposes {stored['ref']}: {reason}",
                evidence=evidence, decision=ctx.decision,
            )
            state = updated["state"]

    note = (
        "Proposed. It does nothing until the controller approves it, and you cannot "
        "approve it yourself."
        if status == policies.PROPOSED
        else "Recorded as a bad habit. It will never be applied, and no rule was created."
    )
    return {"policy": stored["ref"], "status": stored["status"],
            "work_item_state": state, "evidence_count": evidence_count, "note": note}


def apply_policy(ctx, policy_ref: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return policies.apply(ctx.conn, policy_ref, inputs, ctx.cfg)
