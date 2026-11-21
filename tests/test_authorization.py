"""The CFO's standing authorization: routine entries post on their own, inside the caps
and conditions the CFO adopted; anything else waits for a person, with the reason logged."""
from __future__ import annotations

import json

import pytest

from desk import agent, authorization, llm, policies
from desk.__main__ import TASK
from desk.perms import can
from desk.scripted_seat1 import script
from desk.workflow import create_work_item, get_item, transition

from conftest import BANK_TXN


def _propose_deposit(conn, item, cfg):
    agent.run_seat(conn, "seat1", item["id"], llm.FakeLLM(script=script()), TASK.format(bank_txn=BANK_TXN), cfg=cfg)
    assert get_item(conn, item["id"])["state"] == "PROPOSED"


def test_the_authorization_is_a_policy_adopted_by_the_cfo(conn):
    policy = authorization.in_force(conn)
    assert policy and policy["policy_id"] == "POL-A1" and policy["status"] == "APPROVED"
    assert policy["approved_by"] == "human_cfo" and policy["rule"]["kind"] == "standing_authorization"
    assert "WI-REFUND-DEPOSIT" in policy["rule"]["covers"]


def test_the_authorization_may_approve_but_never_post_or_propose():
    assert can("standing_authorization", "approve").allowed
    assert not can("standing_authorization", "post").allowed
    assert not can("standing_authorization", "propose").allowed
    assert can("human_cfo", "approve").allowed and can("human_cfo", "challenge").allowed
    assert not can("human_lawyer", "approve").allowed and can("human_lawyer", "answer").allowed


def test_a_routine_deposit_posts_on_its_own_and_says_why(conn, item, cfg):
    _propose_deposit(conn, item, cfg)
    verdict, result = authorization.apply(conn, item["id"], cfg)
    assert verdict.covered and verdict.policy_ref == "POL-A1@1"
    assert verdict.amount == "14,317.00" and verdict.cap == "25,000.00"
    after = get_item(conn, item["id"])
    assert after["state"] == "POSTED" and after["approved_by"] == "standing_authorization"
    assert result and result["entry"]
    rows = conn.execute("SELECT action, reason, policy_version FROM events WHERE subject = ?"
                        " AND action LIKE 'authorization:%'", (item["id"],)).fetchall()
    assert [r["action"] for r in rows] == ["authorization:applied"]
    assert rows[0]["policy_version"] == "POL-A1@1" and "routine" in rows[0]["reason"]
    assert authorization.last_verdict(conn, item["id"])["covered"] is True


def test_an_entry_above_the_cap_waits_for_the_cfo(conn, item, cfg):
    _propose_deposit(conn, item, cfg)
    policy = authorization.in_force(conn)
    rule = dict(policy["rule"])
    rule["covers"] = {**rule["covers"], "WI-REFUND-DEPOSIT": {"cap": "10000.00"}}
    conn.execute("UPDATE policies SET rule = ? WHERE policy_id = 'POL-A1' AND version = '1'", (json.dumps(rule),))
    verdict, result = authorization.apply(conn, item["id"], cfg)
    assert not verdict.covered and result is None
    assert "above the 10,000.00 cap" in verdict.reason
    assert get_item(conn, item["id"])["state"] == "PROPOSED"
    assert authorization.last_verdict(conn, item["id"])["covered"] is False


def test_pushback_a_send_back_or_no_authorization_means_a_person_decides(conn, item, cfg):
    from desk import challenge

    _propose_deposit(conn, item, cfg)
    challenge.raise_challenge(conn, item["id"], raised_by="human_cfo", text="Are you sure?", llm=None, cfg=cfg)
    verdict = authorization.check(conn, item["id"], cfg)
    assert not verdict.covered and "pushed back" in verdict.reason

    entry = {"postings": [{"account": "Expenses:COGS:Tariff", "amount": "1.00"},
                          {"account": "Assets:Inventory:Parts", "amount": "-1.00"}]}
    other = create_work_item(conn, kind="WI-REFUND-SPLIT", seat="seat2", actor="sync",
                             payload={"refund_id": "RFD-001", "journal_entry": entry}, evidence=[])
    transition(conn, other["id"], "propose", "ai_agent.seat2")
    transition(conn, other["id"], "revise", "human_cfo", reason="look again")
    transition(conn, other["id"], "propose", "ai_agent.seat2")
    verdict = authorization.check(conn, other["id"], cfg)
    assert not verdict.covered and "sent back" in verdict.reason

    conn.execute("UPDATE policies SET status = 'REVOKED' WHERE policy_id = 'POL-A1'")
    verdict = authorization.check(conn, other["id"], cfg)
    assert not verdict.covered and "no standing authorization" in verdict.reason


def test_kinds_outside_the_authorization_explain_themselves(conn, cfg):
    close = create_work_item(conn, kind="WI-CUST-002-CLOSE", seat="seat3", actor="sync",
                             payload={"customer_id": "CUST-002", "revised_amount": "0.00"}, evidence=[])
    transition(conn, close["id"], "propose", "ai_agent.seat3")
    verdict = authorization.check(conn, close["id"], cfg)
    assert not verdict.covered and "a person reviews the settlement" in verdict.reason
    assert policies.get(conn, "POL-A1@1")["status"] == "APPROVED"
