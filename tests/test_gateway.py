"""Only the gateway posts, and only APPROVED items."""
from __future__ import annotations

import pytest

from desk import gateway, llm, personas
from desk.agent import run_seat
from desk.__main__ import POSTED_HEADER, TASK
from desk.perms import Denied
from desk.scripted_seat1 import script
from desk.tools import FORBIDDEN_TOOL_NAMES, REGISTRY, SEAT_TOOLS, Ctx, dispatch, schemas_for
from desk.workflow import transition

from conftest import BANK_TXN, events

BAD_ENTRY = {
    "date": "2025-10-15",
    "narration": "Entry bean-check will reject",
    "postings": [
        {"account": "Assets:Bank:Nonexistent", "amount": "14317.00", "currency": "USD"},
        {"account": "Assets:Receivable:TariffRefund", "amount": "-14317.00", "currency": "USD"},
    ],
}


def approved_item(conn, item, cfg, journal_entry=None):
    if journal_entry is None:
        client = llm.FakeLLM(script=script())
        run_seat(conn, "seat1", item["id"], client, TASK.format(bank_txn=BANK_TXN), cfg=cfg)
    else:
        transition(conn, item["id"], "propose", "ai_agent.seat1",
                   patch={"payload": {"journal_entry": journal_entry}, "evidence": ["test"]})
    return personas.ScriptedController.load(cfg).review(conn, item["id"])


def test_no_seat_has_a_post_or_approve_tool():
    for seat, names in SEAT_TOOLS.items():
        assert not (set(names) & FORBIDDEN_TOOL_NAMES), seat
        assert not ({t["name"] for t in schemas_for(seat)} & FORBIDDEN_TOOL_NAMES), seat
    assert not (set(REGISTRY) & FORBIDDEN_TOOL_NAMES)


def test_the_agent_cannot_reach_post_through_the_tool_loop(conn, item, cfg):
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "post", {"work_item": item["id"]})
    assert not ok
    assert "has no tool 'post'" in content
    assert events(conn, action="tool:post", subject=item["id"], allowed=False)


def test_a_non_gateway_actor_calling_post_directly_is_refused_and_logged(conn, item, cfg, clean_books):
    approved_item(conn, item, cfg)
    for actor in ("ai_agent.seat1", "human_controller", "sync"):
        with pytest.raises(Denied, match="only the gateway may post"):
            gateway.post(conn, item["id"], actor=actor, cfg=cfg)

    refused = events(conn, action="can:post", subject=item["id"], allowed=False)
    assert {e["actor"] for e in refused} == {"ai_agent.seat1", "human_controller", "sync"}
    assert clean_books.read_text() == POSTED_HEADER
    assert conn.execute("SELECT state FROM work_items WHERE id=?",
                        (item["id"],)).fetchone()[0] == "APPROVED"


def test_posting_a_non_approved_item_is_refused(conn, item, cfg, clean_books):
    # NEW
    with pytest.raises(Denied, match="posts APPROVED items only"):
        gateway.post(conn, item["id"], cfg=cfg)

    # PROPOSED
    client = llm.FakeLLM(script=script())
    run_seat(conn, "seat1", item["id"], client, TASK.format(bank_txn=BANK_TXN), cfg=cfg)
    with pytest.raises(Denied, match="posts APPROVED items only"):
        gateway.post(conn, item["id"], cfg=cfg)

    # POSTED: no double post
    personas.ScriptedController.load(cfg).review(conn, item["id"])
    gateway.post(conn, item["id"], cfg=cfg)
    with pytest.raises(Denied, match="posts APPROVED items only"):
        gateway.post(conn, item["id"], cfg=cfg)

    assert clean_books.read_text().count("2025-10-15 *") == 1
    refused = events(conn, action="post", subject=item["id"], allowed=False)
    assert len(refused) == 3


