"""Onboarding: the seats study the company's history and propose their own rules.

This runs before any event. Each seat gets a work item, reads the history and the
documents, and proposes a versioned rule with an evidence count - or flags a past
practice as a bad habit not to copy. The controller approves each rule; nothing is
applied until it does.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import agent, policies
from .config import CFG, Config
from .llm import LLMClient
from .workflow import CLOSED, create_work_item, get_item, transition

POLICY_KIND = "WI-ONBOARD-POLICY"
REVIEW_KIND = "WI-ONBOARD-REVIEW"

REFUNDABLE_TASK = """\
You are being onboarded. Before any work arrives, study how Brewline's duties and refunds
work and propose your first rule.

Question: which duty programmes are refundable, and which are not?

Look at data/reference/hts_programs.csv (every programme, with a refundable column) and at
the CBP refund notice in data/documents/inbox/. The golden table golden_cbp_duty_lines
shows which programmes were actually charged on Brewline's entries.

Then call propose_policy with policy_id POL-L1, verdict "adopt", and a rule of kind
"program_refundability" listing every programme you found as either refundable or
not_refundable. Count your evidence and cite the reference table and the notice.
"""

SPLIT_TASK = """\
You are being onboarded. Before any work arrives, study how a refund should be split and
propose your first rule.

Question: when duty on a shipment is refunded, how much of the refund belongs to parts
already sold and how much to parts still on hand?

Read the accounting policy POL-001 at data/reference/refund_policy.md - section 3 covers
the split. The golden table golden_erp_inventory_layers has one row per shipment with
qty_received, qty_consumed and qty_on_hand.

Then call propose_policy with policy_id POL-L2, verdict "adopt", and a rule of kind
"refund_split" naming which quantity is the numerator and which is the denominator of the
ratio that gives the sold portion. Count your evidence and cite POL-001.
"""

CLAUSE_TASK = """\
You are being onboarded. Study how customer contracts handle a duty refund and propose a
rule for the clearest case. Another seat will apply it later; you are only writing it down.

Question: when a contract explicitly promises the customer their tariff surcharge back,
what should the desk do?

Read POL-001 at data/reference/refund_policy.md - section 4 covers customer pass-through -
and the contract data/documents/contracts/CON-001-bean-street.md, which has the explicit
wording.

Then call propose_policy with policy_id POL-L3, verdict "adopt", and a rule of kind
"explicit_clause_credit". Count your evidence and cite POL-001 and the contract.
"""

REVIEW_TASK = """\
You are being onboarded. Not everything in the history is a good habit, and you must not
learn the bad ones.

Look at the ledger history. On 2025-09-30 someone booked a tariff refund receivable. Query
golden_ledger_postings for postings to Assets:Receivable:TariffRefund and see what was
booked. Compare it with what the refundable programmes alone would come to - you can total
duty by programme with sum_duties - and with the accounting policy POL-001 at
data/reference/refund_policy.md, section 2.

If that way of estimating the receivable disagrees with the policy, do NOT turn it into a
rule. Call propose_policy with verdict "reject", policy_id BAD-2025-09-30-FORECAST, to flag
it as a practice not to copy. Say in the reason what was included that should not have been,
and cite your sources. Then stop; do not propose a journal entry.
"""

TASKS: tuple[dict[str, Any], ...] = (
    {"seat": "seat1", "kind": POLICY_KIND, "policy_id": "POL-L1",
     "name": "refundable programmes", "task": REFUNDABLE_TASK,
     "segment": "onboard_refundable"},
    {"seat": "seat2", "kind": POLICY_KIND, "policy_id": "POL-L2",
     "name": "refund split", "task": SPLIT_TASK, "segment": "onboard_split"},
    {"seat": "seat1", "kind": POLICY_KIND, "policy_id": "POL-L3",
     "name": "explicit-clause credit", "task": CLAUSE_TASK,
     "segment": "onboard_clause"},
    {"seat": "seat1", "kind": REVIEW_KIND, "policy_id": "BAD-2025-09-30-FORECAST",
     "name": "review of the 2025-09-30 forecast method", "task": REVIEW_TASK,
     "segment": "onboard_review"},
)


@dataclass
class OnboardingResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    runs: list[agent.AgentRun] = field(default_factory=list)
    proposed: list[dict[str, Any]] = field(default_factory=list)
    flagged: list[dict[str, Any]] = field(default_factory=list)
    approved: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def run(
    conn: sqlite3.Connection,
    llm: LLMClient,
    cfg: Config = CFG,
    *,
    controller=None,
    max_turns: int = agent.MAX_TURNS,
) -> OnboardingResult:
    """Open one work item per rule, let the seat propose it, then let the controller approve."""
    from .personas import ScriptedController

    controller = controller or ScriptedController.load(cfg)
    result = OnboardingResult()

    for spec in TASKS:
        item = create_work_item(
            conn, kind=spec["kind"], seat=spec["seat"], actor="sync",
            payload={"onboarding": spec["policy_id"], "name": spec["name"]},
            evidence=["data/reference/refund_policy.md"],
            reason=f"onboarding: {spec['seat']} to study {spec['name']}",
        )
        result.items.append(item)

        chooser = getattr(llm, "for_segment", None)
        client = chooser(spec["segment"]) if chooser else llm
        run_result = agent.run_seat(
            conn, spec["seat"], item["id"], client, spec["task"], cfg=cfg,
            max_turns=max_turns
        )
        result.runs.append(run_result)

        stored = [p for p in policies.listing(conn) if p["policy_id"] == spec["policy_id"]]
        if not stored:
            result.missing.append(spec["policy_id"])
            if get_item(conn, item["id"])["state"] not in (CLOSED,):
                transition(conn, item["id"], "close", "sync",
                           reason=f"the seat proposed nothing for {spec['policy_id']}; nothing to adopt",
                           evidence=["data/reference/refund_policy.md"])
            continue
        latest = sorted(stored, key=lambda p: int(p["version"]))[-1]
        if latest["status"] == policies.FLAGGED_BAD:
            result.flagged.append(latest)
            if get_item(conn, item["id"])["state"] not in (CLOSED,):
                transition(conn, item["id"], "close", "sync",
                           reason=f"{latest['ref']} flagged as a bad habit; nothing to adopt",
                           evidence=[latest["ref"]])
        elif latest["status"] == policies.PROPOSED:
            result.proposed.append(latest)

    # One pass by the controller over everything the seats proposed.
    for item in result.items:
        current = get_item(conn, item["id"])
        if current["state"] == "PROPOSED":
            controller.review(conn, item["id"])

    result.approved = [
        p for p in policies.listing(conn, policies.APPROVED)
        if not str(p.get("proposed_by") or "").startswith("human_")    # the seats' own rules
    ]
    return result
