"""Onboarding: the seats propose rules, and nothing applies before approval."""
from __future__ import annotations

import pytest

from desk import llm, onboarding, policies
from desk.policies import PolicyError
from desk.scripted import onboarding_script, phase_script
from desk.tools import Ctx, dispatch

from conftest import events

def test_the_seats_propose_pol_l1_l2_l3_with_evidence(conn, cfg):
    client = llm.FakeLLM(script=onboarding_script())
    result = onboarding.run(conn, client, cfg)

    approved = {p["policy_id"]: p for p in result.approved}
    assert set(approved) == {"POL-L1", "POL-L2", "POL-L3"}
    for policy in approved.values():
        assert policy["status"] == "APPROVED"
        assert policy["approved_by"] == "human_controller"
        assert policy["proposed_by"].startswith("ai_agent.")
        assert policy["evidence_count"] >= 1, policy["policy_id"]
        assert policy["evidence"], policy["policy_id"]
        # at least one source document, not just golden raw_refs
        assert any(e.startswith("data/documents/") or e.startswith("data/reference/")
                   for e in policy["evidence"]), policy["policy_id"]
    assert not result.missing


def test_pol_l1_classifies_the_programmes_the_way_the_reference_table_does(conn, cfg, onboarded):
    rule = policies.get(conn, "POL-L1@1")["rule"]
    assert rule["kind"] == "program_refundability"
    assert set(rule["refundable"]) == {"IEEPA_CN", "IEEPA_EU"}
    assert set(rule["not_refundable"]) == {"S232_STEEL", "S301_CN", "S122"}


def test_pol_l2_names_the_ratio_the_policy_gives(conn, cfg, onboarded):
    rule = policies.get(conn, "POL-L2@1")["rule"]
    assert rule["kind"] == "refund_split"
    assert rule["numerator"] == "qty_consumed"
    assert rule["denominator"] == "qty_received"


def test_the_2025_09_30_method_is_flagged_as_a_bad_habit_not_learned(conn, cfg):
    client = llm.FakeLLM(script=onboarding_script())
    result = onboarding.run(conn, client, cfg)

    assert len(result.flagged) == 1
    flagged = result.flagged[0]
    assert flagged["status"] == "FLAGGED_BAD"
    assert flagged["policy_id"] == "BAD-2025-09-30-FORECAST"
    assert "10,500" in flagged["reason"] or "10500" in flagged["reason"]
    assert "232" in flagged["reason"]

    # it is not approved, and it has no executor, so it can never be applied
    assert flagged["ref"] not in {p["ref"] for p in result.approved}
    with pytest.raises(PolicyError, match="not APPROVED"):
        policies.apply(conn, flagged["ref"], {}, cfg)


def test_nothing_applies_before_the_controller_approves(conn, cfg):
    """A proposed rule is inert: apply_policy refuses it until it is approved."""
    from desk.scripted import onboard_refundable

    client = llm.FakeLLM(script=onboard_refundable())
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1", cfg=cfg)

    from desk.workflow import create_work_item
    item = create_work_item(conn, kind="WI-ONBOARD-POLICY", seat="seat1", actor="sync")
    from desk import agent
    agent.run_seat(conn, "seat1", item["id"], client, "propose POL-L1", cfg=cfg)

    proposed = policies.get(conn, "POL-L1@1")
    assert proposed["status"] == "PROPOSED"

    ok, content = dispatch(
        Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1", work_item_id=item["id"], cfg=cfg),
        "apply_policy", {"policy_ref": "POL-L1@1", "inputs": {"entry_nos": ["BRK-2500101-1"]}},
    )
    assert not ok and "not APPROVED" in content
    assert events(conn, action="tool:apply_policy", allowed=False)


def test_an_agent_cannot_approve_its_own_policy(conn, cfg, onboarded):
    from desk.perms import Denied

    item = conn.execute(
        "SELECT id FROM work_items WHERE kind = 'WI-ONBOARD-POLICY' ORDER BY id"
    ).fetchone()[0]
    with pytest.raises(Denied, match="may not approve"):
        from desk.workflow import transition
        transition(conn, item, "approve", "ai_agent.seat1")

    with pytest.raises(Denied, match="may not approve_policy"):
        policies.approve(conn, "POL-L1@1", "ai_agent.seat1")
    assert events(conn, action="approve_policy", subject="POL-L1@1", allowed=False)


def test_an_unexecutable_rule_is_refused_at_proposal_time(conn, cfg, item):
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "propose_policy", {
        "policy_id": "POL-BAD", "name": "vibes",
        "verdict": "adopt", "rule": {"kind": "whatever_feels_right"},
        "reason": "it seems fine", "evidence": ["data/reference/refund_policy.md"],
        "evidence_count": 1,
    })
    assert not ok and "cannot be executed" in content
    assert not [p for p in policies.listing(conn) if p["policy_id"] == "POL-BAD"]


def test_a_policy_with_no_source_document_is_refused(conn, cfg, item):
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "propose_policy", {
        "policy_id": "POL-L9", "name": "no sources",
        "verdict": "adopt",
        "rule": {"kind": "program_refundability", "refundable": ["IEEPA_CN"],
                 "not_refundable": []},
        "reason": "because", "evidence": ["data/views/customs/cbp_entries.json#BRK-2500101-1"],
        "evidence_count": 1,
    })
    assert not ok and "no source document" in content