def test_bean_check_failure_removes_the_entry_and_leaves_the_item_approved(conn, item, cfg, clean_books):
    approved_item(conn, item, cfg, journal_entry=BAD_ENTRY)
    before = clean_books.read_text()

    with pytest.raises(gateway.PostFailed, match="bean-check failed"):
        gateway.post(conn, item["id"], cfg=cfg)

    assert clean_books.read_text() == before == POSTED_HEADER
    assert conn.execute("SELECT state FROM work_items WHERE id=?",
                        (item["id"],)).fetchone()[0] == "APPROVED"
    refused = events(conn, action="post", subject=item["id"], allowed=False)
    assert len(refused) == 1 and "bean-check failed" in refused[0]["reason"]
    assert gateway.bean_check(cfg).ok


def test_unbalanced_postings_never_reach_the_books(conn, item, cfg, clean_books):
    unbalanced = {**BAD_ENTRY, "postings": [
        {"account": "Assets:Bank:Increase:Operating", "amount": "14317.00"},
        {"account": "Assets:Receivable:TariffRefund", "amount": "-13900.00"},
    ]}
    approved_item(conn, item, cfg, journal_entry=unbalanced)
    with pytest.raises(gateway.PostFailed, match="do not balance"):
        gateway.post(conn, item["id"], cfg=cfg)
    assert clean_books.read_text() == POSTED_HEADER


def test_propose_refuses_an_unbalanced_entry_before_it_becomes_a_proposal(conn, item, cfg):
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "propose", {
        "rationale": "deliberately wrong",
        "journal_entry": {
            "date": "2025-10-15", "narration": "wrong",
            "postings": [
                {"account": "Assets:Bank:Increase:Operating", "amount": "14317.00"},
                {"account": "Assets:Receivable:TariffRefund", "amount": "-13900.00"},
            ],
        },
        "policy_ref": "POL-001@1",
        "evidence": ["data/reference/refund_policy.md"],
    })
    assert not ok and "do not balance" in content
    assert conn.execute("SELECT state FROM work_items WHERE id=?",
                        (item["id"],)).fetchone()[0] == "NEW"


def test_propose_refuses_a_proposal_with_no_evidence(conn, item, cfg):
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "propose", {
        "rationale": "no sources",
        "journal_entry": {
            "date": "2025-10-15", "narration": "x",
            "postings": [
                {"account": "Assets:Bank:Increase:Operating", "amount": "1.00"},
                {"account": "Income:Interest:TariffRefund", "amount": "-1.00"},
            ],
        },
        "policy_ref": "POL-001@1",
        "evidence": [],
    })
    assert not ok and "cite a source" in content


def test_propose_refuses_evidence_that_is_only_golden_raw_refs(conn, item, cfg):
    """Phase 1 fix 2: a raw_ref says where a number was copied from, not its source."""
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "propose", {
        "rationale": "views only",
        "journal_entry": {
            "date": "2025-10-15", "narration": "x",
            "postings": [
                {"account": "Assets:Bank:Increase:Operating", "amount": "1.00"},
                {"account": "Income:Interest:TariffRefund", "amount": "-1.00"},
            ],
        },
        "policy_ref": "POL-001@1",
        "evidence": ["data/views/bank/transactions.csv#BNK-0026", "BNK-0026"],
    })
    assert not ok and "no source document" in content


def test_propose_refuses_a_policy_that_is_not_approved(conn, item, cfg):
    from desk import policies

    policies.store(conn, policy_id="POL-L9", name="not yet approved",
                   rule={"kind": "program_refundability", "refundable": ["IEEPA_CN"],
                         "not_refundable": []},
                   status=policies.PROPOSED, evidence=["data/reference/refund_policy.md"],
                   evidence_count=1, reason="pending", actor="ai_agent.seat1")
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "propose", {
        "rationale": "resting on an unapproved rule",
        "journal_entry": {
            "date": "2025-10-15", "narration": "x",
            "postings": [
                {"account": "Assets:Bank:Increase:Operating", "amount": "1.00"},
                {"account": "Income:Interest:TariffRefund", "amount": "-1.00"},
            ],
        },
        "policy_ref": "POL-L9@1",
        "evidence": ["data/reference/refund_policy.md"],
    })
    assert not ok and "not APPROVED" in content


