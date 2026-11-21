"""The five invariants: detection, and gating by effect."""
from __future__ import annotations

from decimal import Decimal

import pytest

from desk import invariants, personas
from desk.invariants import INV_1, INV_2, INV_4, INV_5
from desk.perms import Denied
from desk.workflow import get_item, transition

from conftest import REFUND, events

# JE-4's split, as split_refund produces it (CLAUDE.md 10.2).
GOOD_SPLIT = {
    "refund_id": REFUND,
    "by_shipment": {
        "SHP-001": {"principal": "4000.00", "consumed": 12, "received": 100,
                    "to_cogs": "480.00", "to_inventory": "3520.00"},
        "SHP-002": {"principal": "2400.00", "consumed": 3, "received": 40,
                    "to_cogs": "180.00", "to_inventory": "2220.00"},
        "SHP-003": {"principal": "1200.00", "consumed": 9, "received": 100,
                    "to_cogs": "108.00", "to_inventory": "1092.00"},
        "SHP-004": {"principal": "3000.00", "consumed": 0, "received": 75,
                    "to_cogs": "0.00", "to_inventory": "3000.00"},
        "SHP-005": {"principal": "1800.00", "consumed": 0, "received": 30,
                    "to_cogs": "0.00", "to_inventory": "1800.00"},
        "SHP-006": {"principal": "1500.00", "consumed": 0, "received": 125,
                    "to_cogs": "0.00", "to_inventory": "1500.00"},
    },
    "to_cogs_total": "768.00",
    "to_inventory_total": "13132.00",
    "principal_total": "13900.00",
}

JE3 = [("Assets:Bank:Increase:Operating", "14317.00"),
       ("Assets:Receivable:TariffRefund", "-13900.00"),
       ("Income:Interest:TariffRefund", "-417.00")]
JE4 = [("Expenses:COGS:Tariff", "13132.00"),
       ("Assets:Inventory:Parts", "-13132.00")]


def make_proposal(conn, kind, postings, detail=None, *, seat="seat1", parent_id=None):
    """A work item sitting in PROPOSED, proposed by a seat agent."""
    from desk.workflow import create_work_item

    item = create_work_item(conn, kind=kind, seat=seat, actor="sync", parent_id=parent_id,
                            payload={}, evidence=["test"])
    payload = {
        "journal_entry": {
            "date": "2025-10-15", "narration": f"test {kind}",
            "postings": [{"account": a, "amount": m, "currency": "USD"} for a, m in postings],
        },
        "policy_ref": "POL-001@1",
    }
    if detail:
        payload["detail"] = detail
    return transition(conn, item["id"], "propose", f"ai_agent.{seat}",
                      patch={"payload": payload, "evidence": ["test"]})


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #

def test_pm1_is_detected_by_invariant_2(conn, cfg):
    """INV-2 reports the non-refundable duty only: 10,500.00, not the whole 10,800.00 gap."""
    found = {v.invariant: v for v in invariants.evaluate(conn, cfg)}
    assert INV_2 in found, "PM-1 (Section 232 in the refund forecast) was not detected"
    violation = found[INV_2]
    assert violation.magnitude == Decimal("10500.00")
    assert violation.subject == "Assets:Receivable:TariffRefund"
    assert violation.opens_kind == "WI-FORECAST-CORRECTION"
    assert "S232_STEEL" in violation.message


def test_the_receivable_breaks_down_by_cause(conn, cfg):
    """The whole 10,500.00 excess is duty under a programme that is never refunded."""
    breakdown = invariants.receivable_breakdown(conn, cfg)
    assert breakdown["receivable_balance"] == "24400.00"
    assert breakdown["refundable_outstanding"] == "13900.00"
    assert breakdown["excess"] == "10500.00"
    assert breakdown["by_cause"] == {"non_refundable_duty": "10500.00"}
    assert breakdown["non_refundable_programs_identified"] == ["S232_STEEL"]
    assert breakdown["work_items"]["non_refundable_duty"] == "WI-FORECAST-CORRECTION"


def test_the_cause_accounts_for_the_whole_excess(conn, cfg):
    breakdown = invariants.receivable_breakdown(conn, cfg)
    assert (Decimal(breakdown["by_cause"]["non_refundable_duty"])
            == Decimal(breakdown["excess"]))


