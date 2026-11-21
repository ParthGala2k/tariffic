"""The agent harness: model + tools + loop + context.

One loop, provider-neutral. Every tool call goes through the registry, so it
is checked with can() and logged as an event. The seat's tool set is the only
thing it can do; it has no post and no approve.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .config import CFG, Config
from .db import log_event_now
from .llm import LLMClient
from .tools import Ctx, dispatch, schemas_for
from .workflow import NEEDS_INFO, PROPOSED, get_item

MAX_TURNS = 12

SEAT_ROLES = {
    "seat1": (
        "You are Seat 1 at Brewline Equipment Co.'s tariff desk: the trade-compliance "
        "accountant. You classify duties by programme, reconcile the duty the books paid "
        "against the CBP 7501, correct the refund forecast, match refund deposits to "
        "entries, and split principal from interest."
    ),
    "seat2": (
        "You are Seat 2 at Brewline Equipment Co.'s tariff desk: the cost accountant. "
        "When duty is refunded, some of the parts it was paid on have already been sold "
        "and some are still on the shelf. You split each entry's refund between the two "
        "using the ERP's stock layers, and draft the journal entry that moves it."
    ),
    "seat3": (
        "You are Seat 3 at Brewline Equipment Co.'s tariff desk: the revenue accountant. "
        "Customers were billed a separately stated tariff surcharge. Now the duty behind "
        "it has been refunded, and you decide what each customer is owed - by reading "
        "their contract, not by guessing.\n\n"
        "A clause is either explicit or it is not. Where it plainly says the customer "
        "gets the surcharge back, apply the approved rule and propose the credit. Where "
        "the wording is open to more than one reading, it is not yours to decide: ask "
        "the lawyer and wait. That is the job, not a failure of it."
    ),
}

HOUSE_RULES = """
How you work:
- You do the work; code sets the limits. You have no post tool and no approve tool, and
  you may never approve your own proposal. A human controller approves; the gateway posts.
- Never do arithmetic yourself. Get every figure from a compute tool or apply_policy and
  copy it verbatim. If a figure you need has no tool, say so rather than working it out.
- Every proposal must cite a SOURCE DOCUMENT in its evidence: a path under
  data/documents/ or data/reference/. A raw_ref from a golden table says where you copied
  a number from, not where it came from, so raw_refs alone are refused. Read the relevant
  document with read_doc and cite it.
- Every proposal must name the approved policy it rests on, as policy_ref, for example
  POL-001@1 for the controller's written policy. Use get_policies to see what exists.
- Apply a rule only through apply_policy, and only once the controller has approved it.
- You may read the golden tables with run_sql and documents under data/documents/ and
  data/reference/ with read_doc. Ground-truth folders are off limits and asking for them
  is logged as a refusal.
- When the facts decide the answer, propose. When they do not, ask a human. Do not guess.
  An answer that comes with no document attached is opinion: record it, and keep your
  figures. Only a document with matching parties and dates changes a proposal.
- Journal entries use debits positive and credits negative, and must sum to 0.00.
- The desk's working date is {today}. The books are closed before {asserted} and carry
  balance assertions there, so date every entry {asserted} or later. A correction is a new
  entry on the day you make it, never a rewrite of a closed period.
- One work item, one entry. If you find a second problem, it has its own work item - say so
  in your rationale and leave it out of this entry.
- Five invariants are checked before any approval. If your proposal would create a new
  violation, approval is refused and you will be told which invariant and why.

