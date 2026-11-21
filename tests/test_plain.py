"""The plain-language layer: a CFO can read what they are approving without the jargon."""
from __future__ import annotations

import dataclasses
import re

import pytest
from fastapi.testclient import TestClient

from desk import agent, llm, plain
from desk.__main__ import TASK
from desk.api import app
from desk.brief import brief
from desk.config import CFG
from desk.runs import MANAGER
from desk.scripted_seat1 import script

from conftest import BANK_TXN

JARGON = re.compile(r"\bWI-\d{4}\b|POL-L\d@\d|raw_ref|golden_|run_sql|match_deposit|\.md\b|\.csv\b")


def test_names_are_plain():
    assert plain.SEAT_NAMES == {"seat1": "Trade compliance", "seat2": "Cost accounting", "seat3": "Revenue accounting"}
    assert plain.account_name("Assets:Bank:Increase:Operating") == "Operating cash"
    assert plain.account_name("Income:Interest:TariffRefund") == "Interest on the refund"
    assert plain.document_title("data/documents/inbox/2025-10-14-cbp-refund-notice.md", CFG) == "CBP refund notice"
    assert plain.document_title("data/documents/contracts/CON-001-bean-street.md", CFG) == "Bean Street contract (CON-001)"
    assert plain.document_title("data/reference/refund_policy.md", CFG) == "Accounting policy"
    assert plain.document_title("data/documents/inbox/2025-10-16-cfo-email.md", CFG).startswith("Email from")


def test_the_deposit_reads_in_plain_words(conn, item, cfg):
    agent.run_seat(conn, "seat1", item["id"], llm.FakeLLM(script=script()), TASK.format(bank_txn=BANK_TXN), cfg=cfg)
    b = brief(conn, item["id"], cfg)
    p = plain.plain(conn, cfg, b)

    assert p["title"] == "Record the tariff refund that arrived from U.S. Customs"
    assert "14,317.00" in p["summary"] and "13,900.00" in p["summary"] and "417.00" in p["summary"]
    assert 1 <= len(p["why"]) <= 3
    assert p["decision"] == "Approve or decline"
    assert p["if_approved"].startswith("The entry posts")
    assert p["proposer"] == "Trade compliance"
    assert p["amount"] == "14,317.00"
    assert [m["label"] for m in p["money_lines"]] == ["Operating cash", "Tariff refund receivable", "Interest on the refund"]
    assert {e["title"] for e in p["evidence"]} >= {"CBP refund notice", "Accounting policy"}
    assert p["computed"] is True
    for text in [p["title"], p["summary"], *p["why"], p["if_approved"], *[e["title"] for e in p["evidence"]]]:
        assert not JARGON.search(text), text


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


def test_an_event_becomes_an_issue_with_plain_stages(client):
    started = client.post("/simulate/refund-arrives", json={"llm": "scripted", "controller": "scripted"})
    assert started.status_code == 202
    body = started.json()
    assert body["event"] == "refund-arrives" and body["llm"] == "scripted"
    MANAGER.wait(body["run"]["run_id"], timeout=240)

    issues = client.get("/issues").json()["issues"]
    kinds = {i["kind"] for i in issues}
    assert "onboarding" in kinds and "WI-REFUND-EVENT" in kinds
    refund = next(i for i in issues if i["kind"] == "WI-REFUND-EVENT")
    assert refund["title"].startswith("A tariff refund arrived: 14,317.00")
    assert refund["state"] == "done" or refund["awaiting"] == ["desk"]
    titles = [s["title"] for s in refund["stages"]]
    assert titles[0] == "Correct the refund estimate"
    assert titles[1] == "Record the tariff refund that arrived from U.S. Customs"
    assert any(t.startswith("Credit Bean Street") for t in titles)
    assert any(t.startswith("Harbor Roasters: is a credit owed?") for t in titles)
    for s in refund["stages"]:
        assert not JARGON.search(s["title"]), s["title"]
        assert not JARGON.search(s["summary"]), s["summary"]
    assert {s["owner"] for s in refund["stages"]} <= {"Trade compliance", "Cost accounting", "Revenue accounting"}

    detail = client.get(f"/work-items/{refund['stages'][0]['id']}/brief").json()
    assert detail["plain"]["title"] == titles[0]
    card = client.get("/attention").json()["handled_on_its_own"][0]
    assert card["plain"]["title"] and not JARGON.search(card["plain"]["summary"])
