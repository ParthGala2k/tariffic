"""Learned policies: stored as data, executed by code.

A policy is a versioned rule in the `policies` table. The seat agents word the
rule (which programmes are refundable, which ratio splits a refund); the
executors below run it deterministically. That split matters: the agent can get
a rule wrong and an invariant or the scorer will catch it, but the same rule
always produces the same numbers.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from typing import Any, Callable

from .db import log_event_now, now

PROPOSED = "PROPOSED"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
FLAGGED_BAD = "FLAGGED_BAD"


class PolicyError(Exception):
    """A policy could not be stored, found, or executed."""


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def parse_ref(policy_ref: str) -> tuple[str, str]:
    """`POL-L2@1` -> ('POL-L2', '1')."""
    if "@" not in policy_ref:
        raise PolicyError(
            f"policy reference {policy_ref!r} must be <id>@<version>, e.g. POL-L1@1"
        )
    policy_id, version = policy_ref.split("@", 1)
    if not policy_id or not version:
        raise PolicyError(f"policy reference {policy_ref!r} must be <id>@<version>")
    return policy_id, version


def get(conn: sqlite3.Connection, policy_ref: str) -> dict[str, Any]:
    policy_id, version = parse_ref(policy_ref)
    row = conn.execute(
        "SELECT * FROM policies WHERE policy_id = ? AND version = ?", (policy_id, version)
    ).fetchone()
    if row is None:
        known = [f"{r[0]}@{r[1]}" for r in conn.execute(
            "SELECT policy_id, version FROM policies ORDER BY policy_id")]
        raise PolicyError(
            f"no policy {policy_ref}. Known policies: {', '.join(known) or 'none yet'}"
        )
    policy = dict(row)
    policy["rule"] = json.loads(policy["rule"] or "{}")
    policy["evidence"] = json.loads(policy["evidence"] or "[]")
    policy["ref"] = f"{policy['policy_id']}@{policy['version']}"
    return policy


def listing(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM policies"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    rows = conn.execute(sql + " ORDER BY policy_id, version", params).fetchall()
    out = []
    for row in rows:
        policy = dict(row)
        policy["rule"] = json.loads(policy["rule"] or "{}")
        policy["evidence"] = json.loads(policy["evidence"] or "[]")
        policy["ref"] = f"{policy['policy_id']}@{policy['version']}"
        out.append(policy)
    return out


def next_version(conn: sqlite3.Connection, policy_id: str) -> str:
    rows = conn.execute(
        "SELECT version FROM policies WHERE policy_id = ?", (policy_id,)
    ).fetchall()
    return str(max((int(r[0]) for r in rows), default=0) + 1)


def store(
    conn: sqlite3.Connection,
    *,
    policy_id: str,
    name: str,
    rule: dict[str, Any],
    status: str,
    evidence: list[str],
    evidence_count: int,
    reason: str,
    actor: str,
    scope: str | None = None,
    work_item_id: str | None = None,
) -> dict[str, Any]:
    """Write a proposed or flagged policy, with its audit row, in one transaction."""
    version = next_version(conn, policy_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO policies (policy_id, version, name, status, rule, scope, evidence,"
            " evidence_count, reason, work_item_id, proposed_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (policy_id, version, name, status, json.dumps(rule), scope,
             json.dumps(evidence), evidence_count, reason, work_item_id, actor, now()),
        )
        log_event_now_inline(conn, actor=actor, policy_id=policy_id, version=version,
                             status=status, reason=reason, evidence=evidence,
                             work_item_id=work_item_id)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get(conn, f"{policy_id}@{version}")


def log_event_now_inline(conn, *, actor, policy_id, version, status, reason, evidence,
                         work_item_id) -> None:
    from .db import log_event

    log_event(
        conn, actor=actor, action="propose_policy", subject=work_item_id, allowed=True,
        reason=f"{policy_id}@{version} {status}: {reason}",
        diff={"before": None, "after": {"policy": f"{policy_id}@{version}", "status": status}},
        evidence=evidence, policy_version=f"{policy_id}@{version}",
    )


def approve(conn: sqlite3.Connection, policy_ref: str, actor: str, reason: str = "") -> dict[str, Any]:
    """The controller approves a proposed policy. A policy never self-approves."""
    from .perms import can

    policy = get(conn, policy_ref)
    decision = can(actor, "approve_policy", {"id": policy_ref, "proposed_by": policy["proposed_by"]})
    conn.execute("BEGIN IMMEDIATE")
    try:
        from .db import log_event

        log_event(conn, actor=actor, action="can:approve_policy", subject=policy_ref,
                  allowed=decision.allowed, reason=decision.reason)
        if not decision.allowed:
            conn.execute("ROLLBACK")
            from .perms import Denied
            log_event_now(conn, actor=actor, action="approve_policy", subject=policy_ref,
                          allowed=False, reason=decision.reason)
            raise Denied(decision.reason, actor=actor, action="approve_policy", subject=policy_ref)
        if policy["status"] != PROPOSED:
            conn.execute("ROLLBACK")
            from .perms import Denied
            why = f"refused: {policy_ref} is {policy['status']}, only PROPOSED policies are approved"
            log_event_now(conn, actor=actor, action="approve_policy", subject=policy_ref,
                          allowed=False, reason=why)
            raise Denied(why, actor=actor, action="approve_policy", subject=policy_ref)
        conn.execute(
            "UPDATE policies SET status = ?, approved_by = ? WHERE policy_id = ? AND version = ?",
            (APPROVED, actor, policy["policy_id"], policy["version"]),
        )
        log_event(conn, actor=actor, action="approve_policy", subject=policy_ref, allowed=True,
                  reason=reason or f"{policy_ref} approved",
                  diff={"before": {"status": PROPOSED}, "after": {"status": APPROVED}},
                  policy_version=policy_ref)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    return get(conn, policy_ref)


# --------------------------------------------------------------------------- #
# Executors: one per rule kind. These are the deterministic part.
# --------------------------------------------------------------------------- #

def _exec_document(conn, rule, inputs, cfg) -> dict[str, Any]:
    raise PolicyError(
        "POL-001 is a written policy document, not an executable rule. "
        "Read it with read_doc and cite it as evidence."
    )


def _exec_program_refundability(conn, rule, inputs, cfg) -> dict[str, Any]:
    """Split duty on the given entries into refundable and not, by programme."""
    entry_nos = inputs.get("entry_nos")
    if not entry_nos:
        raise PolicyError("program_refundability needs inputs.entry_nos (a list of entry numbers)")
    refundable = set(rule.get("refundable") or [])
    not_refundable = set(rule.get("not_refundable") or [])
    if not refundable:
        raise PolicyError("the rule lists no refundable programmes")

    placeholders = ",".join("?" * len(entry_nos))
    rows = conn.execute(
        f"SELECT entry_no, program, duty, raw_ref FROM golden_cbp_duty_lines"
        f" WHERE entry_no IN ({placeholders}) ORDER BY entry_no, program",
        list(entry_nos),
    ).fetchall()
    if not rows:
        raise PolicyError(f"no duty lines found for {', '.join(entry_nos)}")

    by_program: dict[str, Decimal] = {}
    unknown: set[str] = set()
    for row in rows:
        by_program[row["program"]] = by_program.get(row["program"], Decimal("0.00")) + _money(row["duty"])
        if row["program"] not in refundable and row["program"] not in not_refundable:
            unknown.add(row["program"])
    if unknown:
        raise PolicyError(
            f"the rule does not classify {', '.join(sorted(unknown))}; "
            "every programme on these entries must be listed as refundable or not"
        )

    refundable_total = sum((v for k, v in by_program.items() if k in refundable), Decimal("0.00"))
    not_refundable_total = sum((v for k, v in by_program.items() if k in not_refundable), Decimal("0.00"))
    return {
        "entry_nos": list(entry_nos),
        "by_program": {k: f"{v:.2f}" for k, v in sorted(by_program.items())},
        "refundable_programs": sorted(refundable & set(by_program)),
        "not_refundable_programs": sorted(not_refundable & set(by_program)),
        "refundable_total": f"{refundable_total:.2f}",
        "not_refundable_total": f"{not_refundable_total:.2f}",
        "total_duty": f"{refundable_total + not_refundable_total:.2f}",
        "raw_refs": sorted({r["raw_ref"] for r in rows}),
    }


def _exec_refund_split(conn, rule, inputs, cfg) -> dict[str, Any]:
    """Split each entry's refunded principal between parts sold and parts on hand."""
    refund_id = inputs.get("refund_id")
    if not refund_id:
        raise PolicyError("refund_split needs inputs.refund_id")

    numerator = rule.get("numerator", "qty_consumed")
    denominator = rule.get("denominator", "qty_received")
    allowed_cols = {"qty_consumed", "qty_received", "qty_on_hand"}
    if numerator not in allowed_cols or denominator not in allowed_cols:
        raise PolicyError(
            f"numerator/denominator must be one of {sorted(allowed_cols)}; "
            f"got {numerator!r}/{denominator!r}"
        )

    lines = conn.execute(
        "SELECT e.entry_no, e.principal, e.raw_ref AS refund_ref,"
        " l.shipment_id, l.qty_received, l.qty_on_hand, l.qty_consumed, l.raw_ref AS layer_ref"
        " FROM golden_cbp_refund_entries e"
        " JOIN golden_erp_inventory_layers l ON l.entry_no = e.entry_no"
        " WHERE e.refund_id = ? ORDER BY l.shipment_id",
        (refund_id,),
    ).fetchall()
    if not lines:
        raise PolicyError(
            f"no ERP inventory layers match the entries on refund {refund_id}; "
            "has sync run?"
        )

    by_shipment, to_cogs_total, to_inv_total, principal_total = {}, Decimal("0.00"), Decimal("0.00"), Decimal("0.00")
    for line in lines:
        principal = _money(line["principal"])
        num = Decimal(str(line[numerator]))
        den = Decimal(str(line[denominator]))
        if den == 0:
            raise PolicyError(f"{line['shipment_id']}: {denominator} is zero, cannot split")
        to_cogs = (principal * num / den).quantize(Decimal("0.01"))
        to_inventory = principal - to_cogs
        by_shipment[line["shipment_id"]] = {
            "entry_no": line["entry_no"],
            "principal": f"{principal:.2f}",
            "consumed": int(line["qty_consumed"]),
            "received": int(line["qty_received"]),
            "on_hand": int(line["qty_on_hand"]),
            "to_cogs": f"{to_cogs:.2f}",
            "to_inventory": f"{to_inventory:.2f}",
        }
        to_cogs_total += to_cogs
        to_inv_total += to_inventory
        principal_total += principal

    if to_cogs_total + to_inv_total != principal_total:
        raise PolicyError(
            f"split does not add up: {to_cogs_total} + {to_inv_total} != {principal_total}"
        )

    return {
        "refund_id": refund_id,
        "formula": f"to_cogs = principal x {numerator} / {denominator}; to_inventory = principal - to_cogs",
        "by_shipment": by_shipment,
        "to_cogs_total": f"{to_cogs_total:.2f}",
        "to_inventory_total": f"{to_inv_total:.2f}",
        "principal_total": f"{principal_total:.2f}",
        "raw_refs": sorted({r["refund_ref"] for r in lines} | {r["layer_ref"] for r in lines}),
    }


