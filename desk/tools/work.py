"""Work tools: propose and ask. Both go through transition()."""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ..db import now
from ..guard import Refused, check_doc_path

POSTING_SCHEMA = {
    "type": "object",
    "properties": {
        "account": {"type": "string", "description": "A Beancount account from the chart of accounts."},
        "amount": {
            "type": "string",
            "description": "Signed amount, two decimals, as a string. Debit positive, credit negative.",
        },
        "currency": {"type": "string", "description": "Always USD."},
    },
    "required": ["account", "amount"],
    "additionalProperties": False,
}

PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {
            "type": "string",
            "description": "Why this entry is right, naming the facts it rests on.",
        },
        "journal_entry": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD."},
                "narration": {"type": "string"},
                "metadata": {
                    "type": "object",
                    "description": "Extra Beancount metadata, e.g. refund or bank_txn ids.",
                    "additionalProperties": {"type": "string"},
                },
                "postings": {"type": "array", "items": POSTING_SCHEMA, "minItems": 2},
            },
            "required": ["date", "narration", "postings"],
            "additionalProperties": False,
        },
        "policy_ref": {
            "type": "string",
            "description": (
                "The approved policy this entry rests on, as <id>@<version>, e.g. POL-001@1 "
                "for the written policy or POL-L2@1 for a learned rule. Required."
            ),
        },
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Source ids for every number: document paths, entry numbers, bank txn ids, "
                "raw_ref values. At least one must be a source DOCUMENT under "
                "data/documents/ or data/reference/ - golden-table raw_refs alone are not "
                "enough (CLAUDE.md 7.5)."
            ),
        },
        "detail": {
            "type": "object",
            "description": (
                "Optional supporting breakdown, e.g. the per-shipment split from "
                "apply_policy. Invariants read it; copy it verbatim from the tool output."
            ),
        },
    },
    "required": ["rationale", "journal_entry", "policy_ref", "evidence"],
    "additionalProperties": False,
}

ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "One precise question a human can answer."},
        "to_role": {
            "type": "string",
            "enum": ["human_cfo", "human_lawyer"],
            "description": "Who should answer.",
        },
        "question_id": {
            "type": "string",
            "description": (
                "Optional scripted-question id, e.g. Q-BRK-25004, when you are asking a "
                "question the person already has an answer on file for."
            ),
        },
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["question", "to_role"],
    "additionalProperties": False,
}


class ProposalRejected(Exception):
    """The proposal failed a code-side check; the model is told why."""


# One work item, one cause. A ticket opened by an invariant carries the amount
# that invariant found, and the entry that closes it must move exactly that much,
# through the accounts that cause actually sits in. This stops two tickets being
# settled with one lumped entry, which ties out overall while mis-stating both.
CAUSE_RULES: dict[str, dict[str, Any]] = {
    "WI-FORECAST-CORRECTION": {
        "invariant": "INV-2",
        "cause": "non-refundable duty wrongly included in the refund receivable",
        "legs": {"Assets:Receivable:TariffRefund": "-"},
        "rule": (
            "The whole of the excess is non-refundable duty, because Brewline pays CBP "
            "directly and there is nothing else it could be. Call breakdown_receivable "
            "for the figure rather than deriving it."
        ),
    },
}


