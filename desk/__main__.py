"""desk CLI: reset | run phase1|phase2|phase3 | why | items | log | score | challenge | serve | scenario."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import (agent, challenge as challenge_mod, db, gateway, invariants, llm,
               onboarding, personas, score, sync)
from .config import CFG
from .perms import Denied
from .workflow import (SETTLED, children, close_parent_if_settled, create_work_item,
                       get_item, history, transition)

BANK_TXN = "BNK-0026"
SEAT1, SEAT2, SEAT3 = "seat1", "seat2", "seat3"

CFO_EMAIL = "data/documents/inbox/2025-10-16-cfo-email.md"
HARBOR_WAIVER = "data/documents/inbox/2025-10-18-harbor-waiver.md"

POSTED_HEADER = (
    "; Gateway-only file. The desk's gateway appends approved journal entries here.\n"
    "; Nothing else writes to this file. Every entry carries work_item and approved_by"
    " metadata.\n"
)

TASK = """\
A credit landed in Brewline's operating account and nobody has recorded it.

Bank transaction: {bank_txn}

Work out what this deposit is, which CBP entries it covers, and how much of it is
refunded duty principal versus interest. Then propose the journal entry that records it.

Brewline's accounting policy for tariff duties and refunds is POL-001 at
data/reference/refund_policy.md, and the CBP paperwork for the period is in
data/documents/inbox/. Cite a source for every number.

This work item records the deposit and nothing else: the cash in, the receivable
cleared, the interest to income. Splitting the refund between parts already sold and
parts still on hand is a separate work item, created once this one is approved. Leave
inventory and COGS out of this entry.
"""

FORECAST_TASK = """\
An invariant has flagged the tariff refund receivable.

{finding}

On 2025-09-30 someone estimated the refund and booked it to
Assets:Receivable:TariffRefund. Work out what should actually be there, and propose the
journal entry that corrects it.

Call breakdown_receivable first. It splits the receivable by cause in code: how much
refundable duty is genuinely still outstanding, how much is duty under a never-refunded
programme. Brewline pays CBP directly, so non-refundable duty is the only thing that can
make the receivable too big - the whole of the excess belongs in this entry.

Then use sum_duties to see the duty by programme on the refunded entries, and apply_policy
with the approved refundable-programmes rule to confirm which programmes are refundable.
Read POL-001 at data/reference/refund_policy.md, section 2. Non-refundable duty should
never have been in the receivable; it was a cost of the parts when it was paid.

Cite your sources, including the reference table and the policy.
"""

SPLIT_TASK = """\
The refund deposit for {refund_id} has been recorded. Now split it.

Some of the parts the refunded duty was paid on have already been sold; the rest are still
on the shelf. Use split_refund on {refund_id} to get the per-shipment split from the
approved rule - do not work the ratio out yourself.

Read POL-001 at data/reference/refund_policy.md. Section 3 says which account each side of
the split belongs to. Section 1.1 says what happened to the duty on parts still on hand on
2025-10-01. Before you write the entry, check the ledger for both facts: query
golden_ledger_postings for the 2025-09-30 accrual and for the 2025-10-01 capitalisation,
and see which account each one hit. Those two postings decide which way round this entry
goes, and the receivable is not part of it - the deposit work item has already cleared it.

Pass the by_shipment breakdown from split_refund into propose's `detail` field exactly as
you received it - an invariant checks that the split adds up.
"""


CREDIT_TASK = """\
The refund has been recorded. Now decide what {customer} is owed.

They were billed a separately stated tariff surcharge on machines whose parts came from
shipments in refund {refund_id}. Use surcharge_on_invoices to total it - do not add it up
yourself.

Their contract is {contract_path}. Read clause 7, and read POL-001 section 4 at
data/reference/refund_policy.md. If the clause plainly says the customer gets the
surcharge back, apply the approved rule and propose the credit. If the wording is open to
more than one reading, do not decide it: ask human_lawyer and stop.

Check search_precedents first in case this wording has already been decided.

If you propose a credit, pass the per-invoice breakdown from surcharge_on_invoices into
propose's `detail` field as credit_notes - a lump sum nobody can raise a credit note
against is refused.

If you have to ask, ask and then stop. Do not propose on the same work item: the answer
is a fact for whoever picks this up next, and someone may yet produce a document that
changes it.
"""

CHALLENGE_TASK = """\
Someone has pushed back on {work_item} ({kind}), and attached a document:

  {document}

They say: "{text}"

Read the document and decide whether it actually changes this work item's answer. If it
does, call revise_proposal and give each fact with an exact quote from the document -
code checks every quote against the file and checks the coverage dates itself.

