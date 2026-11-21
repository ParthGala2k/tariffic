"""The brief is built by code from the log, and it says what the human must decide."""
from __future__ import annotations

import json
from decimal import Decimal

from desk import agent, gateway, llm, personas
from desk.__main__ import TASK
from desk.agent import run_seat
from desk.brief import brief, classify_ref
from desk.scripted import segment
from desk.scripted_seat1 import script

from conftest import BANK_TXN


def run_seat1(conn, item, cfg):
    return agent.run_seat(conn, "seat1", item["id"], llm.FakeLLM(script=script()),
                          TASK.format(bank_txn=BANK_TXN), cfg=cfg)


def test_brief_of_an_unknown_item_is_none(conn, cfg):
    assert brief(conn, "WI-9999", cfg) is None


def test_brief_for_a_proposed_deposit_entry(conn, item, cfg):
    run_seat1(conn, item, cfg)
    b = brief(conn, item["id"], cfg)

    assert b["headline"].startswith("Record the refund deposit") and "14,317.00" in b["headline"]
    assert b["decision_needed"].startswith("Approve or reject this journal entry")
    assert b["work_item"]["state"] == "PROPOSED"

    postings = b["proposal"]["postings"]
    assert len(postings) >= 2
    assert sum(Decimal(p["amount"]) for p in postings) == Decimal("0.00")
    assert {p["side"] for p in postings} == {"debit", "credit"}
    assert b["proposal"]["policy"]["ref"] == "POL-001@1"
    assert b["proposal"]["policy"]["status"] == "APPROVED"
    assert b["proposal"]["entry_text"] and "(pending approval)" in b["proposal"]["entry_text"]

    documents = [e for e in b["evidence"] if e["kind"] == "document"]
    assert documents and all(e["exists"] for e in documents)
    assert any("refund-notice" in e["ref"] for e in documents)

    assert any(c["tool"] == "match_deposit" for c in b["computations"])
    assert b["agent_turns"] >= 1
    assert any("did no arithmetic" in p for p in b["key_points"])
    assert any("Cites" in p for p in b["key_points"])
    assert all(not t["action"].startswith("can:") for t in b["timeline"])
    json.dumps(b, default=str)                      # it must travel over the API


def test_brief_after_posting_has_nothing_left_to_decide(conn, item, cfg, clean_books):
    run_seat1(conn, item, cfg)
    personas.ScriptedController.load(cfg).review(conn, item["id"])
    gateway.post(conn, item["id"], cfg=cfg)
    b = brief(conn, item["id"], cfg)
    assert b["decision_needed"].startswith("Nothing: posted")
    assert b["work_item"]["approved_by"] == "human_controller"
    assert "post" in [t["action"] for t in b["timeline"]]
    assert b["invariant_gate"] and b["invariant_gate"]["allowed"] is True
    assert any("Invariant gate passed" in p for p in b["key_points"])


def test_brief_shows_the_question_the_answer_and_the_precedent(conn, cfg, onboarded,
                                                               customer_item):
    item = customer_item("CUST-002", kind="WI-CUST-002-QUESTION")
    run_seat(conn, "seat3", item["id"], llm.FakeLLM(script=segment("harbor_question")),
             "Decide what Harbor Roasters is owed", cfg=cfg)
    b = brief(conn, item["id"], cfg)

    assert b["work_item"]["state"] == "NEEDS_INFO"
    assert b["questions"] and b["questions"][0]["to_role"] == "human_lawyer"
    assert b["questions"][0]["state"] == "ANSWERED"
    assert b["precedents"] and b["precedents"][0]["decided_by"] == "human_lawyer"
    assert any(p.startswith("Asked human_lawyer") for p in b["key_points"])
    assert any(p.startswith("Precedent on file") for p in b["key_points"])
    assert "resumed" in b["decision_needed"]
    assert any("data/documents/contracts/CON-002" in s for s in b["sources_read"])


def test_evidence_refs_are_classified(cfg):
    assert classify_ref("data/reference/refund_policy.md", cfg) == "document"
    assert classify_ref("data/documents/inbox/2025-10-14-cbp-refund-notice.md", cfg) == "document"
    assert classify_ref("work_item:WI-0001", cfg) == "work_item"
    assert classify_ref("POL-001@1", cfg) == "policy"
    assert classify_ref("data/views/customs/cbp_entries.json#BRK-2500101-1", cfg) == "source_row"
    assert classify_ref("INV-1003", cfg) == "identifier"
    assert classify_ref("data/truth/world.json", cfg) != "document"     # never citable