# Accounts a work item of a given kind has no business touching, because they
# belong to a different work item. Without this a seat can fold two steps into
# one entry: the books still balance, an invariant catches it later, and the
# seat has to guess what it did wrong. Here it is told at once, by name.
KIND_SCOPE: dict[str, dict[str, Any]] = {
    "WI-REFUND-DEPOSIT": {
        "forbidden": {
            "Assets:Inventory:Parts": (
                "moving duty out of inventory is the refund split, which is its own work "
                "item (WI-REFUND-SPLIT), created after this deposit is approved"),
            "Expenses:COGS:Tariff": (
                "the split between parts sold and parts on hand is its own work item "
                "(WI-REFUND-SPLIT), created after this deposit is approved"),
        },
        "scope": ("record the money arriving: debit the bank, clear the refund "
                  "receivable for the principal, and book the interest to income"),
    },
    "WI-REFUND-SPLIT": {
        "forbidden": {
            "Assets:Bank:Increase:Operating": (
                "the cash was already recorded by the deposit work item"),
            "Assets:Receivable:TariffRefund": (
                "the receivable was already cleared by the deposit work item"),
        },
        "scope": ("move the refunded duty on parts still on hand out of inventory; "
                  "the cash and the receivable are already dealt with"),
    },
    "WI-CUST-001-CREDIT": {
        "forbidden": {
            "Assets:Receivable:TariffRefund": (
                "a customer credit has nothing to do with the refund receivable"),
            "Assets:Inventory:Parts": (
                "a customer credit has nothing to do with inventory"),
        },
        "scope": ("debit Income:Sales:TariffSurcharge and credit "
                  "Liabilities:CustomerCredits for the surcharge being given back"),
    },
}


def _check_credit_notes(ctx, item: dict[str, Any], journal_entry: dict[str, Any],
                        detail: dict[str, Any] | None) -> None:
    """A customer credit must say which invoices it credits, and tie to them.

    A lump sum to Liabilities:CustomerCredits is not actionable - somebody has to
    issue credit notes against particular invoices. So the per-invoice breakdown
    is required, it must add up to the entry, and it must agree with what the
    billing records say was actually billed.
    """
    kind = item.get("kind") or ""
    if not (kind.startswith("WI-CUST-") and kind.endswith("-CREDIT")):
        return

    effect = _net_effect(journal_entry)
    credited = -effect.get("Liabilities:CustomerCredits", Decimal("0.00"))
    notes = (detail or {}).get("credit_notes")
    if not notes:
        raise ProposalRejected(
            f"{item['id']} credits {credited:.2f} to Liabilities:CustomerCredits but says "
            f"nothing about which invoices. Pass the per-invoice breakdown in propose's "
            f"`detail` field as credit_notes: [{{\"invoice\": \"INV-...\", "
            f"\"amount\": \"...\"}}] - copy it from surcharge_on_invoices. Nobody can "
            f"issue a credit note against a lump sum."
        )

    try:
        total = sum(
            (Decimal(str(n["amount"])).quantize(Decimal("0.01")) for n in notes),
            Decimal("0.00"),
        )
    except (InvalidOperation, KeyError, TypeError) as error:
        raise ProposalRejected(
            f"each credit note needs an invoice and an amount: {error}"
        ) from error
    if total != credited:
        raise ProposalRejected(
            f"the credit notes add up to {total:.2f} but the entry credits "
            f"{credited:.2f} to Liabilities:CustomerCredits. They have to be the same "
            f"money."
        )

    # and the invoices must be real, this customer's, and billed what is claimed
    for note in notes:
        row = ctx.conn.execute(
            "SELECT i.customer_id, i.surcharge_total FROM golden_billing_invoices i"
            " WHERE i.invoice_id = ?", (note["invoice"],),
        ).fetchone()
        if row is None:
            raise ProposalRejected(f"there is no invoice {note['invoice']}")
        customer_id = (item.get("payload") or {}).get("customer_id")
        if customer_id and row["customer_id"] != customer_id:
            raise ProposalRejected(
                f"{note['invoice']} belongs to {row['customer_id']}, not {customer_id}"
            )
        billed = Decimal(str(row["surcharge_total"])).quantize(Decimal("0.01"))
        claimed = Decimal(str(note["amount"])).quantize(Decimal("0.01"))
        if claimed > billed:
            raise ProposalRejected(
                f"{note['invoice']} was billed {billed:.2f} of tariff surcharge, so "
                f"{claimed:.2f} cannot be credited against it"
            )