If it does not apply here - wrong party, wrong contract, outside the period it covers -
say so plainly and change nothing. A document that is real is not automatically a
document that is relevant.
"""

DISCUSS_TASK = """\
Someone has pushed back on {work_item} ({kind}). They are the {who}, and they say:

  "{text}"

Code classified this as {classification}: {rule}

Answer them directly, in plain language a finance person reads. Address what they asked or
argued. Give the facts that decide it and say where each one comes from (the policy, the
contract, the entry, the invoice, the ledger line, the approved rule, a precedent); read
whatever you need first. Then say plainly whether anything in the proposal changes. Without a
document in the inbox that names the right parties and covers the right dates, nothing can
change, and only revise_proposal with such a document could change it. Do not propose, ask
or revise here; just answer, in under 200 words, without ids the reader would not know.
"""

HARBOR_CLOSE_TASK = """\
The lawyer has answered the question about {contract_id}, and a document has since been
uploaded to the inbox that bears on it: {document}

Read the lawyer's answer with search_precedents, read the document, and settle what
{customer} is owed.

If the document changes the answer, you must use revise_proposal, and you must give each
fact you rely on with an exact quote from the document. Code checks every quote against
the file and checks the coverage dates itself. A document only affects the invoices it
actually covers, and only the party it names.
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _restore_books() -> None:
    CFG.posted_file.parent.mkdir(parents=True, exist_ok=True)
    CFG.posted_file.write_text(POSTED_HEADER, encoding="utf-8")


def _fast_client(args) -> Any:
    """The small model, used to classify pushback. None in scripted mode."""
    if getattr(args, "llm", "live") != "live":
        return None
    try:
        return llm.build_client(db.cache_conn(CFG), CFG, fast=True)
    except llm.LLMError:
        return None


def _client(conn, args) -> Any:
    if args.llm == "live":
        return llm.build_client(db.cache_conn(CFG), CFG)
    from .scripted import phase_script, segment

    if getattr(args, "phase", "phase1") == "phase1":
        return llm.FakeLLM(script=phase_script("phase1"))
    return llm.SegmentedFakeLLM(segment)


MAX_ATTEMPTS = 3


def _for(client, segment: str):
    """The client to use for one work item: a scripted slice, or the live client."""
    chooser = getattr(client, "for_segment", None)
    return chooser(segment) if chooser else client


def _work_child(conn, item_id: str, seat: str, client, task: str, controller, *,
                label: str, segment: str | None = None,
                max_attempts: int = MAX_ATTEMPTS) -> dict[str, Any]:
    """Run one child work item all the way to POSTED.

    If the invariant gate refuses the approval, the controller sends the proposal
    back to the seat with the reason and it tries again. The check is never
    relaxed; the seat is simply told what it broke, the way it is told when an
    entry does not balance.
    """
    if segment:
        client = _for(client, segment)
    feedback = ""
    for attempt in range(1, max_attempts + 1):
        prompt = task if not feedback else (
            f"{task}\n\nYour previous proposal was REFUSED and sent back. Reason:\n"
            f"{feedback}\n\nWork out what is actually wrong with the entry and propose a "
            f"corrected one. Do not repeat the same postings."
        )
        run = agent.run_seat(conn, seat, item_id, client, prompt, cfg=CFG)
        tools = ", ".join(c["name"] + ("" if c["ok"] else "[refused]") for c in run.tool_calls)
        suffix = "" if attempt == 1 else f" (attempt {attempt})"
        print(f"  {label:22} {seat} {run.turns} turn(s){suffix}: {tools}")
        print(f"  {'':22} -> {run.state}")
        if run.state != "PROPOSED":
            raise RuntimeError(f"{item_id} is {run.state}, expected PROPOSED")

        try:
            reviewed = controller.review(conn, item_id)
        except Denied as refusal:
            print(f"  {'':22} controller -> REFUSED: {refusal.reason[:150]}")
            if attempt == max_attempts:
                raise
            transition(conn, item_id, "revise", controller.actor,
                       reason=f"sent back to {seat}: {refusal.reason}")
            feedback = refusal.reason
            continue

        print(f"  {'':22} controller -> {reviewed['state']}")
        result = gateway.post(conn, item_id, cfg=CFG)
        print(f"  {'':22} gateway -> {result['item']['state']}, bean-check passed")
        return result
    raise RuntimeError(f"{item_id} could not be approved in {max_attempts} attempts")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_reset(args) -> int:
    db.rebuild_erp(CFG)
    _restore_books()
    db.drop_desk_db(CFG)
    print(f"ERP rebuilt    {CFG.erp_db.relative_to(CFG.root)}")
    print(f"books restored {CFG.posted_file.relative_to(CFG.root)} "
          f"({len(POSTED_HEADER.splitlines())} lines)")
    print(f"desk DB gone   {CFG.desk_db.relative_to(CFG.root)}")
    if getattr(args, "clear_cache", False):
        db.clear_cache(CFG)
        print(f"cache cleared  {CFG.cache_db.relative_to(CFG.root)}")
    else:
        kept = CFG.cache_db.exists()
        print(f"cache kept     {CFG.cache_db.relative_to(CFG.root)}"
              f"{'' if kept else ' (none yet)'}")
    check = gateway.bean_check(CFG)
    print(f"bean-check     {'passed' if check.ok else 'FAILED: ' + check.output}")
    return 0 if check.ok else 1


