"""The Creao agents as the seats' brains: routing by ticket, the form fields, the context,
the answer parser, and the whole desk loop on a dry run that never touches the network."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from desk import agent, config, creao, llm
from desk.__main__ import TASK
from desk.llm import LLMResponse, ToolCall
from desk.scripted import segment
from desk.scripted_seat1 import script
from desk.workflow import get_item

from conftest import BANK_TXN


def test_keys_are_found_however_they_were_pasted(tmp_path):
    env = tmp_path / ".env"
    env.write_text('STOCK_CUSTOMER_KEY =capi_stock\nTariffs_Refunds _KEY = "capi_tariffs"\n'
                   'Sourcing_analyst_KEY=capi_sourcing\nOTHER=1\n', encoding="utf-8")
    keys = config.creao_keys(env, environ={})
    assert keys == {"tariffs": "capi_tariffs", "sourcing": "capi_sourcing", "stock": "capi_stock"}
    assert config.creao_keys(tmp_path / "missing.env", environ={}) == {}
    assert config.creao_keys(tmp_path / "missing.env", environ={"CREAO_SOURCING_KEY": "capi_env"}) == {"sourcing": "capi_env"}


def test_tickets_route_to_the_agent_that_owns_the_kind():
    assert creao.agent_for("WI-REFUND-DEPOSIT", "seat1").key == "tariffs"
    assert creao.agent_for("WI-FORECAST-CORRECTION", "seat1").key == "tariffs"
    assert creao.agent_for("WI-ONBOARD-REVIEW", "seat1").key == "tariffs"
    assert creao.agent_for("WI-ONBOARD-POLICY", "seat1", {"onboarding": "POL-L1"}).key == "tariffs"
    assert creao.agent_for("WI-ONBOARD-POLICY", "seat2", {"onboarding": "POL-L2"}).key == "stock"
    assert creao.agent_for("WI-ONBOARD-POLICY", "seat1", {"onboarding": "POL-L3"}).key == "stock"
    assert creao.agent_for("WI-REFUND-SPLIT", "seat2").key == "stock"      # seat2's work, the stock agent's ticket
    assert creao.agent_for("WI-CUST-001-CREDIT", "seat3").key == "stock"
    assert creao.agent_for("WI-CUST-002-CLOSE", "seat3").key == "stock"
    assert creao.agent_for("WI-SOURCING-REVIEW", "seat2").key == "sourcing"
    assert creao.agent_for("WI-CORRECTION", "seat3").key == "stock"
    assert creao.agent_for("WI-CORRECTION", "seat1").key == "tariffs"


def test_form_fields_match_each_agents_form():
    tariffs, stock, sourcing = creao.AGENTS["tariffs"], creao.AGENTS["stock"], creao.AGENTS["sourcing"]
    assert creao.form_inputs(tariffs, "WI-REFUND-DEPOSIT", "WI-0006", "ctx") == {
        "ticket_kind": "WI-REFUND-DEPOSIT", "ticket_id": "WI-0006", "context": "ctx"}
    assert creao.form_inputs(stock, "WI-CUST-003-CREDIT", "WI-0012", "ctx") == {
        "ticket": "ctx", "ticket_kind": "WI-CUST-001-CREDIT"}        # nearest listed kind
    assert creao.form_inputs(sourcing, "WI-SOURCING-WATCH", "WI-0020", "ctx") == {
        "ticket_kind": "WI-SOURCING-WATCH", "ticket": "ctx"}
    assert creao.select_kind(tariffs, "WI-CORRECTION") == "WI-DUTY-RECONCILIATION"


def test_the_context_carries_the_rules_the_tools_the_task_and_the_results():
    tools = [{"name": "run_sql", "description": "Read the golden tables.",
              "parameters": {"properties": {"sql": {}, "limit": {}}, "required": ["sql"]}}]
    messages = [{"role": "user", "content": "Record the deposit BNK-0026."},
                {"role": "assistant", "content": "Looking it up.",
                 "tool_calls": [{"id": "c1", "name": "run_sql", "arguments": {"sql": "SELECT 1"}}]},
                {"role": "tool", "tool_call_id": "c1", "name": "run_sql", "content": '{"rows": [[1]]}'}]
    text = creao.render_context("House rules here.", messages, tools, kind="WI-REFUND-DEPOSIT", item_id="WI-0006")
    assert text.startswith("TICKET WI-0006 (WI-REFUND-DEPOSIT)")
    assert "House rules here." in text and "run_sql(sql, limit?)" in text
    assert '{"name": "<tool name>", "arguments": {<the tool\'s arguments>}}' in text
    assert "=== TASK ===\nRecord the deposit BNK-0026." in text
    assert "YOU CALLED run_sql (call c1)" in text and 'TOOL RESULT for run_sql (call c1):\n{"rows": [[1]]}' in text
    assert text.rstrip().endswith("Call the next tool(s), or finish in prose.")


def test_the_parser_finds_tool_calls_in_every_shape_the_agent_might_use():
    fenced = 'I will read the notice first.\n```json\n{"name": "read_doc", "arguments": {"path": "data/documents/inbox"}}\n```\nThen match it.'
    r = creao.parse_output(fenced)
    assert [c.name for c in r.tool_calls] == ["read_doc"] and r.tool_calls[0].arguments == {"path": "data/documents/inbox"}
    assert r.text == "I will read the notice first.\n\nThen match it."

    bare = ('{"name": "match_deposit", "arguments": {"bank_txn_id": "BNK-0026"}}\n'
            '{"tool": "run_sql", "args": {"sql": "SELECT * FROM golden_cbp_refunds"}}')
    r = creao.parse_output(bare)
    assert [(c.name, c.arguments) for c in r.tool_calls] == [
        ("match_deposit", {"bank_txn_id": "BNK-0026"}), ("run_sql", {"sql": "SELECT * FROM golden_cbp_refunds"})]
    assert r.text == ""

    nested = ('{"name": "propose", "arguments": {"rationale": "x", "journal_entry": {"postings": '
              '[{"account": "Assets:Bank:Increase:Operating", "amount": "14317.00"}], "note": "a } inside \\"quotes\\""}}}')
    r = creao.parse_output(nested)
    assert r.tool_calls[0].name == "propose"
    assert r.tool_calls[0].arguments["journal_entry"]["postings"][0]["amount"] == "14317.00"

    wrapped = '{"tool_calls": [{"name": "get_policies", "arguments": {}}, {"name": "ask", "input": {"to_role": "human_lawyer", "question": "?"}}]}'
    r = creao.parse_output(wrapped)
    assert [c.name for c in r.tool_calls] == ["get_policies", "ask"]

    prose = "The deposit matches the notice: 13,900.00 principal and 417.00 interest. Nothing else to call."
    r = creao.parse_output(prose)
    assert r.text == prose and r.tool_calls == ()
    other_json = 'Here is the entry {"account": "Assets:Cash", "amount": 1} for reference.'
    r = creao.parse_output(other_json)
    assert r.tool_calls == () and r.text == other_json
    assert len({c.id for c in creao.parse_output(bare).tool_calls}) == 2


def test_render_and_parse_round_trip_every_recorded_turn():
    for name in ("deposit", "split", "bean_street", "forecast", "harbor_question"):
        for response in segment(name):
            back = creao.parse_output(creao.render_response(response))
            assert [(c.name, c.arguments) for c in back.tool_calls] == [(c.name, c.arguments) for c in response.tool_calls]
            assert back.text.strip() == (response.text or "").strip()


def test_a_dry_run_works_the_deposit_through_the_desk_without_the_network(conn, item, cfg):
    creao_cfg = dataclasses.replace(cfg, provider="creao", creao_keys={}, creao_dry_run=True)
    transport = creao.DryRunTransport(script=lambda name: script())
    client = creao.CreaoClient(creao_cfg, transport).for_segment("deposit")
    run = agent.run_seat(conn, "seat1", item["id"], client, TASK.format(bank_txn=BANK_TXN), cfg=creao_cfg)
    assert get_item(conn, item["id"])["state"] == "PROPOSED"
    assert run.tool_calls and run.tool_calls[-1]["name"] == "propose"
    # every turn went to the tariffs agent, as that agent's own form, with the ticket in it
    assert transport.requests and all(r["agent"] == "tariffs" for r in transport.requests)
    first = transport.requests[0]
    assert first["app_id"] == creao.AGENTS["tariffs"].app_id
    assert first["inputs"]["ticket_kind"] == "WI-REFUND-DEPOSIT" and first["inputs"]["ticket_id"] == item["id"]
    assert "=== TASK ===" in first["inputs"]["context"] and BANK_TXN in first["inputs"]["context"]
    later = transport.requests[-1]["inputs"]["context"]
    assert "TOOL RESULT for" in later and "=== YOUR NEXT TURN ===" in later
    assert all("capi_" not in json.dumps(r["inputs"]) for r in transport.requests)
    # the log shows the Creao brain, and every tool call went through the desk's own registry
    start = conn.execute("SELECT evidence FROM events WHERE action='agent:start' AND subject=?", (item["id"],)).fetchone()
    assert json.loads(start["evidence"])["provider"] == "creao"
    assert conn.execute("SELECT COUNT(*) FROM events WHERE action LIKE 'tool:%' AND subject=?", (item["id"],)).fetchone()[0] == len(run.tool_calls)


def test_build_client_picks_creao_and_never_caches_a_dry_run(cfg):
    creao_cfg = dataclasses.replace(cfg, provider="creao", creao_keys={"tariffs": "capi_x", "stock": "capi_y"},
                                    creao_dry_run=True)
    client = llm.build_client(None, creao_cfg)
    assert isinstance(client, creao.CreaoClient) and isinstance(client.transport, creao.DryRunTransport)
    assert creao_cfg.live_ready and creao_cfg.api_key == "capi_x"
    live_cfg = dataclasses.replace(creao_cfg, creao_dry_run=False)
    wrapped = llm.build_client(None, live_cfg)
    assert isinstance(wrapped, llm.CachingClient) and isinstance(wrapped._inner.transport, creao.HttpTransport)
    missing = dataclasses.replace(creao_cfg, creao_keys={"tariffs": "capi_x"})
    assert not missing.live_ready


def test_a_missing_key_is_refused_before_any_network_call(cfg):
    creao_cfg = dataclasses.replace(cfg, provider="creao", creao_keys={}, creao_dry_run=False)
    client = creao.CreaoClient(creao_cfg, creao.HttpTransport("https://example.invalid")).for_ticket(
        "seat1", {"id": "WI-0001", "kind": "WI-REFUND-DEPOSIT", "payload": {}})
    with pytest.raises(llm.LLMError, match="no API key"):
        client.complete("rules", [{"role": "user", "content": "task"}], [])


def test_the_polled_result_text_is_read_from_whichever_field_the_platform_used():
    assert creao.result_text({"status": "completed", "result": {"text": "done"}}) == "done"
    assert creao.result_text({"result": {"chatText": "hi"}}) == "hi"
    assert creao.result_text({"result": {"blocks": [{"type": "thinking", "text": "x"}, {"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}) == "a\nb"
    assert creao.result_text({"output": "o"}) == "o"
    assert creao.result_text({"status": "completed"}) == ""
    # what the Tariffs & Refunds agent really returned on 2026-10-25: a truncated JSON
    # wrapper in `text`, the whole reply with the fenced calls in `chatText`
    real = {"status": "completed", "result": {"text": '{"response":"I will read the notice first. The bash just echoed those; the desk to"}', "chatText": 'I will read the notice first.\n\n```json\n{"name": "run_sql", "arguments": {"sql": "SELECT 1"}}\n{"name": "get_policies", "arguments": {}}\n```\n\nI am pausing here until the results come back.'}}
    parsed = creao.parse_output(creao.result_text(real))
    assert [c.name for c in parsed.tool_calls] == ["run_sql", "get_policies"]
    assert parsed.text.startswith("I will read the notice first.") and parsed.text.endswith("come back.")
    assert creao.unwrap('{"response": "plain inside"}') == "plain inside"
    assert creao.parse_output('{"response": "{\\"name\\": \\"ask\\", \\"arguments\\": {\\"to_role\\": \\"human_lawyer\\", \\"question\\": \\"q\\"}}"}').tool_calls[0].name == "ask"


def test_the_http_transport_creates_polls_and_reads_the_run(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status_code, payload):
            self.status_code, self._payload, self.text = status_code, payload, json.dumps(payload)

        def json(self):
            return self._payload

    class Client:
        def __init__(self, *a, **k):
            self.polls = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            calls.append(("POST", url, json, headers["Authorization"]))
            return Response(202, {"id": "run-1", "status": "pending", "appId": "app"})

        def get(self, url, headers=None):
            self.polls += 1
            calls.append(("GET", url, None, headers["Authorization"]))
            if self.polls < 2:
                return Response(200, {"id": "run-1", "status": "running"})
            return Response(200, {"id": "run-1", "status": "completed",
                                  "result": {"text": '{"name": "propose", "arguments": {"rationale": "r"}}'}})

    import httpx
    monkeypatch.setattr(httpx, "Client", Client)
    transport = creao.HttpTransport("https://agent.creao.ai", poll_seconds=0)
    text = creao.HttpTransport.run_unguarded(          # the suite guards .run; this is the one test of the real one
        transport, creao.AGENTS["tariffs"], {"ticket_kind": "WI-REFUND-DEPOSIT", "ticket_id": "WI-0006", "context": "c"}, "capi_secret")
    assert creao.parse_output(text).tool_calls[0].name == "propose"
    assert calls[0][0] == "POST" and calls[0][1].endswith(f"/api/v1/apps/{creao.AGENTS['tariffs'].app_id}/runs")
    assert calls[0][2] == {"inputs": {"ticket_kind": "WI-REFUND-DEPOSIT", "ticket_id": "WI-0006", "context": "c"}}
    assert calls[0][3] == "Bearer capi_secret" and calls[-1][1].endswith("/runs/run-1")
    assert transport.last_run["status"] == "completed"


def test_the_cache_wrapper_still_binds_each_ticket_to_its_agent(cfg):
    """A live run goes through the cache; the binding must pass through it, or every
    ticket would reach the Tariffs agent as "(no ticket)"."""
    live_cfg = dataclasses.replace(cfg, provider="creao", creao_keys={"tariffs": "a", "stock": "b"}, creao_dry_run=False)
    wrapped = llm.build_client(None, live_cfg)
    bound = wrapped.for_ticket("seat3", {"id": "WI-0009", "kind": "WI-CUST-001-CREDIT", "payload": {}})
    assert isinstance(bound, llm.CachingClient)
    assert bound._inner.agent().key == "stock" and bound.model == "creao:stock"
    assert wrapped.for_ticket("seat1", {"id": "WI-0006", "kind": "WI-FORECAST-CORRECTION", "payload": {}}).model == "creao:tariffs"
    assert wrapped.model == "creao-apps"


def test_the_log_names_the_creao_agent_behind_each_ticket(conn, item, cfg):
    creao_cfg = dataclasses.replace(cfg, provider="creao", creao_keys={}, creao_dry_run=True)
    client = creao.CreaoClient(creao_cfg, creao.DryRunTransport(script=lambda name: script())).for_segment("deposit")
    agent.run_seat(conn, "seat1", item["id"], client, TASK.format(bank_txn=BANK_TXN), cfg=creao_cfg)
    start = conn.execute("SELECT evidence FROM events WHERE action='agent:start' AND subject=?", (item["id"],)).fetchone()
    assert json.loads(start["evidence"])["model"] == "creao:tariffs"