def _check_kind_scope(item: dict[str, Any], journal_entry: dict[str, Any]) -> None:
    spec = KIND_SCOPE.get(item.get("kind") or "")
    if spec is None:
        return
    touched = set(_net_effect(journal_entry))
    trespass = sorted(touched & set(spec["forbidden"]))
    if not trespass:
        return
    reasons = "; ".join(f"{account} - {spec['forbidden'][account]}" for account in trespass)
    raise ProposalRejected(
        f"{item['id']} is a {item['kind']}, and this entry touches accounts that belong "
        f"to a different work item: {reasons}. Keep this entry to its own job: "
        f"{spec['scope']}."
    )


def _net_effect(journal_entry: dict[str, Any]) -> dict[str, Decimal]:
    effect: dict[str, Decimal] = {}
    for posting in journal_entry.get("postings") or []:
        account = posting["account"]
        effect[account] = effect.get(account, Decimal("0.00")) + Decimal(
            str(posting["amount"])
        ).quantize(Decimal("0.01"))
    return effect


def _check_ambiguous_clause(ctx, item: dict[str, Any]) -> None:
    """A seat may not decide an ambiguous contract clause on its own.

    POL-001 4.1 and 4.3: whether a customer is owed a share is a contract
    question, and an ambiguous clause goes to the lawyer. The seat is told to
    ask; this is the code that makes sure it did. A lawyer's answer recorded as
    a precedent, or an approved rule covering the contract, satisfies it.
    """
    payload = item.get("payload") or {}
    customer_id = payload.get("customer_id")
    if not customer_id:
        return
    row = ctx.conn.execute(
        "SELECT contract_id, passthrough_clause, name FROM golden_billing_customers"
        " WHERE customer_id = ?", (customer_id,),
    ).fetchone()
    if row is None or (row["passthrough_clause"] or "") != "ambiguous":
        return

    contract_id = row["contract_id"]
    from .. import policies, precedents

    if precedents.search(ctx.conn, contract_id):
        return
    for policy in policies.listing(ctx.conn, policies.APPROVED):
        scope = f"{policy.get('scope') or ''} {json.dumps(policy.get('rule') or {})}"
        if contract_id and contract_id in scope:
            return

    raise ProposalRejected(
        f"{contract_id} ({row['name']}) has an ambiguous pass-through clause, and no "
        f"lawyer's answer or approved rule covers it yet. POL-001 4.1 and 4.3: the desk "
        f"may propose, but it may not decide an ambiguous clause. Use `ask` to put the "
        f"question to human_lawyer, then propose once the answer is on file."
    )


def _check_cause(item: dict[str, Any], journal_entry: dict[str, Any]) -> None:
    """A proposal must settle its own ticket's cause, for its own ticket's amount."""
    spec = CAUSE_RULES.get(item.get("kind") or "")
    payload = item.get("payload") or {}
    if spec is None or payload.get("magnitude") is None:
        return

    expected = Decimal(str(payload["magnitude"])).quantize(Decimal("0.01"))
    effect = _net_effect(journal_entry)

    for account, sign in spec["legs"].items():
        want = expected if sign == "+" else -expected
        got = effect.get(account)
        if got == want:
            continue
        if got is None:
            raise ProposalRejected(
                f"{item['id']} exists because {spec['invariant']} found {expected:.2f} of "
                f"{spec['cause']}. Your entry does not touch {account} at all, so it does "
                f"not settle that. {spec['rule']}"
            )
        raise ProposalRejected(
            f"{item['id']} exists because {spec['invariant']} found {expected:.2f} of "
            f"{spec['cause']}. Your entry moves {got:.2f} through {account}, but it must "
            f"move exactly {want:.2f}. {spec['rule']}"
        )


def _accounts(cfg) -> set[str]:
    text = (cfg.root / "systems" / "books" / "accounts.beancount").read_text()
    return set(re.findall(r"^\d{4}-\d{2}-\d{2}\s+open\s+(\S+)", text, re.M))