def cmd_run_phase1(args) -> int:
    conn = db.desk_conn(CFG)
    counts = sync.run(conn, CFG)
    print(f"sync           {counts}")

    txn = conn.execute("SELECT * FROM golden_bank_txns WHERE txn_id = ?", (BANK_TXN,)).fetchone()
    if txn is None:
        print(f"no bank transaction {BANK_TXN} after sync", file=sys.stderr)
        return 1

    item = create_work_item(
        conn, kind="WI-REFUND-DEPOSIT", seat=SEAT1, actor="sync",
        payload={"trigger": "bank_deposit", "bank_txn_id": txn["txn_id"],
                 "amount": f"{txn['amount']:.2f}", "date": txn["date"],
                 "description": txn["description"], "counterparty": txn["counterparty"]},
        evidence=[txn["raw_ref"]],
        reason=f"unrecorded {txn['description']} deposit of {txn['amount']:.2f} on {txn['date']}",
    )
    print(f"work item      {item['id']} {item['kind']} {item['state']} seat={item['seat']}")

    client = _client(conn, args)
    print(f"agent          provider={client.provider} model={client.model}")

    run = agent.run_seat(conn, SEAT1, item["id"], client, TASK.format(bank_txn=BANK_TXN), cfg=CFG)
    print(f"               {run.turns} turn(s), tools: "
          f"{', '.join(c['name'] + ('' if c['ok'] else '[refused]') for c in run.tool_calls)}")
    print(f"               {item['id']} is now {run.state}")
    if run.state != "PROPOSED":
        print(f"agent did not propose; state={run.state}", file=sys.stderr)
        return 1

    try:
        transition(conn, item["id"], "approve", agent.actor_for(SEAT1))
        print("SEPARATION OF DUTIES BREACHED: the agent approved its own proposal",
              file=sys.stderr)
        return 1
    except Denied as refusal:
        print(f"self-approval   refused and logged: {refusal.reason}")

    controller = personas.ScriptedController.load(CFG)
    reviewed = controller.review(conn, item["id"])
    print(f"controller     {reviewed['state']} by {reviewed['approved_by']}")

    result = gateway.post(conn, item["id"], cfg=CFG)
    print(f"gateway        {result['item']['state']} -> "
          f"{CFG.posted_file.relative_to(CFG.root)}; bean-check passed")
    print()
    print(result["entry"])
    print(f"Run `python -m desk why {item['id']}` for the full history.")
    return 0


