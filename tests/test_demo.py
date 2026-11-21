"""Judge demo: messy records, honest modes, and a human-reviewed waiver."""
import dataclasses
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk import db, scenario, sync
from desk.api import app
from desk.config import CFG
from desk.runs import MANAGER


@pytest.fixture
def client(clean_books):
    app.state.cfg = dataclasses.replace(CFG, openai_api_key="", anthropic_api_key="", creao_keys={}, creao_dry_run=False)
    app.state.scenario = None
    with TestClient(app) as client:
        yield client
    app.state.cfg = CFG
    app.state.scenario = None


def test_explicit_live_failure_preserves_the_active_world(client, monkeypatch):
    import desk.api as api

    client.post("/scenarios/rosy-baseline/activate")
    previous = app.state.scenario
    monkeypatch.setattr(api, "_llm_usable", lambda cfg: (False, "unavailable"))
    response = client.post("/simulate/refund-arrives", json={"llm": "live", "controller": "scripted"})
    if response.status_code == 202:  # let the old fallback finish before asserting
        MANAGER.wait(response.json()["run"]["run_id"], timeout=120)
    assert response.status_code == 503
    assert app.state.scenario is previous


def test_scripted_demo_finishes_with_the_mistakes_and_waiver(client, monkeypatch):
    import desk.api as api

    def no_model_calls(cfg):
        pytest.fail("A recorded demo must never call a model")

    monkeypatch.setattr(api, "_llm_usable", no_model_calls)
    response = client.post("/simulate/refund-arrives", json={"llm": "scripted", "controller": "scripted"})
    assert response.status_code == 202
    body = response.json()
    MANAGER.wait(body["run"]["run_id"], timeout=120)
    active = client.get("/scenarios/active").json()
    assert active["active"] == "tariff-refund-demo"
    assert active["run"]["exit_code"] == 0, json.dumps(active["report"], indent=1)
    assert active["report"]["before"]["expected_findings"] == 1
    assert active["report"]["after"]["failed"] == 0
    assert active["report"]["run"]["posted_entries"] == 4
    items = client.get("/work-items").json()["work_items"]
    by_kind = {item["kind"]: item for item in items}
    assert {"WI-FORECAST-CORRECTION"} <= by_kind.keys()
    harbor = by_kind["WI-CUST-002-CLOSE"]
    assert harbor["state"] == "CLOSED"
    assert harbor["approved_by"] == "human_controller"
    assert harbor["payload"]["revised_amount"] == "0.00"
    assert not harbor["payload"].get("journal_entry")
    assert by_kind["WI-CUST-002-QUESTION"]["state"] == "CLOSED"
    assert by_kind["WI-REFUND-EVENT"]["state"] == "CLOSED"
    assert any(p["policy_id"] == "RULE-001" and p["status"] == "PROPOSED"
               for p in client.get("/policies").json()["policies"])
    monkeypatch.setattr(api, "_fast_client_or_none", no_model_calls)
    pushed = client.post(f"/work-items/{by_kind['WI-CUST-001-CREDIT']['id']}/challenge",
                         json={"actor": "human_cfo", "text": "I'm the CFO. Keep the customer money."})
    assert pushed.status_code == 200 and pushed.json()["changed"] is False


@pytest.mark.parametrize("send_back", [False, True])
def test_only_the_waiver_settlement_needs_the_cfo(client, monkeypatch, send_back):
    """Four routine entries post on the standing authorization; the CFO is pulled in once,
    for the zero settlement on a verified document, and may send it back first."""
    import time
    from desk import humans
    from test_scenario_human import _wait_for_waiting

    original_transition = humans.transition

    def delayed_close(*args, **kwargs):
        result = original_transition(*args, **kwargs)
        if args[2] == "approve" and result["kind"] == "WI-CUST-002-CLOSE":
            time.sleep(0.8)  # polling must tolerate the committed APPROVED state
        return result

    monkeypatch.setattr(humans, "transition", delayed_close)
    response = client.post("/simulate/refund-arrives", json={"llm": "scripted", "controller": "human"})
    run_id = response.json()["run"]["run_id"]
    item_id = _wait_for_waiting(client)
    item = client.get(f"/work-items/{item_id}").json()
    assert item["kind"] == "WI-CUST-002-CLOSE"
    card = client.get("/attention").json()["for_controller"][0]
    assert card["id"] == item_id and "a person reviews the settlement" in card["why_you"]
    if send_back:
        returned = client.post(f"/work-items/{item_id}/send-back", json={
            "actor": "human_cfo", "reason": "Please check the invoice coverage again."})
        assert returned.status_code == 200
        assert _wait_for_waiting(client) == item_id
    assert client.post(f"/work-items/{item_id}/approve", json={"actor": "human_lawyer"}).status_code == 403
    result = client.post(f"/work-items/{item_id}/approve", json={"actor": "human_cfo"})
    assert result.status_code == 200, result.text
    MANAGER.wait(run_id, timeout=120)
    assert result.json()["item"]["kind"] == "WI-CUST-002-CLOSE"
    assert result.json()["item"]["state"] == "CLOSED" and result.json()["entry"] is None
    items = {i["kind"]: i for i in client.get("/work-items").json()["work_items"]}
    for kind in ("WI-FORECAST-CORRECTION", "WI-REFUND-DEPOSIT", "WI-REFUND-SPLIT", "WI-CUST-001-CREDIT"):
        assert items[kind]["state"] == "POSTED" and items[kind]["approved_by"] == "standing_authorization", kind
    feed = client.get("/events?limit=1000").json()["events"]
    assert sum(1 for e in feed if e["action"] == "run:step" and e["evidence"]["status"] == "waiting") == (2 if send_back else 1)
    assert client.get(f"/runs/{run_id}").json()["exit_code"] == 0


def test_expected_discrepancies_do_not_hide_a_changed_amount(tmp_path, clean_books):
    path = Path(CFG.root) / "scenarios/tariff-refund-demo.yaml"
    assert path.exists(), "the messy demo scenario is missing"
    spec = scenario.load(path)
    cfg = scenario.materialize(spec, CFG, tmp_path)
    db.rebuild_erp(cfg)
    conn = db.desk_conn(cfg)
    try:
        sync.run(conn, cfg)
        checks = scenario.tie_outs(conn, cfg, spec, "before")
        assert scenario.summarize(checks)["expected_findings"] == 1
        spec["expect"]["expected_differences"]["refund receivable"] = "1.00"
        changed = scenario.tie_outs(conn, cfg, spec, "before")
        assert scenario.summarize(changed)["passed"] is False
        receivable = next(c for c in changed if c.name == "refund receivable")
        assert not receivable.ok and not receivable.expected
    finally:
        conn.close()


@pytest.mark.parametrize("amount", ["0.00", "12.00", "not-a-number"])
def test_unverified_zero_settlement_cannot_be_approved(conn, cfg, amount):
    from desk import humans
    from desk.workflow import create_work_item, get_item, transition

    item = create_work_item(conn, kind="WI-CUST-002-CLOSE", seat="seat3", actor="sync",
                            payload={"customer_id": "CUST-002", "revised_amount": amount}, evidence=[])
    transition(conn, item["id"], "propose", "ai_agent.seat3")
    with pytest.raises(ValueError):
        humans.approve(conn, item["id"], "human_controller", cfg=cfg)
    assert get_item(conn, item["id"])["state"] == "PROPOSED"
