"""The work graph: a parent closes only once every child has settled."""
from __future__ import annotations

import pytest

from desk import gateway, personas
from desk.perms import Denied
from desk.workflow import (CLOSED, SETTLED, children, close_parent_if_settled,
                           create_work_item, get_item, transition, unsettled_children)

from conftest import events
from test_invariants import JE3, make_proposal


@pytest.fixture
def graph(conn):
    parent = create_work_item(conn, kind="WI-REFUND-EVENT", seat=None, actor="sync",
                               payload={"trigger": "bank_deposit"})
    child_a = create_work_item(conn, kind="WI-DUTY-RECONCILIATION", seat="seat1", actor="sync",
                                parent_id=parent["id"])
    child_b = create_work_item(conn, kind="WI-REFUND-DEPOSIT", seat="seat1", actor="sync",
                                parent_id=parent["id"])
    return parent, child_a, child_b


def test_children_are_linked_to_their_parent(conn, graph):
    parent, child_a, child_b = graph
    assert [c["id"] for c in children(conn, parent["id"])] == [child_a["id"], child_b["id"]]
    assert get_item(conn, child_a["id"])["parent_id"] == parent["id"]


def test_the_parent_will_not_close_while_a_child_is_open(conn, graph):
    parent, child_a, _ = graph
    assert close_parent_if_settled(conn, parent["id"]) is None
    assert get_item(conn, parent["id"])["state"] == "NEW"

    refused = events(conn, action="close", subject=parent["id"], allowed=False)
    assert len(refused) == 1
    assert child_a["id"] in refused[0]["reason"]


def test_closing_the_parent_directly_is_refused_too(conn, graph):
    """The gate lives in transition(), not just in the helper."""
    parent, _, _ = graph
    with pytest.raises(Denied, match="unfinished children"):
        transition(conn, parent["id"], "close", "sync")
    assert get_item(conn, parent["id"])["state"] == "NEW"


def test_the_parent_closes_once_every_child_settles(conn, cfg, graph, clean_books):
    parent, child_a, child_b = graph

    # one child rejected, one posted: both count as settled
    transition(conn, child_a["id"], "propose", "ai_agent.seat1",
               patch={"payload": {}, "evidence": ["x"]})
    transition(conn, child_a["id"], "reject", "human_controller", reason="not needed")
    assert get_item(conn, child_a["id"])["state"] == "REJECTED"

    assert close_parent_if_settled(conn, parent["id"]) is None   # child_b still open

    payload = make_proposal(conn, "WI-REFUND-DEPOSIT", JE3)
    transition(conn, child_b["id"], "propose", "ai_agent.seat1",
               patch={"payload": payload["payload"], "evidence": ["x"]})
    personas.ScriptedController.load(cfg).review(conn, child_b["id"])
    gateway.post(conn, child_b["id"], cfg=cfg)

    assert not unsettled_children(conn, parent["id"])
    closed = close_parent_if_settled(conn, parent["id"])
    assert closed["state"] == CLOSED
    assert set(closed["evidence"]) == {child_a["id"], child_b["id"]}


def test_closing_an_already_settled_parent_is_a_no_op(conn, graph):
    parent, child_a, child_b = graph
    for child in (child_a, child_b):
        transition(conn, child["id"], "close", "sync", reason="not needed")
    closed = close_parent_if_settled(conn, parent["id"])
    assert closed["state"] == CLOSED
    again = close_parent_if_settled(conn, parent["id"])
    assert again["state"] == CLOSED
    assert len(events(conn, action="close", subject=parent["id"], allowed=True)) == 1


def test_a_childless_parent_closes(conn):
    parent = create_work_item(conn, kind="WI-REFUND-EVENT", seat=None, actor="sync")
    assert close_parent_if_settled(conn, parent["id"])["state"] == CLOSED


def test_settled_is_what_the_handover_says():
    assert SETTLED == ("POSTED", "REJECTED", "CLOSED")


def test_a_refused_approval_goes_back_to_the_seat(conn, cfg):
    """The invariant gate refuses, the controller sends it back, the state machine allows it."""
    from desk import personas
    from desk.workflow import NEEDS_INFO, PROPOSED

    bad_split = {
        "refund_id": "RFD-001",
        "by_shipment": {"SHP-001": {"principal": "4000.00", "consumed": 12,
                                    "received": 100, "to_cogs": "999.00",
                                    "to_inventory": "3520.00"}},
        "to_cogs_total": "999.00", "to_inventory_total": "3520.00",
        "principal_total": "4000.00",
    }
    item = make_proposal(conn, "WI-REFUND-SPLIT", [
        ("Expenses:COGS:Tariff", "13132.00"),
        ("Assets:Inventory:Parts", "-13132.00"),
    ], bad_split, seat="seat2")

    with pytest.raises(Denied, match="INV-4"):
        personas.ScriptedController.load(cfg).review(conn, item["id"])
    assert get_item(conn, item["id"])["state"] == PROPOSED

    sent_back = transition(conn, item["id"], "revise", "human_controller",
                           reason="sent back to seat2: INV-4 split does not add up")
    assert sent_back["state"] == NEEDS_INFO

    # and the seat can propose again from there
    good = make_proposal.__wrapped__ if hasattr(make_proposal, "__wrapped__") else None
    again = transition(conn, item["id"], "propose", "ai_agent.seat2",
                       patch={"payload": {"journal_entry": {
                           "date": "2025-10-15", "narration": "corrected",
                           "postings": [
                               {"account": "Expenses:COGS:Tariff", "amount": "13132.00"},
                               {"account": "Assets:Inventory:Parts", "amount": "-13132.00"}]},
                           "policy_ref": "POL-001@1"}, "evidence": ["x"]})
    assert again["state"] == PROPOSED


def test_only_the_controller_may_send_a_proposal_back(conn, cfg):
    from desk.perms import can

    assert can("human_controller", "revise").allowed
    assert not can("ai_agent.seat1", "revise").allowed
    assert not can("gateway", "revise").allowed