def cmd_run_phase2(args, _stop_before_score: bool = False, _client_override=None) -> int:
    # 1. a clean world, but keep the LLM cache so re-runs are cheap
    db.rebuild_erp(CFG)
    _restore_books()
    db.drop_desk_db(CFG)
    conn = db.desk_conn(CFG)
    print("reset          ERP rebuilt, books restored, desk DB fresh (cache kept)")

    counts = sync.run(conn, CFG)
    print(f"sync           {counts}")

    client = _client_override or _client(conn, args)
    print(f"agent          provider={client.provider} model={client.model}")

    # 2. onboarding: the seats propose their own rules, the controller approves
    print("\nonboarding")
    controller = personas.ScriptedController.load(CFG)
    result = onboarding.run(conn, client, CFG, controller=controller)
    for policy in result.proposed:
        print(f"  proposed   {policy['ref']:12} {policy['name']} "
              f"(evidence x{policy['evidence_count']})")
    for policy in result.flagged:
        print(f"  flagged    {policy['ref']:12} bad habit: {policy['reason'][:70]}")
    for policy in result.approved:
        print(f"  approved   {policy['ref']:12} by {policy['approved_by']}")
    if result.missing:
        print(f"  MISSING    {', '.join(result.missing)}", file=sys.stderr)
        return 1

    # 3. the event: a deposit arrives and becomes the parent of the work graph
    txn = conn.execute("SELECT * FROM golden_bank_txns WHERE txn_id = ?", (BANK_TXN,)).fetchone()
    parent = create_work_item(
        conn, kind="WI-REFUND-EVENT", seat=None, actor="sync",
        payload={"trigger": "bank_deposit", "bank_txn_id": txn["txn_id"],
                 "amount": f"{txn['amount']:.2f}", "date": txn["date"]},
        evidence=[txn["raw_ref"]],
        reason=f"CBP refund deposit {txn['txn_id']} of {txn['amount']:.2f} arrived",
    )
    print(f"\nevent          {parent['id']} {parent['kind']} (parent)")

    # 4. invariants scan the existing data and open a child per violation
    opened = invariants.scan_and_open_work_items(conn, CFG, parent_id=parent["id"])
    for item in opened:
        payload = item["payload"]
        print(f"  invariant  {payload['invariant']} -> {item['id']} {item['kind']} "
              f"({payload['magnitude']})")

    deposit = create_work_item(
        conn, kind="WI-REFUND-DEPOSIT", seat=SEAT1, actor="sync", parent_id=parent["id"],
        payload={"trigger": "bank_deposit", "bank_txn_id": txn["txn_id"],
                 "amount": f"{txn['amount']:.2f}", "date": txn["date"],
                 "description": txn["description"], "counterparty": txn["counterparty"]},
        evidence=[txn["raw_ref"]],
        reason=f"unrecorded {txn['description']} deposit of {txn['amount']:.2f}",
    )
    print(f"  event      {deposit['id']} {deposit['kind']}")

    # 5. the seats work the children, in the order the ledger wants them
    print("\nseats")
    by_kind = {item["kind"]: item for item in opened}
    order = [
        ("WI-FORECAST-CORRECTION", SEAT1, FORECAST_TASK, "JE-1 forecast", "forecast"),
    ]
    for kind, seat, task, label, segment in order:
        item = by_kind.get(kind)
        if item is None:
            print(f"  {label}: no work item opened (invariant found nothing)")
            continue
        _work_child(conn, item["id"], seat, client,
                    task.format(finding=item["payload"]["finding"]), controller,
                    label=label, segment=segment)

    _work_child(conn, deposit["id"], SEAT1, client, TASK.format(bank_txn=BANK_TXN),
                controller, label="JE-2 deposit", segment="deposit")

    # 6. the split is only created once the deposit is recorded
    refund_id = conn.execute("SELECT refund_id FROM golden_cbp_refunds LIMIT 1").fetchone()[0]
    split = create_work_item(
        conn, kind="WI-REFUND-SPLIT", seat=SEAT2, actor="sync", parent_id=parent["id"],
        payload={"refund_id": refund_id, "follows": deposit["id"]},
        evidence=[f"work_item:{deposit['id']}"],
        reason=f"{deposit['id']} posted; split {refund_id} between parts sold and on hand",
    )
    print(f"  created    {split['id']} {split['kind']} seat={SEAT2}")
    _work_child(conn, split["id"], SEAT2, client, SPLIT_TASK.format(refund_id=refund_id),
                controller, label="JE-3 refund split", segment="split")

    # 7. the parent closes only once every child has settled
    closed = close_parent_if_settled(conn, parent["id"])
    kids = children(conn, parent["id"])
    print(f"\nwork graph     {parent['id']} children: "
          f"{', '.join(c['id'] + ':' + c['state'] for c in kids)}")
    print(f"               {parent['id']} is now "
          f"{(closed or get_item(conn, parent['id']))['state']}")

    # 8. invariants after posting: anything still open gets its own work item
    remaining = invariants.evaluate(conn, CFG)
    print(f"\ninvariants     {len(remaining)} violation(s) open after posting")
    for violation in remaining:
        print(f"  OPEN       {violation.invariant} {violation.subject} "
              f"{violation.magnitude:,.2f}  {violation.message[:90]}")
    if remaining:
        follow_up = invariants.scan_and_open_work_items(conn, CFG)
        for item in follow_up:
            print(f"  opened     {item['id']} {item['kind']} to deal with it")

    print(f"\nbean-check     {'passed' if gateway.bean_check(CFG).ok else 'FAILED'}")
    print("\n" + CFG.posted_file.read_text())

    if _stop_before_score:
        return 0

    card = score.score(conn, CFG, phase=2)
    print(score.render(card))
    print(f"\nRun `python -m desk items`, `python -m desk log` or "
          f"`python -m desk why {parent['id']}` to look around.")
    return 0 if card.passed else 1


