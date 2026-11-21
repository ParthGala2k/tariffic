"""Every seat gets exactly the tools in its jurisdiction (CLAUDE.md section 5),
and nothing outside it runs, however it is asked for."""
from __future__ import annotations

import pytest

from desk.tools import FORBIDDEN_TOOL_NAMES, REGISTRY, SEAT_TOOLS, Ctx, dispatch, schemas_for

from conftest import BANK_TXN, REFUND, events

# The handover's seat table, by tool kind.
EXPECTED = {
    "seat1": {
        "read": {"run_sql", "read_doc", "get_policies"},
        "compute": {"match_deposit", "sum_duties", "reconcile_duty_paid", "breakdown_receivable"},
        "work": {"propose", "ask", "propose_policy", "apply_policy"},
    },
    "seat2": {
        "read": {"run_sql", "read_doc", "get_policies"},
        "compute": {"split_refund"},
        "work": {"propose", "ask", "propose_policy", "apply_policy"},
    },
    "seat3": {
        "read": {"run_sql", "read_doc", "get_policies", "search_precedents"},
        "compute": {"surcharge_on_invoices"},
        "work": {"propose", "ask", "apply_policy", "revise_proposal"},
    },
}

# Minimal valid arguments for every read and compute tool.
ARGS = {
    "run_sql": {"sql": "SELECT entry_no FROM golden_cbp_entries LIMIT 1"},
    "read_doc": {"path": "data/reference/refund_policy.md"},
    "get_policies": {},
    "search_precedents": {"query": ""},
    "match_deposit": {"bank_txn_id": BANK_TXN},
    "sum_duties": {"entry_no": "BRK-2500101-1"},
    "reconcile_duty_paid": {},
    "breakdown_receivable": {},
    "split_refund": {"refund_id": REFUND},
    "surcharge_on_invoices": {"customer_id": "CUST-001", "refund_id": REFUND},
}

OUTSIDE = [(seat, tool) for seat in SEAT_TOOLS for tool in REGISTRY if tool not in SEAT_TOOLS[seat]]
INSIDE = [(seat, tool) for seat in SEAT_TOOLS for tool in SEAT_TOOLS[seat]
          if REGISTRY[tool].kind != "work"]


def test_every_seat_has_exactly_the_handover_tools():
    for seat, by_kind in EXPECTED.items():
        actual = {"read": set(), "compute": set(), "work": set()}
        for name in SEAT_TOOLS[seat]:
            actual[REGISTRY[name].kind].add(name)
        assert actual == by_kind, seat
        assert [t["name"] for t in schemas_for(seat)] == list(SEAT_TOOLS[seat])


def test_no_agent_tool_can_post_approve_or_reject():
    assert not FORBIDDEN_TOOL_NAMES & set(REGISTRY)
    for seat, names in SEAT_TOOLS.items():
        assert not FORBIDDEN_TOOL_NAMES & set(names), seat
    assert {"post", "approve", "reject", "approve_policy"} <= FORBIDDEN_TOOL_NAMES


def test_the_three_seats_do_not_share_their_specialist_tools():
    specialist = {seat: EXPECTED[seat]["compute"] for seat in EXPECTED}
    for a in specialist:
        for b in specialist:
            if a != b:
                assert not specialist[a] & specialist[b], (a, b)


@pytest.mark.parametrize("seat,tool", OUTSIDE, ids=[f"{s}-{t}" for s, t in OUTSIDE])
def test_a_tool_outside_the_jurisdiction_is_refused_and_logged(conn, item, cfg, seat, tool):
    ctx = Ctx(conn=conn, actor=f"ai_agent.{seat}", seat=seat, work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, tool, ARGS.get(tool, {}))
    assert ok is False
    assert content.startswith("refused:") and tool in content
    refused = events(conn, action=f"tool:{tool}", subject=item["id"], allowed=False)
    assert refused and refused[-1]["actor"] == f"ai_agent.{seat}"


@pytest.mark.parametrize("seat,tool", INSIDE, ids=[f"{s}-{t}" for s, t in INSIDE])
def test_read_and_compute_tools_inside_the_jurisdiction_run(conn, item, cfg, onboarded,
                                                            seat, tool):
    ctx = Ctx(conn=conn, actor=f"ai_agent.{seat}", seat=seat, work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, tool, ARGS[tool])
    assert ok is True, content
    ran = events(conn, action=f"tool:{tool}", subject=item["id"], allowed=True)
    assert ran and ran[-1]["actor"] == f"ai_agent.{seat}"


def test_an_unknown_seat_has_no_tools(conn, item, cfg):
    ctx = Ctx(conn=conn, actor="ai_agent.seat9", seat="seat9", work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "run_sql", ARGS["run_sql"])
    assert ok is False and "seat9" in content
    with pytest.raises(KeyError):
        schemas_for("seat9")
