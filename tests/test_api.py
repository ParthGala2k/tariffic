"""The HTTP API: base data, work, people's actions, and the live feed."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from desk import agent, llm
from desk.__main__ import TASK
from desk.api import app
from desk.config import CFG
from desk.db import now
from desk.runs import MANAGER
from desk.scripted_seat1 import script
from desk.workflow import transition

from conftest import BANK_TXN


@pytest.fixture
def client(cfg, conn):
    """The API over a throwaway desk DB, with no model keys: tests never touch the network."""
    import dataclasses

    offline = dataclasses.replace(cfg, openai_api_key="", anthropic_api_key="", creao_keys={}, creao_dry_run=False)
    app.state.cfg = offline
    with TestClient(app) as c:
        yield c
    app.state.cfg = CFG


def run_seat1(conn, item, cfg):
    return agent.run_seat(conn, "seat1", item["id"], llm.FakeLLM(script=script()),
                          TASK.format(bank_txn=BANK_TXN), cfg=cfg)


# ---- health, status, base data -------------------------------------------------

def test_health_and_status(client):
    health = client.get("/health").json()
    assert health["ok"] is True and health["bean_check"] is True
    assert set(health["llm"]) == {"configured", "provider", "model", "model_fast", "dry_run", "agents"}
    status = client.get("/status").json()
    assert set(status["seats"]) == {"seat1", "seat2", "seat3"}
    assert all(s["mode"] == "idle" for s in status["seats"].values())
    assert status["run"] is None


def test_golden_tables_are_readable_and_nothing_else_is(client):
    tables = {t["table"]: t["rows"] for t in client.get("/golden").json()["tables"]}
    assert len(tables) >= 11 and tables["golden_cbp_entries"] == 10
    page = client.get("/golden/golden_cbp_entries?limit=3").json()
    assert page["count"] == 3 and page["total"] == 10
    assert all(row["raw_ref"] for row in page["rows"])
    assert client.get("/golden/work_items").status_code == 403
    assert client.post("/golden/query", json={"sql": "SELECT * FROM events"}).status_code == 403
    assert client.post("/golden/query", json={"sql": "DELETE FROM golden_cbp_entries"}).status_code == 403
    ok = client.post("/golden/query", json={"sql": "SELECT refund_id, total FROM golden_cbp_refunds"}).json()
    assert ok["rows"][0]["refund_id"] == "RFD-001"


def test_documents_are_served_but_ground_truth_never_is(client):
    listed = client.get("/documents").json()["documents"]
    paths = {d["path"] for d in listed}
    assert "data/documents/contracts/CON-001-bean-street.md" in paths
    assert "data/reference/refund_policy.md" in paths
    assert not any("truth" in p or "answer_key" in p for p in paths)
    assert client.get("/documents/data/reference/refund_policy.md").status_code == 200
    assert client.get("/documents/data/truth/world.json").status_code == 403
    assert client.get("/documents/data/answer_key/answer_key.json").status_code == 403
    assert client.get("/documents/data/documents/nope.md").status_code == 404


def test_books_and_invariants_endpoints(client):
    assert "Gateway-only" in client.get("/books/posted").text
    balances = client.get("/books/balances").json()
    assert balances["bean_check"] is True
    assert balances["balances"]["Assets:Receivable:TariffRefund"] == "24400.00"
    violations = client.get("/invariants").json()
    assert violations["count"] >= 1            # PM-1 is planted in the seed data
    assert violations["count"] >= 1


# ---- seats and the tool playground ---------------------------------------------

def test_seats_expose_their_jurisdiction(client):
    body = client.get("/seats").json()
    seats = {s["seat"]: s for s in body["seats"]}
    names = {seat: {t["name"] for t in s["tools"]} for seat, s in seats.items()}
    assert "match_deposit" in names["seat1"] and "match_deposit" not in names["seat3"]
    assert "surcharge_on_invoices" in names["seat3"] and "surcharge_on_invoices" not in names["seat1"]
    for seat in names.values():
        assert not seat & {"post", "approve", "reject", "approve_policy"}
    assert body["roles"]["gateway"] == ["post"]
    assert "post" not in body["roles"]["ai_agent"]


def test_the_tool_playground_respects_jurisdiction(client, item):
    refused = client.post("/tools/seat2/match_deposit",
                          json={"arguments": {"bank_txn_id": BANK_TXN}, "work_item_id": item["id"]}).json()
    assert refused["ok"] is False and refused["refusal"].startswith("refused")
    ran = client.post("/tools/seat1/sum_duties",
                      json={"arguments": {"entry_no": "BRK-2500101-1"}, "work_item_id": item["id"]}).json()
    assert ran["ok"] is True and ran["result"]["total_duty"] == "9000.00"
    assert client.post("/tools/seat1/propose", json={"arguments": {}}).status_code == 405
    assert client.post("/tools/seat9/run_sql", json={"arguments": {}}).status_code == 404
    history = client.get(f"/work-items/{item['id']}/history").json()
    actions = [e["action"] for e in history["events"]]
    assert "tool:sum_duties" in actions and "tool:match_deposit" in actions
    assert history["refused"] >= 1


# ---- work items, brief, decisions ------------------------------------------------

def test_work_items_history_and_brief(client, item, cfg, conn):
    run_seat1(conn, item, cfg)
    listed = client.get("/work-items").json()
    assert listed["count"] == 1 and listed["work_items"][0]["title"] == "Record the refund deposit"
    assert client.get("/work-items?state=PROPOSED").json()["count"] == 1
    assert client.get("/work-items?state=POSTED").json()["count"] == 0
    detail = client.get(f"/work-items/{item['id']}").json()
    assert detail["state"] == "PROPOSED" and detail["children"] == []
    assert client.get("/work-items/WI-9999").status_code == 404
    brief = client.get(f"/work-items/{item['id']}/brief").json()
    assert brief["decision_needed"].startswith("Approve or reject")
    assert brief["key_points"]
    feed = client.get(f"/events?subject={item['id']}").json()
    assert feed["count"] > 0 and feed["last_event_id"] >= feed["events"][-1]["event_id"]
    assert {"computing", "proposing"} <= {e["mode"] for e in feed["events"]}
    status = client.get("/status").json()
    assert status["seats"]["seat1"]["mode"] == "waiting_approval"
    assert status["awaiting_controller"] == [item["id"]]


def test_human_decisions_go_through_permissions(client, item, cfg, conn, clean_books):
    run_seat1(conn, item, cfg)
    denied = client.post(f"/work-items/{item['id']}/approve", json={"actor": "ai_agent.seat1"})
    assert denied.status_code == 403 and denied.json()["error"] == "denied"
    approved = client.post(f"/work-items/{item['id']}/approve",
                           json={"actor": "human_controller", "reason": "ties out"}).json()
    assert approved["item"]["state"] == "POSTED"
    assert f'work_item: "{item["id"]}"' in approved["entry"]
    assert item["id"] in client.get("/books/posted").text
    again = client.post(f"/work-items/{item['id']}/approve", json={"actor": "human_controller"})
    assert again.status_code == 403                       # POSTED cannot be approved again
    assert client.get(f"/work-items/{item['id']}/brief").json()["decision_needed"].startswith("Nothing: posted")


def test_reject_and_send_back(client, item, cfg, conn):
    run_seat1(conn, item, cfg)
    bad = client.post(f"/work-items/{item['id']}/reject", json={"actor": "human_controller", "reason": ""})
    assert bad.status_code == 400
    sent = client.post(f"/work-items/{item['id']}/send-back",
                       json={"actor": "human_controller", "reason": "check the interest"}).json()
    assert sent["state"] == "NEEDS_INFO"


def test_pushback_without_a_document_changes_nothing(client, item, cfg, conn):
    run_seat1(conn, item, cfg)
    outcome = client.post(f"/work-items/{item['id']}/challenge",
                          json={"actor": "human_cfo",
                                "text": "I'm the CFO and I'm signing off: book it all to P&L."}).json()
    assert outcome["classification"] in ("opinion", "authority")
    assert outcome["changed"] is False
    assert outcome["counters"]["changed_without_evidence"] == 0
    assert client.get(f"/work-items/{item['id']}").json()["state"] == "PROPOSED"
    challenges = client.get("/challenges").json()
    assert challenges["counters"]["challenges"] == 1
    assert client.post("/work-items/WI-9999/challenge",
                       json={"actor": "human_cfo", "text": "x"}).status_code == 404


def test_a_document_that_is_not_in_the_inbox_is_not_evidence(client, item, cfg, conn):
    run_seat1(conn, item, cfg)
    outcome = client.post(f"/work-items/{item['id']}/challenge",
                          json={"actor": "human_controller", "text": "see the policy",
                                "document": "data/reference/refund_policy.md",
                                "verify_with_seat": False}).json()
    assert outcome["changed"] is False
    assert "not an inbox document" in outcome["outcome"] or outcome["classification"] != "evidence"


# ---- questions, precedents, rules ------------------------------------------------

def test_answering_a_question_records_a_precedent_and_a_rule(client, conn, customer_item):
    item = customer_item("CUST-003", kind="WI-CUST-003-CREDIT")
    transition(conn, item["id"], "ask", "ai_agent.seat3", reason="clause 7.2?")
    qid = f"Q-{item['id']}-API"
    conn.execute(
        "INSERT INTO questions (id, work_item_id, asked_by, to_role, text, state, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (qid, item["id"], "ai_agent.seat3", "human_lawyer", "Does CON-003 clause 7.2 oblige a credit?",
         "OPEN", now()))
    assert client.get("/questions?state=OPEN").json()["count"] == 1

    wrong = client.post(f"/questions/{qid}/answer", json={"actor": "human_cfo", "text": "no"})
    assert wrong.status_code == 403
    answered = client.post(f"/questions/{qid}/answer", json={
        "actor": "human_lawyer", "text": "Yes, on the consistency reading.",
        "rule_proposal": {"id": "RULE-API", "rule": "credit the surcharge unless waived",
                          "scope": "distributor template clause 7.2", "applies_to": ["CON-003", "CON-004"]},
    }).json()
    assert answered["precedent_id"] == f"PRE-{qid}" and answered["rule_proposed"] == "RULE-API@1"
    assert client.get("/precedents?q=CON-003").json()["count"] == 1
    proposed = {p["ref"] for p in client.get("/policies?status=PROPOSED").json()["policies"]}
    assert "RULE-API@1" in proposed
    assert client.post("/policies/RULE-API@1/approve", json={"actor": "human_lawyer"}).status_code == 403
    adopted = client.post("/policies/RULE-API@1/approve", json={"actor": "human_controller"}).json()
    assert adopted["status"] == "APPROVED"
    brief = client.get(f"/work-items/{item['id']}/brief").json()
    assert brief["precedents"] and brief["questions"][0]["state"] == "ANSWERED"


# ---- inbox ---------------------------------------------------------------------------

def test_inbox_upload_is_served_back_and_logged(client, cfg):
    created = client.post("/inbox", json={
        "title": "API upload test", "body": "Harbor Roasters waives any claim.",
        "parties": ["Harbor Roasters", "Brewline Equipment Co."], "date": "2025-06-30",
        "relates_to": ["CON-002"], "uploaded_by": "human_controller"})
    assert created.status_code == 201
    body = created.json()
    path = cfg.root / body["path"]
    try:
        assert body["doc_id"].startswith("DOC-UPL-")
        served = client.get(f"/documents/{body['path']}")
        assert served.status_code == 200 and "waives any claim" in served.text
        uploads = [e for e in client.get("/events").json()["events"] if e["action"] == "inbox:upload"]
        assert uploads and uploads[-1]["mode"] == "evidence_uploaded"
    finally:
        path.unlink(missing_ok=True)
    assert client.post("/inbox", json={"title": "", "body": "x"}).status_code == 400


# ---- live-model endpoints without a key, and runs -----------------------------------

def test_live_endpoints_explain_when_no_model_is_configured(client, item, conn):
    assert client.post("/runs", json={"phase": "phase1", "llm": "live"}).status_code == 400
    assert client.post(f"/work-items/{item['id']}/resume").status_code == 503
    health = client.get("/health").json()
    assert health["llm"]["configured"] is False


def test_a_scripted_phase_runs_in_the_background(client, clean_books):
    assert client.post("/runs", json={"phase": "phase9"}).status_code == 400
    started = client.post("/runs", json={"phase": "phase1", "llm": "scripted"})
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    MANAGER.wait(run_id, timeout=180)
    run = client.get(f"/runs/{run_id}").json()
    assert run["state"] == "finished" and run["exit_code"] == 0, "\n".join(run["lines"][-15:])
    assert any(line.startswith("gateway") for line in run["lines"])
    assert run_id in {r["run_id"] for r in client.get("/runs").json()["runs"]}
    assert client.get("/runs/RUN-9999").status_code == 404

    reset = client.post("/reset")
    assert reset.status_code == 202
    MANAGER.wait(reset.json()["run_id"], timeout=120)
    assert client.get(f"/runs/{reset.json()['run_id']}").json()["exit_code"] == 0