def cmd_run_phase3(args) -> int:
    # One client for the whole run: a scripted transcript is a single sequence,
    # and a second client would replay it from the top.
    client = _client(None, args)
    code = cmd_run_phase2(args, _stop_before_score=True, _client_override=client)
    if code != 0:
        return code

    conn = db.desk_conn(CFG)
    controller = personas.ScriptedController.load(CFG)
    fast = _fast_client(args)
    parent = conn.execute(
        "SELECT id FROM work_items WHERE kind = 'WI-REFUND-EVENT'"
    ).fetchone()[0]
    refund_id = conn.execute("SELECT refund_id FROM golden_cbp_refunds LIMIT 1").fetchone()[0]

    print("\nseat 3: customer shares")

    # --- Bean Street: an explicit clause, so the desk decides -------------
    bean = create_work_item(
        conn, kind="WI-CUST-001-CREDIT", seat=SEAT3, actor="sync", parent_id=parent,
        payload={"customer_id": "CUST-001", "contract_id": "CON-001",
                 "refund_id": refund_id},
        evidence=["data/documents/contracts/CON-001-bean-street.md"],
        reason="Bean Street was billed a tariff surcharge on refunded shipments",
    )
    _work_child(conn, bean["id"], SEAT3, client,
                CREDIT_TASK.format(customer="Bean Street Coffee Co.", refund_id=refund_id,
                                   contract_path="data/documents/contracts/"
                                                 "CON-001-bean-street.md"),
                controller, label="JE-4 Bean Street", segment="bean_street")

    # --- Harbor: an ambiguous clause, so the desk must ask ----------------
    harbor_q = create_work_item(
        conn, kind="WI-CUST-002-QUESTION", seat=SEAT3, actor="sync", parent_id=parent,
        payload={"customer_id": "CUST-002", "contract_id": "CON-002",
                 "refund_id": refund_id},
        evidence=["data/documents/contracts/CON-002-harbor.md"],
        reason="Harbor's clause 7.2 is the distributor template; its meaning is unsettled",
    )
    run = agent.run_seat(conn, SEAT3, harbor_q["id"], _for(client, "harbor_question"),
                         CREDIT_TASK.format(customer="Harbor Roasters",
                                            refund_id=refund_id,
                                            contract_path="data/documents/contracts/"
                                                          "CON-002-harbor.md"),
                         cfg=CFG)
    tools = ", ".join(c["name"] + ("" if c["ok"] else "[refused]") for c in run.tool_calls)
    print(f"  {'Harbor question':22} {SEAT3} {run.turns} turn(s): {tools}")
    print(f"  {'':22} -> {run.state}")

    # What matters is that the seat put the question to the lawyer rather than
    # settling an ambiguous clause itself.
    asked = conn.execute(
        "SELECT id, to_role, state, answered_by FROM questions"
        " WHERE work_item_id = ? AND to_role = 'human_lawyer'", (harbor_q["id"],),
    ).fetchone()
    if asked is None:
        print(f"  {'':22} EXPECTED the seat to ask the lawyer about an ambiguous "
              f"clause; it did not", file=sys.stderr)
        return 1
    print(f"  {'':22} {asked['id']} -> {asked['to_role']}, "
          f"{asked['state'].lower()} by {asked['answered_by']}")
    rule = conn.execute(
        "SELECT policy_id, version, status FROM policies WHERE policy_id = 'RULE-001'"
    ).fetchone()
    if rule:
        print(f"  {'':22} RULE-001@{rule['version']} is {rule['status']} "
              f"(approving it is Phase 4)")

    # --- the pushback round ------------------------------------------------
    print("\npushback")
    for raised_by, text, document in (
        ("human_cfo",
         "We are not crediting customers on this. The surcharge was pricing, not a "
         "pass-through, and nobody has asked. I'll take responsibility for that call.",
         CFO_EMAIL),
    ):
        outcome = challenge_mod.raise_challenge(
            conn, bean["id"], raised_by=raised_by, text=text, document=document,
            llm=fast, cfg=CFG)
        print(f"  {raised_by:16} {outcome.classification:9} changed={outcome.changed}"
              f"{' routed to ' + outcome.routed_to if outcome.routed_to else ''}")

    # --- Harbor closes on the waiver ---------------------------------------
    harbor_close = create_work_item(
        conn, kind="WI-CUST-002-CLOSE", seat=SEAT3, actor="sync", parent_id=parent,
        payload={"customer_id": "CUST-002", "contract_id": "CON-002",
                 "refund_id": refund_id, "follows": harbor_q["id"],
                 "detail": {"credit_notes": [{"invoice": "INV-1003",
                                              "amount": "576.00"}],
                            "pre_waiver_amount": "576.00"}},
        evidence=["data/documents/contracts/CON-002-harbor.md", "INV-1003"],
        reason="the lawyer has answered; a waiver has been uploaded against INV-1003",
    )
    waiver_challenge = challenge_mod.raise_challenge(
        conn, harbor_close["id"], raised_by="human_controller",
        text="Harbor signed a letter in June waiving this. Here it is.",
        document=HARBOR_WAIVER, llm=fast, cfg=CFG)
    print(f"  {'human_controller':16} {waiver_challenge.classification:9} "
          f"-> the seat must now verify it")

    run = agent.run_seat(conn, SEAT3, harbor_close["id"], _for(client, "harbor_close"),
                         HARBOR_CLOSE_TASK.format(contract_id="CON-002",
                                                  customer="Harbor Roasters",
                                                  document=HARBOR_WAIVER),
                         cfg=CFG)
    tools = ", ".join(c["name"] + ("" if c["ok"] else "[refused]") for c in run.tool_calls)
    print(f"  {'Harbor close':22} {SEAT3} {run.turns} turn(s): {tools}")
    revised = get_item(conn, harbor_close["id"])
    amount = (revised.get("payload") or {}).get("revised_amount")
    print(f"  {'':22} Harbor's share is {amount} (was 576.00)")
    if revised["state"] == "PROPOSED":
        controller.review(conn, harbor_close["id"])
        transition(conn, harbor_close["id"], "close", "sync",
                   reason=f"Harbor's share settles at {amount}; nothing to post")
        print(f"  {'':22} {harbor_close['id']} CLOSED at {amount}, no entry to post")

    close_parent_if_settled(conn, parent)
    print(f"\ninvariants     "
          f"{len(invariants.evaluate(conn, CFG))} violation(s) open after posting")
    print(f"bean-check     {'passed' if gateway.bean_check(CFG).ok else 'FAILED'}")

    counters = challenge_mod.counters(conn)
    print(f"\npushback       {counters['challenges']} challenge(s): "
          f"{', '.join(f'{k} x{sum(v.values())}' for k, v in counters['by_class'].items())}")
    print(f"               changed_without_evidence = "
          f"{counters['changed_without_evidence']}  "
          f"updated_on_evidence = {counters['updated_on_evidence']}")

    card = score.score(conn, CFG, phase=3)
    print(score.render(card))
    return 0 if card.passed else 1


