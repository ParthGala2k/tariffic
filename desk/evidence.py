"""Evidence checking for the challenge protocol (CLAUDE.md 6.8).

The split matters, and it is the point of this module:

* **The agent reads.** It extracts structured facts from the document - which
  customer, which contract, which invoices or what cutoff date - and for each
  fact gives an exact quote from the document it came from.
* **Code verifies.** Every quote must appear verbatim in the document. Then the
  coverage test is arithmetic: the work item's customer and contract must match
  the extracted ones, and each affected invoice's date must fall on or before
  the extracted cutoff (or be named outright).

So the model's job is reading comprehension, which it is good at and which is
checkable; the decision to let a proposal change is code's, and rests on facts
that have been checked back against the source text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .config import CFG, Config
from .guard import check_doc_path

INBOX = "data/documents/inbox"

# Facts the agent may extract. Each needs {"value": ..., "quote": "..."}.
FACT_FIELDS = ("customer", "contract", "coverage_cutoff_date", "covered_invoices", "effect")
REQUIRED_FACTS = ("customer", "contract", "effect")


class EvidenceRejected(Exception):
    """The document, or the facts drawn from it, did not hold up."""

    def __init__(self, reason: str, *, checks: list[dict] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.checks = checks or []


@dataclass
class Check:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class Verified:
    document: str
    doc_id: str
    facts: dict[str, Any]
    checks: list[Check] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"document": self.document, "doc_id": self.doc_id, "facts": self.facts,
                "checks": [c.as_dict() for c in self.checks]}


def _normalise(text: str) -> str:
    """Collapse whitespace so a quote spanning a line break still matches."""
    return re.sub(r"\s+", " ", text).strip().lower()


def frontmatter(text: str) -> dict[str, Any]:
    """Parse the YAML-ish frontmatter at the top of an inbox document."""
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}
    data: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[key.strip()] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            data[key.strip()] = value
    return data


def load_inbox_document(path: str, cfg: Config = CFG) -> tuple[Path, str, dict[str, Any]]:
    """Resolve an inbox document and return its path, text and frontmatter."""
    resolved = check_doc_path(path, cfg)          # the ground-truth guard still applies
    relative = resolved.relative_to(cfg.root).as_posix()
    if not relative.startswith(INBOX):
        raise EvidenceRejected(
            f"refused: {relative} is not an inbox document. Evidence must be a document "
            f"that actually arrived, under {INBOX}/."
        )
    if resolved.is_dir() or not resolved.exists():
        raise EvidenceRejected(f"refused: {relative} is not a document")
    text = resolved.read_text(encoding="utf-8")
    meta = frontmatter(text)
    if not meta.get("doc_id"):
        raise EvidenceRejected(
            f"refused: {relative} carries no doc_id, so it cannot be cited as evidence"
        )
    return resolved, text, meta


def _iso(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        return None


def verify(
    path: str,
    facts: dict[str, Any],
    *,
    customer_id: str | None = None,
    customer_name: str | None = None,
    contract_id: str | None = None,
    invoices: list[dict[str, Any]] | None = None,
    cfg: Config = CFG,
) -> Verified:
    """Check extracted facts against the document and against the work item.

    `invoices` is a list of {"invoice_id": ..., "invoice_date": ...} the proposal
    covers. Raises EvidenceRejected with the failing check; returns the verified
    facts otherwise.
    """
    resolved, text, meta = load_inbox_document(path, cfg)
    relative = resolved.relative_to(cfg.root).as_posix()
    body = _normalise(text)
    checks: list[Check] = []

    if not isinstance(facts, dict) or not facts:
        raise EvidenceRejected(
            "refused: no extracted facts. Read the document and give, for each fact, "
            "its value and an exact quote from the document: "
            f"{', '.join(REQUIRED_FACTS)} are required."
        )

    missing = [f for f in REQUIRED_FACTS if f not in facts]
    if missing:
        raise EvidenceRejected(
            f"refused: these facts are required and missing: {', '.join(missing)}. "
            "Each needs a value and an exact quote from the document."
        )

    # ---- 1. every quote must really be in the document ----------------------
    for name, fact in facts.items():
        if name not in FACT_FIELDS:
            raise EvidenceRejected(
                f"refused: {name!r} is not a fact this check understands. "
                f"Use: {', '.join(FACT_FIELDS)}."
            )
        if not isinstance(fact, dict) or "value" not in fact or "quote" not in fact:
            raise EvidenceRejected(
                f"refused: fact {name!r} must be {{\"value\": ..., \"quote\": \"...\"}} - "
                "the quote is what lets code check your reading against the document."
            )
        quote = _normalise(str(fact["quote"]))
        if not quote:
            raise EvidenceRejected(f"refused: fact {name!r} has an empty quote")
        if quote not in body:
            raise EvidenceRejected(
                f"refused: the quote given for {name!r} does not appear in {relative}. "
                f"Quote the document exactly. You wrote: {str(fact['quote'])[:160]!r}",
                checks=[c.as_dict() for c in checks],
            )
        checks.append(Check(f"quote:{name}", True,
                            f"verbatim in {relative}: {str(fact['quote'])[:80]!r}"))

    # ---- 2. the parties must be this work item's parties --------------------
    stated_customer = str(facts["customer"]["value"])
    candidates = {c for c in (customer_id, customer_name) if c}
    if candidates and not any(
        _normalise(c) in _normalise(stated_customer)
        or _normalise(stated_customer) in _normalise(c)
        for c in candidates
    ):
        raise EvidenceRejected(
            f"refused: the document is about {stated_customer!r}, but this work item is "
            f"about {' / '.join(sorted(candidates))}. A document only counts as evidence "
            f"for the party it names.",
            checks=[c.as_dict() for c in checks],
        )
    checks.append(Check("parties:customer", True,
                        f"{stated_customer} matches {customer_id or customer_name}"))

    stated_contract = str(facts["contract"]["value"])
    if contract_id and _normalise(contract_id) not in _normalise(stated_contract):
        raise EvidenceRejected(
            f"refused: the document is about contract {stated_contract!r}, but this work "
            f"item is about {contract_id}.",
            checks=[c.as_dict() for c in checks],
        )
    checks.append(Check("parties:contract", True,
                        f"{stated_contract} matches {contract_id}"))

    # the frontmatter has to agree with the agent's reading of who it concerns
    related = meta.get("relates_to") or []
    if related and contract_id and contract_id not in related:
        raise EvidenceRejected(
            f"refused: {relative} declares relates_to {related}, which does not include "
            f"{contract_id}.",
            checks=[c.as_dict() for c in checks],
        )
    checks.append(Check("frontmatter:relates_to", True, f"{related or 'not declared'}"))

    # ---- 3. coverage: dates, in code ---------------------------------------
    invoices = invoices or []
    cutoff_fact = facts.get("coverage_cutoff_date")
    named_fact = facts.get("covered_invoices")
    named = []
    if named_fact is not None:
        value = named_fact["value"]
        named = [value] if isinstance(value, str) else list(value)

    if not cutoff_fact and not named:
        raise EvidenceRejected(
            "refused: the document must say what it covers. Extract either "
            "coverage_cutoff_date (a date, with its quote) or covered_invoices (the "
            "invoice ids it names, with their quote).",
            checks=[c.as_dict() for c in checks],
        )

    if cutoff_fact:
        cutoff = _iso(cutoff_fact["value"])
        if cutoff is None:
            raise EvidenceRejected(
                f"refused: coverage_cutoff_date {cutoff_fact['value']!r} is not a date in "
                "YYYY-MM-DD form.",
                checks=[c.as_dict() for c in checks],
            )
        uncovered = []
        for invoice in invoices:
            invoice_date = _iso(invoice.get("invoice_date"))
            if invoice_date is None or invoice_date > cutoff:
                uncovered.append(
                    f"{invoice.get('invoice_id')} dated {invoice.get('invoice_date')}"
                )
        if uncovered:
            raise EvidenceRejected(
                f"refused: the document covers charges up to {cutoff.isoformat()}, which "
                f"does not reach {', '.join(uncovered)}. A document only changes the "
                f"invoices it actually covers.",
                checks=[c.as_dict() for c in checks],
            )
        checks.append(Check(
            "coverage:dates", True,
            f"every affected invoice is dated on or before {cutoff.isoformat()}: "
            + ", ".join(f"{i.get('invoice_id')} {i.get('invoice_date')}" for i in invoices),
        ))

    if named:
        ours = {i.get("invoice_id") for i in invoices}
        outside = sorted(ours - set(named))
        if outside:
            raise EvidenceRejected(
                f"refused: the document names {', '.join(named)}, which does not cover "
                f"{', '.join(outside)}.",
                checks=[c.as_dict() for c in checks],
            )
        checks.append(Check("coverage:named_invoices", True, f"names {', '.join(named)}"))

    return Verified(document=relative, doc_id=str(meta["doc_id"]), facts=facts, checks=checks)
