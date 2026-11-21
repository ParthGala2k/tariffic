"""Seat 3: customer shares read off the contracts."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from desk import llm, policies, precedents
from desk.agent import run_seat
from desk.scripted import segment
from desk.tools import SEAT_TOOLS, Ctx, dispatch
from desk.tools.compute import PartialRefund, surcharge_on_invoices
from desk.workflow import get_item

from conftest import REFUND, events

CON1 = "data/documents/contracts/CON-001-bean-street.md"


def test_seat3_has_the_revenue_accountants_tools_and_no_others():
    assert SEAT_TOOLS["seat3"] == (
        "run_sql", "read_doc", "get_policies", "search_precedents",
        "surcharge_on_invoices",
        "propose", "ask", "apply_policy", "revise_proposal",
    )
    assert "match_deposit" not in SEAT_TOOLS["seat3"]       # Seat 1's
    assert "split_refund" not in SEAT_TOOLS["seat3"]        # Seat 2's
    assert "post" not in SEAT_TOOLS["seat3"]
    assert "approve" not in SEAT_TOOLS["seat3"]


# --------------------------------------------------------------------------- #
# the arithmetic
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("customer_id,expected,notes", [
    ("CUST-001", "1632.00", {"INV-1001": "1152.00", "INV-1002": "480.00"}),
    ("CUST-002", "576.00", {"INV-1003": "576.00"}),      # before the waiver
    ("CUST-003", "240.00", {"INV-1004": "240.00"}),
    ("CUST-004", "0.00", {}),                            # no 2025 invoices
])
def test_surcharge_on_invoices_matches_the_billing_records(conn, cfg, customer_id,
                                                           expected, notes):
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3", cfg=cfg)
    result = surcharge_on_invoices(ctx, customer_id=customer_id, refund_id=REFUND)
    assert result["total_surcharge"] == expected
    assert {n["invoice"]: n["amount"] for n in result["credit_notes"]} == notes


def test_it_only_counts_surcharge_on_refunded_shipments(conn, cfg):
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3", cfg=cfg)
    result = surcharge_on_invoices(ctx, customer_id="CUST-001", refund_id=REFUND)
    for line in result["by_invoice"]:
        assert line["shipments_not_refunded"] == []
        assert set(line["shipments_refunded"]) <= set(result["refunded_shipments"])


def test_a_mixed_surcharge_line_is_not_guessed_at(conn, cfg, customer_item):
    """A line covering refunded and unrefunded shipments goes to a human."""
    conn.execute(
        "UPDATE golden_billing_invoice_lines SET shipment_ids = ?"
        " WHERE invoice_id = 'INV-1001' AND kind = 'tariff_surcharge'",
        ("SHP-001,SHP-007",),        # SHP-007 is not in RFD-001
    )
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3", cfg=cfg)
    with pytest.raises(PartialRefund) as caught:
        surcharge_on_invoices(ctx, customer_id="CUST-001", refund_id=REFUND)
    assert "INV-1001" in caught.value.reason
    assert "SHP-007" in caught.value.reason
    assert caught.value.detail["partial_lines"][0]["shipments_not_refunded"] == ["SHP-007"]
    assert caught.value.detail["partial_lines"][0]["shipments_refunded"] == ["SHP-001"]


def test_a_mixed_line_parks_the_work_item_and_asks_the_controller(conn, cfg, customer_item):
    """The seat is handed the breakdown and the ticket waits for an answer."""
    conn.execute(
        "UPDATE golden_billing_invoice_lines SET shipment_ids = ?"
        " WHERE invoice_id = 'INV-1001' AND kind = 'tariff_surcharge'",
        ("SHP-001,SHP-007",),
    )
    item = customer_item("CUST-001", kind="WI-CUST-001-CREDIT")
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3",
              work_item_id=item["id"], cfg=cfg)

    ok, content = dispatch(ctx, "surcharge_on_invoices",
                           {"customer_id": "CUST-001", "refund_id": REFUND})
    assert not ok
    assert "SHP-007" in content and "does not say how to split" in content

    ok, _ = dispatch(ctx, "ask", {
        "question": ("INV-1001's surcharge covers SHP-001 (refunded) and SHP-007 (not "
                     "refunded). The contract does not say how to split one line. How "
                     "should it be apportioned?"),
        "to_role": "human_controller",
        "evidence": ["INV-1001"],
    })
    assert ok
    assert get_item(conn, item["id"])["state"] == "NEEDS_INFO"
    question = conn.execute(
        "SELECT to_role, text FROM questions WHERE work_item_id = ?", (item["id"],)
    ).fetchone()
    assert question["to_role"] == "human_controller"
    assert "SHP-007" in question["text"]


# --------------------------------------------------------------------------- #
# Bean Street: an explicit clause
# --------------------------------------------------------------------------- #

def test_bean_street_gets_1632_with_the_per_invoice_breakdown(conn, cfg, onboarded,
                                                              customer_item):
    item = customer_item("CUST-001", kind="WI-CUST-001-CREDIT")
    run = run_seat(conn, "seat3", item["id"],
                   llm.FakeLLM(script=segment("bean_street")),
                   "decide what Bean Street is owed", cfg=cfg)
    assert run.state == "PROPOSED"

    payload = get_item(conn, item["id"])["payload"]
    postings = {p["account"]: Decimal(p["amount"])
                for p in payload["journal_entry"]["postings"]}
    assert postings == {"Income:Sales:TariffSurcharge": Decimal("1632.00"),
                        "Liabilities:CustomerCredits": Decimal("-1632.00")}
    assert payload["detail"]["credit_notes"] == [
        {"invoice": "INV-1001", "amount": "1152.00"},
        {"invoice": "INV-1002", "amount": "480.00"},
    ]
    assert payload["policy_ref"] == "POL-L3@1"


def test_bean_streets_evidence_cites_the_clause_and_the_invoices(conn, cfg, onboarded,
                                                                 customer_item):
    item = customer_item("CUST-001", kind="WI-CUST-001-CREDIT")
    run_seat(conn, "seat3", item["id"], llm.FakeLLM(script=segment("bean_street")),
             "decide", cfg=cfg)
    evidence = get_item(conn, item["id"])["evidence"]
    assert CON1 in evidence
    assert "INV-1001" in evidence and "INV-1002" in evidence
    rationale = get_item(conn, item["id"])["payload"]["rationale"]
    assert "7.2" in rationale
    assert "1,152.00" in rationale and "480.00" in rationale


# --------------------------------------------------------------------------- #
# Harbor: an ambiguous clause is not the desk's to decide
# --------------------------------------------------------------------------- #

def test_seat3_asks_the_lawyer_about_harbor_rather_than_deciding(conn, cfg, onboarded,
                                                                 customer_item):
    item = customer_item("CUST-002", kind="WI-CUST-002-QUESTION")
    run = run_seat(conn, "seat3", item["id"],
                   llm.FakeLLM(script=segment("harbor_question")),
                   "decide what Harbor is owed", cfg=cfg)

    assert run.state == "NEEDS_INFO", "the seat decided an ambiguous clause itself"
    assert "ask" in [c["name"] for c in run.tool_calls]
    question = conn.execute(
        "SELECT id, to_role, state FROM questions WHERE work_item_id = ?", (item["id"],)
    ).fetchone()
    assert question["to_role"] == "human_lawyer"
    assert question["id"] == "Q-CON-002-SHARE"
    assert get_item(conn, item["id"])["payload"].get("journal_entry") is None


def test_code_refuses_a_decision_on_an_ambiguous_clause_with_nothing_to_rest_on(
        conn, cfg, onboarded, customer_item):
    """Even if the model tries to decide Harbor, code stops it."""
    item = customer_item("CUST-002", kind="WI-CUST-002-CREDIT")
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "propose", {
        "rationale": "I reckon they are owed it",
        "journal_entry": {
            "date": "2025-10-15", "narration": "Harbor credit",
            "postings": [{"account": "Income:Sales:TariffSurcharge", "amount": "576.00"},
                         {"account": "Liabilities:CustomerCredits", "amount": "-576.00"}],
        },
        "policy_ref": "POL-L3@1",
        "evidence": ["data/documents/contracts/CON-002-harbor.md"],
    })
    assert not ok
    assert "ambiguous" in content
    assert "human_lawyer" in content
    assert get_item(conn, item["id"])["state"] == "NEW"
    assert events(conn, action="tool:propose", subject=item["id"], allowed=False)


def test_the_lawyers_answer_becomes_a_precedent_and_rule_001_stays_proposed(
        conn, cfg, onboarded, customer_item):
    item = customer_item("CUST-002", kind="WI-CUST-002-QUESTION")
    run_seat(conn, "seat3", item["id"], llm.FakeLLM(script=segment("harbor_question")),
             "decide", cfg=cfg)

    found = precedents.search(conn, "CON-002")
    assert len(found) == 1
    assert found[0]["decided_by"] == "human_lawyer"
    assert "consistency" in found[0]["decision"]

    rule = policies.get(conn, "RULE-001@1")
    assert rule["status"] == "PROPOSED", "RULE-001 must not be approved in Phase 3"
    assert rule["proposed_by"] == "human_lawyer"
    assert set(rule["rule"]["applies_to"]) == {"CON-002", "CON-003", "CON-004"}


def test_rule_001_cannot_be_applied_while_it_is_only_proposed(conn, cfg, onboarded,
                                                              customer_item):
    item = customer_item("CUST-002", kind="WI-CUST-002-QUESTION")
    run_seat(conn, "seat3", item["id"], llm.FakeLLM(script=segment("harbor_question")),
             "decide", cfg=cfg)
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "apply_policy",
                           {"policy_ref": "RULE-001@1", "inputs": {}})
    assert not ok and "not APPROVED" in content


def test_northwind_and_cascade_are_left_alone_in_phase_3(conn, cfg):
    """Phase 3 touches Bean Street and Harbor only."""
    rows = conn.execute(
        "SELECT id FROM work_items WHERE json_extract(payload, '$.customer_id')"
        " IN ('CUST-003','CUST-004')"
    ).fetchall()
    assert rows == []


# --------------------------------------------------------------------------- #
# a credit must say which invoices it credits
# --------------------------------------------------------------------------- #

def _credit(conn, cfg, item, amount, detail=None, seat="seat3"):
    ctx = Ctx(conn=conn, actor=f"ai_agent.{seat}", seat=seat,
              work_item_id=item["id"], cfg=cfg)
    args = {
        "rationale": "CON-001 clause 7.2",
        "journal_entry": {
            "date": "2025-10-15", "narration": "customer credit",
            "postings": [
                {"account": "Income:Sales:TariffSurcharge", "amount": amount},
                {"account": "Liabilities:CustomerCredits", "amount": f"-{amount}"},
            ],
        },
        "policy_ref": "POL-L3@1",
        "evidence": [CON1],
    }
    if detail is not None:
        args["detail"] = detail
    return dispatch(ctx, "propose", args)


def test_a_lump_sum_credit_is_refused(conn, cfg, onboarded, customer_item):
    item = customer_item("CUST-001", kind="WI-CUST-001-CREDIT")
    ok, content = _credit(conn, cfg, item, "1632.00")
    assert not ok
    assert "which invoices" in content
    assert "credit_notes" in content
    assert "surcharge_on_invoices" in content


def test_credit_notes_that_do_not_add_up_are_refused(conn, cfg, onboarded, customer_item):
    item = customer_item("CUST-001", kind="WI-CUST-001-CREDIT")
    ok, content = _credit(conn, cfg, item, "1632.00", detail={"credit_notes": [
        {"invoice": "INV-1001", "amount": "1152.00"}]})
    assert not ok
    assert "add up to 1152.00" in content and "1632.00" in content


def test_crediting_more_than_was_billed_is_refused(conn, cfg, onboarded, customer_item):
    item = customer_item("CUST-001", kind="WI-CUST-001-CREDIT")
    ok, content = _credit(conn, cfg, item, "2000.00", detail={"credit_notes": [
        {"invoice": "INV-1001", "amount": "2000.00"}]})
    assert not ok
    assert "was billed 1152.00" in content


def test_crediting_another_customers_invoice_is_refused(conn, cfg, onboarded,
                                                         customer_item):
    item = customer_item("CUST-001", kind="WI-CUST-001-CREDIT")
    ok, content = _credit(conn, cfg, item, "576.00", detail={"credit_notes": [
        {"invoice": "INV-1003", "amount": "576.00"}]})     # Harbor's invoice
    assert not ok
    assert "belongs to CUST-002" in content


def test_a_credit_with_a_correct_breakdown_is_accepted(conn, cfg, onboarded,
                                                        customer_item):
    item = customer_item("CUST-001", kind="WI-CUST-001-CREDIT")
    ok, content = _credit(conn, cfg, item, "1632.00", detail={"credit_notes": [
        {"invoice": "INV-1001", "amount": "1152.00"},
        {"invoice": "INV-1002", "amount": "480.00"}]})
    assert ok, content
    assert get_item(conn, item["id"])["state"] == "PROPOSED"


# --------------------------------------------------------------------------- #
# the lawyer answers the question asked, not the id quoted
# --------------------------------------------------------------------------- #

def test_the_lawyer_recognises_the_question_without_a_scripted_id(conn, cfg, onboarded,
                                                                   customer_item):
    """A real person answers what they were asked."""
    item = customer_item("CUST-002", kind="WI-CUST-002-QUESTION")
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "ask", {
        "question": ("Does clause 7.2 of CON-002 (Harbor Roasters) oblige Brewline to "
                     "pass through refunded IEEPA duties billed as a tariff surcharge?"),
        "to_role": "human_lawyer",
    })
    assert ok, content
    reply = json.loads(content)
    assert reply["answer"] and "consistency" in reply["answer"]

    assert events(conn, action="match_question", subject=item["id"])
    assert precedents.search(conn, "CON-002")
    assert policies.get(conn, "RULE-001@1")["status"] == "PROPOSED"


def test_a_question_nobody_scripted_is_left_unanswered(conn, cfg, onboarded,
                                                        customer_item):
    item = customer_item("CUST-002", kind="WI-CUST-002-QUESTION")
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "ask", {
        "question": "What is the capital of France?",
        "to_role": "human_lawyer",
    })
    assert ok
    assert json.loads(content)["answer"] is None
    assert get_item(conn, item["id"])["state"] == "NEEDS_INFO"
