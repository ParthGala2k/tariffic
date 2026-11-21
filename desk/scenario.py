"""Scenario runner: a sandboxed copy of the world, patched, tied out, and worked.

A scenario is a YAML file (see scenarios/). It names the world it wants by
patching a fresh copy of data/, systems/ and personas/ under .sandbox/<name>/,
says what the books must show before the desk touches anything, optionally
runs the desk against that world, and says what the books must show after.

The repo's own data is never written. Every path the desk uses - documents,
books, ERP, desk DB - points into the sandbox; only the LLM cache is shared.

Patch operations (each under `patches:` with a `file:`):

  replace:    [{old, new, count}]           exact text replacement, count must match
  json_set:   [{where: {k: v}, set: {dotted.path: value}}]
  json_delete:[{where: {k: v}}]             drop matching rows from a JSON list
  json_write: <value>                       overwrite the whole file
  csv_set:    [{where: {k: v}, set: {col: value}}]
  csv_delete: [{where: {k: v}}]
  recompute_running_balance: {balance, amount, direction, credit_value}
  remove_file: true

The tie-outs are the point. They compare the same fact as each system of record
sees it: ledger vs bank feed, ledger vs ERP, ledger vs CBP, ledger vs
billing, and the five invariants. A check is a pair of numbers from two places
that must be equal; the report says which two places.
"""
from __future__ import annotations

import csv
import dataclasses
import json
import shutil
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from . import agent, db, gateway, invariants, llm as llm_mod, onboarding, personas, score, sync
from .config import CFG, Config
from .perms import Denied
from .workflow import (NEEDS_INFO, POSTED, PROPOSED, close_parent_if_settled, create_work_item,
                       get_item, transition)

ZERO = Decimal("0.00")
BANK = "Assets:Bank:Increase:Operating"
INVENTORY = "Assets:Inventory:Parts"
RECEIVABLE = "Assets:Receivable:TariffRefund"
TRADE = "Assets:Receivable:Trade"
COGS_TARIFF = "Expenses:COGS:Tariff"
COGS_PARTS = "Expenses:COGS:Parts"
SALES = ("Income:Sales:Machines", "Income:Sales:TariffSurcharge")


class ScenarioError(Exception):
    pass