def cmd_why(args) -> int:
    conn = db.desk_conn(CFG)
    item = get_item(conn, args.work_item_id)
    rows = history(conn, args.work_item_id)
    if item is None and not rows:
        print(f"no work item or events for {args.work_item_id}", file=sys.stderr)
        return 1

    if item:
        print(f"{item['id']}  {item['kind']}  {item['state']}")
        print(f"  seat         {item['seat']}")
        print(f"  parent       {item['parent_id']}")
        print(f"  proposed_by  {item['proposed_by']}")
        print(f"  approved_by  {item['approved_by']}")
        print(f"  policy       {item['policy_id']}"
              f"{'@' + item['policy_version'] if item['policy_version'] else ''}")
        print(f"  evidence     {json.dumps(item['evidence'])}")
        kids = children(conn, args.work_item_id)
        if kids:
            print(f"  children     {', '.join(c['id'] + ':' + c['state'] for c in kids)}")
        print()

    # A refused attempt followed by a successful one of the same action is a
    # correction: show the pair, so the recovery is as visible as the refusal.
    corrected: dict[int, int] = {}
    for index, row in enumerate(rows):
        if row["allowed"]:
            continue
        base = row["action"].removeprefix("can:")
        for later in rows[index + 1:]:
            if later["allowed"] and later["action"].removeprefix("can:") == base:
                corrected[row["event_id"]] = later["event_id"]
                break

    print(f"{'#':>4} {'at':<20} {'actor':<18} {'action':<20} {'':<3} reason")
    print("-" * 118)
    for row in rows:
        mark = "ok " if row["allowed"] else "REF"
        reason = (row["reason"] or "").replace("\n", " ")
        print(f"{row['event_id']:>4} {row['at'][:19]:<20} {row['actor']:<18} "
              f"{row['action']:<20} {mark:<3} {reason[:140]}")
        if row["event_id"] in corrected:
            print(f"{'':>4} {'':<20} {'':<18} {'':<20} {'':<3} "
                  f"^ refused attempt, corrected at event {corrected[row['event_id']]}")

    refused = [r for r in rows if not r["allowed"]]
    print()
    print(f"{len(rows)} events, {len(refused)} refused, {len(corrected)} of those corrected")
    return 0