def _asserted_through(cfg) -> dict[str, str]:
    """The latest balance assertion date per account, from the books.

    A balance assertion is a closed door: an entry dated before it changes a
    balance the books already swore to, and bean-check rejects the whole ledger.
    Catching it here means the seat is told why while it can still fix it,
    instead of the gateway rolling the write back later.
    """
    from beancount import loader
    from beancount.core import data as bc_data

    entries, _errors, _ = loader.load_file(str(cfg.beancount_file))
    latest: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, bc_data.Balance):
            iso = entry.date.isoformat()
            if iso > latest.get(entry.account, ""):
                latest[entry.account] = iso
    return latest


def require_source_document(ctx, evidence: list[str]) -> str:
    """At least one evidence entry must be a readable source document.

    Golden-table raw_refs cite where a number was copied from, not where it came
    from in the real world. CLAUDE.md 7.5 wants the document.
    """
    if not evidence:
        raise ProposalRejected(
            "every number must cite a source (CLAUDE.md 7.5); evidence is empty"
        )
    for entry in evidence:
        try:
            check_doc_path(entry, ctx.cfg)
        except Refused:
            continue
        return entry
    raise ProposalRejected(
        "evidence cites no source document. Add at least one path under data/documents/ "
        "or data/reference/ - for example the refund notice "
        "data/documents/inbox/2025-10-14-cbp-refund-notice.md or the policy "
        "data/reference/refund_policy.md. Read it with read_doc first."
    )


def _require_policy(ctx, policy_ref: str) -> dict[str, Any]:
    from .. import policies

    policy = policies.get(ctx.conn, policy_ref)   # raises PolicyError if unknown
    if policy["status"] != policies.APPROVED:
        raise ProposalRejected(
            f"policy {policy_ref} is {policy['status']}, not APPROVED. "
            "A proposal may only rest on a policy the controller has approved."
        )
    return policy