def money(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ScenarioError(f"not a money amount: {value!r}") from error


def fmt(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:,.2f}"


@dataclass
class Check:
    group: str
    name: str
    left_label: str
    left: str
    right_label: str
    right: str
    ok: bool
    note: str = ""
    expected: bool = False

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def pair(group, name, left_label, left, right_label, right, note="") -> Check:
    """A check that two amounts, from two places, are equal."""
    return Check(group, name, left_label, fmt(left), right_label, fmt(right),
                 left is not None and right is not None and left == right, note)


# --------------------------------------------------------------------------- #
# loading and materialising
# --------------------------------------------------------------------------- #

def load(path: str | Path) -> dict[str, Any]:
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not spec.get("name"):
        raise ScenarioError(f"{path} has no name")
    spec.setdefault("patches", [])
    spec.setdefault("expect", {})
    return spec


def materialize(spec: dict[str, Any], base: Config = CFG,
                sandbox_dir: str | Path | None = None) -> Config:
    """Copy the world into a fresh sandbox, patch it, and return a Config for it."""
    root = Path(sandbox_dir).resolve() if sandbox_dir else base.root / ".sandbox"
    target = root / spec["name"]
    if target.exists():
        shutil.rmtree(target)
    ignore = shutil.ignore_patterns("*.db", "*.db-wal", "*.db-shm", "__pycache__", ".sandbox")
    for folder in ("data", "systems", "personas"):
        shutil.copytree(base.root / folder, target / folder, ignore=ignore)

    from .__main__ import POSTED_HEADER

    posted = target / "systems" / "books" / "posted" / "desk.beancount"
    posted.parent.mkdir(parents=True, exist_ok=True)
    posted.write_text(POSTED_HEADER, encoding="utf-8")

    for patch in spec["patches"]:
        apply_patch(target, patch)

    return dataclasses.replace(
        base, root=target,
        beancount_file=target / "systems" / "books" / "main.beancount",
        erp_db=target / "systems" / "erp" / "erp.db",
        desk_db=target / "systems" / "desk" / "desk.db",
    )


# --------------------------------------------------------------------------- #
# patch operations
# --------------------------------------------------------------------------- #

def _matches(row: Any, where: dict[str, Any] | None) -> bool:
    if not where:
        return True
    if not isinstance(row, dict):
        return False
    return all(str(row.get(k)) == str(v) for k, v in where.items())


def _set_path(obj: Any, path: str | list, value: Any) -> None:
    parts = path if isinstance(path, list) else str(path).split(".")
    for part in parts[:-1]:
        obj = obj[int(part)] if isinstance(obj, list) else obj.setdefault(part, {})
    last = parts[-1]
    if isinstance(obj, list):
        obj[int(last)] = value
    else:
        obj[last] = value


def apply_patch(root: Path, patch: dict[str, Any]) -> None:
    path = root / patch["file"]
    if patch.get("remove_file"):
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        raise ScenarioError(f"patch target {patch['file']} does not exist in the sandbox")

    if "replace" in patch:
        text = path.read_text(encoding="utf-8")
        for op in patch["replace"]:
            count = int(op.get("count", 1))
            found = text.count(op["old"])
            if found != count:
                raise ScenarioError(
                    f"{patch['file']}: expected {count} of {op['old'][:60]!r}, found {found}")
            text = text.replace(op["old"], op["new"])
        path.write_text(text, encoding="utf-8")

    if "json_write" in patch:
        path.write_text(json.dumps(patch["json_write"], indent=2) + "\n", encoding="utf-8")

    if "json_set" in patch or "json_delete" in patch:
        data = json.loads(path.read_text(encoding="utf-8"))
        for op in patch.get("json_set") or []:
            targets = ([r for r in data if _matches(r, op.get("where"))]
                       if isinstance(data, list) else [data])
            if not targets:
                raise ScenarioError(f"{patch['file']}: json_set matched nothing for {op.get('where')}")
            for target in targets:
                for dotted, value in (op.get("set") or {}).items():
                    _set_path(target, dotted, value)
        for op in patch.get("json_delete") or []:
            if not isinstance(data, list):
                raise ScenarioError(f"{patch['file']}: json_delete needs a JSON list")
            before = len(data)
            data[:] = [r for r in data if not _matches(r, op.get("where"))]
            if len(data) == before:
                raise ScenarioError(f"{patch['file']}: json_delete matched nothing for {op.get('where')}")
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    if any(k in patch for k in ("csv_set", "csv_delete", "recompute_running_balance")):
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
        for op in patch.get("csv_set") or []:
            hit = [r for r in rows if _matches(r, op.get("where"))]
            if not hit:
                raise ScenarioError(f"{patch['file']}: csv_set matched nothing for {op.get('where')}")
            for row in hit:
                for column, value in (op.get("set") or {}).items():
                    row[column] = str(value)
        for op in patch.get("csv_delete") or []:
            before = len(rows)
            rows = [r for r in rows if not _matches(r, op.get("where"))]
            if len(rows) == before:
                raise ScenarioError(f"{patch['file']}: csv_delete matched nothing for {op.get('where')}")
        spec = patch.get("recompute_running_balance")
        if spec:
            running = ZERO
            for row in rows:
                amount = money(row[spec["amount"]])
                running += amount if row[spec["direction"]] == spec.get("credit_value", "credit") else -amount
                row[spec["balance"]] = f"{running:.2f}"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


# --------------------------------------------------------------------------- #
# facts from each system of record
# --------------------------------------------------------------------------- #

def _ledger_entries(cfg: Config):
    from beancount import loader
    from beancount.core import data as bc

    entries, errors, _ = loader.load_file(str(cfg.beancount_file))
    if errors:
        raise ScenarioError(f"beancount load errors: {errors[:3]}")
    return [e for e in entries if isinstance(e, bc.Transaction)]


def _ledger_facts(cfg: Config) -> dict[str, Any]:
    txns = _ledger_entries(cfg)
    duties = purchases = capitalised = ZERO
    bank_postings: list[tuple[str, Decimal]] = []
    for txn in txns:
        meta = txn.meta or {}
        for posting in txn.postings:
            amount = money(posting.units.number)
            pmeta = posting.meta or {}
            if posting.account == COGS_TARIFF and pmeta.get("tariff_program") and amount > 0:
                duties += amount
            if posting.account == INVENTORY and meta.get("po") and amount > 0:
                purchases += amount
            if posting.account == INVENTORY and meta.get("policy") == "POL-001" and amount > 0:
                capitalised += amount
            if posting.account == BANK:
                bank_postings.append((txn.date.isoformat(), amount))
    return {"balances": invariants.ledger_balances(cfg), "duties_paid": duties,
            "purchases": purchases, "capitalised_duty": capitalised,
            "bank_postings": bank_postings}


def _sum(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Decimal:
    row = conn.execute(sql, params).fetchone()
    return money(row[0] or 0) if row else ZERO


def tie_outs(conn: sqlite3.Connection, cfg: Config, spec: dict[str, Any],
             stage: str) -> list[Check]:
    """Compare every fact across the places it must agree. `stage` is 'before' or 'after'."""
    checks: list[Check] = []
    expect = spec.get("expect") or {}
    if stage == "after":
        expect = (spec.get("run") or {}).get("expect") or {}
    ledger = _ledger_facts(cfg)
    bal = ledger["balances"]

    # ---- books <-> bank feed --------------------------------------------------
    feed_credits = _sum(conn, "SELECT SUM(amount) FROM golden_bank_txns WHERE direction='credit'")
    feed_debits = _sum(conn, "SELECT SUM(amount) FROM golden_bank_txns WHERE direction='debit'")
    feed_final = feed_credits - feed_debits
    ledger_bank_keys = {(d, a) for d, a in ledger["bank_postings"]}
    unrecorded = [dict(r) for r in conn.execute(
        "SELECT txn_id, date, amount FROM golden_bank_txns WHERE direction='credit' ORDER BY txn_id")
        if (r["date"], money(r["amount"])) not in ledger_bank_keys]
    unrecorded_total = sum((money(r["amount"]) for r in unrecorded), ZERO)
    checks.append(pair(
        "books <-> bank", "operating account",
        "ledger " + BANK, bal.get(BANK, ZERO),
        "bank feed closing balance less deposits not yet booked", feed_final - unrecorded_total,
        f"not yet booked: {', '.join(r['txn_id'] for r in unrecorded) or 'none'}"))
    if "unrecorded_deposits" in expect:
        want = list(expect["unrecorded_deposits"] or [])
        got = [r["txn_id"] for r in unrecorded]
        checks.append(Check("books <-> bank", "deposits awaiting the desk", "expected", ", ".join(want) or "none",
                            "bank feed", ", ".join(got) or "none", got == want))
    duty_paid = _sum(conn, "SELECT SUM(amount) FROM golden_bank_txns WHERE direction='debit' AND description LIKE 'DUTY PMT%'")
    duty_per_cbp = _sum(conn, "SELECT SUM(total_duty) FROM golden_cbp_entries")
    checks.append(pair("books <-> customs", "duty paid", "bank debits to Customs", duty_paid,
                       "duty per the CBP 7501", duty_per_cbp))
    vendors_paid = _sum(conn, "SELECT SUM(amount) FROM golden_bank_txns WHERE direction='debit' AND type='wire'")
    erp_purchases = _sum(conn, "SELECT SUM(qty_received*unit_cost) FROM golden_erp_inventory_layers")
    checks.append(pair("books <-> bank", "vendors paid", "bank wires out", vendors_paid,
                       "ERP shipments received at cost", erp_purchases))
    customer_cash = _sum(conn, "SELECT SUM(amount) FROM golden_bank_txns WHERE direction='credit'"
                               " AND type='ach' AND counterparty NOT LIKE 'US TREASURY%'")
    billed_paid = _sum(conn, "SELECT SUM(total) FROM golden_billing_invoices WHERE paid_date IS NOT NULL")
    checks.append(pair("books <-> bank", "customers paid", "bank credits from customers", customer_cash,
                       "billing invoices marked paid", billed_paid))

    # ---- books <-> customs ---------------------------------------------------
    cbp_duty = _sum(conn, "SELECT SUM(duty) FROM golden_cbp_duty_lines")
    checks.append(pair("books <-> customs", "duties paid: ledger vs CBP", "ledger duty postings",
                       ledger["duties_paid"], "CBP 7501 duty lines", cbp_duty))
    refundable = invariants._reference_refundable(cfg)
    placeholders = ",".join("?" * len(refundable)) or "''"
    refundable_liquidated = _sum(
        conn, f"SELECT SUM(l.duty) FROM golden_cbp_duty_lines l JOIN golden_cbp_entries e"
              f" ON e.entry_no = l.entry_no WHERE e.liquidation_status='liquidated'"
              f" AND l.program IN ({placeholders})", tuple(sorted(refundable)))
    notice_principal = _sum(conn, "SELECT SUM(total_principal) FROM golden_cbp_refunds")
    notice_interest = _sum(conn, "SELECT SUM(total_interest) FROM golden_cbp_refunds")
    notice_total = _sum(conn, "SELECT SUM(total) FROM golden_cbp_refunds")
    recorded_principal = ZERO
    for refund in conn.execute("SELECT refund_id, total_principal, total, payment_date FROM golden_cbp_refunds"):
        if (refund["payment_date"], money(refund["total"])) in ledger_bank_keys:
            recorded_principal += money(refund["total_principal"])
    checks.append(pair("books <-> customs", "refund receivable", "ledger " + RECEIVABLE,
                       bal.get(RECEIVABLE, ZERO),
                       "refundable duty on liquidated entries less refunds already booked",
                       refundable_liquidated - recorded_principal,
                       f"refundable programmes: {', '.join(sorted(refundable))}"))
    if notice_total:
        checks.append(pair("books <-> customs", "refund notice ties", "principal + interest",
                           notice_principal + notice_interest, "notice total", notice_total))
        treasury = _sum(conn, "SELECT SUM(amount) FROM golden_bank_txns WHERE counterparty LIKE 'US TREASURY%'")
        checks.append(pair("books <-> customs", "refund deposit = notice", "bank credit from US Treasury",
                           treasury, "CBP refund notice total", notice_total))
        checks.append(pair("books <-> customs", "notice principal = refundable duty on its entries",
                           "notice principal", notice_principal,
                           "CBP refundable duty on the notice's entries",
                           _sum(conn, f"SELECT SUM(l.duty) FROM golden_cbp_duty_lines l WHERE l.program IN ({placeholders})"
                                      f" AND l.entry_no IN (SELECT entry_no FROM golden_cbp_refund_entries)",
                                tuple(sorted(refundable)))))
    else:
        treasury = _sum(conn, "SELECT SUM(amount) FROM golden_bank_txns WHERE counterparty LIKE 'US TREASURY%'")
        checks.append(pair("books <-> customs", "no refund, no Treasury deposit", "bank credits from US Treasury",
                           treasury, "refund notices", ZERO))

    # ---- books <-> ERP -------------------------------------------------------------
    landed = invariants.erp_landed_value_on_hand(conn)
    refunded_on_hand = ZERO
    split_posted = [dict(r) for r in conn.execute(
        "SELECT payload FROM work_items WHERE kind='WI-REFUND-SPLIT' AND state='POSTED'")]
    for row in split_posted:
        detail = (json.loads(row["payload"]) or {}).get("detail") or {}
        refunded_on_hand += money(detail.get("to_inventory_total") or sum(
            money(s.get("to_inventory", 0)) for s in (detail.get("by_shipment") or {}).values()))
    checks.append(pair("books <-> ERP", "inventory", "ledger " + INVENTORY, bal.get(INVENTORY, ZERO),
                       "ERP landed value on hand less refunded duty on parts on hand",
                       landed - refunded_on_hand,
                       f"landed {fmt(landed)}; refunded duty on hand {fmt(refunded_on_hand)}"))
    checks.append(pair("books <-> ERP", "purchases", "ledger PO receipts into inventory",
                       ledger["purchases"], "ERP qty received x unit cost", erp_purchases))
    consumed = _sum(conn, "SELECT SUM(c.qty*l.unit_cost) FROM golden_erp_consumption c"
                          " JOIN golden_erp_inventory_layers l ON l.layer_id=c.layer_id")
    checks.append(pair("books <-> ERP", "cost of parts sold", "ledger " + COGS_PARTS,
                       bal.get(COGS_PARTS, ZERO), "ERP consumption x unit cost", consumed))
    duty_on_hand = _sum(conn, "SELECT SUM(qty_on_hand*duty_per_unit) FROM golden_erp_inventory_layers")
    checks.append(pair("books <-> ERP", "duty capitalised on parts on hand", "ledger POL-001 capitalisation",
                       ledger["capitalised_duty"], "ERP qty on hand x duty per unit", duty_on_hand))

    # ---- books <-> billing -------------------------------------------------------------
    history_sales = _sum(conn, f"SELECT -SUM(amount) FROM golden_ledger_postings WHERE account IN ('{SALES[0]}','{SALES[1]}')")
    billed = _sum(conn, "SELECT SUM(total) FROM golden_billing_invoices")
    checks.append(pair("books <-> billing", "sales", "ledger sales + surcharge (history)", history_sales,
                       "billing invoices total", billed))
    unpaid = _sum(conn, "SELECT SUM(total) FROM golden_billing_invoices WHERE paid_date IS NULL")
    checks.append(pair("books <-> billing", "trade receivable", "ledger " + TRADE, bal.get(TRADE, ZERO),
                       "billing invoices unpaid", unpaid))

    # ---- the refund split, by construction -----------------------------------------------
    rows = conn.execute(
        "SELECT e.principal, l.qty_consumed, l.qty_received FROM golden_cbp_refund_entries e"
        " JOIN golden_erp_inventory_layers l ON l.entry_no = e.entry_no").fetchall()
    if rows:
        to_cogs = sum(((money(r["principal"]) * Decimal(r["qty_consumed"]) / Decimal(r["qty_received"]))
                       .quantize(Decimal("0.01")) for r in rows), ZERO)
        principal = sum((money(r["principal"]) for r in rows), ZERO)
        checks.append(pair("refund split", "adds up", "to COGS + to inventory",
                           to_cogs + (principal - to_cogs), "principal", principal,
                           f"to COGS {fmt(to_cogs)}, to inventory {fmt(principal - to_cogs)}"))

    # ---- integrity -------------------------------------------------------------------------
    trial = sum(bal.values(), ZERO)
    checks.append(pair("integrity", "trial balance", "sum of every account", trial, "zero", ZERO))
    check = gateway.bean_check(cfg)
    checks.append(Check("integrity", "bean-check", "bean-check", "passed" if check.ok else "FAILED",
                        "expected", "passed", check.ok, check.output[:200]))
    violations = invariants.evaluate(conn, cfg)
    want_violations = expect.get("invariant_violations")
    checks.append(Check("integrity", "invariant violations", "found", str(len(violations)),
                        "expected", str(want_violations) if want_violations is not None else "0",
                        len(violations) == (want_violations if want_violations is not None else 0),
                        "; ".join(f"{v.invariant} {v.subject} {v.magnitude:,.2f}" for v in violations)[:200]))

    # ---- the scenario's own expected balances -------------------------------------------------
    key = "ledger_before_run" if stage == "before" else "ledger_after_run"
    for account, amount in (expect.get(key) or {}).items():
        checks.append(pair("expected balances", account, "ledger", bal.get(account, ZERO),
                           "scenario", money(amount)))
    expected = expect.get("expected_differences") or {}
    found = set()
    for check in checks:
        if not check.ok and check.name in expected:
            difference = money(check.left.replace(",", "")) - money(check.right.replace(",", ""))
            check.expected = difference == money(expected[check.name])
            if check.expected:
                found.add(check.name)
    if expected:
        checks.append(Check("integrity", "expected discrepancies detected", "found",
                            ", ".join(sorted(found)), "expected", ", ".join(sorted(expected)),
                            found == set(expected)))
    return checks


# --------------------------------------------------------------------------- #
# working the world with the desk
# --------------------------------------------------------------------------- #

def _client(cfg: Config, mode: str):
    if mode == "live":
        return llm_mod.build_client(db.cache_conn(cfg), cfg)
    from .scripted import segment

    return llm_mod.SegmentedFakeLLM(segment)


def _for(client, name: str | None):
    chooser = getattr(client, "for_segment", None)
    return chooser(name) if (chooser and name) else client


def _await_human(conn, item_id: str, wait_seconds: int, log, *, settling: tuple[str, ...] = ()) -> str | None:
    """Block until a person at the screen moves the item out of PROPOSED."""
    import time

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        state = get_item(conn, item_id)["state"]
        if state != PROPOSED and state not in settling:
            return state
        time.sleep(0.5)
    return None


def _work(conn, cfg, item_id, seat, client, task, controller, log, *, segment=None,
          max_attempts=3, step: str | None = None, human: bool = False,
          wait_seconds: int = 1800) -> dict[str, Any]:
    client = _for(client, segment)
    feedback = ""
    for attempt in range(1, max_attempts + 1):
        prompt = task if not feedback else (
            f"{task}\n\nYour previous proposal was REFUSED and sent back. Reason:\n{feedback}\n\n"
            f"Work out what is actually wrong with the entry and propose a corrected one.")
        run = agent.run_seat(conn, seat, item_id, client, prompt, cfg=cfg)
        tools = ", ".join(c["name"] + ("" if c["ok"] else "[refused]") for c in run.tool_calls)
        log(f"  {item_id} {seat} {run.turns} turn(s): {tools} -> {run.state}")
        if run.state != PROPOSED:
            raise ScenarioError(f"{item_id} ended {run.state}, expected PROPOSED: {run.final_text[:200]}")
        if human:
            # The seat has proposed. A routine entry inside the CFO's standing
            # authorization posts on its own; anything else waits for the person at the
            # screen, and the tracker shows the step as waiting on the CFO.
            from . import authorization

            verdict, auto = authorization.apply(conn, item_id, cfg)
            if verdict.covered:
                log(f"  {item_id} posted on the standing authorization: {verdict.reason}")
                return auto or {"item": get_item(conn, item_id)}
            log(f"  {item_id} needs the CFO: {verdict.reason}")
            mark_step(conn, step or "work", "waiting", work_item=item_id,
                      note=f"{item_id} proposed by {seat}; waiting for the CFO: {verdict.reason}")
            log(f"  {item_id} proposed; waiting for a human")
            outcome = _await_human(conn, item_id, wait_seconds, log)
            if outcome is None:
                raise ScenarioError(f"no decision on {item_id} within {wait_seconds}s")
            if outcome == POSTED:
                log(f"  {item_id} approved and posted by the controller + gateway")
                return {"item": get_item(conn, item_id)}
            if outcome == "APPROVED":
                # The approver's own call usually posts a moment later; give it that
                # moment, and post ourselves only if nobody did.
                import time

                for _ in range(20):
                    if get_item(conn, item_id)["state"] == POSTED:
                        break
                    time.sleep(0.25)
                if get_item(conn, item_id)["state"] == POSTED:
                    log(f"  {item_id} approved and posted by the controller + gateway")
                    return {"item": get_item(conn, item_id)}
                try:
                    result = gateway.post(conn, item_id, cfg=cfg)
                except Denied:
                    if get_item(conn, item_id)["state"] == POSTED:
                        return {"item": get_item(conn, item_id)}
                    raise
                log(f"  {item_id} approved; posted by gateway")
                return result
            if outcome == NEEDS_INFO:
                row = conn.execute(
                    "SELECT reason FROM events WHERE subject = ? AND action = 'revise'"
                    " ORDER BY event_id DESC LIMIT 1", (item_id,)).fetchone()
                feedback = (row["reason"] if row else "sent back by the controller")
                log(f"  {item_id} sent back: {feedback[:120]}")
                mark_step(conn, step or "work", "started", work_item=item_id,
                          note="sent back; the seat tries again")
                continue
            raise ScenarioError(f"{item_id} was {outcome} by the controller; the run stops here")
        try:
            controller.review(conn, item_id)
        except Denied as refusal:
            log(f"  {item_id} controller REFUSED: {refusal.reason[:160]}")
            if attempt == max_attempts:
                raise ScenarioError(f"{item_id} refused {max_attempts} times: {refusal.reason}")
            transition(conn, item_id, "revise", controller.actor, reason=refusal.reason)
            feedback = refusal.reason
            continue
        result = gateway.post(conn, item_id, cfg=cfg)
        log(f"  {item_id} approved by controller; posted by gateway")
        return result
    raise ScenarioError(f"{item_id} could not be approved")


def mark_step(conn, step: str, status: str, *, work_item: str | None = None,
              note: str = "") -> None:
    """One audit row per workflow step boundary, so a tracker can draw the run off the log."""
    db.log_event_now(
        conn, actor="sync", action="run:step", subject=work_item, allowed=status != "failed",
        reason=f"{step} {status}" + (f": {note[:160]}" if note else ""),
        evidence={"step": step, "status": status, "work_item": work_item},
    )


def summarize(checks: list[Check]) -> dict[str, Any]:
    failed = [c for c in checks if not c.ok and not c.expected]
    return {"total": len(checks), "ok": sum(c.ok for c in checks), "failed": len(failed),
            "expected_findings": sum(c.expected for c in checks), "passed": not failed}


def run_desk(conn, cfg, spec, llm_mode, log, *, controller_mode: str | None = None) -> dict[str, Any]:
    """Work the world. `controller_mode` is 'scripted' (the YAML persona decides at once)
    or 'human' (every money entry waits for a person to approve, reject or send it back;
    onboarding rules stay scripted so the demo starts with the policies in place)."""
    plan = spec.get("run") or {}
    steps = list(plan.get("steps") or [])
    mode = controller_mode or plan.get("controller") or "scripted"
    if mode not in ("scripted", "human"):
        raise ScenarioError(f"controller must be scripted or human, not {mode!r}")
    client = _client(cfg, llm_mode)
    controller = personas.ScriptedController.load(cfg)
    summary: dict[str, Any] = {"llm": llm_mode, "controller": mode,
                               "provider": getattr(client, "provider", "?"),
                               "model": getattr(client, "model", "?"), "steps": [],
                               "opened_by_invariants": [], "posted": [], "questions": []}
    log(f"desk run: {llm_mode} ({summary['provider']} {summary['model']}) controller={mode} steps={steps}")

    state: dict[str, Any] = {"parent": None, "human": mode == "human"}
    for step in steps:
        mark_step(conn, step, "started")
        try:
            work_item = _run_step(conn, cfg, spec, step, client, controller, state, summary, log)
        except Exception as error:  # noqa: BLE001 - the log must show where the run stopped
            mark_step(conn, step, "failed", note=f"{type(error).__name__}: {error}")
            raise
        mark_step(conn, step, "finished", work_item=work_item)

    parent = state["parent"]
    if parent:
        close_parent_if_settled(conn, parent)
        summary["parent"] = get_item(conn, parent)["state"]
    summary["posted_entries"] = conn.execute(
        "SELECT COUNT(*) FROM work_items WHERE state = ?", (POSTED,)).fetchone()[0]
    return summary


def _run_step(conn, cfg, spec, step, client, controller, state, summary, log) -> str | None:
    """Run one workflow step; returns the work item it worked, if any."""
    from .__main__ import CREDIT_TASK, FORECAST_TASK, SPLIT_TASK, TASK

    name, _, arg = step.partition(":")
    parent = state["parent"]
    if True:
        if name == "onboarding":
            result = onboarding.run(conn, client, cfg, controller=controller)
            log(f"  onboarding: proposed {[p['ref'] for p in result.proposed]}, "
                f"flagged {[p['ref'] for p in result.flagged]}, approved {[p['ref'] for p in result.approved]}")
            if result.missing:
                log(f"  onboarding: did not produce {result.missing}; the run goes on and the"
                    " report says so")
            summary["onboarding_missing"] = list(result.missing)
            summary["steps"].append({"step": step, "approved": [p["ref"] for p in result.approved],
                                     "missing": list(result.missing)})
            return None

        if parent is None:
            txn = conn.execute("SELECT * FROM golden_bank_txns WHERE counterparty LIKE 'US TREASURY%'"
                               " ORDER BY date DESC LIMIT 1").fetchone()
            parent = create_work_item(
                conn, kind="WI-REFUND-EVENT", seat=None, actor="sync",
                payload={"trigger": "bank_deposit", "bank_txn_id": txn["txn_id"] if txn else None,
                         "amount": f"{txn['amount']:.2f}" if txn else None,
                         "date": txn["date"] if txn else None,
                         "counterparty": txn["counterparty"] if txn else None,
                         "scenario": spec["name"]},
                evidence=[txn["raw_ref"]] if txn else [], reason=f"scenario {spec['name']}")["id"]
            state["parent"] = parent

        if name == "invariants":
            opened = invariants.scan_and_open_work_items(conn, cfg, parent_id=parent)
            summary["opened_by_invariants"] = [i["kind"] for i in opened]
            log(f"  invariants opened: {[i['kind'] for i in opened] or 'nothing'}")
            tasks = {"WI-FORECAST-CORRECTION": (FORECAST_TASK, "forecast"),
                     }
            for item in opened:
                task, seg = tasks.get(item["kind"], (FORECAST_TASK, None))
                result = _work(conn, cfg, item["id"], "seat1", client,
                               task.format(finding=item["payload"]["finding"]), controller, log,
                               segment=seg, step=step, human=state["human"])
                summary["posted"].append(item["kind"])
            summary["steps"].append({"step": step, "opened": summary["opened_by_invariants"]})
            return parent

        if name == "deposit":
            txn = conn.execute("SELECT * FROM golden_bank_txns WHERE counterparty LIKE 'US TREASURY%'"
                               " ORDER BY date DESC LIMIT 1").fetchone()
            if txn is None:
                raise ScenarioError("no Treasury deposit in the bank feed to record")
            item = create_work_item(
                conn, kind="WI-REFUND-DEPOSIT", seat="seat1", actor="sync", parent_id=parent,
                payload={"trigger": "bank_deposit", "bank_txn_id": txn["txn_id"],
                         "amount": f"{txn['amount']:.2f}", "date": txn["date"],
                         "description": txn["description"], "counterparty": txn["counterparty"]},
                evidence=[txn["raw_ref"]], reason=f"unrecorded deposit {txn['txn_id']}")
            _work(conn, cfg, item["id"], "seat1", client, TASK.format(bank_txn=txn["txn_id"]),
                  controller, log, segment="deposit", step=step, human=state["human"])
            summary["posted"].append("WI-REFUND-DEPOSIT")
            summary["steps"].append({"step": step, "work_item": item["id"]})
            return item["id"]

        if name == "split":
            refund_id = conn.execute("SELECT refund_id FROM golden_cbp_refunds LIMIT 1").fetchone()[0]
            item = create_work_item(
                conn, kind="WI-REFUND-SPLIT", seat="seat2", actor="sync", parent_id=parent,
                payload={"refund_id": refund_id}, evidence=[f"refund:{refund_id}"],
                reason=f"split {refund_id}")
            _work(conn, cfg, item["id"], "seat2", client, SPLIT_TASK.format(refund_id=refund_id),
                  controller, log, segment="split", step=step, human=state["human"])
            summary["posted"].append("WI-REFUND-SPLIT")
            summary["steps"].append({"step": step, "work_item": item["id"]})
            return item["id"]

        if name in ("credit", "question"):
            customer = conn.execute(
                "SELECT customer_id, name, contract_id FROM golden_billing_customers WHERE customer_id = ?",
                (arg,)).fetchone()
            if customer is None:
                raise ScenarioError(f"no customer {arg}")
            refund_id = conn.execute("SELECT refund_id FROM golden_cbp_refunds LIMIT 1").fetchone()[0]
            contract = next((p for p in (cfg.root / "data" / "documents" / "contracts").glob(
                f"{customer['contract_id']}-*.md")), None)
            if contract is None:
                raise ScenarioError(f"no contract file for {customer['contract_id']}")
            contract_path = contract.relative_to(cfg.root).as_posix()
            kind = f"WI-{arg}-{'CREDIT' if name == 'credit' else 'QUESTION'}"
            item = create_work_item(
                conn, kind=kind, seat="seat3", actor="sync", parent_id=parent,
                payload={"customer_id": arg, "contract_id": customer["contract_id"], "refund_id": refund_id},
                evidence=[contract_path], reason=f"{customer['name']} was billed a surcharge")
            task = CREDIT_TASK.format(customer=customer["name"], refund_id=refund_id,
                                      contract_path=contract_path)
            if name == "credit":
                _work(conn, cfg, item["id"], "seat3", client, task, controller, log,
                      segment="bean_street" if arg == "CUST-001" else None,
                      step=step, human=state["human"])
                summary["posted"].append(kind)
            else:
                run = agent.run_seat(conn, "seat3", item["id"],
                                     _for(client, "harbor_question" if arg == "CUST-002" else None),
                                     task, cfg=cfg)
                asked = [dict(r) for r in conn.execute(
                    "SELECT id, to_role, state, answered_by FROM questions WHERE work_item_id = ?",
                    (item["id"],))]
                log(f"  {item['id']} seat3 {run.turns} turn(s) -> {run.state}; asked {asked}")
                if run.state != NEEDS_INFO or not asked:
                    raise ScenarioError(f"{item['id']}: expected the seat to ask, got {run.state}")
                summary["questions"].append({"work_item": item["id"], **asked[0]})
            summary["steps"].append({"step": step, "work_item": item["id"]})
            return item["id"]

        if name == "harbor-close":
            from . import challenge, humans
            from .__main__ import HARBOR_CLOSE_TASK, HARBOR_WAIVER

            question = conn.execute(
                "SELECT w.id FROM work_items w JOIN questions q ON q.work_item_id=w.id "
                "WHERE w.kind='WI-CUST-002-QUESTION' AND q.state='ANSWERED'"
            ).fetchone()
            if not question:
                raise ScenarioError("Harbor needs the lawyer's answer before the waiver")
            refund_id = conn.execute("SELECT refund_id FROM golden_cbp_refunds LIMIT 1").fetchone()[0]
            invoices = conn.execute("SELECT invoice_id, surcharge_total FROM golden_billing_invoices "
                                    "WHERE customer_id='CUST-002'").fetchall()
            # shortcut: this demo has one Harbor invoice; extend the settlement step for multi-invoice worlds.
            if len(invoices) != 1:
                raise ScenarioError("the Harbor demo requires exactly one invoice")
            invoice = invoices[0]
            item = create_work_item(
                conn, kind="WI-CUST-002-CLOSE", seat="seat3", actor="sync", parent_id=parent,
                payload={"customer_id": "CUST-002", "contract_id": "CON-002", "refund_id": refund_id,
                         "follows": question["id"], "detail": {
                             "credit_notes": [{"invoice": invoice["invoice_id"],
                                               "amount": f"{money(invoice['surcharge_total']):.2f}"}],
                             "pre_waiver_amount": f"{money(invoice['surcharge_total']):.2f}"}},
                evidence=["data/documents/contracts/CON-002-harbor.md", invoice["invoice_id"]],
                reason="the simulated lawyer answered; a signed waiver now needs verification")
            challenge.raise_challenge(conn, item["id"], raised_by="human_controller",
                                      text="Harbor signed this waiver. Check the parties and dates.",
                                      document=HARBOR_WAIVER, cfg=cfg)
            task = HARBOR_CLOSE_TASK.format(contract_id="CON-002", customer="Harbor Roasters",
                                            document=HARBOR_WAIVER)
            feedback = ""
            for attempt in range(3):
                run = agent.run_seat(conn, "seat3", item["id"], _for(client, "harbor_close"),
                                     task + feedback, cfg=cfg)
                if run.state != PROPOSED:
                    raise ScenarioError(f"Harbor waiver ended {run.state}, expected PROPOSED")
                if not state["human"]:
                    humans.approve(conn, item["id"], controller.actor, cfg=cfg)
                    break
                from . import authorization

                authorization.apply(conn, item["id"], cfg)     # never covered: logs why the CFO is needed
                mark_step(conn, step, "waiting", work_item=item["id"],
                          note="verified waiver; waiting for the CFO to review the zero settlement")
                outcome = _await_human(conn, item["id"], 1800, log, settling=("APPROVED",))
                if outcome == NEEDS_INFO:
                    row = conn.execute("SELECT reason FROM events WHERE subject=? AND action='revise' "
                                       "ORDER BY event_id DESC LIMIT 1", (item["id"],)).fetchone()
                    feedback = f"\nThe controller sent this back: {row['reason'] if row else 'recheck the evidence'}. Verify again."
                    mark_step(conn, step, "started", work_item=item["id"], note="sent back for another verification")
                    continue
                if outcome != "CLOSED":
                    raise ScenarioError(f"Harbor waiver was {outcome}; settlement was not approved")
                break
            else:
                raise ScenarioError("Harbor waiver was sent back three times; the run stops here")
            transition(conn, question["id"], "close", "sync",
                       reason="Harbor settled on the verified waiver; see the linked settlement",
                       evidence=[item["id"], HARBOR_WAIVER])
            summary["steps"].append({"step": step, "work_item": item["id"]})
            return item["id"]

        raise ScenarioError(f"unknown step {step!r}")


def _run_expectations(conn, cfg, spec, summary) -> list[Check]:
    expect = (spec.get("run") or {}).get("expect") or {}
    checks = []
    if "work_items_opened_by_invariants" in expect:
        want = sorted(expect["work_items_opened_by_invariants"] or [])
        got = sorted(summary.get("opened_by_invariants") or [])
        checks.append(Check("desk run", "work items opened by invariants", "expected",
                            ", ".join(want) or "none", "actual", ", ".join(got) or "none", want == got))
    if "onboarding_missing" in summary:
        missing = summary["onboarding_missing"]
        checks.append(Check("desk run", "onboarding learned every rule", "expected",
                            "three rules and the bad-habit flag", "actual",
                            "missing " + ", ".join(missing) if missing else "all learned", not missing))
    if "posted_entries" in expect:
        checks.append(Check("desk run", "entries posted", "expected", str(expect["posted_entries"]),
                            "actual", str(summary.get("posted_entries")),
                            int(expect["posted_entries"]) == int(summary.get("posted_entries", -1))))
    if "questions_to" in expect:
        want = list(expect["questions_to"])
        got = [q["to_role"] for q in summary.get("questions") or []]
        checks.append(Check("desk run", "questions asked", "expected", ", ".join(want) or "none",
                            "actual", ", ".join(got) or "none", want == got))
    refused = conn.execute("SELECT COUNT(*) FROM events WHERE allowed = 0 AND action NOT LIKE 'can:%'").fetchone()[0]
    checks.append(Check("desk run", "refused attempts logged", "count", str(refused), "note",
                        "informational", True, "a refusal is code holding the line, not a failure"))
    if expect.get("scorer"):
        try:
            card = score.score(conn, cfg, phase=int(expect["scorer"].get("phase", 3)))
            fails = [f"{l.label}: {l.actual}" for l in card.failures]
            checks.append(Check("desk run", "scorer (not scenario-aware)", "result",
                                "PASS" if card.passed else f"{len(fails)} failure(s)", "note",
                                "informational", True, "; ".join(fails)[:300]))
        except Exception as error:  # noqa: BLE001 - reported, never fatal
            checks.append(Check("desk run", "scorer (not scenario-aware)", "result",
                                f"{type(error).__name__}", "note", "informational", True, str(error)[:200]))
    return checks


# --------------------------------------------------------------------------- #
# the whole thing
# --------------------------------------------------------------------------- #

def render(spec, cfg, checks: list[Check], summary: dict | None) -> str:
    lines = [f"scenario {spec['name']}: {(spec.get('description') or '').strip().splitlines()[0] if spec.get('description') else ''}",
             f"sandbox  {cfg.root}", ""]
    for issue in spec.get("known_issues") or []:
        lines.append(f"known issue: {str(issue).strip()}")
    if spec.get("known_issues"):
        lines.append("")
    width = max(len(c.name) for c in checks) if checks else 20
    group = None
    for c in checks:
        if c.group != group:
            group = c.group
            lines.append(group)
            lines.append("-" * len(group))
        mark = "ok  " if c.ok else "FOUND" if c.expected else "FAIL"
        lines.append(f"  [{mark}] {c.name:<{width}}  {c.left_label} = {c.left}  |  {c.right_label} = {c.right}")
        if c.note:
            lines.append(f"         {'':<{width}}  {c.note}")
    failed = [c for c in checks if not c.ok and not c.expected]
    lines.append("")
    if summary:
        lines.append(f"desk run: {summary['llm']} ({summary['provider']} {summary['model']}); "
                     f"posted {summary['posted_entries']} entr{'y' if summary['posted_entries'] == 1 else 'ies'}; "
                     f"parent {summary.get('parent')}")
    totals = summarize(checks)
    lines.append(f"RESULT: {'PASS' if not failed else f'{len(failed)} FAILURE(S)'} "
                 f"({totals['ok']}/{len(checks)} checks agree; "
                 f"{totals['expected_findings']} expected starting findings)")
    return "\n".join(lines)


def run_file(path: str | Path, *, llm: str | None = None, run: bool = True,
             sandbox_dir: str | Path | None = None, base: Config = CFG,
             log=print) -> tuple[int, dict[str, Any]]:
    spec = load(path)
    cfg = materialize(spec, base, sandbox_dir)
    log(f"sandbox {cfg.root}")
    db.rebuild_erp(cfg)
    conn = db.desk_conn(cfg)
    report: dict[str, Any] = {"scenario": spec["name"], "sandbox": str(cfg.root), "checks": []}
    try:
        counts = sync.run(conn, cfg)
        log(f"sync   {counts}")
        before = tie_outs(conn, cfg, spec, "before")
        for c in before:
            c.name = f"[before] {c.name}"
        report["checks"].extend(c.as_dict() for c in before)
        checks = list(before)
        summary = None
        if run and spec.get("run"):
            mode = llm or spec["run"].get("llm") or "scripted"
            try:
                summary = run_desk(conn, cfg, spec, mode, log)
                after = tie_outs(conn, cfg, spec, "after") + _run_expectations(conn, cfg, spec, summary)
            except Exception as error:  # noqa: BLE001 - the report must say why the run stopped
                summary = {"llm": mode, "provider": "?", "model": "?", "posted_entries":
                           conn.execute("SELECT COUNT(*) FROM work_items WHERE state='POSTED'").fetchone()[0],
                           "error": f"{type(error).__name__}: {error}"}
                after = [Check("desk run", "run completed", "result",
                               f"FAILED: {type(error).__name__}: {str(error)[:160]}",
                               "expected", "completed", False)]
            for c in after:
                c.name = f"[after] {c.name}"
            checks += after
            report["checks"].extend(c.as_dict() for c in after)
            report["run"] = summary
        text = render(spec, cfg, checks, summary)
        report["passed"] = summarize(checks)["passed"]
        (cfg.root / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        (cfg.root / "report.txt").write_text(text, encoding="utf-8")
        log("")
        log(text)
        return (0 if report["passed"] else 1), report
    finally:
        conn.close()
