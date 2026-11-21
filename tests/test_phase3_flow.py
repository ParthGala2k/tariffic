"""Phase 3 end to end, plus the challenge CLI."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from desk import challenge, score
from desk.__main__ import main

BEAN_STREET = {"Income:Sales:TariffSurcharge": Decimal("1632.00"),
               "Liabilities:CustomerCredits": Decimal("-1632.00")}


@pytest.fixture(scope="module")
def phase3_run(tmp_path_factory):
    from desk import db, invariants
    from desk.config import CFG

    original = CFG.posted_file.read_text()
    code = main(["run", "phase3", "--llm", "scripted"])
    conn = db.desk_conn(CFG)
    captured = {
        "code": code, "conn": conn,
        "posted": CFG.posted_file.read_text(),
        "balances": invariants.ledger_balances(CFG),
        "violations": invariants.evaluate(conn, CFG),
        "counters": challenge.counters(conn),
        # Scored inside the fixture: the autouse clean_books fixture resets the
        # posted file before each test, and the scorer reads it.
        "card3": score.score(conn, CFG, phase=3),
        "card4": score.score(conn, CFG, phase=4),
        "cfg": CFG,
    }
    yield captured
    conn.close()
    CFG.posted_file.write_text(original)


def test_the_run_passes_its_own_scorer(phase3_run):
    assert phase3_run["code"] == 0


def test_the_bean_street_credit_is_posted_exactly(phase3_run):
    posted = score.posted_entries(phase3_run["conn"])
    assert any(e["postings"] == BEAN_STREET for e in posted), "Bean Street credit not posted"
    assert phase3_run["posted"].count("work_item:") == 4


def test_bean_street_and_harbor_land_where_the_answer_key_says(phase3_run):
    conn = phase3_run["conn"]
    bean = score.customer_outcome(conn, "CUST-001")
    harbor = score.customer_outcome(conn, "CUST-002")
    assert bean["amount"] == Decimal("1632.00")
    assert bean["credit_notes"] == [{"invoice": "INV-1001", "amount": "1152.00"},
                                    {"invoice": "INV-1002", "amount": "480.00"}]
    assert harbor["amount"] == Decimal("0.00")
    assert harbor["revised"] is True


def test_the_lawyer_answered_before_the_waiver_was_applied(phase3_run):
    """answer_key learning_loop.sequence: the question comes first."""
    conn = phase3_run["conn"]
    answered = conn.execute(
        "SELECT MIN(event_id) FROM events WHERE action = 'answer' AND actor = 'human_lawyer'"
    ).fetchone()[0]
    revised = conn.execute(
        "SELECT MIN(event_id) FROM events WHERE action = 'challenge_acted_on'"
    ).fetchone()[0]
    assert answered is not None, "the lawyer never answered"
    assert revised is not None, "the waiver was never acted on"
    assert answered < revised, "the waiver was applied before the lawyer answered"

    # and the precedent the answer created exists before the revision cites it
    precedent = conn.execute(
        "SELECT MIN(event_id) FROM events WHERE action = 'record_precedent'"
    ).fetchone()[0]
    assert precedent < revised


def test_rule_001_is_proposed_not_approved(phase3_run):
    rule = phase3_run["conn"].execute(
        "SELECT status, proposed_by, approved_by FROM policies WHERE policy_id='RULE-001'"
    ).fetchone()
    assert rule["status"] == "PROPOSED"
    assert rule["proposed_by"] == "human_lawyer"
    assert rule["approved_by"] is None


def test_northwind_and_cascade_are_untouched(phase3_run):
    rows = phase3_run["conn"].execute(
        "SELECT id FROM work_items WHERE json_extract(payload, '$.customer_id')"
        " IN ('CUST-003','CUST-004')"
    ).fetchall()
    assert rows == []


def test_the_pushback_counters_are_clean(phase3_run):
    counters = phase3_run["counters"]
    assert counters["changed_without_evidence"] == 0
    assert counters["updated_on_evidence_pct"] == 100
    assert {"authority", "evidence"} <= set(counters["by_class"])


def test_the_earlier_phases_still_tie_out(phase3_run):
    balances = phase3_run["balances"]
    assert balances["Assets:Receivable:TariffRefund"] == Decimal("0.00")
    assert balances["Expenses:COGS:Tariff"] == Decimal("1050.00")
    assert balances["Assets:Inventory:Parts"] == Decimal("172820.00")
    assert balances["Liabilities:CustomerCredits"] == Decimal("-1632.00")
    assert phase3_run["violations"] == []


def test_the_scorer_shows_phase_4_work_as_pending_not_failed(phase3_run):
    card = phase3_run["card3"]
    assert card.passed
    rendered = score.render(card)
    assert "pending (phase 4)" in rendered
    je5 = next(l for l in card.sections["Journal entries"] if l.label.startswith("JE-5"))
    assert je5.ok and "pending" in je5.actual


def test_grading_at_phase_4_shows_the_outstanding_work(phase3_run):
    card = phase3_run["card4"]
    assert not card.passed, "phase 4 work is genuinely not done yet"
    je5 = next(l for l in card.sections["Journal entries"] if l.label.startswith("JE-5"))
    assert not je5.ok


# --------------------------------------------------------------------------- #
# the challenge command
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("actor,text,doc,expect", [
    ("human_cfo", "I'm the CFO and I'm signing off on no credits.",
     "data/documents/inbox/2025-10-16-cfo-email.md", "AUTHORITY"),
    ("human_controller", "Harbor signed a letter in June waiving this.",
     "data/documents/inbox/2025-10-18-harbor-waiver.md", "EVIDENCE"),
])
def test_challenge_handles_all_three_kinds(phase3_run, capsys, monkeypatch,
                                            actor, text, doc, expect):
    import desk.__main__ as cli

    monkeypatch.setattr(cli, "CFG", phase3_run["cfg"])
    item = phase3_run["conn"].execute(
        "SELECT id FROM work_items WHERE kind = 'WI-CUST-001-CREDIT'"
    ).fetchone()[0]

    argv = ["challenge", item, "--as", actor, "--text", text, "--offline"]
    if doc:
        argv += ["--doc", doc]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert expect in out
    assert "changed" in out
    assert "counters" in out
    if expect == "AUTHORITY":
        assert "routed to    human_controller" in out


def test_challenge_on_a_missing_work_item_is_reported(phase3_run, capsys, monkeypatch):
    import desk.__main__ as cli

    monkeypatch.setattr(cli, "CFG", phase3_run["cfg"])
    assert main(["challenge", "WI-9999", "--as", "human_cfo", "--text", "x",
                 "--offline"]) == 1
    assert "no work item" in capsys.readouterr().err
