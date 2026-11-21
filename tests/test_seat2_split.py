"""Seat 2: the refund split, computed by the approved rule."""
from __future__ import annotations

from decimal import Decimal

import pytest

from desk import llm, policies, score
from desk.agent import run_seat
from desk.policies import PolicyError
from desk.scripted import seat2_split
from desk.tools import SEAT_TOOLS, Ctx, dispatch
from desk.tools.compute import ComputeError, split_refund

from conftest import REFUND


def test_seat2_has_the_cost_accountants_tools_and_nothing_more():
    assert SEAT_TOOLS["seat2"] == (
        "run_sql", "read_doc", "get_policies", "split_refund",
        "propose", "ask", "propose_policy", "apply_policy",
    )
    # Seat 2 is not the customs seat: no match_deposit, no duty reconciliation
    assert "match_deposit" not in SEAT_TOOLS["seat2"]
    assert "reconcile_duty_paid" not in SEAT_TOOLS["seat2"]


def test_split_refund_matches_the_answer_key_per_shipment(conn, cfg, onboarded):
    key = score.load_answer_key(cfg)
    ctx = Ctx(conn=conn, actor="ai_agent.seat2", seat="seat2", cfg=cfg)
    result = split_refund(ctx, refund_id=REFUND)

    for shipment, want in key["refund_split"]["by_shipment"].items():
        got = result["by_shipment"][shipment]
        assert Decimal(got["to_cogs"]) == Decimal(str(want["cogs"])), shipment
        assert Decimal(got["to_inventory"]) == Decimal(str(want["inventory"])), shipment
        assert got["consumed"] == want["consumed"], shipment
        assert got["received"] == want["received"], shipment

    assert Decimal(result["to_cogs_total"]) == Decimal(str(key["refund_split"]["to_cogs"]))
    assert Decimal(result["to_inventory_total"]) == Decimal(
        str(key["refund_split"]["to_inventory"]))
    assert Decimal(result["principal_total"]) == Decimal("13900.00")
    assert result["policy"] == "POL-L2@1"


def test_the_split_adds_back_to_the_principal(conn, cfg, onboarded):
    ctx = Ctx(conn=conn, actor="ai_agent.seat2", seat="seat2", cfg=cfg)
    result = split_refund(ctx, refund_id=REFUND)
    for shipment, row in result["by_shipment"].items():
        assert Decimal(row["to_cogs"]) + Decimal(row["to_inventory"]) == Decimal(
            row["principal"]), shipment
    assert (Decimal(result["to_cogs_total"]) + Decimal(result["to_inventory_total"])
            == Decimal(result["principal_total"]))


def test_split_refund_refuses_to_run_without_an_approved_policy(conn, cfg):
    ctx = Ctx(conn=conn, actor="ai_agent.seat2", seat="seat2", cfg=cfg)
    with pytest.raises(ComputeError, match="no approved refund_split policy"):
        split_refund(ctx, refund_id=REFUND)


def test_a_wrong_ratio_in_the_rule_produces_a_wrong_split(conn, cfg, onboarded):
    """The rule is the agent's to get right; the executor just runs it faithfully."""
    policies.store(
        conn, policy_id="POL-L2W", name="inverted split", rule={
            "kind": "refund_split", "numerator": "qty_on_hand",
            "denominator": "qty_received"},
        status=policies.PROPOSED, evidence=["data/reference/refund_policy.md"],
        evidence_count=1, reason="deliberately inverted", actor="ai_agent.seat2")
    policies.approve(conn, "POL-L2W@1", "human_controller")

    ctx = Ctx(conn=conn, actor="ai_agent.seat2", seat="seat2", cfg=cfg)
    inverted = split_refund(ctx, refund_id=REFUND, policy_ref="POL-L2W@1")
    # it still adds up - invariant 4 would pass - but it is the wrong answer
    assert Decimal(inverted["to_cogs_total"]) == Decimal("13132.00")
    assert Decimal(inverted["to_inventory_total"]) == Decimal("768.00")
    assert (Decimal(inverted["to_cogs_total"]) + Decimal(inverted["to_inventory_total"])
            == Decimal("13900.00"))


def test_the_executor_rejects_a_column_that_is_not_a_quantity(conn, cfg, onboarded):
    policies.store(
        conn, policy_id="POL-L2X", name="nonsense split", rule={
            "kind": "refund_split", "numerator": "unit_cost",
            "denominator": "qty_received"},
        status=policies.PROPOSED, evidence=["data/reference/refund_policy.md"],
        evidence_count=1, reason="nonsense", actor="ai_agent.seat2")
    policies.approve(conn, "POL-L2X@1", "human_controller")
    with pytest.raises(PolicyError, match="numerator/denominator"):
        policies.apply(conn, "POL-L2X@1", {"refund_id": REFUND}, cfg)


def test_seat2_proposes_je4_with_its_breakdown(conn, cfg, onboarded):
    from desk.workflow import create_work_item

    item = create_work_item(conn, kind="WI-REFUND-SPLIT", seat="seat2", actor="sync",
                            payload={"refund_id": REFUND})
    client = llm.FakeLLM(script=seat2_split())
    run = run_seat(conn, "seat2", item["id"], client, "split the refund", cfg=cfg)

    assert run.state == "PROPOSED"
    assert [c["name"] for c in run.tool_calls] == ["split_refund", "read_doc", "propose"]

    payload = conn.execute(
        "SELECT payload FROM work_items WHERE id = ?", (item["id"],)
    ).fetchone()[0]
    import json
    payload = json.loads(payload)
    postings = {p["account"]: Decimal(p["amount"])
                for p in payload["journal_entry"]["postings"]}
    assert postings == {"Expenses:COGS:Tariff": Decimal("13132.00"),
                        "Assets:Inventory:Parts": Decimal("-13132.00")}
    assert payload["detail"]["to_inventory_total"] == "13132.00"
    assert len(payload["detail"]["by_shipment"]) == 6
    assert payload["policy_ref"] == "POL-L2@1"
