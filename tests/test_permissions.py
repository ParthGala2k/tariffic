"""Permissions and separation of duties, enforced in code."""
from __future__ import annotations

import pytest

from desk import llm, personas
from desk.agent import run_seat
from desk.__main__ import TASK
from desk.perms import Denied, can
from desk.scripted_seat1 import script
from desk.tools import REGISTRY, Ctx, dispatch, schemas_for
from desk.workflow import transition

from conftest import BANK_TXN, events


def propose_as_agent(conn, item, cfg):
    client = llm.FakeLLM(script=script())
    return run_seat(conn, "seat1", item["id"], client, TASK.format(bank_txn=BANK_TXN), cfg=cfg)


def test_agent_cannot_approve_its_own_proposal_and_the_attempt_is_logged(conn, item, cfg):
    propose_as_agent(conn, item, cfg)
    assert conn.execute("SELECT proposed_by FROM work_items WHERE id=?",
                        (item["id"],)).fetchone()[0] == "ai_agent.seat1"

    with pytest.raises(Denied) as caught:
        transition(conn, item["id"], "approve", "ai_agent.seat1")

    # Two independent layers refuse it: ai_agent has no approve action at all,
    # and separation of duties would block it even if the role were widened.
    assert "may not approve" in caught.value.reason

    refused = events(conn, action="approve", subject=item["id"], allowed=False)
    assert len(refused) == 1
    assert refused[0]["actor"] == "ai_agent.seat1"
    assert refused[0]["allowed"] == 0
    assert "may not approve" in refused[0]["reason"]

    # The item did not move.
    assert conn.execute("SELECT state FROM work_items WHERE id=?",
                        (item["id"],)).fetchone()[0] == "PROPOSED"


def test_separation_of_duties_blocks_the_proposer_even_with_the_role(monkeypatch, item):
    """Widen ai_agent's role and separation of duties still refuses the proposer."""
    from desk import perms

    monkeypatch.setitem(perms.ROLES, "ai_agent", perms.ROLES["ai_agent"] | {"approve"})
    decision = perms.can("ai_agent.seat1", "approve",
                         {**item, "proposed_by": "ai_agent.seat1"})
    assert not decision.allowed
    assert "separation of duties" in decision.reason
    assert perms.can("ai_agent.seat1", "approve",
                     {**item, "proposed_by": "ai_agent.seat2"}).allowed


def test_separation_of_duties_applies_to_humans_too(conn, item, cfg):
    """A controller who proposed an item may not approve it either."""
    transition(conn, item["id"], "propose", "ai_agent.seat1",
               patch={"payload": {"journal_entry": None}, "evidence": ["x"]})
    conn.execute("UPDATE work_items SET proposed_by='human_controller' WHERE id=?", (item["id"],))
    with pytest.raises(Denied, match="separation of duties"):
        transition(conn, item["id"], "approve", "human_controller")


def test_approval_by_a_different_actor_is_allowed(conn, item, cfg):
    propose_as_agent(conn, item, cfg)
    approved = personas.ScriptedController.load(cfg).review(conn, item["id"])
    assert approved["state"] == "APPROVED"
    assert approved["approved_by"] == "human_controller"


def test_role_matrix_matches_the_handover():
    assert can("sync", "create_work_item").allowed
    assert not can("ai_agent.seat1", "create_work_item").allowed
    assert can("ai_agent.seat1", "propose").allowed
    assert can("ai_agent.seat1", "ask").allowed
    assert not can("ai_agent.seat1", "approve").allowed
    assert not can("ai_agent.seat1", "post").allowed
    assert can("human_controller", "approve").allowed
    assert not can("human_controller", "post").allowed
    assert can("human_lawyer", "answer").allowed
    assert not can("human_lawyer", "approve").allowed
    assert can("gateway", "post").allowed
    assert not can("gateway", "approve").allowed
    assert not can("nobody", "propose").allowed


def test_invalid_transitions_are_refused_and_logged(conn, item):
    with pytest.raises(Denied, match="invalid transition"):
        transition(conn, item["id"], "approve", "human_controller")  # still NEW
    refused = events(conn, action="approve", subject=item["id"], allowed=False)
    assert len(refused) == 1
    assert "invalid transition" in refused[0]["reason"]


def test_a_tool_outside_the_seats_set_is_refused_and_logged(conn, item, cfg):
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "split_refund", {})          # a Seat 2 tool
    assert not ok and "has no tool" in content
    assert events(conn, action="tool:split_refund", allowed=False)


def test_every_can_check_is_logged_allowed_or_denied(conn, item, cfg):
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    dispatch(ctx, "run_sql", {"sql": "SELECT txn_id FROM golden_bank_txns"})
    allowed = events(conn, action="can:run_sql", subject=item["id"], allowed=True)
    assert len(allowed) == 1 and allowed[0]["actor"] == "ai_agent.seat1"