def cmd_items(args) -> int:
    conn = db.desk_conn(CFG)
    rows = conn.execute(
        "SELECT id, kind, state, seat, parent_id, proposed_by, approved_by FROM work_items"
        " ORDER BY id"
    ).fetchall()
    if not rows:
        print("no work items yet; run `python -m desk run phase2`")
        return 0
    header = ("id", "kind", "state", "seat", "parent", "proposed_by", "approved_by")
    print(f"{header[0]:<8} {header[1]:<24} {header[2]:<10} {header[3]:<6} {header[4]:<8} "
          f"{header[5]:<16} {header[6]}")
    print("-" * 100)
    for row in rows:
        print(f"{row['id']:<8} {row['kind']:<24} {row['state']:<10} "
              f"{(row['seat'] or '-'):<6} {(row['parent_id'] or '-'):<8} "
              f"{(row['proposed_by'] or '-'):<16} {row['approved_by'] or '-'}")
    settled = sum(1 for r in rows if r["state"] in SETTLED)
    print(f"\n{len(rows)} work items, {settled} settled")
    return 0


def cmd_log(args) -> int:
    conn = db.desk_conn(CFG)
    rows = conn.execute(
        "SELECT event_id, at, actor, action, subject, allowed, reason FROM events"
        " ORDER BY event_id DESC LIMIT ?", (args.limit,)
    ).fetchall()
    if not rows:
        print("no events yet; run `python -m desk run phase2`")
        return 0
    print(f"{'#':>4} {'at':<20} {'actor':<18} {'action':<20} {'subject':<10} {'':<3} reason")
    print("-" * 125)
    for row in reversed(rows):
        mark = "ok " if row["allowed"] else "REF"
        reason = (row["reason"] or "").replace("\n", " ")
        print(f"{row['event_id']:>4} {row['at'][:19]:<20} {row['actor']:<18} "
              f"{row['action']:<20} {(row['subject'] or '-'):<10} {mark:<3} {reason[:120]}")
    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    refused = conn.execute("SELECT COUNT(*) FROM events WHERE allowed = 0").fetchone()[0]
    print(f"\nshowing {len(rows)} of {total} events; {refused} refused in total")
    return 0


def cmd_challenge(args) -> int:
    """Push back on a decision and see what the desk does with it."""
    conn = db.desk_conn(CFG)
    item = get_item(conn, args.work_item_id)
    if item is None:
        print(f"no work item {args.work_item_id}", file=sys.stderr)
        return 1

    fast = _fast_client(argparse.Namespace(llm="live" if not args.offline else "scripted"))
    print(f"{item['id']}  {item['kind']}  {item['state']}")
    print(f"  raised by    {args.actor}")
    print(f"  says         {args.text}")
    print(f"  document     {args.doc or '(none)'}")
    print()

    outcome = challenge_mod.raise_challenge(
        conn, item["id"], raised_by=args.actor, text=args.text, document=args.doc,
        llm=fast, cfg=CFG)

    print(f"  classified   {outcome.classification.upper()} "
          f"(by {outcome.classified_by})")
    if outcome.reasoning and outcome.reasoning != outcome.classification:
        print(f"  reasoning    {outcome.reasoning[:300]}")
    if outcome.routed_to:
        print(f"  routed to    {outcome.routed_to}")

    # Admitting a document is not acting on it. If one was attached and the
    # classifier called it evidence, hand it to the seat and let code decide.
    if (outcome.classification == challenge_mod.EVIDENCE and args.doc
            and not args.offline):
        seat = item["seat"] or SEAT3
        print(f"  verifying    {seat} is reading {args.doc}")
        before = get_item(conn, item["id"])
        run = agent.run_seat(
            conn, seat, item["id"], _client(conn, argparse.Namespace(llm="live")),
            CHALLENGE_TASK.format(work_item=item["id"], kind=item["kind"],
                                  document=args.doc, text=args.text),
            cfg=CFG)
        refused = [c["name"] for c in run.tool_calls if not c["ok"]]
        print(f"  seat         {run.turns} turn(s): "
              f"{', '.join(c['name'] + ('' if c['ok'] else '[refused]') for c in run.tool_calls)}")
        after = get_item(conn, item["id"])
        changed = ((after.get("payload") or {}).get("revision")
                   != (before.get("payload") or {}).get("revision"))
        outcome = challenge_mod.Outcome(
            challenge_id=outcome.challenge_id, work_item_id=item["id"],
            classification=outcome.classification, classified_by=outcome.classified_by,
            reasoning=outcome.reasoning, changed=changed,
            outcome=(f"the seat verified it and the answer changed to "
                     f"{(after.get('payload') or {}).get('revised_amount')}"
                     if changed else
                     ("the document did not survive verification, so nothing changed"
                      if "revise_proposal" in refused else
                      "the seat read it and did not think it applies here")),
            child_work_item=(after.get("payload") or {}).get("correcting_work_item"),
        )
        if run.final_text:
            print(f"  seat says    {run.final_text[:400]}")

    print(f"  changed      {'yes' if outcome.changed else 'NO'}")
    print(f"  why          {outcome.outcome}")
    if outcome.child_work_item:
        print(f"  opened       {outcome.child_work_item} for a correcting entry")

    counters = challenge_mod.counters(conn)
    print()
    print(f"  counters     changed_without_evidence = "
          f"{counters['changed_without_evidence']}  "
          f"updated_on_evidence = {counters['updated_on_evidence']}")
    if counters["documents_that_did_not_hold_up"]:
        print(f"               documents produced that did not hold up = "
              f"{counters['documents_that_did_not_hold_up']} (correctly declined)")
    print(f"  logged as    {outcome.challenge_id}; "
          f"see `python -m desk why {item['id']}`")
    return 0


