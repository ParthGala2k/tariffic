"""The API can switch onto a scenario's sandbox, work it in the background, and
tell the CFO who has to act."""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from desk.api import app
from desk.config import CFG
from desk.runs import MANAGER


@pytest.fixture
def client(clean_books):
    offline = dataclasses.replace(CFG, openai_api_key="", anthropic_api_key="", creao_keys={}, creao_dry_run=False)
    app.state.cfg = offline
    app.state.scenario = None
    with TestClient(app) as c:
        yield c
    try:
        c.post("/scenarios/deactivate")
    finally:
        app.state.cfg = CFG
        app.state.scenario = None


def test_scenarios_are_listed(client):
    body = client.get("/scenarios").json()
    names = {s["name"] for s in body["scenarios"]}
    assert {"rosy-baseline", "rosy-pending-refund"} <= names
    rosy = next(s for s in body["scenarios"] if s["name"] == "rosy-baseline")
    assert rosy["steps"][0] == "onboarding" and "question:CUST-002" in rosy["steps"]
    assert body["active"] is None
    assert client.get("/scenarios/active").json() == {"active": None}
    assert client.post("/scenarios/nope/activate").status_code == 404


def test_activate_run_and_attention(client):
    activated = client.post("/scenarios/rosy-baseline/activate").json()
    assert activated["active"] == "rosy-baseline"
    assert activated["report"]["before"]["passed"] is True
    assert ".sandbox" in activated["sandbox"]

    # the whole API now reads the sandbox: fresh log, golden tables synced there
    assert client.get("/scenarios/active").json()["active"] == "rosy-baseline"
    assert client.get("/golden/golden_cbp_entries").json()["total"] == 10
    assert client.get("/invariants").json()["count"] == 0          # the clean world
    assert client.post("/scenarios/rosy-pending-refund/run").status_code == 409   # not active

    started = client.post("/scenarios/rosy-baseline/run", json={"llm": "scripted"})
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    MANAGER.wait(run_id, timeout=240)
    run = client.get(f"/runs/{run_id}").json()
    assert run["state"] == "finished" and run["exit_code"] == 0, "\n".join(run["lines"][-20:])

    active = client.get("/scenarios/active").json()
    assert active["report"]["after"]["passed"] is True
    assert active["report"]["run"]["posted_entries"] == 3

    # the tracker's raw material: one row per step boundary
    feed = client.get("/events?limit=1000").json()["events"]
    steps = [e for e in feed if e["action"] == "run:step"]
    finished = [e["evidence"]["step"] for e in steps if e["evidence"]["status"] == "finished"]
    assert finished == ["onboarding", "invariants", "deposit", "split", "credit:CUST-001", "question:CUST-002"]
    assert any(e["action"] == "run:tie_outs" and e["allowed"] for e in feed)

    # who has to act, and what the desk settled on its own
    attention = client.get("/attention").json()
    assert attention["for_controller"] == []                       # scripted controller already decided
    assert [q["kind"] for q in attention["routed_to_lawyer"]] == ["WI-CUST-002-QUESTION"]
    assert attention["routed_to_lawyer"][0]["state"] == "ANSWERED"
    handled = {h["kind"] for h in attention["handled_on_its_own"]}
    assert {"WI-REFUND-DEPOSIT", "WI-REFUND-SPLIT", "WI-CUST-001-CREDIT"} <= handled
    assert attention["invariants"]["open"] == []
    assert any(r["ref"] == "RULE-001@1" and r["status"] == "PROPOSED" for r in attention["rules"])

    status = client.get("/status").json()
    assert status["seats"]["seat3"]["mode"] == "answered"           # asked, answered, not yet resumed

    done = client.post("/scenarios/deactivate").json()
    assert done == {"active": None, "was": "rosy-baseline"}
    assert client.get("/scenarios/active").json() == {"active": None}