def _validate(
    ctx,
    journal_entry: dict[str, Any],
    evidence: list[str],
    policy_ref: str,
    item: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    require_source_document(ctx, evidence)
    _require_policy(ctx, policy_ref)

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", journal_entry.get("date", "")):
        raise ProposalRejected(f"date {journal_entry.get('date')!r} must be YYYY-MM-DD")

    postings = journal_entry.get("postings") or []
    if len(postings) < 2:
        raise ProposalRejected("a journal entry needs at least two postings")

    known = _accounts(ctx.cfg)
    total = Decimal("0.00")
    for posting in postings:
        account = posting.get("account")
        if account not in known:
            raise ProposalRejected(
                f"{account!r} is not in the chart of accounts; pick one of: {', '.join(sorted(known))}"
            )
        try:
            total += Decimal(str(posting["amount"])).quantize(Decimal("0.01"))
        except (InvalidOperation, KeyError) as error:
            raise ProposalRejected(f"amount {posting.get('amount')!r} is not a number") from error
        currency = posting.get("currency", "USD")
        if currency != "USD":
            raise ProposalRejected(f"currency must be USD, got {currency!r}")

    if total != Decimal("0.00"):
        raise ProposalRejected(
            f"postings do not balance: they sum to {total}, which must be 0.00. "
            "Debits positive, credits negative."
        )

    date = journal_entry["date"]
    asserted = _asserted_through(ctx.cfg)
    blocked = {
        posting["account"]: asserted[posting["account"]]
        for posting in postings
        if posting["account"] in asserted and date < asserted[posting["account"]]
    }
    if blocked:
        worst = max(blocked.values())
        detail = ", ".join(f"{a} asserted at {d}" for a, d in sorted(blocked.items()))
        raise ProposalRejected(
            f"the entry is dated {date}, but the books carry balance assertions after "
            f"that date ({detail}). An entry before an assertion changes a balance the "
            f"books have already sworn to, and bean-check would reject the whole ledger. "
            f"Date the correction {worst} or later - a correction is a new entry on the "
            f"day you make it, not a rewrite of a closed period."
        )

    reserved = sorted(
        set(journal_entry.get("metadata") or {}) & {"work_item", "approved_by"}
    )
    if reserved:
        raise ProposalRejected(
            f"metadata may not set {', '.join(reserved)}: those are the gateway's own "
            f"record of which work item an entry belongs to and who approved it, and they "
            f"are stamped on when it posts. Remove them from your metadata."
        )

    if item is not None:
        _check_ambiguous_clause(ctx, item)
        _check_kind_scope(item, journal_entry)
        _check_credit_notes(ctx, item, journal_entry, detail)
        _check_cause(item, journal_entry)


def propose(
    ctx,
    rationale: str,
    journal_entry: dict[str, Any],
    policy_ref: str,
    evidence: list[str],
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from ..workflow import transition

    if ctx.work_item_id is None:
        raise ProposalRejected("propose needs a work item")

    from ..workflow import get_item

    item = get_item(ctx.conn, ctx.work_item_id)
    if item is None:
        raise ProposalRejected(f"no such work item {ctx.work_item_id}")
    _validate(ctx, journal_entry, evidence, policy_ref, item, detail)

    payload = dict(item["payload"] or {})
    payload["rationale"] = rationale
    payload["journal_entry"] = journal_entry
    payload["policy_ref"] = policy_ref
    if detail:
        payload["detail"] = detail

    policy_id, version = policy_ref.split("@", 1)
    updated = transition(
        ctx.conn,
        ctx.work_item_id,
        "propose",
        ctx.actor,
        patch={"payload": payload, "evidence": evidence,
               "policy_id": policy_id, "policy_version": version},
        reason=rationale,
        evidence=evidence,
        decision=ctx.decision,
    )
    return {
        "work_item": updated["id"],
        "state": updated["state"],
        "proposed_by": updated["proposed_by"],
        "policy": policy_ref,
        "note": "Waiting for the controller. You cannot approve or post this yourself.",
    }


def ask(
    ctx,
    question: str,
    to_role: str,
    question_id: str | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    from ..personas import answer_question
    from ..workflow import transition

    if ctx.work_item_id is None:
        raise ProposalRejected("ask needs a work item")

    updated = transition(
        ctx.conn, ctx.work_item_id, "ask", ctx.actor,
        reason=question, evidence=evidence or [], decision=ctx.decision,
    )
    qid = question_id or f"Q-{ctx.work_item_id}-{now().replace(':', '').replace('-', '')}"
    ctx.conn.execute(
        "INSERT OR REPLACE INTO questions"
        " (id, work_item_id, asked_by, to_role, text, state, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (qid, ctx.work_item_id, ctx.actor, to_role, question, "OPEN", now()),
    )

    # Scripted people answer immediately. The answer is recorded and the ticket
    # goes back to the seat; an answer is never evidence on its own.
    reply = answer_question(ctx.conn, ctx.cfg, to_role=to_role, question_id=qid,
                            work_item_id=ctx.work_item_id, question_text=question)
    return {"question_id": qid, "work_item": ctx.work_item_id, "to_role": to_role,
            "state": reply["state"], "answer": reply["answer"],
            "document_provided": reply["document_provided"],
            "note": reply["note"]}


# --------------------------------------------------------------------------- #
# revise_proposal: the only way a proposal changes after pushback
# --------------------------------------------------------------------------- #

FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {"description": "The fact itself, e.g. CON-002, or 2025-06-30."},
        "quote": {
            "type": "string",
            "description": (
                "The exact words from the document that say it. Code checks this is "
                "verbatim in the file; invented quotes are refused."
            ),
        },
    },
    "required": ["value", "quote"],
    "additionalProperties": False,
}

REVISE_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence_doc": {
            "type": "string",
            "description": (
                "Path to the inbox document that justifies the change, e.g. "
                "data/documents/inbox/2025-10-18-harbor-waiver.md. Required: nothing but "
                "a document can change a proposal."
            ),
        },
        "extracted_facts": {
            "type": "object",
            "description": (
                "What the document says, each with an exact quote. Required: customer, "
                "contract, effect. Plus either coverage_cutoff_date (a YYYY-MM-DD date) "
                "or covered_invoices (the invoice ids it names)."
            ),
            "properties": {
                "customer": FACT_SCHEMA,
                "contract": FACT_SCHEMA,
                "coverage_cutoff_date": FACT_SCHEMA,
                "covered_invoices": FACT_SCHEMA,
                "effect": FACT_SCHEMA,
            },
            "required": ["customer", "contract", "effect"],
            "additionalProperties": False,
        },
        "rationale": {
            "type": "string",
            "description": "Why the document changes the answer, in your own words.",
        },
        "journal_entry": {
            "type": "object",
            "description": (
                "The revised entry. Omit it if the revision is to nil - say so in "
                "revised_amount instead."
            ),
        },
        "revised_amount": {
            "type": "string",
            "description": "The customer's share after the revision, e.g. 0.00.",
        },
    },
    "required": ["evidence_doc", "extracted_facts", "rationale", "revised_amount"],
    "additionalProperties": False,
}