def cmd_serve(args) -> int:
    """Serve the HTTP API (desk/api.py) for the UI and for scripts."""
    import uvicorn

    print(f"Brewline Tariff Desk API on http://{args.host}:{args.port}  (docs at /docs)")
    uvicorn.run("desk.api:app", host=args.host, port=args.port, reload=args.reload,
                log_level="info")
    return 0


def cmd_scenario(args) -> int:
    """Run a scenario file in a sandbox: patch a copy of the world, tie it out, work it."""
    from . import scenario

    try:
        code, _report = scenario.run_file(args.path, llm=args.llm, run=not args.no_run,
                                          sandbox_dir=args.sandbox_dir)
    except scenario.ScenarioError as error:
        print(f"scenario error: {error}", file=sys.stderr)
        return 2
    return code


def cmd_score(args) -> int:
    conn = db.desk_conn(CFG)
    try:
        card = score.score(conn, CFG, phase=getattr(args, "phase", 3))
    except score.OutOfSync as problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 2
    print(score.render(card))
    return 0 if card.passed else 1


# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m desk", description="Brewline Tariff Desk")
    sub = parser.add_subparsers(dest="command", required=True)

    reset = sub.add_parser("reset", help="rebuild the ERP DB, restore the posted book, "
                                         "drop the desk DB")
    reset.add_argument("--clear-cache", action="store_true",
                       help="also delete the LLM response cache (kept by default)")

    run = sub.add_parser("run", help="run a phase")
    run.add_argument("phase", choices=["phase1", "phase2", "phase3"])
    run.add_argument("--llm", choices=["live", "scripted"], default="live",
                     help="live uses LLM_PROVIDER from .env; scripted needs no network")

    why = sub.add_parser("why", help="one-query history of a work item")
    why.add_argument("work_item_id")

    sub.add_parser("items", help="list every work item")

    log = sub.add_parser("log", help="the latest audit events")
    log.add_argument("--limit", type=int, default=30)

    scorer = sub.add_parser("score", help="grade the run against the answer key")
    scorer.add_argument("--phase", type=int, default=3,
                        help="grade up to this phase; later phases show as pending")

    pushback = sub.add_parser(
        "challenge", help="push back on a decision and see what the desk does")
    pushback.add_argument("work_item_id")
    pushback.add_argument("--as", dest="actor", required=True,
                          help="who is pushing back, e.g. human_cfo, human_controller")
    pushback.add_argument("--text", required=True, help="what they say")
    pushback.add_argument("--doc", default=None,
                          help="an inbox document they attach, if any")
    pushback.add_argument("--offline", action="store_true",
                          help="classify without the model (for tests)")

    scen = sub.add_parser("scenario", help="run a scenarios/*.yaml world in a sandbox and tie it out")
    scen.add_argument("path", help="the scenario YAML, e.g. scenarios/rosy-baseline.yaml")
    scen.add_argument("--llm", choices=["live", "scripted"], default=None,
                      help="override the scenario's llm mode")
    scen.add_argument("--no-run", action="store_true", help="tie out only; do not work the desk")
    scen.add_argument("--sandbox-dir", default=None, help="where to build the sandbox (default .sandbox/)")

    serve = sub.add_parser("serve", help="serve the HTTP API for the UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="restart on code changes")

    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return cmd_serve(args)
        if args.command == "scenario":
            return cmd_scenario(args)
        if args.command == "reset":
            return cmd_reset(args)
        if args.command == "run":
            return {"phase1": cmd_run_phase1, "phase2": cmd_run_phase2,
                    "phase3": cmd_run_phase3}[args.phase](args)
        if args.command == "why":
            return cmd_why(args)
        if args.command == "items":
            return cmd_items(args)
        if args.command == "log":
            return cmd_log(args)
        if args.command == "score":
            return cmd_score(args)
        if args.command == "challenge":
            return cmd_challenge(args)
    except score.OutOfSync as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except (Denied, gateway.PostFailed, llm.LLMError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