def test_duty_paid_agrees_with_the_7501(conn, cfg):
    """Brewline pays CBP directly, so INV-1 is quiet on a healthy world."""
    assert invariants.duty_paid_vs_cbp(conn, cfg) == {}
    assert not [v for v in invariants.evaluate(conn, cfg) if v.invariant == INV_1]


def test_invariant_1_fires_when_the_books_disagree_with_customs(conn, cfg):
    conn.execute("UPDATE golden_bank_txns SET amount = amount + 300"
                 " WHERE reference = 'BRK-2500225-4'")
    found = {v.invariant: v for v in invariants.evaluate(conn, cfg)}
    assert INV_1 in found
    assert found[INV_1].magnitude == Decimal("300.00")
    assert found[INV_1].subject == "BRK-2500225-4"
    assert found[INV_1].opens_kind == "WI-DUTY-RECONCILIATION"
    assert "more than the CBP 7501" in found[INV_1].message


def test_only_pm1_is_found_on_the_untouched_books(conn, cfg):
    by_invariant = {}
    for violation in invariants.evaluate(conn, cfg):
        by_invariant.setdefault(violation.invariant, []).append(violation.subject)
    assert by_invariant[INV_2] == ["Assets:Receivable:TariffRefund"]
    # every other check is clean
    assert INV_1 not in by_invariant
    assert "INV-3" not in by_invariant
    assert INV_4 not in by_invariant
    assert INV_5 not in by_invariant


def test_a_violation_in_existing_data_opens_a_work_item(conn, cfg):
    opened = invariants.scan_and_open_work_items(conn, cfg)
    assert {item["kind"] for item in opened} == {"WI-FORECAST-CORRECTION"}
    # re-running does not duplicate it
    assert invariants.scan_and_open_work_items(conn, cfg) == []
    assert len(opened) == 1


# --------------------------------------------------------------------------- #
# invariant 5, before and after the split
# --------------------------------------------------------------------------- #

def test_invariant_5_ties_out_before_the_split(conn, cfg):
    """185,952.00 == 185,952.00 with no split posted."""
    assert invariants.erp_landed_value_on_hand(conn) == Decimal("185952.00")
    balances = invariants.ledger_balances(cfg)
    assert balances["Assets:Inventory:Parts"] == Decimal("185952.00")
    assert not [v for v in invariants.evaluate(conn, cfg) if v.invariant == INV_5]


def test_invariant_5_ties_out_after_a_correct_split(conn, cfg, clean_books):
    """172,820.00 == 185,952.00 - 13,132.00 once the split has posted.

    This is the false-positive guard: a correct split must not leave INV-5 open.
    """
    from desk import gateway

    item = make_proposal(conn, "WI-REFUND-SPLIT", JE4, GOOD_SPLIT, seat="seat2")
    personas.ScriptedController.load(cfg).review(conn, item["id"])
    gateway.post(conn, item["id"], cfg=cfg)

    balances = invariants.ledger_balances(cfg)
    assert balances["Assets:Inventory:Parts"] == Decimal("172820.00")
    open_inv5 = [v for v in invariants.evaluate(conn, cfg) if v.invariant == INV_5]
    assert not open_inv5, f"INV-5 fired falsely after a correct split: {open_inv5}"


def test_invariant_5_fires_when_inventory_does_not_tie_out(conn, cfg, clean_books):
    """A split posted without its breakdown leaves inventory short and INV-5 open."""
    from desk import gateway

    item = make_proposal(conn, "WI-REFUND-SPLIT", JE4, detail={"refund_id": REFUND,
                                                               "by_shipment": {}},
                         seat="seat2")
    # INV-4 would refuse this on approval, so post it past the gate deliberately
    import desk.workflow as workflow
    workflow.INVARIANT_GATE = False
    try:
        personas.ScriptedController.load(cfg).review(conn, item["id"])
        gateway.post(conn, item["id"], cfg=cfg)
    finally:
        workflow.INVARIANT_GATE = True

    open_inv5 = [v for v in invariants.evaluate(conn, cfg) if v.invariant == INV_5]
    assert open_inv5, "inventory is 13,132.00 short of the tie-out but INV-5 stayed quiet"
    assert open_inv5[0].magnitude == Decimal("13132.00")