def _exec_distributor_passthrough(conn, rule, inputs, cfg) -> dict[str, Any]:
    """RULE-001. Proposed by the lawyer in Phase 3; approved and applied in Phase 4."""
    raise PolicyError(
        "RULE-001 generalises the lawyer's answer to other distributor contracts. "
        "Approving and applying it is Phase 4; it is PROPOSED only."
    )


def _exec_explicit_clause_credit(conn, rule, inputs, cfg) -> dict[str, Any]:
    """POL-L3. For a customer whose contract explicitly promises the surcharge back, the
    credit is the full surcharge billed on machines built from refunded shipments.
    Inputs: customer_id, refund_id. An ambiguous clause is out of scope and refused, so
    the seat has to ask the lawyer instead of applying this rule to it."""
    from types import SimpleNamespace

    from .tools import compute

    customer_id = (inputs or {}).get("customer_id")
    refund_id = (inputs or {}).get("refund_id")
    if not customer_id or not refund_id:
        raise PolicyError("explicit_clause_credit needs inputs customer_id and refund_id")
    customer = conn.execute(
        "SELECT customer_id, name, contract_id, passthrough_clause, raw_ref"
        " FROM golden_billing_customers WHERE customer_id = ?", (customer_id,)).fetchone()
    if customer is None:
        raise PolicyError(f"no customer {customer_id}")
    if (customer["passthrough_clause"] or "").lower() != "explicit":
        raise PolicyError(
            f"{customer['contract_id']} ({customer['name']}) has an {customer['passthrough_clause']}"
            " pass-through clause; this rule covers explicit clauses only. Ask the lawyer.")
    try:
        billed = compute.surcharge_on_invoices(SimpleNamespace(conn=conn, cfg=cfg), customer_id, refund_id)
    except (compute.ComputeError, compute.PartialRefund) as error:
        raise PolicyError(str(error)) from error
    credits = [{"invoice_id": e["invoice_id"], "invoice_date": e["invoice_date"],
                "credit": e["affected_amount"], "shipments": e["shipments_refunded"]}
               for e in billed["by_invoice"] if e["affected"] == "yes"]
    total = sum((Decimal(c["credit"]) for c in credits), Decimal("0.00"))
    return {
        "customer_id": customer_id, "customer": customer["name"], "contract_id": customer["contract_id"],
        "refund_id": refund_id, "credit_basis": rule.get("credit_basis"),
        "credit_total": f"{total:.2f}", "by_invoice": credits,
        "debit_account": rule.get("debit_account", "Income:Sales:TariffSurcharge"),
        "credit_account": rule.get("credit_account", "Liabilities:CustomerCredits"),
        "interest": rule.get("interest", "retained by Brewline"),
        "raw_refs": billed.get("raw_refs", []),
    }


EXECUTORS: dict[str, Callable[..., dict[str, Any]]] = {
    "document": _exec_document,
    "program_refundability": _exec_program_refundability,
    "refund_split": _exec_refund_split,
    "explicit_clause_credit": _exec_explicit_clause_credit,
    "distributor_passthrough": _exec_distributor_passthrough,
}


def apply(conn: sqlite3.Connection, policy_ref: str, inputs: dict[str, Any], cfg) -> dict[str, Any]:
    """Execute an APPROVED policy. Proposed or flagged policies are refused."""
    policy = get(conn, policy_ref)
    if policy["status"] != APPROVED:
        raise PolicyError(
            f"refused: {policy_ref} is {policy['status']}, not APPROVED. "
            "A policy must be approved by the controller before it can be applied."
        )
    kind = (policy["rule"] or {}).get("kind")
    executor = EXECUTORS.get(kind)
    if executor is None:
        raise PolicyError(
            f"{policy_ref} has rule kind {kind!r}, which has no executor. "
            f"Known kinds: {', '.join(sorted(EXECUTORS))}"
        )
    result = executor(conn, policy["rule"], inputs or {}, cfg)
    result["policy"] = policy_ref
    result["policy_name"] = policy["name"]
    return result
