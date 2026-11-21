"""The five invariants (CLAUDE.md 6.6).

Two rules govern how they are used:

* **Gating is by effect.** A proposal is judged by simulating its posting on top
  of the current books. Approval is refused only if the proposal creates a *new*
  violation or makes an existing one *worse*. A pre-existing violation - PM-1
  sitting in the 2025-09-30 receivable, say - never blocks an unrelated proposal.
* **A violation found in existing data opens a work item**, it does not stop work.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from .config import CFG, Config

ZERO = Decimal("0.00")

INV_1 = "INV-1"   # duty paid per the books must equal the CBP 7501
INV_2 = "INV-2"   # the refund receivable may only hold refundable programmes
INV_3 = "INV-3"   # refund received <= refundable duty; deposit == notice total
INV_4 = "INV-4"   # a refund split must add up
INV_5 = "INV-5"   # ledger inventory ties to the ERP landed value on hand

TITLES = {
    INV_1: "duty paid equals the CBP 7501",
    INV_2: "the refund receivable holds only refundable programmes",
    INV_3: "refund received does not exceed refundable duty; deposit equals the notice",
    INV_4: "a refund split adds up",
    INV_5: "ledger inventory ties to the ERP landed value on hand",
}

RECEIVABLE_REFUND = "Assets:Receivable:TariffRefund"
INVENTORY_PARTS = "Assets:Inventory:Parts"
COGS_TARIFF = "Expenses:COGS:Tariff"


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Violation:
    invariant: str
    subject: str
    magnitude: Decimal
    message: str
    evidence: tuple[str, ...] = ()
    opens_kind: str | None = None     # the work-item kind that fixes it

    @property
    def key(self) -> tuple[str, str]:
        return (self.invariant, self.subject)

    def as_dict(self) -> dict[str, Any]:
        return {"invariant": self.invariant, "title": TITLES[self.invariant],
                "subject": self.subject, "magnitude": f"{self.magnitude:.2f}",
                "message": self.message, "evidence": list(self.evidence),
                "opens_kind": self.opens_kind}


@dataclass
class Candidate:
    """A proposal being judged, treated as if it had already posted."""
    item_id: str
    kind: str
    postings: list[tuple[str, Decimal]] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "Candidate | None":
        payload = item.get("payload") or {}
        entry = payload.get("journal_entry") or {}
        postings = [
            (p["account"], _money(p["amount"])) for p in entry.get("postings") or []
        ]
        return cls(item_id=item["id"], kind=item["kind"], postings=postings,
                   detail=payload.get("detail") or {}, payload=payload)


# --------------------------------------------------------------------------- #
# The facts the checks run against
# --------------------------------------------------------------------------- #

def ledger_balances(cfg: Config = CFG, extra: Iterable[tuple[str, Decimal]] = ()) -> dict[str, Decimal]:
    """Account balances from the books, optionally with a simulated entry added."""
    from beancount import loader
    from beancount.core import data as bc_data

    entries, errors, _ = loader.load_file(str(cfg.beancount_file))
    if errors:
        raise RuntimeError(f"beancount load errors: {errors[:3]}")

    balances: dict[str, Decimal] = {}
    for entry in entries:
        if not isinstance(entry, bc_data.Transaction):
            continue
        for posting in entry.postings:
            balances[posting.account] = balances.get(posting.account, ZERO) + _money(
                posting.units.number
            )
    for account, amount in extra:
        balances[account] = balances.get(account, ZERO) + _money(amount)
    return balances


def _settled_items(conn: sqlite3.Connection, kind: str, candidate: Candidate | None) -> list[dict]:
    """Posted work items of a kind, plus the candidate if it is of that kind."""
    rows = conn.execute(
        "SELECT * FROM work_items WHERE kind = ? AND state = 'POSTED'", (kind,)
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"] or "{}")
        items.append(item)
    if candidate is not None and candidate.kind == kind:
        if not any(i["id"] == candidate.item_id for i in items):
            items.append({"id": candidate.item_id, "kind": kind, "payload": candidate.payload})
    return items


def _refundable_programs(conn: sqlite3.Connection) -> set[str]:
    """Which programmes are refundable, per the approved learned policy if there is one.

    Falls back to the reference table's own `refundable` column, so the invariant
    still works before onboarding has happened.
    """
    from . import policies

    for policy in policies.listing(conn, policies.APPROVED):
        rule = policy["rule"] or {}
        if rule.get("kind") == "program_refundability" and rule.get("refundable"):
            return set(rule["refundable"])
    return set()


def _reference_refundable(cfg: Config) -> set[str]:
    import csv

    path = cfg.root / "data" / "reference" / "hts_programs.csv"
    with path.open() as handle:
        return {
            row["program_id"] for row in csv.DictReader(handle)
            if (row.get("refundable") or "").strip().lower() == "yes"
        }


# --------------------------------------------------------------------------- #
# What is actually sitting in the refund receivable, and why
# --------------------------------------------------------------------------- #

def duty_paid_vs_cbp(conn: sqlite3.Connection, cfg: Config = CFG) -> dict[str, Decimal]:
    """Per entry: what the books paid in duty, minus what the CBP 7501 says.

    Brewline pays CBP directly, so these must agree line for line. A difference
    means the books and the customs paperwork disagree about what was paid, which
    is never something to plug.
    """
    cbp: dict[str, Decimal] = {}
    for row in conn.execute(
        "SELECT entry_no, total_duty FROM golden_cbp_entries"
    ).fetchall():
        cbp[row["entry_no"]] = _money(row["total_duty"])

    paid: dict[str, Decimal] = {}
    for row in conn.execute(
        "SELECT reference, amount FROM golden_bank_txns"
        " WHERE direction = 'debit' AND description LIKE 'DUTY PMT%'"
    ).fetchall():
        entry_no = (row["reference"] or "").strip()
        if entry_no:
            paid[entry_no] = paid.get(entry_no, ZERO) + _money(row["amount"])

    difference: dict[str, Decimal] = {}
    for entry_no in sorted(set(cbp) | set(paid)):
        delta = paid.get(entry_no, ZERO) - cbp.get(entry_no, ZERO)
        if delta != ZERO:
            difference[entry_no] = delta
    return difference


def receivable_breakdown(
    conn: sqlite3.Connection,
    cfg: Config = CFG,
    balances: dict[str, Decimal] | None = None,
    candidate: "Candidate | None" = None,
) -> dict[str, Any]:
    """Break the refund receivable down by cause.

    The receivable should hold only refundable duty the desk is still waiting
    for. Anything above that is an excess, and the excess has named causes:
    duty under a programme that is never refunded. Brewline pays CBP directly,
    so there is no other way for the receivable to be overstated.
    """
    import itertools

    if balances is None:
        balances = ledger_balances(cfg, candidate.postings if candidate else ())

    refundable = _refundable_programs(conn) or _reference_refundable(cfg)
    outstanding = ZERO
    refunds, raw_refs = [], ["data/reference/hts_programs.csv"]

    for refund in conn.execute("SELECT refund_id, raw_ref FROM golden_cbp_refunds").fetchall():
        received = _settled_items(conn, "WI-REFUND-DEPOSIT", candidate)
        already = any(refund["refund_id"] in json.dumps(i["payload"]) for i in received)
        rows = conn.execute(
            "SELECT program, duty, raw_ref FROM golden_cbp_duty_lines WHERE entry_no IN"
            " (SELECT entry_no FROM golden_cbp_refund_entries WHERE refund_id = ?)",
            (refund["refund_id"],),
        ).fetchall()
        by_program: dict[str, Decimal] = {}
        for row in rows:
            by_program[row["program"]] = by_program.get(row["program"], ZERO) + _money(row["duty"])
        refundable_duty = sum((v for k, v in by_program.items() if k in refundable), ZERO)
        if not already:
            outstanding += refundable_duty
        refunds.append({"refund_id": refund["refund_id"], "deposit_recorded": already,
                        "by_program": by_program, "refundable_duty": refundable_duty})
        raw_refs.append(refund["raw_ref"])

    balance = balances.get(RECEIVABLE_REFUND, ZERO)
    excess = balance - outstanding

    # Brewline pays CBP directly, so the only way the receivable can be too big
    # is duty under a programme that is never refunded.
    non_refundable_component = max(ZERO, excess)

    # Which non-refundable programmes account for that remainder?
    non_refundable_by_program: dict[str, Decimal] = {}
    for entry in refunds:
        for program, duty in entry["by_program"].items():
            if program not in refundable:
                non_refundable_by_program[program] = (
                    non_refundable_by_program.get(program, ZERO) + duty
                )
    identified: list[str] = []
    programs = sorted(non_refundable_by_program)
    for size in range(1, min(len(programs), 3) + 1):
        for combo in itertools.combinations(programs, size):
            if sum((non_refundable_by_program[p] for p in combo), ZERO) == non_refundable_component:
                identified = list(combo)
                break
        if identified:
            break

    return {
        "receivable_account": RECEIVABLE_REFUND,
        "receivable_balance": f"{balance:.2f}",
        "refundable_outstanding": f"{outstanding:.2f}",
        "excess": f"{excess:.2f}",
        "by_cause": {"non_refundable_duty": f"{non_refundable_component:.2f}"},
        "non_refundable_duty_by_program": {
            k: f"{v:.2f}" for k, v in sorted(non_refundable_by_program.items())
        },
        "non_refundable_programs_identified": identified,
        "refundable_programs": sorted(refundable),
        "work_items": {"non_refundable_duty": "WI-FORECAST-CORRECTION"},
        "raw_refs": sorted(set(raw_refs)),
    }


# --------------------------------------------------------------------------- #
# The five checks
# --------------------------------------------------------------------------- #

def check_duty_paid_vs_cbp(conn, balances, cfg, candidate) -> list[Violation]:
    """INV-1. The duty the books paid must equal the CBP 7501, entry by entry.

    Brewline pays CBP directly, so there is nothing in between to explain a gap.
    A difference means the books and the customs paperwork disagree about what was
    paid, and that is a finding, not a rounding.
    """
    difference = duty_paid_vs_cbp(conn, cfg)
    if not difference:
        return []

    violations = []
    for entry_no, delta in sorted(difference.items()):
        direction = "more than" if delta > ZERO else "less than"
        violations.append(Violation(
            INV_1, entry_no, abs(delta),
            (f"the books paid {abs(delta):.2f} {direction} the CBP 7501 says was due on "
             f"{entry_no}. Brewline pays CBP directly, so the two must agree"),
            ("data/views/bank/transactions.csv", "data/views/customs/cbp_entries.json"),
            opens_kind="WI-DUTY-RECONCILIATION",
        ))
    return violations


def check_forecast_refundable_only(conn, balances, cfg, candidate) -> list[Violation]:
    """INV-2. Catches PM-1: non-refundable duty counted in the refund receivable.

    Anything above the refundable duty still outstanding is duty under a programme
    that is never refunded, and that is what this reports.
    """
    breakdown = receivable_breakdown(conn, cfg, balances, candidate)
    amount = _money(breakdown["by_cause"]["non_refundable_duty"])
    if amount <= ZERO:
        return []

    identified = breakdown["non_refundable_programs_identified"]
    names = ", ".join(identified) if identified else "non-refundable programmes"
    aside = ""
    return [Violation(
        INV_2, RECEIVABLE_REFUND, amount,
        (f"{RECEIVABLE_REFUND} holds {_money(breakdown['receivable_balance']):.2f} but only "
         f"{_money(breakdown['refundable_outstanding']):.2f} of refundable duty is still "
         f"outstanding. {amount:.2f} of that gap is duty under {names}, which is never "
         f"refunded and must not sit in the refund receivable.{aside}"),
        tuple(breakdown["raw_refs"]),
        opens_kind="WI-FORECAST-CORRECTION",
    )]


def check_refund_received(conn, balances, cfg, candidate) -> list[Violation]:
    """INV-3. The deposit equals the notice, and principal never exceeds refundable duty."""
    refundable = _refundable_programs(conn) or _reference_refundable(cfg)
    violations = []
    for refund in conn.execute("SELECT * FROM golden_cbp_refunds").fetchall():
        lines = conn.execute(
            "SELECT principal, interest FROM golden_cbp_refund_entries WHERE refund_id = ?",
            (refund["refund_id"],),
        ).fetchall()
        principal = sum((_money(r["principal"]) for r in lines), ZERO)
        interest = sum((_money(r["interest"]) for r in lines), ZERO)

        if principal + interest != _money(refund["total"]):
            violations.append(Violation(
                INV_3, f"{refund['refund_id']}:total",
                abs(principal + interest - _money(refund["total"])),
                (f"{refund['refund_id']}: lines total {principal + interest:.2f} but the "
                 f"notice says {_money(refund['total']):.2f}"),
                (refund["raw_ref"],),
            ))

        duty_rows = conn.execute(
            "SELECT program, duty FROM golden_cbp_duty_lines WHERE entry_no IN"
            " (SELECT entry_no FROM golden_cbp_refund_entries WHERE refund_id = ?)",
            (refund["refund_id"],),
        ).fetchall()
        refundable_duty = sum(
            (_money(r["duty"]) for r in duty_rows if r["program"] in refundable), ZERO
        )
        if principal > refundable_duty:
            violations.append(Violation(
                INV_3, f"{refund['refund_id']}:principal", principal - refundable_duty,
                (f"{refund['refund_id']}: principal {principal:.2f} exceeds the refundable "
                 f"duty on those entries, {refundable_duty:.2f}"),
                (refund["raw_ref"], "data/reference/hts_programs.csv"),
            ))
    return violations


def check_split_adds_up(conn, balances, cfg, candidate) -> list[Violation]:
    """INV-4. to_cogs + to_inventory must equal the principal, per entry and in total."""
    violations = []
    items = _settled_items(conn, "WI-REFUND-SPLIT", candidate)
    # also judge a split that is only proposed, when it is the candidate
    for item in items:
        payload = item["payload"]
        detail = payload.get("detail") or {}
        by_shipment = detail.get("by_shipment") or {}
        if not by_shipment:
            violations.append(Violation(
                INV_4, item["id"], ZERO,
                (f"{item['id']} is a refund split but carries no per-shipment breakdown in "
                 "its detail, so the split cannot be checked. Copy the by_shipment output "
                 "of split_refund into propose's detail field"),
                (),
            ))
            continue

        cogs_total, inventory_total, principal_total = ZERO, ZERO, ZERO
        for shipment, row in sorted(by_shipment.items()):
            principal = _money(row.get("principal", 0))
            to_cogs = _money(row.get("to_cogs", 0))
            to_inventory = _money(row.get("to_inventory", 0))
            if to_cogs + to_inventory != principal:
                violations.append(Violation(
                    INV_4, f"{item['id']}:{shipment}",
                    abs(to_cogs + to_inventory - principal),
                    (f"{shipment}: to_cogs {to_cogs:.2f} + to_inventory {to_inventory:.2f} "
                     f"= {to_cogs + to_inventory:.2f}, which is not the principal "
                     f"{principal:.2f}"),
                    (),
                ))
            cogs_total += to_cogs
            inventory_total += to_inventory
            principal_total += principal

        refund_id = detail.get("refund_id")
        if refund_id:
            row = conn.execute(
                "SELECT total_principal, raw_ref FROM golden_cbp_refunds WHERE refund_id = ?",
                (refund_id,),
            ).fetchone()
            if row is not None and principal_total != _money(row["total_principal"]):
                violations.append(Violation(
                    INV_4, f"{item['id']}:{refund_id}",
                    abs(principal_total - _money(row["total_principal"])),
                    (f"the split covers {principal_total:.2f} of principal but "
                     f"{refund_id} refunded {_money(row['total_principal']):.2f}"),
                    (row["raw_ref"],),
                ))

        entry = payload.get("journal_entry") or {}
        posted_inventory = -sum(
            (_money(p["amount"]) for p in entry.get("postings") or []
             if p.get("account") == INVENTORY_PARTS), ZERO
        )
        if entry and posted_inventory != inventory_total:
            violations.append(Violation(
                INV_4, f"{item['id']}:entry",
                abs(posted_inventory - inventory_total),
                (f"the entry moves {posted_inventory:.2f} out of {INVENTORY_PARTS} but the "
                 f"split says the on-hand portion is {inventory_total:.2f}"),
                (),
            ))
    return violations


def erp_landed_value_on_hand(conn: sqlite3.Connection) -> Decimal:
    """Σ qty_on_hand × (unit_cost + duty_per_unit) over the ERP layers."""
    rows = conn.execute(
        "SELECT qty_on_hand, unit_cost, duty_per_unit FROM golden_erp_inventory_layers"
    ).fetchall()
    return sum(
        (Decimal(str(r["qty_on_hand"])) * (_money(r["unit_cost"]) + _money(r["duty_per_unit"]))
         for r in rows), ZERO
    ).quantize(Decimal("0.01"))


def check_inventory_tie_out(conn, balances, cfg, candidate) -> list[Violation]:
    """INV-5. Ledger inventory == ERP landed value on hand − refunded duty on on-hand parts.

    The subtraction matters: once a refund split has posted, the duty on parts
    still on the shelf has been refunded and is no longer part of their cost. So
    the tie-out is 185,952.00 before the split and 172,820.00 after it.
    """
    landed = erp_landed_value_on_hand(conn)
    if landed == ZERO:
        return []

    refunded_on_hand = ZERO
    for item in _settled_items(conn, "WI-REFUND-SPLIT", candidate):
        detail = (item["payload"] or {}).get("detail") or {}
        if detail.get("to_inventory_total") is not None:
            refunded_on_hand += _money(detail["to_inventory_total"])
        else:
            refunded_on_hand += sum(
                (_money(r.get("to_inventory", 0))
                 for r in (detail.get("by_shipment") or {}).values()), ZERO
            )

    expected = landed - refunded_on_hand
    actual = balances.get(INVENTORY_PARTS, ZERO)
    if actual == expected:
        return []
    return [Violation(
        INV_5, INVENTORY_PARTS, abs(actual - expected),
        (f"{INVENTORY_PARTS} is {actual:.2f} but the ERP landed value on hand "
         f"({landed:.2f}) less refunded duty on parts on hand ({refunded_on_hand:.2f}) "
         f"is {expected:.2f}"),
        ("systems/erp/erp.db#inventory_layers",),
    )]


CHECKS = (
    (INV_1, check_duty_paid_vs_cbp),
    (INV_2, check_forecast_refundable_only),
    (INV_3, check_refund_received),
    (INV_4, check_split_adds_up),
    (INV_5, check_inventory_tie_out),
)


# --------------------------------------------------------------------------- #
# Running them
# --------------------------------------------------------------------------- #

def evaluate(
    conn: sqlite3.Connection,
    cfg: Config = CFG,
    candidate: Candidate | None = None,
) -> list[Violation]:
    """Every violation that holds right now - or would hold if `candidate` posted."""
    balances = ledger_balances(cfg, candidate.postings if candidate else ())
    found: list[Violation] = []
    for _name, check in CHECKS:
        found.extend(check(conn, balances, cfg, candidate))
    return found


@dataclass
class Gate:
    allowed: bool
    reason: str
    before: list[Violation]
    after: list[Violation]
    new: list[Violation]
    worse: list[Violation]


def gate(conn: sqlite3.Connection, item: dict[str, Any], cfg: Config = CFG) -> Gate:
    """Judge a proposal by its effect.

    Refuse only if posting it would create a new violation or deepen an existing
    one. Violations that are already there and unaffected do not block it.
    """
    candidate = Candidate.from_item(item)
    before = evaluate(conn, cfg)
    after = evaluate(conn, cfg, candidate)

    before_by_key = {v.key: v for v in before}
    new = [v for v in after if v.key not in before_by_key]
    worse = [
        v for v in after
        if v.key in before_by_key and v.magnitude > before_by_key[v.key].magnitude
    ]

    if not new and not worse:
        improved = [
            v for v in before
            if v.key not in {a.key for a in after}
            or next((a for a in after if a.key == v.key), v).magnitude < v.magnitude
        ]
        note = f"; improves {len(improved)} existing" if improved else ""
        return Gate(True, f"no new or worsened invariant violations{note}",
                    before, after, [], [])

    parts = [f"{v.invariant} {v.message}" for v in new + worse]
    kind = "would create" if new else "would worsen"
    return Gate(False, f"refused: {kind} an invariant violation - " + "; ".join(parts),
                before, after, new, worse)


def scan_and_open_work_items(
    conn: sqlite3.Connection,
    cfg: Config = CFG,
    *,
    parent_id: str | None = None,
    actor: str = "sync",
) -> list[dict[str, Any]]:
    """Open a work item for each violation found in existing data.

    One ticket per (invariant, subject). Re-running is safe: a ticket that is
    already open or settled for the same violation is not duplicated.
    """
    from .db import log_event_now
    from .workflow import create_work_item

    opened = []
    for violation in evaluate(conn, cfg):
        if violation.opens_kind is None:
            log_event_now(
                conn, actor=actor, action="invariant", subject=parent_id, allowed=False,
                reason=f"refused: {violation.invariant} violated - {violation.message}",
                evidence=list(violation.evidence),
            )
            continue
        existing = conn.execute(
            "SELECT id FROM work_items WHERE kind = ? AND json_extract(payload, '$.violation')"
            " = ?", (violation.opens_kind, f"{violation.invariant}:{violation.subject}"),
        ).fetchone()
        if existing is not None:
            continue
        item = create_work_item(
            conn, kind=violation.opens_kind, seat="seat1", actor=actor,
            parent_id=parent_id,
            payload={"violation": f"{violation.invariant}:{violation.subject}",
                     "invariant": violation.invariant,
                     "subject": violation.subject,
                     "magnitude": f"{violation.magnitude:.2f}",
                     "finding": violation.message},
            evidence=list(violation.evidence),
            reason=f"{violation.invariant} violated: {violation.message}",
        )
        opened.append(item)
    return opened
