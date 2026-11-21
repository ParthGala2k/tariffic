"""One work item, one cause: each entry must settle its own ticket, for its own amount."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from desk import invariants, llm
from desk.agent import run_seat
from desk.scripted import seat1_forecast
from desk.tools import Ctx, dispatch
from desk.tools.work import CAUSE_RULES
from desk.workflow import get_item

from conftest import REFUND, events

POLICY = "data/reference/refund_policy.md"


@pytest.fixture
def opened(conn, cfg, onboarded):
    """The two tickets the invariants open, with their own amounts on them.

    Onboarding runs first, so POL-L1 exists and the seats can cite it.
    """
    items = invariants.scan_and_open_work_items(conn, cfg)
    return {item["kind"]: item for item in items}


def _propose(conn, cfg, item, postings, *, seat="seat1"):
    ctx = Ctx(conn=conn, actor=f"ai_agent.{seat}", seat=seat,
              work_item_id=item["id"], cfg=cfg)
    return dispatch(ctx, "propose", {
        "rationale": "test",
        "journal_entry": {
            "date": "2025-10-15", "narration": "test",
            "postings": [{"account": a, "amount": m, "currency": "USD"} for a, m in postings],
        },
        "policy_ref": "POL-001@1",
        "evidence": [POLICY],
    })


# --------------------------------------------------------------------------- #
# the tickets carry their own cause's amount
# --------------------------------------------------------------------------- #

def test_the_forecast_ticket_carries_its_own_amount(opened):
    assert opened["WI-FORECAST-CORRECTION"]["payload"]["magnitude"] == "10500.00"


def test_forecast_correction_accepts_its_own_amount(conn, cfg, opened):
    ok, content = _propose(conn, cfg, opened["WI-FORECAST-CORRECTION"], [
        ("Expenses:COGS:Tariff", "10500.00"),
        ("Assets:Receivable:TariffRefund", "-10500.00"),
    ])
    assert ok, content
    assert get_item(conn, opened["WI-FORECAST-CORRECTION"]["id"])["state"] == "PROPOSED"


def test_a_forecast_correction_for_the_wrong_amount_is_refused(conn, cfg, opened):
    """The ticket carries the amount the invariant found; the entry must match it."""
    item = opened["WI-FORECAST-CORRECTION"]
    ok, content = _propose(conn, cfg, item, [
        ("Expenses:COGS:Tariff", "10800.00"),
        ("Assets:Receivable:TariffRefund", "-10800.00"),
    ])
    assert not ok
    assert "10500.00" in content and "-10800.00" in content
    assert "INV-2" in content
    assert "breakdown_receivable" in content
    assert get_item(conn, item["id"])["state"] == "NEW"
    assert events(conn, action="tool:propose", subject=item["id"], allowed=False)


def test_a_short_forecast_correction_is_refused_too(conn, cfg, opened):
    ok, content = _propose(conn, cfg, opened["WI-FORECAST-CORRECTION"], [
        ("Expenses:COGS:Tariff", "10000.00"),
        ("Assets:Receivable:TariffRefund", "-10000.00"),
    ])
    assert not ok and "must move exactly -10500.00" in content


def test_a_forecast_correction_that_misses_the_receivable_is_refused(conn, cfg, opened):
    ok, content = _propose(conn, cfg, opened["WI-FORECAST-CORRECTION"], [
        ("Expenses:COGS:Tariff", "10500.00"),
        ("Assets:Inventory:Parts", "-10500.00"),
    ])
    assert not ok
    assert "does not touch Assets:Receivable:TariffRefund at all" in content


# --------------------------------------------------------------------------- #
# a work item may not do another work item's job
# --------------------------------------------------------------------------- #



# --------------------------------------------------------------------------- #
# the check only applies where a cause is recorded
# --------------------------------------------------------------------------- #

def test_a_ticket_with_no_recorded_cause_is_not_constrained(conn, cfg, item):
    """The deposit ticket is not opened by an invariant, so no cause rule applies."""
    assert item["kind"] not in CAUSE_RULES
    ok, content = _propose(conn, cfg, item, [
        ("Assets:Bank:Increase:Operating", "14317.00"),
        ("Assets:Receivable:TariffRefund", "-13900.00"),
        ("Income:Interest:TariffRefund", "-417.00"),
    ])
    assert ok, content


def test_breakdown_receivable_is_available_to_seat1(conn, cfg, item):
    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "breakdown_receivable", {})
    assert ok
    result = json.loads(content)
    assert result["by_cause"] == {"non_refundable_duty": "10500.00"}
    assert result["excess"] == "10500.00"
    assert "WI-FORECAST-CORRECTION" in result["note"]


def test_breakdown_receivable_is_not_seat2s(conn, cfg, item):
    from desk.tools import SEAT_TOOLS

    assert "breakdown_receivable" not in SEAT_TOOLS["seat2"]
    ctx = Ctx(conn=conn, actor="ai_agent.seat2", seat="seat2",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "breakdown_receivable", {})
    assert not ok and "has no tool" in content


# --------------------------------------------------------------------------- #
# the scripted run produces the key's decomposition
# --------------------------------------------------------------------------- #

def test_the_scripted_seat_produces_the_forecast_correction_at_10500(conn, cfg, opened):
    forecast = opened["WI-FORECAST-CORRECTION"]
    run_seat(conn, "seat1", forecast["id"],
             llm.FakeLLM(script=seat1_forecast()), "fix the forecast", cfg=cfg)
    item = get_item(conn, forecast["id"])
    assert item["state"] == "PROPOSED"
    postings = {p["account"]: Decimal(p["amount"])
                for p in item["payload"]["journal_entry"]["postings"]}
    assert postings == {"Expenses:COGS:Tariff": Decimal("10500.00"),
                        "Assets:Receivable:TariffRefund": Decimal("-10500.00")}

# --------------------------------------------------------------------------- #
# a work item may not do another work item's job
# --------------------------------------------------------------------------- #

def test_the_deposit_entry_may_not_also_do_the_split(conn, cfg, item):
    """What the live model tried: folding the refund split into the deposit."""
    ok, content = _propose(conn, cfg, item, [
        ("Assets:Bank:Increase:Operating", "14317.00"),
        ("Assets:Receivable:TariffRefund", "-13900.00"),
        ("Income:Interest:TariffRefund", "-417.00"),
        ("Expenses:COGS:Tariff", "13132.00"),
        ("Assets:Inventory:Parts", "-13132.00"),
    ])
    assert not ok
    assert "WI-REFUND-SPLIT" in content
    assert "Assets:Inventory:Parts" in content
    assert get_item(conn, item["id"])["state"] == "NEW"


def test_the_deposit_entry_on_its_own_is_fine(conn, cfg, item):
    ok, content = _propose(conn, cfg, item, [
        ("Assets:Bank:Increase:Operating", "14317.00"),
        ("Assets:Receivable:TariffRefund", "-13900.00"),
        ("Income:Interest:TariffRefund", "-417.00"),
    ])
    assert ok, content


def test_the_split_may_not_touch_the_cash_or_the_receivable(conn, cfg):
    from desk.workflow import create_work_item

    split = create_work_item(conn, kind="WI-REFUND-SPLIT", seat="seat2", actor="sync",
                             payload={"refund_id": REFUND}, evidence=["x"])
    ok, content = _propose(conn, cfg, split, [
        ("Assets:Receivable:TariffRefund", "13900.00"),
        ("Expenses:COGS:Tariff", "-768.00"),
        ("Assets:Inventory:Parts", "-13132.00"),
    ], seat="seat2")
    assert not ok
    assert "already cleared by the deposit work item" in content


def test_a_customer_credit_may_not_touch_the_refund_receivable(conn, cfg, customer_item,
                                                                onboarded):
    bean = customer_item("CUST-001", kind="WI-CUST-001-CREDIT")
    ok, content = _propose(conn, cfg, bean, [
        ("Assets:Receivable:TariffRefund", "1632.00"),
        ("Liabilities:CustomerCredits", "-1632.00"),
    ], seat="seat3")
    assert not ok
    assert "nothing to do with the refund receivable" in content