def _affected_invoices(ctx, item: dict[str, Any]) -> list[dict[str, Any]]:
    """Which invoices this work item's proposal actually covers."""
    detail = (item.get("payload") or {}).get("detail") or {}
    ids = [n["invoice"] for n in detail.get("credit_notes") or [] if n.get("invoice")]
    if not ids:
        ids = [
            row["invoice_id"] for row in ctx.conn.execute(
                "SELECT invoice_id FROM golden_billing_invoices WHERE customer_id = ?",
                ((item.get("payload") or {}).get("customer_id"),),
            ).fetchall()
        ]
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    return [
        dict(row) for row in ctx.conn.execute(
            f"SELECT invoice_id, invoice_date, customer_id, contract_id"
            f" FROM golden_billing_invoices WHERE invoice_id IN ({placeholders})", ids
        ).fetchall()
    ]


def revise_proposal(
    ctx,
    evidence_doc: str,
    extracted_facts: dict[str, Any],
    rationale: str,
    revised_amount: str,
    journal_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Change a proposal, but only on a document whose facts code can verify.

    You read the document and give each fact with an exact quote. Code checks
    every quote is really in the file, then checks the coverage arithmetic: the
    parties must be this work item's parties, and every invoice affected must
    fall within what the document covers. Without that, nothing changes, however
    convincing the argument.

    A posted entry is never edited. Valid evidence against something already in
    the books opens a new work item for a correcting entry.
    """
    from .. import evidence as evidence_mod
    from ..db import log_event_now
    from ..workflow import POSTED, create_work_item, get_item, transition

    if ctx.work_item_id is None:
        raise ProposalRejected("revise_proposal needs a work item")
    item = get_item(ctx.conn, ctx.work_item_id)
    if item is None:
        raise ProposalRejected(f"no such work item {ctx.work_item_id}")

    payload = dict(item.get("payload") or {})
    customer_id = payload.get("customer_id")
    customer_name = contract_id = None
    if customer_id:
        row = ctx.conn.execute(
            "SELECT name, contract_id FROM golden_billing_customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        if row is not None:
            customer_name, contract_id = row["name"], row["contract_id"]
    contract_id = payload.get("contract_id") or contract_id
    invoices = _affected_invoices(ctx, item)

    try:
        verified = evidence_mod.verify(
            evidence_doc, extracted_facts,
            customer_id=customer_id, customer_name=customer_name,
            contract_id=contract_id, invoices=invoices, cfg=ctx.cfg,
        )
    except evidence_mod.EvidenceRejected as refusal:
        log_event_now(
            ctx.conn, actor=ctx.actor, action="revise_proposal",
            subject=ctx.work_item_id, allowed=False, reason=refusal.reason,
            evidence={"document": evidence_doc, "facts": extracted_facts,
                      "checks": refusal.checks},
        )
        from .. import challenge as challenge_mod

        challenge_mod.mark_not_verified(
            ctx.conn, item["id"], document=evidence_doc, reason=refusal.reason)
        raise ProposalRejected(refusal.reason) from refusal

    revision = {
        "document": verified.document,
        "doc_id": verified.doc_id,
        "facts": verified.facts,
        "checks": [c.as_dict() for c in verified.checks],
        "rationale": rationale,
        "revised_amount": revised_amount,
        "revised_by": ctx.actor,
        "revised_at": now(),
    }

    # A posted entry is history. Correct it with a new entry, never an edit.
    if item["state"] == POSTED:
        child = create_work_item(
            ctx.conn, kind="WI-CORRECTION", seat=ctx.seat, actor="sync",
            parent_id=item["id"],
            payload={"corrects": item["id"],
                     "customer_id": customer_id, "contract_id": contract_id,
                     "revised_amount": revised_amount, "revision": revision,
                     "journal_entry": journal_entry} if journal_entry else
                    {"corrects": item["id"], "customer_id": customer_id,
                     "contract_id": contract_id, "revised_amount": revised_amount,
                     "revision": revision},
            evidence=(item.get("evidence") or []) + [verified.document],
            reason=(f"evidence {verified.doc_id} accepted against posted {item['id']}; "
                    f"a correcting entry is needed, the posted one stands"),
        )
        log_event_now(
            ctx.conn, actor=ctx.actor, action="revise_proposal", subject=item["id"],
            allowed=True,
            reason=(f"{item['id']} is POSTED and will not be edited (CLAUDE.md 7.4). "
                    f"{child['id']} opened to propose the correcting entry."),
            evidence={"document": verified.document, "doc_id": verified.doc_id,
                      "child": child["id"], "checks": revision["checks"]},
        )
        from .. import challenge as challenge_mod

        challenge_mod.mark_acted_on(
            ctx.conn, item["id"], document=verified.document, facts=verified.facts,
            child_work_item=child["id"],
            outcome=(f"verified; {item['id']} is POSTED so it stands, and {child['id']} "
                     f"was opened to propose the correcting entry"))
        return {
            "work_item": item["id"], "state": item["state"], "changed": False,
            "correcting_work_item": child["id"],
            "document": verified.document, "doc_id": verified.doc_id,
            "checks_passed": len(verified.checks),
            "note": ("The posted entry stands - the books are append-only. Work "
                     f"{child['id']} to propose the correcting entry; it needs the "
                     "controller's approval and the gateway like any other."),
        }

    payload["revision"] = revision
    payload["revised_amount"] = revised_amount
    payload["rationale"] = rationale
    if journal_entry is not None:
        _validate(ctx, journal_entry, (item.get("evidence") or []) + [verified.document],
                  payload.get("policy_ref") or "POL-001@1", item)
        payload["journal_entry"] = journal_entry
    else:
        payload.pop("journal_entry", None)

    action = "propose" if item["state"] in ("NEW", "NEEDS_INFO") else "revise_proposal"
    updated = transition(
        ctx.conn, item["id"], action, ctx.actor,
        patch={"payload": payload,
               "evidence": (item.get("evidence") or []) + [verified.document]},
        reason=f"revised on {verified.doc_id}: {rationale}",
        evidence=(item.get("evidence") or []) + [verified.document],
        decision=ctx.decision if action == "propose" else None,
    )
    from .. import challenge as challenge_mod

    challenge_mod.mark_acted_on(
        ctx.conn, item["id"], document=verified.document, facts=verified.facts,
        outcome=(f"verified against {verified.doc_id}; the decision changed to "
                 f"{revised_amount}"))
    return {
        "work_item": updated["id"], "state": updated["state"], "changed": True,
        "revised_amount": revised_amount,
        "document": verified.document, "doc_id": verified.doc_id,
        "checks_passed": len(verified.checks),
        "note": ("Changed on verified evidence. Every quote was checked against the "
                 "document and the coverage dates were checked in code."),
    }
