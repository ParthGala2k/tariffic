"""Minimal sync layer (Phase 1).

Loads golden tables from the local view files and the Beancount books.
Every golden row carries raw_ref back to the row it came from.
No Mockoon, Increase or Stripe yet.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import CFG, Config
from .db import log_event_now

BANK_VIEW = "data/views/bank/transactions.csv"
CBP_ENTRIES_VIEW = "data/views/customs/cbp_entries.json"
CBP_REFUNDS_VIEW = "data/views/customs/cbp_refunds.json"
BILLING_CUSTOMERS_VIEW = "data/views/billing/customers.json"
BILLING_INVOICES_VIEW = "data/views/billing/invoices.json"


def _read_json(cfg: Config, rel: str) -> Any:
    return json.loads((cfg.root / rel).read_text())


def sync_bank(conn: sqlite3.Connection, cfg: Config = CFG) -> int:
    rows = list(csv.DictReader((cfg.root / BANK_VIEW).read_text().splitlines()))
    conn.executemany(
        "INSERT OR REPLACE INTO golden_bank_txns"
        " (txn_id, date, direction, amount, type, counterparty, description, reference, raw_ref)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (r["txn_id"], r["date"], r["direction"], float(r["amount"]), r["type"],
             r["counterparty"], r["description"], r["reference"] or None,
             f"{BANK_VIEW}#{r['txn_id']}")
            for r in rows
        ],
    )
    return len(rows)


def sync_customs_entries(conn: sqlite3.Connection, cfg: Config = CFG) -> int:
    entries = _read_json(cfg, CBP_ENTRIES_VIEW)
    for entry in entries:
        ref = f"{CBP_ENTRIES_VIEW}#{entry['entry_no']}"
        conn.execute(
            "INSERT OR REPLACE INTO golden_cbp_entries (entry_no, entry_date, importer_no,"
            " importer_ref, country_of_origin, exporter, commercial_hts, description,"
            " entered_value, total_duty, liquidation_status, liquidation_date, refund_status,"
            " refund_id, raw_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry["entry_no"], entry["entry_date"], entry.get("importer_no"),
             entry.get("importer_ref"), entry.get("country_of_origin"), entry.get("exporter"),
             entry.get("commercial_hts"), entry.get("description"), entry.get("entered_value"),
             entry.get("total_duty"), entry.get("liquidation_status"),
             entry.get("liquidation_date"), entry.get("refund_status"), entry.get("refund_id"),
             ref),
        )
        for line in entry.get("duty_lines", []):
            conn.execute(
                "INSERT OR REPLACE INTO golden_cbp_duty_lines"
                " (entry_no, program, hts_ch99, rate_pct, duty, raw_ref) VALUES (?,?,?,?,?,?)",
                (entry["entry_no"], line["program"], line.get("hts_ch99"),
                 line.get("rate_pct"), line["duty"], f"{ref}:{line['program']}"),
            )
    return len(entries)


def sync_customs_refunds(conn: sqlite3.Connection, cfg: Config = CFG) -> int:
    refunds = _read_json(cfg, CBP_REFUNDS_VIEW)
    for refund in refunds:
        ref = f"{CBP_REFUNDS_VIEW}#{refund['refund_id']}"
        conn.execute(
            "INSERT OR REPLACE INTO golden_cbp_refunds (refund_id, notice_date, payment_date,"
            " payment_method, payee, importer_no, reason, total_principal, total_interest,"
            " total, raw_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (refund["refund_id"], refund["notice_date"], refund["payment_date"],
             refund.get("payment_method"), refund.get("payee"), refund.get("importer_no"),
             refund.get("reason"), refund["total_principal"], refund["total_interest"],
             refund["total"], ref),
        )
        for line in refund.get("entries", []):
            conn.execute(
                "INSERT OR REPLACE INTO golden_cbp_refund_entries"
                " (refund_id, entry_no, program, hts_ch99, principal, interest, raw_ref)"
                " VALUES (?,?,?,?,?,?,?)",
                (refund["refund_id"], line["entry_no"], line.get("program"),
                 line.get("hts_ch99"), line["principal"], line["interest"],
                 f"{ref}:{line['entry_no']}"),
            )
    return len(refunds)


def sync_erp(conn: sqlite3.Connection, cfg: Config = CFG) -> int:
    """Stock layers and what has been consumed from each, with the entry_no crosswalk."""
    from .db import erp_conn

    if not cfg.erp_db.exists():
        raise RuntimeError(f"{cfg.erp_db} is missing; run `python -m desk reset` first")

    erp = erp_conn(cfg)
    try:
        layers = erp.execute(
            "SELECT l.layer_id, l.shipment_id, s.entry_no, l.part_id, l.qty_received,"
            " l.qty_on_hand, l.unit_cost, l.duty_per_unit,"
            " COALESCE((SELECT SUM(c.qty) FROM consumption c WHERE c.layer_id = l.layer_id), 0)"
            "   AS qty_consumed"
            " FROM inventory_layers l JOIN shipments s ON s.shipment_id = l.shipment_id"
            " ORDER BY l.layer_id"
        ).fetchall()
        consumption = erp.execute(
            "SELECT so_id, layer_id, part_id, qty FROM consumption ORDER BY so_id, layer_id"
        ).fetchall()
    finally:
        erp.close()

    for layer in layers:
        conn.execute(
            "INSERT OR REPLACE INTO golden_erp_inventory_layers (layer_id, shipment_id,"
            " entry_no, part_id, qty_received, qty_on_hand, qty_consumed, unit_cost,"
            " duty_per_unit, raw_ref) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (layer["layer_id"], layer["shipment_id"], layer["entry_no"], layer["part_id"],
             layer["qty_received"], layer["qty_on_hand"], layer["qty_consumed"],
             layer["unit_cost"], layer["duty_per_unit"],
             f"systems/erp/erp.db#inventory_layers:{layer['layer_id']}"),
        )
    for row in consumption:
        conn.execute(
            "INSERT OR REPLACE INTO golden_erp_consumption (so_id, layer_id, part_id, qty, raw_ref)"
            " VALUES (?,?,?,?,?)",
            (row["so_id"], row["layer_id"], row["part_id"], row["qty"],
             f"systems/erp/erp.db#consumption:{row['so_id']}:{row['layer_id']}"),
        )
    return len(layers)


def sync_billing(conn: sqlite3.Connection, cfg: Config = CFG) -> int:
    """Customers and invoices, with every line. Amounts arrive in cents.

    Contracts are not synced: they are documents, and Seat 3 reads the wording
    itself with read_doc. Only the contract id travels into the golden tables.
    """
    customers = _read_json(cfg, BILLING_CUSTOMERS_VIEW)
    for customer in customers:
        metadata = customer.get("metadata") or {}
        conn.execute(
            "INSERT OR REPLACE INTO golden_billing_customers (customer_id, name, email,"
            " contract_id, passthrough_clause, raw_ref) VALUES (?,?,?,?,?,?)",
            (customer["truth_id"], customer["name"], customer.get("email"),
             metadata.get("contract_id"), metadata.get("passthrough_clause"),
             f"{BILLING_CUSTOMERS_VIEW}#{customer['truth_id']}"),
        )

    invoices = _read_json(cfg, BILLING_INVOICES_VIEW)
    for invoice in invoices:
        ref = f"{BILLING_INVOICES_VIEW}#{invoice['truth_id']}"
        metadata = invoice.get("metadata") or {}

        # Price the lines first, so the header can carry its own totals and the
        # line rows have an invoice to point at.
        priced = []
        machine_total = surcharge_total = 0.0
        for index, line in enumerate(invoice.get("lines", []), 1):
            amount = round(line["unit_amount"] * line["quantity"] / 100.0, 2)
            if line.get("kind") == "tariff_surcharge":
                surcharge_total += amount
            else:
                machine_total += amount
            priced.append((index, line, amount))

        conn.execute(
            "INSERT OR REPLACE INTO golden_billing_invoices (invoice_id, customer_id,"
            " contract_id, invoice_date, paid_date, machine_total, surcharge_total, total,"
            " raw_ref) VALUES (?,?,?,?,?,?,?,?,?)",
            (invoice["truth_id"], invoice["customer"], metadata.get("contract_id"),
             invoice["date"], invoice.get("paid_out_of_band"),
             round(machine_total, 2), round(surcharge_total, 2),
             round(machine_total + surcharge_total, 2), ref),
        )
        for index, line, amount in priced:
            line_meta = line.get("metadata") or {}
            conn.execute(
                "INSERT OR REPLACE INTO golden_billing_invoice_lines (invoice_id, line_no,"
                " kind, product, description, quantity, unit_amount, amount, machine_id,"
                " shipment_ids, tariff_program, surcharge_basis, raw_ref)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (invoice["truth_id"], index, line.get("kind"), line.get("product"),
                 line.get("description"), line["quantity"],
                 round(line["unit_amount"] / 100.0, 2), amount,
                 line_meta.get("machine_id"), line_meta.get("shipment_ids"),
                 line_meta.get("tariff_program"), line_meta.get("surcharge_basis"),
                 f"{ref}:line{index}"),
            )
    return len(invoices)


def sync_books(conn: sqlite3.Connection, cfg: Config = CFG) -> int:
    """Load the ledger through the beancount loader."""
    from beancount import loader
    from beancount.core import data as bc_data

    entries, errors, _options = loader.load_file(str(cfg.beancount_file))
    if errors:
        raise RuntimeError(f"beancount load errors in {cfg.beancount_file}: {errors[:3]}")

    count = 0
    for entry in entries:
        if not isinstance(entry, bc_data.Transaction):
            continue
        source = f"{Path(entry.meta['filename']).relative_to(cfg.root).as_posix()}:{entry.meta['lineno']}"
        for index, posting in enumerate(entry.postings):
            conn.execute(
                "INSERT OR REPLACE INTO golden_ledger_postings"
                " (posting_id, date, narration, account, amount, currency, raw_ref)"
                " VALUES (?,?,?,?,?,?,?)",
                (f"{source}#{index}", entry.date.isoformat(), entry.narration,
                 posting.account, float(posting.units.number), posting.units.currency, source),
            )
            count += 1
    return count


def run(conn: sqlite3.Connection, cfg: Config = CFG, actor: str = "sync") -> dict[str, int]:
    """Sync every Phase 1 source of record into the golden tables."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        counts = {
            "bank_txns": sync_bank(conn, cfg),
            "cbp_entries": sync_customs_entries(conn, cfg),
            "cbp_refunds": sync_customs_refunds(conn, cfg),
            "erp_layers": sync_erp(conn, cfg),
            "billing_invoices": sync_billing(conn, cfg),
            "ledger_postings": sync_books(conn, cfg),
        }
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log_event_now(
        conn, actor=actor, action="sync", subject=None, allowed=True,
        reason="golden tables refreshed from local views and the books",
        evidence=[BANK_VIEW, CBP_ENTRIES_VIEW, CBP_REFUNDS_VIEW,
                  BILLING_CUSTOMERS_VIEW, BILLING_INVOICES_VIEW,
                  cfg.erp_db.relative_to(cfg.root).as_posix(),
                  cfg.beancount_file.relative_to(cfg.root).as_posix()],
        diff={"before": None, "after": counts},
    )
    return counts
