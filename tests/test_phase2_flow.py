"""Phase 2 end to end, with a fake LLM: four entries, two mistakes caught."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from desk import invariants, score
from desk.__main__ import POSTED_HEADER, main

from conftest import events

EXPECTED = {
    "JE-1": {"Expenses:COGS:Tariff": Decimal("10500.00"),
             "Assets:Receivable:TariffRefund": Decimal("-10500.00")},
    "JE-2": {"Assets:Bank:Increase:Operating": Decimal("14317.00"),
             "Assets:Receivable:TariffRefund": Decimal("-13900.00"),
             "Income:Interest:TariffRefund": Decimal("-417.00")},
    "JE-3": {"Expenses:COGS:Tariff": Decimal("13132.00"),
             "Assets:Inventory:Parts": Decimal("-13132.00")},
}

FINAL_BALANCES = {
    "Assets:Receivable:TariffRefund": Decimal("0.00"),
    "Expenses:COGS:Tariff": Decimal("1050.00"),
    "Assets:Inventory:Parts": Decimal("172820.00"),
    "Income:Interest:TariffRefund": Decimal("-417.00"),
}


@pytest.fixture(scope="module")
def phase2_run(tmp_path_factory):
    """Run `desk run phase2 --llm scripted` once for the whole module."""
    import dataclasses
    import sqlite3

    from desk import db
    from desk.config import CFG

    original = CFG.posted_file.read_text()
    code = main(["run", "phase2", "--llm", "scripted"])
    conn = db.desk_conn(CFG)
    # Capture everything that depends on the posted file now: the autouse
    # clean_books fixture resets that file before each individual test.
    captured = {
        "code": code,
        "conn": conn,
        "posted": CFG.posted_file.read_text(),
        "balances": invariants.ledger_balances(CFG),
        "violations": invariants.evaluate(conn, CFG),
        "cfg": CFG,
    }
    yield captured
    conn.close()
    CFG.posted_file.write_text(original)


def test_the_run_passes_its_own_scorer(phase2_run):
    assert phase2_run["code"] == 0, "the Phase 2 run did not pass its own scorer"


def test_all_three_entries_are_posted_exactly(phase2_run):
    conn = phase2_run["conn"]
    posted = score.posted_entries(conn)
    for je_id, expected in EXPECTED.items():
        assert any(e["postings"] == expected for e in posted), f"{je_id} not posted exactly"
    assert len([e for e in posted if e["postings"]]) == 3


def test_the_final_balances_match_the_answer_key(phase2_run):
    balances = phase2_run["balances"]
    for account, expected in FINAL_BALANCES.items():
        assert balances[account] == expected, account


def test_no_invariant_violation_is_left_open(phase2_run):
    remaining = phase2_run["violations"]
    assert remaining == [], [v.as_dict() for v in remaining]


def test_every_posted_entry_carries_its_work_item_and_approver(phase2_run):
    text = phase2_run["posted"]
    assert text.startswith(POSTED_HEADER)
    assert text.count("work_item:") == 3
    assert text.count('approved_by: "human_controller"') == 3



def test_the_split_matches_the_answer_key_per_shipment(phase2_run):
    conn = phase2_run["conn"]
    key = score.load_answer_key(phase2_run["cfg"])
    split = next(e for e in score.posted_entries(conn) if e["kind"] == "WI-REFUND-SPLIT")
    actual = split["detail"]["by_shipment"]
    for shipment, want in key["refund_split"]["by_shipment"].items():
        got = actual[shipment]
        assert Decimal(got["to_cogs"]) == Decimal(str(want["cogs"])), shipment
        assert Decimal(got["to_inventory"]) == Decimal(str(want["inventory"])), shipment
        assert int(got["consumed"]) == want["consumed"]
        assert int(got["received"]) == want["received"]
    assert sum(Decimal(r["to_cogs"]) for r in actual.values()) == Decimal("768.00")
    assert sum(Decimal(r["to_inventory"]) for r in actual.values()) == Decimal("13132.00")


def test_the_parent_closes_only_after_every_child_posts(phase2_run):
    conn = phase2_run["conn"]
    parent = conn.execute(
        "SELECT id, state FROM work_items WHERE kind = 'WI-REFUND-EVENT'"
    ).fetchone()
    assert parent["state"] == "CLOSED"
    kids = conn.execute(
        "SELECT id, kind, state FROM work_items WHERE parent_id = ?", (parent["id"],)
    ).fetchall()
    assert len(kids) == 3
    assert {k["state"] for k in kids} == {"POSTED"}
    assert {k["kind"] for k in kids} == {
        "WI-FORECAST-CORRECTION", "WI-REFUND-DEPOSIT", "WI-REFUND-SPLIT"}


def test_every_proposal_cites_a_source_document_and_a_policy(phase2_run):
    conn = phase2_run["conn"]
    rows = conn.execute(
        "SELECT id, payload, evidence, policy_id, policy_version FROM work_items"
        " WHERE state = 'POSTED'"
    ).fetchall()
    assert rows
    for row in rows:
        evidence = json.loads(row["evidence"])
        assert any(e.startswith("data/documents/") or e.startswith("data/reference/")
                   for e in evidence), f"{row['id']} cites no source document"
        assert row["policy_id"] and row["policy_version"], row["id"]


def test_policies_were_learned_and_a_bad_habit_flagged(phase2_run):
    conn = phase2_run["conn"]
    approved = conn.execute(
        "SELECT policy_id FROM policies WHERE status = 'APPROVED' ORDER BY policy_id"
    ).fetchall()
    assert [r[0] for r in approved] == ["POL-001", "POL-A1", "POL-L1", "POL-L2", "POL-L3"]
    flagged = conn.execute(
        "SELECT policy_id, reason FROM policies WHERE status = 'FLAGGED_BAD'"
    ).fetchall()
    assert len(flagged) == 1 and flagged[0]["policy_id"] == "BAD-2025-09-30-FORECAST"


def test_one_can_event_per_action(phase2_run):
    """Fix from Phase 1: dispatch and transition no longer both log the same check."""
    conn = phase2_run["conn"]
    for item_id, in conn.execute(
        "SELECT id FROM work_items WHERE state = 'POSTED'"
    ).fetchall():
        for action in ("propose", "approve", "post"):
            checks = events(conn, action=f"can:{action}", subject=item_id)
            assert len(checks) <= 1, f"{item_id}: {len(checks)} can:{action} rows"
