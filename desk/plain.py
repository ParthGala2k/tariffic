"""Plain words for the screen.

A CFO must see at a glance what they are being asked to approve. This module
turns the brief's structured fields into a headline, one sentence, at most three
reasons, humanised money lines and document titles. It is built by code from
rows, never by the model, and every technical name it hides (account strings,
policy refs, work item ids, tool names) stays in the brief for the details view.
"""
from __future__ import annotations

import re
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any

from .config import CFG, Config
from .evidence import frontmatter

SEAT_NAMES = {"seat1": "Trade compliance", "seat2": "Cost accounting", "seat3": "Revenue accounting"}
SEAT_JOBS = {
    "seat1": "classifies duties, reconciles what was paid with Customs, matches refunds",
    "seat2": "splits refunds between parts sold and parts in stock",
    "seat3": "decides what each customer is owed under their contract",
}

ACCOUNT_NAMES = {
    "Assets:Bank:Increase:Operating": "Operating cash",
    "Assets:Receivable:TariffRefund": "Tariff refund receivable",
    "Assets:Receivable:Trade": "Customer receivables",
    "Assets:Inventory:Parts": "Parts inventory",
    "Liabilities:Payable:Vendors": "Owed to vendors",
    "Liabilities:CustomerCredits": "Credits owed to customers",
    "Equity:Opening-Balances": "Opening balances",
    "Income:Sales:Machines": "Machine sales",
    "Income:Sales:TariffSurcharge": "Tariff surcharge billed",
    "Income:Interest:TariffRefund": "Interest on the refund",
    "Expenses:COGS:Parts": "Cost of parts sold",
    "Expenses:COGS:Tariff": "Tariff cost",
}

DOC_TYPES = {
    "refund_notice": "CBP refund notice", "bank_memo": "Bank memo", "email": "Email",
    "signed_letter": "Signed letter", "contract": "Contract", "accounting_policy": "Accounting policy",
    "uploaded_evidence": "Uploaded document", "policy": "Policy",
}


