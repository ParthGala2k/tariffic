"""A run with a human at the screen: routine entries post on the CFO's standing
authorization, and the run pauses only where a person is genuinely needed."""
from __future__ import annotations

import dataclasses
import time

import pytest
from fastapi.testclient import TestClient

from desk import db
from desk.api import app
from desk.config import CFG
from desk.runs import MANAGER


@pytest.fixture
def client(clean_books):
    app.state.cfg = dataclasses.replace(CFG, openai_api_key="", anthropic_api_key="", creao_keys={}, creao_dry_run=False)
    app.state.scenario = None
    with TestClient(app) as c:
        yield c
    try:
        c.post("/scenarios/deactivate")
    finally:
        app.state.cfg = CFG
        app.state.scenario = None


def _wait_for_waiting(client, done=(), timeout=120):
    """Poll until a step is waiting on a person with an item still PROPOSED.

    `done` holds items already decided: a step stays "waiting" in the log for a
    moment after its item is posted, until the run marks it finished.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        feed = client.get("/events?limit=1000").json()["events"]
        waiting = [e for e in feed if e["action"] == "run:step" and e["evidence"]["status"] == "waiting"
                   and e["evidence"]["work_item"] not in done]
        for e in reversed(waiting):
            item = client.get(f"/work-items/{e['evidence']['work_item']}").json()
            if item["state"] == "PROPOSED":
                return item["id"]
        active = client.get("/scenarios/active").json()
        run = active.get("run") or {}
        if run.get("state") in ("finished", "failed"):
            detail = client.get(f"/runs/{run['run_id']}").json()
            tail = "; ".join(detail["lines"][-8:])
            raise AssertionError(
                f"the run ended {run['state']} before waiting on a person: {tail} "
                f"error={detail.get('error')} report.run={active.get('report', {}).get('run')}")
        time.sleep(0.3)
    raise AssertionError("no step waited for a person")


def _revoke_standing_authorization(client):
    """The sandbox's own desk DB: with POL-A1 revoked, every entry waits for the CFO."""
    cfg = app.state.scenario["cfg"]
    conn = db.desk_conn(cfg)
    try:
        conn.execute("UPDATE policies SET status = 'REVOKED' WHERE policy_id = 'POL-A1'")
    finally:
        conn.close()


def test_routine_entries_post_on_the_standing_authorization(client):
    client.post("/scenarios/rosy-baseline/activate")
    started = client.post("/scenarios/rosy-baseline/run", json={"llm": "scripted", "controller": "human"})
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    MANAGER.wait(run_id, timeout=180)
    run = client.get(f"/runs/{run_id}").json()
    assert run["state"] == "finished" and run["exit_code"] == 0, "\n".join(run["lines"][-20:])

    items = {i["kind"]: i for i in client.get("/work-items").json()["work_items"]}
    for kind in ("WI-REFUND-DEPOSIT", "WI-REFUND-SPLIT", "WI-CUST-001-CREDIT"):
        assert items[kind]["state"] == "POSTED", kind
        assert items[kind]["approved_by"] == "standing_authorization", kind
    feed = client.get("/events?limit=1000").json()["events"]
    assert sum(1 for e in feed if e["action"] == "run:step" and e["evidence"]["status"] == "waiting") == 0
    applied = [e for e in feed if e["action"] == "authorization:applied"]
    assert len(applied) == 3 and all(e["evidence"]["policy"] == "POL-A1@1" for e in applied)
    assert all("routine" in e["plain"] for e in applied)
    # the lane the CFO sees afterwards: handled on the desk's own authority, with the reason
    handled = client.get("/attention").json()["handled_on_its_own"]
    assert {h["kind"] for h in handled} >= {"WI-REFUND-DEPOSIT", "WI-REFUND-SPLIT", "WI-CUST-001-CREDIT"}
    assert all(h["under_authorization"] for h in handled if h["kind"] != "WI-CUST-002-QUESTION")
    assert client.get("/scenarios/active").json()["report"]["after"]["passed"] is True


def test_without_the_authorization_the_cfo_decides_each_entry(client):
    client.post("/scenarios/rosy-baseline/activate")
    _revoke_standing_authorization(client)
    started = client.post("/scenarios/rosy-baseline/run", json={"llm": "scripted", "controller": "human"})
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    decided, done = [], set()
    for _ in range(3):                       # deposit, split, Bean Street
        item_id = _wait_for_waiting(client, done)
        done.add(item_id)
        item = client.get(f"/work-items/{item_id}").json()
        assert item["state"] == "PROPOSED"
        card = client.get("/attention").json()["for_controller"][0]
        assert card["id"] == item_id and "no standing authorization" in card["why_you"]

        # the lawyer cannot approve; the CFO can, and the gateway posts
        assert client.post(f"/work-items/{item_id}/approve", json={"actor": "human_lawyer"}).status_code == 403
        approved = client.post(f"/work-items/{item_id}/approve",
                               json={"actor": "human_cfo", "reason": "reviewed on screen"}).json()
        assert approved["item"]["state"] == "POSTED" and approved["item"]["approved_by"] == "human_cfo"
        decided.append(item["kind"])

    MANAGER.wait(run_id, timeout=120)
    run = client.get(f"/runs/{run_id}").json()
    assert run["state"] == "finished" and run["exit_code"] == 0, "\n".join(run["lines"][-20:])
    assert decided == ["WI-REFUND-DEPOSIT", "WI-REFUND-SPLIT", "WI-CUST-001-CREDIT"]
    feed = client.get("/events?limit=1000").json()["events"]
    assert sum(1 for e in feed if e["action"] == "run:step" and e["evidence"]["status"] == "waiting") == 3


def test_a_rejection_stops_the_run_honestly(client):
    client.post("/scenarios/rosy-baseline/activate")
    _revoke_standing_authorization(client)
    run_id = client.post("/scenarios/rosy-baseline/run", json={"controller": "human"}).json()["run_id"]
    item_id = _wait_for_waiting(client)
    rejected = client.post(f"/work-items/{item_id}/reject",
                           json={"actor": "human_cfo", "reason": "not today"}).json()
    assert rejected["state"] == "REJECTED"
    MANAGER.wait(run_id, timeout=60)
    run = client.get(f"/runs/{run_id}").json()
    assert run["state"] == "finished" and run["exit_code"] == 1
    active = client.get("/scenarios/active").json()
    failed = [c for c in active["report"]["after"]["checks"] if not c["ok"]]
    assert any("REJECTED" in c["left"] for c in failed)
