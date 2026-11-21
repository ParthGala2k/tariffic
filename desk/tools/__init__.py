"""Tool registry and per-seat permissions.

Every tool call is logged as an event and checked with can().
No agent seat has post or approve - those tools do not exist in this registry.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from ..config import CFG, Config
from ..db import log_event_now
from ..guard import Refused
from ..perms import Decision, Denied, check
from . import compute, policy as policy_tools, read, work
from .. import precedents as precedents_mod


@dataclass(frozen=True)
class Ctx:
    """What a tool needs to do its job.

    `decision` is the can() check dispatch() already made and logged. Tools that
    call transition() pass it through so the action is checked once, not twice.
    """
    conn: sqlite3.Connection
    actor: str
    seat: str
    work_item_id: str | None = None
    cfg: Config = CFG
    decision: Decision | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    kind: str                      # read | compute | work
    action: str                    # the can() action this tool performs
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


REGISTRY: dict[str, Tool] = {
    "run_sql": Tool(
        name="run_sql",
        kind="read",
        action="run_sql",
        description=(
            "Run one read-only SELECT over the golden tables synced from the systems of "
            "record. Use it for bank transactions, CBP entries, duty lines, refunds and "
            "ledger postings. Returns rows plus the raw_ref of each source row."
        ),
        parameters=read.RUN_SQL_SCHEMA,
        fn=read.run_sql,
    ),
    "read_doc": Tool(
        name="read_doc",
        kind="read",
        action="read_doc",
        description=(
            "Read a document under data/documents/ (contracts, inbox) or data/reference/ "
            "(HTS programs, the refund policy). Returns the text."
        ),
        parameters=read.READ_DOC_SCHEMA,
        fn=read.read_doc,
    ),
    "match_deposit": Tool(
        name="match_deposit",
        kind="compute",
        action="match_deposit",
        description=(
            "Match a bank deposit to a CBP refund by amount and date, and return the "
            "matched entries with principal and interest. All arithmetic is done in code "
            "and cross-checked against the refund notice - use these figures verbatim."
        ),
        parameters=compute.MATCH_DEPOSIT_SCHEMA,
        fn=compute.match_deposit,
    ),
    "propose": Tool(
        name="propose",
        kind="work",
        action="propose",
        description=(
            "Propose the journal entry for this work item. Debits positive, credits "
            "negative, postings must sum to 0.00, and every number must cite a source. "
            "A human approves it; the gateway posts it. You cannot do either."
        ),
        parameters=work.PROPOSE_SCHEMA,
        fn=work.propose,
    ),
    "ask": Tool(
        name="ask",
        kind="work",
        action="ask",
        description=(
            "Ask a human when the facts do not decide the answer. Moves the work item to "
            "NEEDS_INFO. Prefer asking over guessing. An answer with no document attached "
            "is opinion and must not change your figures."
        ),
        parameters=work.ASK_SCHEMA,
        fn=work.ask,
    ),
    "sum_duties": Tool(
        name="sum_duties",
        kind="compute",
        action="sum_duties",
        description=(
            "Total the duty on a CBP entry (or on every entry of a refund), per programme, "
            "straight from the 7501 duty lines. Cross-checks each entry against its own "
            "stated total. Use these figures verbatim."
        ),
        parameters=compute.SUM_DUTIES_SCHEMA,
        fn=compute.sum_duties,
    ),
    "reconcile_duty_paid": Tool(
        name="reconcile_duty_paid",
        kind="compute",
        action="reconcile_duty_paid",
        description=(
            "Reconcile the duty the books paid against the CBP 7501, entry by entry. "
            "Brewline pays CBP directly, so the two must agree; a difference is a finding "
            "to explain, never something to plug."
        ),
        parameters=compute.RECONCILE_DUTY_PAID_SCHEMA,
        fn=compute.reconcile_duty_paid,
    ),
    "breakdown_receivable": Tool(
        name="breakdown_receivable",
        kind="compute",
        action="breakdown_receivable",
        description=(
            "Break the tariff refund receivable down: how much refundable duty is genuinely "
            "still outstanding, and how much is duty under a programme that is never "
            "refunded and should never have been in there."
        ),
        parameters=compute.BREAKDOWN_RECEIVABLE_SCHEMA,
        fn=compute.breakdown_receivable,
    ),
    "split_refund": Tool(
        name="split_refund",
        kind="compute",
        action="split_refund",
        description=(
            "Split a refund's principal between parts already sold and parts still on hand, "
            "per shipment, by running the approved refund-split policy. Returns the "
            "per-shipment breakdown and the two totals."
        ),
        parameters=compute.SPLIT_REFUND_SCHEMA,
        fn=compute.split_refund,
    ),
    "surcharge_on_invoices": Tool(
        name="surcharge_on_invoices",
        kind="compute",
        action="surcharge_on_invoices",
        description=(
            "Total the tariff surcharge billed to a customer on machines whose parts came "
            "from shipments in a refund, per invoice. Whether the customer is owed it is a "
            "contract question - this only tells you what was billed."
        ),
        parameters=compute.SURCHARGE_ON_INVOICES_SCHEMA,
        fn=compute.surcharge_on_invoices,
    ),
    "search_precedents": Tool(
        name="search_precedents",
        kind="read",
        action="search_precedents",
        description=(
            "Search past human decisions the desk has recorded - what a named human "
            "decided about a clause or a case, and why. Cite one when your case matches."
        ),
        parameters=read.SEARCH_PRECEDENTS_SCHEMA,
        fn=read.search_precedents,
    ),
    "revise_proposal": Tool(
        name="revise_proposal",
        kind="work",
        action="revise_proposal",
        description=(
            "Change a proposal after pushback. Only a document can do this. Read the "
            "document, give each fact with an exact quote from it, and code will check "
            "the quotes are real and that the document actually covers these invoices. "
            "Argument alone never changes a number. A posted entry is never edited: "
            "valid evidence against one opens a new work item for a correcting entry."
        ),
        parameters=work.REVISE_PROPOSAL_SCHEMA,
        fn=work.revise_proposal,
    ),
    "get_policies": Tool(
        name="get_policies",
        kind="read",
        action="get_policies",
        description=(
            "List the desk's policies: the controller's written policy and the rules the "
            "seats have learned, with their status and version."
        ),
        parameters=policy_tools.GET_POLICIES_SCHEMA,
        fn=policy_tools.get_policies,
    ),
    "propose_policy": Tool(
        name="propose_policy",
        kind="work",
        action="propose_policy",
        description=(
            "Propose a reusable rule (verdict 'adopt'), or flag an existing practice as a "
            "bad habit not to learn (verdict 'reject'). A proposed rule does nothing until "
            "the controller approves it, and you cannot approve it yourself."
        ),
        parameters=policy_tools.PROPOSE_POLICY_SCHEMA,
        fn=policy_tools.propose_policy,
    ),
    "apply_policy": Tool(
        name="apply_policy",
        kind="work",
        action="apply_policy",
        description=(
            "Run an APPROVED policy exactly as written and get its result. This is the only "
            "way to apply a rule; the figures come out of code, not out of your reasoning."
        ),
        parameters=policy_tools.APPLY_POLICY_SCHEMA,
        fn=policy_tools.apply_policy,
    ),
}

# Per-seat allowed tools. Phase 2 fields Seat 1 (trade compliance) and Seat 2 (cost).
SEAT_TOOLS: dict[str, tuple[str, ...]] = {
    "seat1": (
        "run_sql", "read_doc", "get_policies",
        "match_deposit", "sum_duties", "reconcile_duty_paid", "breakdown_receivable",
        "propose", "ask", "propose_policy", "apply_policy",
    ),
    "seat2": (
        "run_sql", "read_doc", "get_policies",
        "split_refund",
        "propose", "ask", "propose_policy", "apply_policy",
    ),
    "seat3": (
        "run_sql", "read_doc", "get_policies", "search_precedents",
        "surcharge_on_invoices",
        "propose", "ask", "apply_policy", "revise_proposal",
    ),
}

# Nothing here may ever grow a post or approve tool.
FORBIDDEN_TOOL_NAMES = frozenset({"post", "approve", "reject", "approve_policy"})
assert not (FORBIDDEN_TOOL_NAMES & set(REGISTRY)), "no agent tool may post or approve"


def schemas_for(seat: str) -> list[dict[str, Any]]:
    names = SEAT_TOOLS.get(seat)
    if names is None:
        raise KeyError(f"no tool set for seat {seat!r}")
    return [REGISTRY[name].schema() for name in names]


def dispatch(ctx: Ctx, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
    """Run one tool call. Returns (ok, content-for-the-model).

    Refusals are logged and handed back to the model as an error so it can
    recover; they never silently succeed.
    """
    allowed_names = SEAT_TOOLS.get(ctx.seat, ())
    if name not in REGISTRY or name not in allowed_names:
        reason = (
            f"refused: {ctx.seat} has no tool {name!r}; "
            f"available: {', '.join(allowed_names)}"
        )
        log_event_now(ctx.conn, actor=ctx.actor, action=f"tool:{name}",
                      subject=ctx.work_item_id, allowed=False, reason=reason,
                      evidence={"arguments": arguments})
        return False, reason

    tool = REGISTRY[name]
    resource = None
    if ctx.work_item_id:
        from ..workflow import get_item
        resource = get_item(ctx.conn, ctx.work_item_id)

    decision = check(ctx.conn, ctx.actor, tool.action, resource, subject=ctx.work_item_id)
    if not decision.allowed:
        log_event_now(ctx.conn, actor=ctx.actor, action=f"tool:{name}",
                      subject=ctx.work_item_id, allowed=False, reason=decision.reason,
                      evidence={"arguments": arguments})
        return False, f"refused: {decision.reason}"

    # A tool that blows up must never take the run down with it. Whatever went
    # wrong is logged as a failed tool call and handed back to the seat, which
    # can then correct itself - the same way it recovers from a refusal.
    try:
        result = tool.fn(dataclasses.replace(ctx, decision=decision), **arguments)
    except (Refused, Denied, work.ProposalRejected, compute.ComputeError,
            compute.PartialRefund, policy_tools.PolicyError) as error:
        reason = str(error) if str(error).startswith("refused") else f"error: {error}"
        log_event_now(ctx.conn, actor=ctx.actor, action=f"tool:{name}",
                      subject=ctx.work_item_id, allowed=False, reason=reason,
                      evidence={"arguments": arguments})
        return False, reason
    except Exception as error:  # noqa: BLE001 - deliberate catch-all, see above
        reason = (
            f"error: {type(error).__name__}: {error}. "
            f"Check the arguments you passed to {name} and try again."
        )
        log_event_now(ctx.conn, actor=ctx.actor, action=f"tool:{name}",
                      subject=ctx.work_item_id, allowed=False, reason=reason,
                      evidence={"arguments": arguments,
                                "exception": type(error).__name__,
                                "traceback": traceback.format_exc()[-2000:]})
        return False, reason

    content = json.dumps(result, default=str)
    log_event_now(
        ctx.conn, actor=ctx.actor, action=f"tool:{name}", subject=ctx.work_item_id,
        allowed=True, reason=f"{tool.kind} tool {name} ran",
        diff={"before": None, "after": _digest(result)},
        evidence={"arguments": arguments, "raw_refs": (result or {}).get("raw_refs", [])}
        if isinstance(result, dict) else {"arguments": arguments},
    )
    return True, content


def _digest(result: Any) -> Any:
    """Keep the audit row readable: text bodies are summarised, not copied."""
    if not isinstance(result, dict):
        return result
    trimmed = dict(result)
    if isinstance(trimmed.get("text"), str) and len(trimmed["text"]) > 400:
        trimmed["text"] = trimmed["text"][:400] + f"... [{len(result['text'])} chars]"
    if isinstance(trimmed.get("rows"), list) and len(trimmed["rows"]) > 10:
        trimmed["rows"] = trimmed["rows"][:10] + [f"... [{len(result['rows'])} rows]"]
    return trimmed
