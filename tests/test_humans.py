"""Humans at the screen have exactly the powers the role matrix gives them."""
from __future__ import annotations

import pytest

from desk import agent, humans, llm, policies, precedents
from desk.__main__ import TASK
from desk.db import now
from desk.evidence import EvidenceRejected
from desk.perms import Denied
from desk.scripted_seat1 import script
from desk.workflow import get_item, transition

from conftest import BANK_TXN, events


def run_seat1(conn, item, cfg):
    return agent.run_seat(conn, "seat1", item["id"], llm.FakeLLM(script=script()),
                          TASK.format(bank_txn=BANK_TXN), cfg=cfg)


def open_question(conn, item, to_role="human_lawyer", text="Does clause 7.2 of CON-003 oblige a credit?"):
    """A seat asked, nobody has answered yet."""
    transition(conn, item["id"], "ask", "ai_agent.seat3", reason=text)
    qid = f"Q-{item['id']}-TEST"
    conn.execute(
        "INSERT INTO questions (id, work_item_id, asked_by, to_role, text, state, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (qid, item["id"], "ai_agent.seat3", to_role, text, "OPEN", now()))
    return qid


# ---- approve / reject / send back ---------------------------------------------

def test_the_controller_approves_and_the_gateway_posts(conn, item, cfg, clean_books):
    run_seat1(conn, item, cfg)
    result = humans.approve(conn, item["id"], "human_controller", "ties to the notice", cfg=cfg)
    assert result["item"]["state"] == "POSTED"
    assert result["item"]["approved_by"] == "human_controller"
    assert f'work_item: "{item["id"]}"' in result["entry"]
    assert result["entry"] in clean_books.read_text()
    assert [e["actor"] for e in events(conn, action="post", subject=item["id"])] == ["gateway"]


def test_the_seat_cannot_approve_through_the_human_path(conn, item, cfg):
    run_seat1(conn, item, cfg)
    with pytest.raises(Denied):
        humans.approve(conn, item["id"], "ai_agent.seat1", cfg=cfg)
    assert get_item(conn, item["id"])["state"] == "PROPOSED"
    assert events(conn, action="approve", subject=item["id"], allowed=False)


def test_approve_without_posting_leaves_it_for_the_gateway(conn, item, cfg):
    run_seat1(conn, item, cfg)
    result = humans.approve(conn, item["id"], "human_controller", post=False, cfg=cfg)
    assert result["item"]["state"] == "APPROVED" and result["entry"] is None


def test_reject_needs_a_reason_and_settles_the_item(conn, item, cfg):
    run_seat1(conn, item, cfg)
    with pytest.raises(ValueError):
        humans.reject(conn, item["id"], "human_controller", "   ")
    rejected = humans.reject(conn, item["id"], "human_controller", "wrong account")
    assert rejected["state"] == "REJECTED"


def test_send_back_returns_the_item_to_the_seat(conn, item, cfg):
    run_seat1(conn, item, cfg)
    sent = humans.send_back(conn, item["id"], "human_controller", "interest looks wrong")
    assert sent["state"] == "NEEDS_INFO"
    assert events(conn, action="revise", subject=item["id"], allowed=True)
    with pytest.raises(Denied):                       # a seat may not send things back
        humans.send_back(conn, item["id"], "ai_agent.seat1", "no")


def test_unknown_items_are_reported(conn, cfg):
    with pytest.raises(humans.NotFound):
        humans.approve(conn, "WI-9999", "human_controller", cfg=cfg)


# ---- answering questions: precedents and proposed rules --------------------------

RULE = {"id": "RULE-TEST", "rule": "credit the full surcharge on affected machines unless waived",
        "scope": "distributor template clause 7.2", "applies_to": ["CON-003", "CON-004"],
        "clause": "CON-003 section 7.2"}


def test_a_lawyers_answer_becomes_a_precedent_and_a_proposed_rule(conn, cfg, customer_item):
    item = customer_item("CUST-003", kind="WI-CUST-003-CREDIT")
    qid = open_question(conn, item)
    result = humans.answer(conn, qid, "human_lawyer", "Yes, on the consistency reading.",
                           rule_proposal=RULE, cfg=cfg)

    assert result["question"]["state"] == "ANSWERED"
    assert result["question"]["answered_by"] == "human_lawyer"
    assert result["precedent_id"] == f"PRE-{qid}"
    found = precedents.search(conn, "CON-003")
    assert found and found[0]["decided_by"] == "human_lawyer"
    assert result["rule_proposed"] == "RULE-TEST@1"
    assert policies.get(conn, "RULE-TEST@1")["status"] == policies.PROPOSED
    assert get_item(conn, item["id"])["state"] == "NEEDS_INFO"      # the seat still has to resume

    with pytest.raises(Denied):                                      # once is enough
        humans.answer(conn, qid, "human_lawyer", "again", cfg=cfg)


def test_only_the_person_asked_may_answer(conn, cfg, customer_item):
    item = customer_item("CUST-003", kind="WI-CUST-003-CREDIT")
    qid = open_question(conn, item, to_role="human_lawyer")
    with pytest.raises(Denied):
        humans.answer(conn, qid, "human_cfo", "our figure is right", cfg=cfg)
    with pytest.raises(Denied):                                      # seats never answer humans' questions
        humans.answer(conn, qid, "ai_agent.seat3", "I decide", cfg=cfg)
    assert events(conn, action="answer", subject=item["id"], allowed=False)


def test_an_answer_may_only_attach_a_real_inbox_document(conn, cfg, customer_item):
    item = customer_item("CUST-003", kind="WI-CUST-003-CREDIT")
    qid = open_question(conn, item)
    with pytest.raises(EvidenceRejected):
        humans.answer(conn, qid, "human_lawyer", "see policy",
                      document="data/reference/refund_policy.md", cfg=cfg)
    ok = humans.answer(conn, qid, "human_lawyer", "see the waiver",
                       document="data/documents/inbox/2025-10-18-harbor-waiver.md", cfg=cfg)
    assert ok["question"]["state"] == "ANSWERED"


def test_only_the_controller_approves_a_rule(conn, cfg, customer_item):
    item = customer_item("CUST-003", kind="WI-CUST-003-CREDIT")
    qid = open_question(conn, item)
    humans.answer(conn, qid, "human_lawyer", "Yes.", rule_proposal=RULE, cfg=cfg)
    with pytest.raises(Denied):
        humans.approve_policy(conn, "RULE-TEST@1", "human_lawyer")
    with pytest.raises(Denied):
        humans.approve_policy(conn, "RULE-TEST@1", "ai_agent.seat3")
    approved = humans.approve_policy(conn, "RULE-TEST@1", "human_controller", "scope is right")
    assert approved["status"] == policies.APPROVED
    assert approved["approved_by"] == "human_controller"


def test_a_rule_proposal_needs_an_executable_kind(conn, cfg, customer_item):
    item = customer_item("CUST-003", kind="WI-CUST-003-CREDIT")
    qid = open_question(conn, item)
    with pytest.raises(ValueError):
        humans.answer(conn, qid, "human_lawyer", "Yes.",
                      rule_proposal={**RULE, "kind": "magic"}, cfg=cfg)


# ---- resuming a seat -------------------------------------------------------------

def test_resume_refuses_while_a_question_is_open_or_the_item_is_not_waiting(
        conn, item, cfg, customer_item):
    waiting = customer_item("CUST-003", kind="WI-CUST-003-CREDIT")
    open_question(conn, waiting)
    with pytest.raises(Denied):
        humans.resume(conn, waiting["id"], llm.FakeLLM(script=[]), cfg=cfg)
    run_seat1(conn, item, cfg)
    with pytest.raises(Denied):                                      # PROPOSED, not NEEDS_INFO
        humans.resume(conn, item["id"], llm.FakeLLM(script=[]), cfg=cfg)


def test_the_resume_task_hands_the_seat_its_answers(conn, cfg, customer_item):
    item = customer_item("CUST-003", kind="WI-CUST-003-CREDIT")
    qid = open_question(conn, item)
    humans.answer(conn, qid, "human_lawyer", "Yes, credit it.", cfg=cfg)
    task = humans.resume_task(conn, get_item(conn, item["id"]))
    assert "Yes, credit it." in task and "human_lawyer" in task
    assert "search_precedents" in task and "opinion" in task
