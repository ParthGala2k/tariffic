"""Compute tools. The arithmetic lives here, never in the model."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

MATCH_DEPOSIT_SCHEMA = {
    "type": "object",
    "properties": {
        "bank_txn_id": {
            "type": "string",
            "description": "Bank transaction id from golden_bank_txns, e.g. BNK-0026.",
        },
        "tolerance_days": {
            "type": "integer",
            "description": "How many days the deposit may sit from the refund payment date (default 7).",
        },
    },
    "required": ["bank_txn_id"],
    "additionalProperties": False,
}


class ComputeError(Exception):
    """The compute tool could not produce an exact answer."""


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _iso(value: str) -> date:
    return date.fromisoformat(value)


def match_deposit(ctx, bank_txn_id: str, tolerance_days: int | None = None) -> dict[str, Any]:
    """Match a bank deposit to a CBP refund by amount and date.

    Returns the matched entries with principal and interest. All arithmetic is
    done here with Decimal and cross-checked against the refund's own totals.
    """
    tolerance = int(tolerance_days if tolerance_days is not None else 7)
    conn = ctx.conn

    txn = conn.execute(
        "SELECT txn_id, date, direction, amount, counterparty, description, raw_ref"
        " FROM golden_bank_txns WHERE txn_id = ?",
        (bank_txn_id,),
    ).fetchone()
    if txn is None:
        raise ComputeError(f"no bank transaction {bank_txn_id} in golden_bank_txns")
    if txn["direction"] != "credit":
        raise ComputeError(f"{bank_txn_id} is a {txn['direction']}, not a deposit")

    amount = _money(txn["amount"])
    txn_date = _iso(txn["date"])

    candidates = []
    for refund in conn.execute(
        "SELECT refund_id, notice_date, payment_date, payee, importer_no, total_principal,"
        " total_interest, total, raw_ref FROM golden_cbp_refunds"
    ).fetchall():
        if _money(refund["total"]) != amount:
            continue
        if abs((_iso(refund["payment_date"]) - txn_date).days) > tolerance:
            continue
        candidates.append(refund)

    if not candidates:
        raise ComputeError(
            f"no CBP refund matches {bank_txn_id}: {amount} on {txn['date']} "
            f"(within {tolerance} days)"
        )
    if len(candidates) > 1:
        raise ComputeError(
            f"{bank_txn_id} matches more than one refund: "
            f"{', '.join(r['refund_id'] for r in candidates)}; a human must decide"
        )

    refund = candidates[0]
    lines = conn.execute(
        "SELECT entry_no, program, hts_ch99, principal, interest, raw_ref"
        " FROM golden_cbp_refund_entries WHERE refund_id = ? ORDER BY entry_no",
        (refund["refund_id"],),
    ).fetchall()
    if not lines:
        raise ComputeError(f"refund {refund['refund_id']} has no entry lines")

    principal = sum((_money(line["principal"]) for line in lines), Decimal("0.00"))
    interest = sum((_money(line["interest"]) for line in lines), Decimal("0.00"))

    # Cross-check against the refund header and the deposit itself.
    if principal != _money(refund["total_principal"]):
        raise ComputeError(
            f"principal mismatch: lines {principal} vs notice {refund['total_principal']}"
        )
    if interest != _money(refund["total_interest"]):
        raise ComputeError(
            f"interest mismatch: lines {interest} vs notice {refund['total_interest']}"
        )
    if principal + interest != amount:
        raise ComputeError(
            f"principal {principal} + interest {interest} != deposit {amount}"
        )

    entry_nos = [line["entry_no"] for line in lines]
    liquidation = {
        row["entry_no"]: row["liquidation_status"]
        for row in conn.execute(
            "SELECT entry_no, liquidation_status FROM golden_cbp_entries WHERE refund_id = ?",
            (refund["refund_id"],),
        ).fetchall()
    }

    return {
        "bank_txn_id": txn["txn_id"],
        "deposit_date": txn["date"],
        "deposit_amount": f"{amount:.2f}",
        "refund_id": refund["refund_id"],
        "notice_date": refund["notice_date"],
        "payee": refund["payee"],
        "importer_no": refund["importer_no"],
        "matched_on": f"amount {amount:.2f} and payment date within {tolerance} days",
        "entry_count": len(lines),
        "entries": [
            {
                "entry_no": line["entry_no"],
                "program": line["program"],
                "hts_ch99": line["hts_ch99"],
                "principal": f"{_money(line['principal']):.2f}",
                "interest": f"{_money(line['interest']):.2f}",
                "liquidation_status": liquidation.get(line["entry_no"]),
            }
            for line in lines
        ],
        "principal": f"{principal:.2f}",
        "interest": f"{interest:.2f}",
        "total": f"{principal + interest:.2f}",
        "raw_refs": [txn["raw_ref"], refund["raw_ref"]] + [line["raw_ref"] for line in lines],
        "entry_nos": entry_nos,
    }


# --------------------------------------------------------------------------- #
# Phase 2 compute tools
# --------------------------------------------------------------------------- #

SUM_DUTIES_SCHEMA = {
    "type": "object",
    "properties": {
        "entry_no": {
            "type": "string",
            "description": "A CBP entry number, e.g. BRK-2500225-4. Omit to total every entry.",
        },
        "refund_id": {
            "type": "string",
            "description": "Total only the entries on this refund, e.g. RFD-001.",
        },
    },
    "additionalProperties": False,
}

RECONCILE_DUTY_PAID_SCHEMA = {
    "type": "object",
    "properties": {
        "entry_no": {
            "type": "string",
            "description": "A CBP entry number. Omit to reconcile every entry.",
        }
    },
    "additionalProperties": False,
}

SPLIT_REFUND_SCHEMA = {
    "type": "object",
    "properties": {
        "refund_id": {"type": "string", "description": "The refund to split, e.g. RFD-001."},
        "policy_ref": {
            "type": "string",
            "description": (
                "Which approved split policy to run, as <id>@<version>. Omit to use the "
                "approved refund_split policy."
            ),
        },
    },
    "required": ["refund_id"],
    "additionalProperties": False,
}


def sum_duties(ctx, entry_no: str | None = None, refund_id: str | None = None) -> dict[str, Any]:
    """Total the duty on an entry, per programme, straight from the CBP 7501 lines."""
    conn = ctx.conn
    where, params = [], []
    if entry_no:
        where.append("d.entry_no = ?")
        params.append(entry_no)
    if refund_id:
        where.append("d.entry_no IN (SELECT entry_no FROM golden_cbp_refund_entries"
                     " WHERE refund_id = ?)")
        params.append(refund_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    rows = conn.execute(
        "SELECT d.entry_no, d.program, d.hts_ch99, d.rate_pct, d.duty, d.raw_ref"
        f" FROM golden_cbp_duty_lines d{clause} ORDER BY d.entry_no, d.program", params
    ).fetchall()
    if not rows:
        raise ComputeError(
            f"no CBP duty lines for {entry_no or refund_id or 'any entry'}"
        )

    by_entry: dict[str, dict[str, str]] = {}
    by_program: dict[str, Decimal] = {}
    total = Decimal("0.00")
    for row in rows:
        duty = _money(row["duty"])
        by_entry.setdefault(row["entry_no"], {})[row["program"]] = f"{duty:.2f}"
        by_program[row["program"]] = by_program.get(row["program"], Decimal("0.00")) + duty
        total += duty

    # Cross-check each entry against the total_duty the entry itself states.
    mismatches = []
    for entry, programs in by_entry.items():
        stated = conn.execute(
            "SELECT total_duty FROM golden_cbp_entries WHERE entry_no = ?", (entry,)
        ).fetchone()
        if stated is None:
            continue
        summed = sum((Decimal(v) for v in programs.values()), Decimal("0.00"))
        if summed != _money(stated["total_duty"]):
            mismatches.append(
                {"entry_no": entry, "lines_sum": f"{summed:.2f}",
                 "entry_total_duty": f"{_money(stated['total_duty']):.2f}"}
            )

    return {
        "scope": {"entry_no": entry_no, "refund_id": refund_id},
        "entry_count": len(by_entry),
        "by_entry": by_entry,
        "by_program": {k: f"{v:.2f}" for k, v in sorted(by_program.items())},
        "total_duty": f"{total:.2f}",
        "line_total_mismatches": mismatches,
        "raw_refs": sorted({r["raw_ref"] for r in rows}),
    }


def reconcile_duty_paid(ctx, entry_no: str | None = None) -> dict[str, Any]:
    """Reconcile the duty the books paid against the CBP 7501, entry by entry.

    Brewline pays CBP directly, so the two must agree. A difference is a finding
    to explain, never something to plug.
    """
    conn = ctx.conn
    cbp_sql = ("SELECT entry_no, total_duty, raw_ref FROM golden_cbp_entries")
    paid_sql = ("SELECT reference AS entry_no, amount, date, raw_ref FROM golden_bank_txns"
                " WHERE direction = 'debit' AND description LIKE 'DUTY PMT%'")
    params: list[Any] = []
    if entry_no:
        cbp_sql += " WHERE entry_no = ?"
        paid_sql += " AND reference = ?"
        params = [entry_no]

    cbp_rows = conn.execute(cbp_sql, params).fetchall()
    paid_rows = conn.execute(paid_sql, params).fetchall()
    if not cbp_rows:
        raise ComputeError(f"no CBP entry {entry_no or '(any)'} in golden_cbp_entries")

    cbp = {r["entry_no"]: _money(r["total_duty"]) for r in cbp_rows}
    refs = {r["entry_no"]: r["raw_ref"] for r in cbp_rows}
    paid: dict[str, Decimal] = {}
    paid_refs: dict[str, str] = {}
    for row in paid_rows:
        key = (row["entry_no"] or "").strip()
        if not key:
            continue
        paid[key] = paid.get(key, Decimal("0.00")) + _money(row["amount"])
        paid_refs[key] = row["raw_ref"]

    by_entry, differences = [], []
    for key in sorted(set(cbp) | set(paid)):
        due = cbp.get(key, Decimal("0.00"))
        was_paid = paid.get(key, Decimal("0.00"))
        row = {"entry_no": key, "duty_per_7501": f"{due:.2f}",
               "duty_paid_per_books": f"{was_paid:.2f}",
               "difference": f"{was_paid - due:.2f}",
               "agrees": was_paid == due,
               "cbp_raw_ref": refs.get(key), "bank_raw_ref": paid_refs.get(key)}
        by_entry.append(row)
        if not row["agrees"]:
            differences.append(row)

    total_due = sum(cbp.values(), Decimal("0.00"))
    total_paid = sum(paid.values(), Decimal("0.00"))
    return {
        "scope": {"entry_no": entry_no},
        "entries_reconciled": len(by_entry),
        "by_entry": by_entry,
        "differences": differences,
        "difference_count": len(differences),
        "total_duty_per_7501": f"{total_due:.2f}",
        "total_duty_paid": f"{total_paid:.2f}",
        "total_difference": f"{total_paid - total_due:.2f}",
        "source_of_truth": "the CBP 7501 duty lines (POL-001 1.2)",
        "raw_refs": sorted(set(refs.values()) | set(paid_refs.values())),
    }


def split_refund(ctx, refund_id: str, policy_ref: str | None = None) -> dict[str, Any]:
    """Split a refund between parts sold and parts on hand, by running the approved rule."""
    from .. import policies

    if policy_ref is None:
        candidates = [
            p for p in policies.listing(ctx.conn, policies.APPROVED)
            if (p["rule"] or {}).get("kind") == "refund_split"
        ]
        if not candidates:
            raise ComputeError(
                "no approved refund_split policy. Propose one with propose_policy and "
                "wait for the controller to approve it."
            )
        policy_ref = sorted(candidates, key=lambda p: int(p["version"]))[-1]["ref"]

    result = policies.apply(ctx.conn, policy_ref, {"refund_id": refund_id}, ctx.cfg)
    result["note"] = (
        "to_cogs credits Expenses:COGS:Tariff; to_inventory credits Assets:Inventory:Parts "
        "(POL-001 3.2). Copy these figures verbatim."
    )
    return result


BREAKDOWN_RECEIVABLE_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def breakdown_receivable(ctx) -> dict[str, Any]:
    """Break the refund receivable down by cause, in code.

    Returns what the receivable holds, how much refundable duty is genuinely
    still outstanding, and how much is duty under a programme that is never
    refunded - which is the only way the receivable can be overstated, because
    Brewline pays CBP directly.
    """
    from .. import invariants

    result = invariants.receivable_breakdown(ctx.conn, ctx.cfg)
    result["note"] = (
        "non_refundable_duty is what WI-FORECAST-CORRECTION has to take out of the "
        "receivable. Use that figure as it stands."
    )
    return result


SURCHARGE_ON_INVOICES_SCHEMA = {
    "type": "object",
    "properties": {
        "customer_id": {"type": "string", "description": "e.g. CUST-001."},
        "refund_id": {"type": "string", "description": "e.g. RFD-001."},
    },
    "required": ["customer_id", "refund_id"],
    "additionalProperties": False,
}


class PartialRefund(Exception):
    """A surcharge line covers both refunded and unrefunded shipments.

    Splitting it would be an invention, so the work item goes to the controller
    with the per-shipment breakdown instead.
    """

    def __init__(self, reason: str, detail: dict[str, Any]):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def surcharge_on_invoices(ctx, customer_id: str, refund_id: str) -> dict[str, Any]:
    """Tariff surcharge billed to a customer on machines whose parts were refunded.

    A surcharge line names the shipments it covers. If every one of them is in
    the refund, the whole line is affected. If only some are, the line cannot be
    split without inventing a basis - that case is raised for a human.
    """
    conn = ctx.conn

    customer = conn.execute(
        "SELECT customer_id, name, contract_id, passthrough_clause, raw_ref"
        " FROM golden_billing_customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if customer is None:
        raise ComputeError(f"no customer {customer_id} in golden_billing_customers")

    refunded_shipments = {
        row["shipment_id"] for row in conn.execute(
            "SELECT l.shipment_id FROM golden_erp_inventory_layers l"
            " WHERE l.entry_no IN (SELECT entry_no FROM golden_cbp_refund_entries"
            "                      WHERE refund_id = ?)", (refund_id,)
        ).fetchall()
    }
    if not refunded_shipments:
        raise ComputeError(f"refund {refund_id} covers no shipments the ERP knows about")

    lines = conn.execute(
        "SELECT l.invoice_id, l.line_no, l.amount, l.description, l.shipment_ids,"
        " l.tariff_program, l.surcharge_basis, l.raw_ref, i.invoice_date, i.contract_id"
        " FROM golden_billing_invoice_lines l"
        " JOIN golden_billing_invoices i ON i.invoice_id = l.invoice_id"
        " WHERE i.customer_id = ? AND l.kind = 'tariff_surcharge'"
        " ORDER BY l.invoice_id, l.line_no", (customer_id,)
    ).fetchall()

    by_invoice, total, partial, raw_refs = [], Decimal("0.00"), [], [customer["raw_ref"]]
    for line in lines:
        cited = [s.strip() for s in (line["shipment_ids"] or "").split(",") if s.strip()]
        hit = [s for s in cited if s in refunded_shipments]
        missed = [s for s in cited if s not in refunded_shipments]
        amount = _money(line["amount"])
        entry = {
            "invoice_id": line["invoice_id"],
            "invoice_date": line["invoice_date"],
            "contract_id": line["contract_id"],
            "surcharge": f"{amount:.2f}",
            "shipments_cited": cited,
            "shipments_refunded": hit,
            "shipments_not_refunded": missed,
            "description": line["description"],
            "surcharge_basis": line["surcharge_basis"],
        }
        raw_refs.append(line["raw_ref"])

        if not hit:
            entry["affected"] = "no"
            entry["affected_amount"] = "0.00"
        elif missed:
            entry["affected"] = "partly"
            entry["affected_amount"] = None
            partial.append(entry)
        else:
            entry["affected"] = "yes"
            entry["affected_amount"] = f"{amount:.2f}"
            total += amount
        by_invoice.append(entry)

    if partial:
        raise PartialRefund(
            "a surcharge line covers both refunded and unrefunded shipments, and the "
            "contract does not say how to split one: "
            + "; ".join(
                f"{e['invoice_id']} {e['surcharge']} covers {', '.join(e['shipments_cited'])} "
                f"but only {', '.join(e['shipments_refunded'])} were refunded"
                for e in partial
            ),
            {"customer_id": customer_id, "refund_id": refund_id,
             "partial_lines": partial, "by_invoice": by_invoice,
             "refunded_shipments": sorted(refunded_shipments)},
        )

    affected = [e for e in by_invoice if e["affected"] == "yes"]
    return {
        "customer_id": customer_id,
        "customer_name": customer["name"],
        "contract_id": customer["contract_id"],
        "passthrough_clause": customer["passthrough_clause"],
        "refund_id": refund_id,
        "refunded_shipments": sorted(refunded_shipments),
        "by_invoice": by_invoice,
        "credit_notes": [
            {"invoice": e["invoice_id"], "amount": e["affected_amount"]} for e in affected
        ],
        "invoice_count": len(affected),
        "total_surcharge": f"{total:.2f}",
        "note": (
            "This is the surcharge billed on machines whose parts came from refunded "
            "shipments. Whether the customer is owed it is a contract question - read the "
            "contract. An ambiguous clause is not yours to decide; ask the lawyer."
        ),
        "raw_refs": raw_refs,
    }
