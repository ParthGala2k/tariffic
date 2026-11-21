"""The Phase 1 pass criteria, end to end, with a fake LLM client."""
from __future__ import annotations

from decimal import Decimal

from desk import agent, gateway, llm, personas
from desk.__main__ import POSTED_HEADER, TASK
from desk.scripted_seat1 import script
from desk.tools import SEAT_TOOLS

from conftest import BANK_TXN, events

JE3 = {
    "Assets:Bank:Increase:Operating": Decimal("14317.00"),
    "Assets:Receivable:TariffRefund": Decimal("-13900.00"),
    "Income:Interest:TariffRefund": Decimal("-417.00"),
}


def run_flow(conn, item, cfg):
    client = llm.FakeLLM(script=script())
    run = agent.run_seat(conn, "seat1", item["id"], client,
                         TASK.format(bank_txn=BANK_TXN), cfg=cfg)
    assert run.state == "PROPOSED", run.state
    reviewed = personas.ScriptedController.load(cfg).review(conn, item["id"])
    assert reviewed["state"] == "APPROVED"
    result = gateway.post(conn, item["id"], cfg=cfg)
    return run, result


def test_phase1_posts_exactly_je3_and_bean_check_passes(conn, item, cfg, clean_books):
    run, result = run_flow(conn, item, cfg)

    assert result["item"]["state"] == "POSTED"
    assert result["item"]["proposed_by"] == "ai_agent.seat1"
    assert result["item"]["approved_by"] == "human_controller"

    text = clean_books.read_text()
    assert text.startswith(POSTED_HEADER)
    assert text.count("2025-10-15 *") == 1

    postings = {}
    for line in result["entry"].splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == "USD" and parts[0].count(":") >= 2:
            postings[parts[0]] = Decimal(parts[1].replace(",", ""))
    assert postings == JE3
    assert sum(postings.values()) == Decimal("0.00")

    assert 'work_item: "' + item["id"] + '"' in result["entry"]
    assert 'approved_by: "human_controller"' in result["entry"]
    assert gateway.bean_check(cfg).ok


def test_the_whole_chain_is_in_the_audit_log(conn, item, cfg):
    run_flow(conn, item, cfg)
    actions = [e["action"] for e in events(conn, subject=item["id"])]
    for expected in ("create_work_item", "agent:start", "tool:match_deposit",
                     "propose", "approve", "post", "agent:finish"):
        assert expected in actions, f"{expected} missing from {actions}"
    assert [e["actor"] for e in events(conn, action="post", subject=item["id"])] == ["gateway"]


def test_evidence_cites_the_notice_and_the_bank_transaction(conn, item, cfg):
    _, result = run_flow(conn, item, cfg)
    evidence = " ".join(result["item"]["evidence"])
    assert "2025-10-14-cbp-refund-notice.md" in evidence
    assert BANK_TXN in evidence


def test_match_deposit_does_the_arithmetic(conn, cfg):
    from desk.tools import Ctx
    from desk.tools.compute import match_deposit

    matched = match_deposit(Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1", cfg=cfg),
                            bank_txn_id=BANK_TXN)
    assert matched["refund_id"] == "RFD-001"
    assert matched["principal"] == "13900.00"
    assert matched["interest"] == "417.00"
    assert matched["total"] == "14317.00"
    assert matched["entry_count"] == 6


def test_seat1_tool_set_is_exactly_what_the_handover_gives_it():
    """Seat 1's read / compute / work tools, and nothing from another seat."""
    assert SEAT_TOOLS["seat1"] == (
        "run_sql", "read_doc", "get_policies",
        "match_deposit", "sum_duties", "reconcile_duty_paid", "breakdown_receivable",
        "propose", "ask", "propose_policy", "apply_policy",
    )
    assert "split_refund" not in SEAT_TOOLS["seat1"]          # Seat 2's tool
    assert "surcharge_on_invoices" not in SEAT_TOOLS["seat1"]  # Seat 3's tool


def test_rerunning_the_agent_hits_the_response_cache(cache, cfg):
    """The cache makes reruns repeatable: the same request is never asked twice."""
    inner = llm.FakeLLM(script=script())
    cached = llm.CachingClient(inner, cache)
    first = cached.complete("sys", [{"role": "user", "content": "hello"}], [])
    second = cached.complete("sys", [{"role": "user", "content": "hello"}], [])
    assert (cached.misses, cached.hits) == (1, 1)
    assert first.to_dict() == second.to_dict()