Chart of accounts (use these exactly):
{accounts}
""".strip()


@dataclass
class AgentRun:
    seat: str
    actor: str
    work_item_id: str
    turns: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    state: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)


def _question_open(conn: sqlite3.Connection, work_item_id: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE work_item_id = ? AND state = 'OPEN'",
        (work_item_id,),
    ).fetchone()
    return bool(row and row[0])


def chart_of_accounts(cfg: Config = CFG) -> list[str]:
    import re
    text = (cfg.root / "systems" / "books" / "accounts.beancount").read_text()
    return re.findall(r"^\d{4}-\d{2}-\d{2}\s+open\s+(\S+)", text, re.M)


WORKING_DATE = "2025-10-15"


def asserted_through(cfg: Config = CFG) -> str:
    """The last date the books have sworn to a balance; entries must be on or after it."""
    from .tools.work import _asserted_through

    dates = _asserted_through(cfg).values()
    return max(dates) if dates else WORKING_DATE


def system_prompt(seat: str, cfg: Config = CFG) -> str:
    role = SEAT_ROLES.get(seat)
    if role is None:
        raise KeyError(f"no role description for seat {seat!r}")
    accounts = "\n".join(f"  {a}" for a in chart_of_accounts(cfg))
    return f"{role}\n\n" + HOUSE_RULES.format(
        accounts=accounts, today=WORKING_DATE, asserted=asserted_through(cfg)
    )


def actor_for(seat: str) -> str:
    return f"ai_agent.{seat}"


def run_seat(
    conn: sqlite3.Connection,
    seat: str,
    work_item_id: str,
    llm: LLMClient,
    task: str,
    *,
    cfg: Config = CFG,
    max_turns: int = MAX_TURNS,
    allowed_tools: set[str] | None = None,
) -> AgentRun:
    """Run one seat agent against one work item until it proposes or asks.

    `allowed_tools` narrows the seat's tool set for this run (a discussion run gets
    read and compute tools only); a call outside it is refused and logged like any
    other refusal."""
    actor = actor_for(seat)
    tools = [t for t in schemas_for(seat) if allowed_tools is None or t["name"] in allowed_tools]
    # A brain that routes by ticket (the Creao agents) is bound to this item first; a
    # model client has no such hook and is used as it is.
    bind = getattr(llm, "for_ticket", None)
    if bind is not None:
        bound = get_item(conn, work_item_id)
        if bound is not None:
            llm = bind(seat, bound)
    system = system_prompt(seat, cfg)
    ctx = Ctx(conn=conn, actor=actor, seat=seat, work_item_id=work_item_id, cfg=cfg)
    run = AgentRun(seat=seat, actor=actor, work_item_id=work_item_id)

    log_event_now(
        conn, actor=actor, action="agent:start", subject=work_item_id, allowed=True,
        reason=f"{seat} picked up {work_item_id}",
        evidence={"tools": [t["name"] for t in tools],
                  "provider": getattr(llm, "provider", "?"),
                  "model": getattr(llm, "model", "?")},
    )

    # The loop stops when the seat's work moves the item on (it proposed, or it asked and
    # nobody has answered). An item that was already PROPOSED when the run began - a
    # discussion run answering pushback, or a verification run on a proposal - must not
    # stop the seat after its first look.
    started = get_item(conn, work_item_id)
    start_state = started["state"] if started else None

    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    for turn in range(1, max_turns + 1):
        run.turns = turn
        response = llm.complete(system, messages, tools)
        messages.append(response.as_assistant_message())
        if response.text:
            run.final_text = response.text
        # The seat's thinking, as it happens: its interim text and the tools it chose.
        # One row per turn, so the UI's "thinking" view is a reading of the log.
        log_event_now(
            conn, actor=actor, action="agent:turn", subject=work_item_id, allowed=True,
            reason=(response.text or "").strip()[:600]
            or f"turn {turn}: calling {', '.join(c.name for c in response.tool_calls) or 'nothing'}",
            evidence={"turn": turn,
                      "tool_calls": [{"name": c.name, "arguments": c.arguments}
                                     for c in response.tool_calls]},
        )

        if not response.tool_calls:
            break

        for call in response.tool_calls:
            if allowed_tools is not None and call.name not in allowed_tools:
                ok, content = False, (f"refused: {call.name} is not available in this run; "
                                      "answer with the facts you have")
                log_event_now(conn, actor=actor, action=f"tool:{call.name}", subject=work_item_id,
                              allowed=False, reason=content,
                              evidence={"arguments": call.arguments, "run": "restricted"})
            else:
                ok, content = dispatch(ctx, call.name, call.arguments)
            run.tool_calls.append({"name": call.name, "ok": ok, "arguments": call.arguments})
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                             "content": content})

        item = get_item(conn, work_item_id)
        if item and item["state"] == PROPOSED and start_state != PROPOSED:
            break
        # A question nobody has answered parks the ticket; the seat stops there.
        # If the person answered at once, the seat may use the answer and go on.
        if (item and item["state"] == NEEDS_INFO and allowed_tools is None
                and _question_open(conn, work_item_id)):
            break

    item = get_item(conn, work_item_id)
    run.state = item["state"] if item else "?"
    run.messages = messages

    log_event_now(
        conn, actor=actor, action="agent:finish", subject=work_item_id, allowed=True,
        reason=f"{seat} finished with {work_item_id} in {run.state} after {run.turns} turn(s)",
        evidence={"tool_calls": [c["name"] for c in run.tool_calls], "note": run.final_text[:500]},
    )
    return run