def money(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def fmt(value: Any) -> str:
    return f"{money(value):,.2f}"


def account_name(account: str) -> str:
    return ACCOUNT_NAMES.get(account) or account.split(":")[-1].replace("-", " ")


def document_title(path: str, cfg: Config = CFG) -> str:
    """What a document is, in words: 'CBP refund notice', 'Bean Street contract (CON-001)'."""
    file = cfg.root / path
    meta: dict[str, Any] = {}
    if file.is_file() and file.suffix.lower() in (".md", ".txt"):
        try:
            meta = frontmatter(file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            meta = {}
    kind = str(meta.get("type") or "")
    doc_id = str(meta.get("doc_id") or "")
    stem = file.stem
    if kind == "contract":
        who = re.sub(r"^CON-\d+-", "", stem).replace("-", " ").title()
        return f"{who} contract ({doc_id or stem.split('-')[0]})"
    if kind == "email":
        sender = str(meta.get("from") or "").split(",")[0].strip()
        return f"Email from {sender}" if sender else "Email"
    if kind == "signed_letter":
        sender = str(meta.get("from") or "").strip()
        return f"Signed letter from {sender}" if sender else "Signed letter"
    if kind in DOC_TYPES:
        return DOC_TYPES[kind]
    if stem == "hts_programs":
        return "Tariff programme table"
    if stem == "refund_policy":
        return "Refund accounting policy"
    return stem.replace("-", " ").replace("_", " ")


def _customer_name(conn: sqlite3.Connection | None, customer_id: str | None) -> str | None:
    if not conn or not customer_id:
        return None
    row = conn.execute("SELECT name FROM golden_billing_customers WHERE customer_id = ?",
                       (customer_id,)).fetchone()
    return row["name"] if row else customer_id


def _first_sentence(text: str | None) -> str:
    if not text:
        return ""
    cut = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return cut[:220]


def plain(conn: sqlite3.Connection | None, cfg: Config, brief: dict[str, Any]) -> dict[str, Any]:
    """The plain-language layer over one brief."""
    w = brief.get("work_item") or {}
    kind, state = w.get("kind") or "", w.get("state") or ""
    trigger = brief.get("trigger") or {}
    proposal = brief.get("proposal") or {}
    postings = proposal.get("postings") or []
    detail = proposal.get("detail") or {}
    policy = proposal.get("policy") or {}
    by = {p["account"]: money(p["amount"]) for p in postings if p.get("account")}
    debits = sum((money(p["amount"]) for p in postings if p.get("side") == "debit"), Decimal("0.00"))
    customer = _customer_name(conn, trigger.get("customer_id"))
    questions = brief.get("questions") or []
    answered = next((q for q in reversed(questions) if q.get("answer")), None)

    title, summary, why, if_approved = "", "", [], None

    if kind == "WI-REFUND-DEPOSIT":
        cash = by.get("Assets:Bank:Increase:Operating", debits)
        principal = -by.get("Assets:Receivable:TariffRefund", Decimal("0"))
        interest = -by.get("Income:Interest:TariffRefund", Decimal("0"))
        title = "Record the tariff refund that arrived from U.S. Customs"
        summary = (f"{fmt(cash)} landed in the operating account on {trigger.get('date', 'the bank date')}. "
                   f"It matches the CBP refund notice: {fmt(principal)} of duty principal and "
                   f"{fmt(interest)} of interest.") if postings else (
                   f"A credit of {fmt(trigger.get('amount'))} arrived from {trigger.get('counterparty', 'the U.S. Treasury')}.")
        why = ["The amount and date match the refund notice to the cent.",
               "The principal clears the receivable booked when the entries liquidated.",
               "The interest is income; it is never passed on to customers."]
        if_approved = "The entry posts to the books. Next the refund is split between parts sold and parts still in stock."

    elif kind == "WI-REFUND-SPLIT":
        to_inv = money(detail.get("to_inventory_total") or -by.get("Assets:Inventory:Parts", Decimal("0")))
        principal = money(detail.get("principal_total") or 0)
        to_cogs = money(detail.get("to_cogs_total") or (principal - to_inv if principal else 0))
        title = "Split the refund between parts sold and parts still in stock"
        summary = (f"Of the {fmt(principal) if principal else 'refunded duty'} refunded, {fmt(to_cogs)} relates to parts "
                   f"already sold and {fmt(to_inv)} to parts still on the shelf. The on-shelf part comes out of inventory cost.")
        why = ["Per shipment: refund x parts consumed / parts received, from the stock records.",
               "The two parts add up to the refund exactly.",
               "Follows the approved refund-split rule."]
        if_approved = "Inventory and cost of goods sold are corrected. Customer credits come next."

    elif kind.startswith("WI-CUST-") and kind.endswith("-CREDIT"):
        notes = detail.get("credit_notes") or []
        who = customer or "the customer"
        title = f"Credit {who} for the tariff surcharge"
        summary = (f"{who} was billed {fmt(debits)} of tariff surcharge on machines built from the refunded "
                   f"shipments. Their contract says the surcharge is credited back when the duties are refunded.")
        why = ["The contract clause is explicit: the full surcharge is credited when duties are refunded.",
               ("Credit notes: " + ", ".join(f"{n.get('invoice')} {fmt(n.get('amount'))}" for n in notes) + ".")
               if notes else "Tied to the invoices that carried the surcharge.",
               "The refund interest stays with Brewline."]
        if_approved = f"A credit of {fmt(debits)} is booked, with credit-note amounts prepared for each invoice."

    elif kind.endswith("-QUESTION"):
        who = customer or "This customer"
        title = f"{who}: is a credit owed?"
        summary = f"{who}'s contract wording is open to more than one reading, so the desk asked the lawyer instead of deciding."
        if answered:
            summary += f" The lawyer answered: {_first_sentence(answered.get('answer'))}"
        why = ["An ambiguous clause is never decided by an agent.",
               "The lawyer's answer is recorded as a precedent for the next case like it."]
        if_approved = None

    elif kind == "WI-CUST-002-CLOSE" or kind.endswith("-CLOSE"):
        who = customer or "The customer"
        revised = proposal.get("revised_amount")
        title = (f"{who}: review the signed waiver" if state == "PROPOSED" else
                 f"{who}: settled on the signed waiver") if revised is not None else f"{who}: settle the credit"
        summary = (f"A signed letter from {who} waives the surcharge credit on the invoices it covers, so the "
                   f"credit settles at {fmt(revised)}." if revised is not None else
                   f"Settle what {who} is owed after the lawyer's answer.")
        why = ["Only a document can change a decision; this one was checked word for word.",
               "The waiver covers the invoice dates in question."]
        if revised is not None and detail.get("pre_waiver_amount") is not None:
            summary += f" Before the waiver, the contract called for {fmt(detail['pre_waiver_amount'])}."
        if_approved = "Nothing posts; the file is closed at the settled amount."

    elif kind == "WI-FORECAST-CORRECTION":
        title = "Correct the refund estimate"
        summary = (f"The quarter-end estimate counted {fmt(debits or trigger.get('magnitude'))} of duty that is never "
                   f"refunded. This takes it back out of the receivable.")
        why = ["Only IEEPA duties are being refunded; Section 232 and 301 duties are not.",
               "The figure comes straight from the customs entries, programme by programme.",
               "The estimate itself was flagged as a habit not to learn."]
        if_approved = "The receivable is reduced to what Customs will actually refund."

    elif kind == "WI-DUTY-RECONCILIATION":
        title = "Explain a difference between the books and Customs"
        summary = (f"The books and the customs form disagree by "
                   f"{fmt(debits or trigger.get('magnitude'))} on one entry. Brewline pays "
                   f"Customs directly, so the two should match exactly.")
        why = ["The customs entry summary is the source of truth for duty.",
               "A difference is explained, never plugged."]
        if_approved = "The difference is explained and the books agree with Customs."
    elif kind == "WI-ONBOARD-REVIEW":
        title = "Review past practice: the September forecast method"
        summary = (_first_sentence(proposal.get("rationale"))
                   or "The desk checks whether last quarter's refund forecast is a method worth keeping.")
        why = ["A habit found in the books is only adopted if the written policy supports it.",
               "A bad habit is flagged so it is never applied again."]
        if_approved = "The method is recorded as a bad habit; nothing posts."

    elif kind.startswith("WI-ONBOARD"):
        name = policy.get("name") or trigger.get("name") or "learned from the books"
        title = f"Adopt a rule: {name}"
        summary = _first_sentence(proposal.get("rationale")) or "A rule the desk learned from the policy and the books."
        why = ["Learned from the written policy and the 2025 history, with the evidence counted.",
               "Once adopted, code applies it the same way every time."]
        if_approved = "The rule becomes applicable to every case inside its scope."

    elif kind == "WI-REFUND-EVENT":
        title = f"A tariff refund arrived: {fmt(trigger.get('amount'))} from U.S. Customs"
        summary = "Recording it, splitting it and settling what customers are owed."
        if_approved = None

    elif kind == "WI-CORRECTION":
        title = "Correcting entry"
        summary = _first_sentence(proposal.get("rationale")) or "A document changed an already-posted decision; a correcting entry is proposed."
        if_approved = "The correcting entry posts; the original stays on the books."

    else:
        from .brief import KIND_TITLES
        title = KIND_TITLES.get(kind) or kind.removeprefix("WI-").replace("-", " ").title()
        summary = _first_sentence(proposal.get("rationale"))
        if_approved = "The entry posts to the books." if postings else None

    verdict = brief.get("authorization") or {}
    why_you = None
    if state == "PROPOSED":
        decision = "Adopt or decline the rule" if policy and not postings and kind.startswith("WI-ONBOARD") else "Approve or decline"
        if verdict and not verdict.get("covered"):
            why_you = verdict.get("reason")
    elif state == "NEEDS_INFO":
        decision = "Waiting on the lawyer" if not answered else "Answered; the desk will continue"
    elif state == "POSTED":
        decision = ("Posted on your standing authorization" if w.get("approved_by") == "standing_authorization"
                    else "Posted to the books")
    elif state == "CLOSED":
        decision = "Settled"
    elif state == "REJECTED":
        decision = "Declined"
    else:
        decision = "Being worked"

    money_lines = []
    for p in postings:
        amt = money(p.get("amount"))
        money_lines.append({"label": account_name(p.get("account", "")), "account": p.get("account"),
                            "amount": f"{abs(amt):,.2f}", "side": "debit" if amt > 0 else "credit"})

    evidence = []
    for e in brief.get("evidence") or []:
        if e.get("kind") == "document":
            evidence.append({"title": document_title(e["ref"], cfg), "path": e["ref"]})
    read = [document_title(p, cfg) for p in brief.get("sources_read") or []]

    return {
        "title": title, "summary": summary, "why": why[:3], "if_approved": if_approved,
        "decision": decision, "why_you": why_you,
        "routine": bool(verdict.get("covered")) if verdict else None,
        "amount": f"{debits:,.2f}" if debits else None,
        "proposer": SEAT_NAMES.get(w.get("seat") or "", w.get("seat")),
        "customer": customer, "money_lines": money_lines, "evidence": evidence, "read": read,
        "computed": bool(brief.get("computations")),
        "checked": bool((brief.get("invariant_gate") or {}).get("allowed")),
    }