def test_propose_refuses_an_entry_dated_before_a_balance_assertion(conn, item, cfg):
    """The books assert balances at 2025-10-01; a correction cannot predate that.

    Caught at proposal time with an explanation, rather than by the gateway
    rolling the write back after the controller has already approved it.
    """
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "propose", {
        "rationale": "dated into the closed period",
        "journal_entry": {
            "date": "2025-09-30", "narration": "rewrite history",
            "postings": [
                {"account": "Assets:Bank:Increase:Operating", "amount": "14317.00"},
                {"account": "Assets:Receivable:TariffRefund", "amount": "-13900.00"},
                {"account": "Income:Interest:TariffRefund", "amount": "-417.00"},
            ],
        },
        "policy_ref": "POL-001@1",
        "evidence": ["data/reference/refund_policy.md"],
    })
    assert not ok
    assert "balance assertions" in content
    assert "2025-10-01" in content
    assert conn.execute("SELECT state FROM work_items WHERE id=?",
                        (item["id"],)).fetchone()[0] == "NEW"


def test_the_same_entry_dated_after_the_assertion_is_accepted(conn, item, cfg):
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, _ = dispatch(ctx, "propose", {
        "rationale": "dated on the working day",
        "journal_entry": {
            "date": "2025-10-14", "narration": "record the deposit",
            "postings": [
                {"account": "Assets:Bank:Increase:Operating", "amount": "14317.00"},
                {"account": "Assets:Receivable:TariffRefund", "amount": "-13900.00"},
                {"account": "Income:Interest:TariffRefund", "amount": "-417.00"},
            ],
        },
        "policy_ref": "POL-001@1",
        "evidence": ["data/reference/refund_policy.md"],
    })
    assert ok
    assert conn.execute("SELECT state FROM work_items WHERE id=?",
                        (item["id"],)).fetchone()[0] == "PROPOSED"


def test_a_proposal_cannot_set_the_gateways_own_metadata(conn, item, cfg):
    """work_item and approved_by are the gateway's attestation, not the seat's to write."""
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    for forged in ({"work_item": "WI-REFUND-DEPOSIT"},
                   {"approved_by": "human_controller"},
                   {"work_item": "WI-9999", "approved_by": "human_controller"}):
        ok, content = dispatch(ctx, "propose", {
            "rationale": "forging the audit stamp",
            "journal_entry": {
                "date": "2025-10-15", "narration": "x", "metadata": forged,
                "postings": [
                    {"account": "Assets:Bank:Increase:Operating", "amount": "1.00"},
                    {"account": "Income:Interest:TariffRefund", "amount": "-1.00"},
                ],
            },
            "policy_ref": "POL-001@1",
            "evidence": ["data/reference/refund_policy.md"],
        })
        assert not ok, forged
        assert "metadata may not set" in content
        assert "gateway" in content


def test_the_gateway_stamps_win_even_if_the_payload_carries_them(conn, item, cfg, clean_books):
    """Belt and braces: the gateway overrides, in case a payload gets there another way."""
    from desk import personas
    from desk.workflow import transition

    payload = {
        "journal_entry": {
            "date": "2025-10-15", "narration": "forged stamps",
            "metadata": {"work_item": "WI-FORECAST-CORRECTION",
                         "approved_by": "ai_agent.seat1",
                         "note": "ordinary metadata"},
            "postings": [
                {"account": "Assets:Bank:Increase:Operating", "amount": "1.00"},
                {"account": "Income:Interest:TariffRefund", "amount": "-1.00"},
            ],
        },
        "policy_ref": "POL-001@1",
    }
    transition(conn, item["id"], "propose", "ai_agent.seat1",
               patch={"payload": payload, "evidence": ["x"]})
    personas.ScriptedController.load(cfg).review(conn, item["id"])
    result = gateway.post(conn, item["id"], cfg=cfg)

    assert f'work_item: "{item["id"]}"' in result["entry"]
    assert 'approved_by: "human_controller"' in result["entry"]
    assert "WI-FORECAST-CORRECTION" not in result["entry"]
    assert 'approved_by: "ai_agent.seat1"' not in result["entry"]
    assert 'note: "ordinary metadata"' in result["entry"]   # ordinary metadata survives
    # exactly one of each stamp
    assert result["entry"].count("work_item:") == 1
    assert result["entry"].count("approved_by:") == 1