# --------------------------------------------------------------------------- #
# gating by effect
# --------------------------------------------------------------------------- #

def test_je3_can_be_approved_with_pm1_still_unfixed(conn, cfg):
    """A pre-existing violation must not block an unrelated, correct proposal."""
    before = {v.key for v in invariants.evaluate(conn, cfg)}
    assert (INV_2, "Assets:Receivable:TariffRefund") in before, "PM-1 should still be open"

    item = make_proposal(conn, "WI-REFUND-DEPOSIT", JE3)
    verdict = invariants.gate(conn, item, cfg)
    assert verdict.allowed, verdict.reason
    assert not verdict.new and not verdict.worse

    approved = personas.ScriptedController.load(cfg).review(conn, item["id"])
    assert approved["state"] == "APPROVED"
    # PM-1 is still there afterwards; it was never this proposal's job
    assert (INV_2, "Assets:Receivable:TariffRefund") in {
        v.key for v in invariants.evaluate(conn, cfg)
    }


def test_a_split_that_does_not_add_up_cannot_be_approved(conn, cfg):
    bad = {
        "refund_id": REFUND,
        "by_shipment": {
            # 480 + 3520 = 4000 is right; 999 + 3520 is not
            "SHP-001": {"principal": "4000.00", "consumed": 12, "received": 100,
                        "to_cogs": "999.00", "to_inventory": "3520.00"},
        },
        "to_cogs_total": "999.00",
        "to_inventory_total": "3520.00",
        "principal_total": "4000.00",
    }
    item = make_proposal(conn, "WI-REFUND-SPLIT", JE4, bad, seat="seat2")

    verdict = invariants.gate(conn, item, cfg)
    assert not verdict.allowed
    assert INV_4 in verdict.reason

    with pytest.raises(Denied, match="INV-4"):
        personas.ScriptedController.load(cfg).review(conn, item["id"])

    refused = events(conn, action="approve", subject=item["id"], allowed=False)
    assert len(refused) == 1 and "INV-4" in refused[0]["reason"]
    assert get_item(conn, item["id"])["state"] == "PROPOSED"


def test_the_gate_logs_its_verdict_either_way(conn, cfg):
    item = make_proposal(conn, "WI-REFUND-DEPOSIT", JE3)
    personas.ScriptedController.load(cfg).review(conn, item["id"])
    gated = events(conn, action="invariant_gate", subject=item["id"])
    assert len(gated) == 1 and gated[0]["allowed"] == 1
    assert "before" in gated[0]["evidence"] and "after" in gated[0]["evidence"]


def test_a_proposal_that_worsens_a_violation_is_refused(conn, cfg):
    """Pushing more non-refundable duty into the receivable deepens INV-2."""
    item = make_proposal(conn, "WI-FORECAST-CORRECTION", [
        ("Assets:Receivable:TariffRefund", "5000.00"),
        ("Expenses:COGS:Tariff", "-5000.00"),
    ])
    verdict = invariants.gate(conn, item, cfg)
    assert not verdict.allowed
    assert INV_2 in verdict.reason
    assert verdict.worse and not verdict.new


def test_a_proposal_that_improves_a_violation_is_allowed(conn, cfg):
    """JE-1 takes the Section 232 duty back out, clearing INV-2 completely."""
    item = make_proposal(conn, "WI-FORECAST-CORRECTION", [
        ("Expenses:COGS:Tariff", "10500.00"),
        ("Assets:Receivable:TariffRefund", "-10500.00"),
    ])
    verdict = invariants.gate(conn, item, cfg)
    assert verdict.allowed, verdict.reason
    before = {v.key: v for v in verdict.before}
    after = {v.key: v for v in verdict.after}
    key = (INV_2, "Assets:Receivable:TariffRefund")
    assert before[key].magnitude == Decimal("10500.00")
    assert key not in after, "INV-2 should be fully cleared by the 10,500.00 correction"
    assert after == {}, "nothing else should be open once PM-1 is corrected"
