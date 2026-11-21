"""The hard guard: data/truth and data/answer_key are unreachable."""
from __future__ import annotations

import json

import pytest

from desk.guard import FORBIDDEN_DIRS, Refused, check_doc_path, check_sql
from desk.tools import Ctx, dispatch
from desk.tools.read import read_doc, run_sql

from conftest import events

FORBIDDEN_PATHS = [
    "data/truth/world.json",
    "data/truth/shipments.csv",
    "data/answer_key/answer_key.json",
    "./data/truth/refunds.csv",
    "data/documents/../truth/world.json",
    "data/documents/../../data/answer_key/answer_key.json",
]

FORBIDDEN_SQL = [
    "SELECT * FROM truth_shipments",
    "SELECT * FROM answer_key",
    "SELECT * FROM golden_bank_txns JOIN truth_deposits USING (amount)",
]


@pytest.fixture
def ctx(conn, item, cfg):
    return Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
               work_item_id=item["id"], cfg=cfg)


@pytest.mark.parametrize("path", FORBIDDEN_PATHS)
def test_read_doc_refuses_ground_truth(ctx, path):
    with pytest.raises(Refused) as caught:
        read_doc(ctx, path=path)
    assert "may never read" in caught.value.reason or "may only read" in caught.value.reason


@pytest.mark.parametrize("path", FORBIDDEN_PATHS)
def test_the_refusal_is_logged(ctx, conn, path):
    ok, content = dispatch(ctx, "read_doc", {"path": path})
    assert not ok and content.startswith("refused")
    refused = events(conn, action="tool:read_doc", allowed=False)
    assert refused and refused[-1]["allowed"] == 0


@pytest.mark.parametrize("sql", FORBIDDEN_SQL)
def test_run_sql_refuses_anything_outside_the_golden_tables(ctx, conn, sql):
    with pytest.raises(Refused):
        run_sql(ctx, sql=sql)
    ok, content = dispatch(ctx, "run_sql", {"sql": sql})
    assert not ok and content.startswith("refused")
    assert events(conn, action="tool:run_sql", allowed=False)


def test_run_sql_is_read_only(ctx):
    for sql in ("DELETE FROM golden_bank_txns",
                "UPDATE golden_bank_txns SET amount = 0",
                "DROP TABLE events",
                "SELECT 1; DROP TABLE events",
                "ATTACH DATABASE '/tmp/x.db' AS x",
                "PRAGMA table_list"):
        with pytest.raises(Refused):
            run_sql(ctx, sql=sql)


def test_run_sql_cannot_read_the_desk_tables_either(ctx):
    for sql in ("SELECT * FROM events", "SELECT * FROM work_items", "SELECT * FROM llm_cache"):
        with pytest.raises(Refused, match="golden_"):
            run_sql(ctx, sql=sql)


def test_read_doc_refuses_anything_outside_the_allowed_folders(ctx):
    for path in ("systems/erp/seed.sql", "systems/books/main.beancount", ".env",
                 "/etc/passwd", "personas/scripts/controller.yaml"):
        with pytest.raises(Refused, match="may only read"):
            read_doc(ctx, path=path)


def test_the_allowed_folders_still_work(ctx):
    policy = read_doc(ctx, path="data/reference/refund_policy.md")
    assert "POL-001" in policy["text"]
    assert policy["raw_refs"] == ["data/reference/refund_policy.md"]

    contract = read_doc(ctx, path="data/documents/contracts/CON-001-bean-street.md")
    assert contract["path"].endswith("CON-001-bean-street.md")

    rows = run_sql(ctx, sql="SELECT txn_id, amount, raw_ref FROM golden_bank_txns"
                            " WHERE txn_id = 'BNK-0026'")
    assert rows["row_count"] == 1
    assert rows["raw_refs"] == ["data/views/bank/transactions.csv#BNK-0026"]


def test_the_forbidden_list_is_what_the_handover_says():
    assert FORBIDDEN_DIRS == ("data/truth", "data/answer_key")


def test_a_broken_query_is_handed_back_with_the_real_column_names(ctx, conn):
    """A typo in the model's SQL must not crash the desk."""
    from desk.tools.read import BadQuery

    with pytest.raises(BadQuery, match="did not run"):
        run_sql(ctx, sql="SELECT amount FROM golden_cbp_entries")
    ok, content = dispatch(ctx, "run_sql", {
        "sql": "SELECT nonsense FROM golden_bank_txns"})
    assert not ok
    assert "did not run" in content
    assert "Columns available" in content and "txn_id" in content
    assert events(conn, action="tool:run_sql", allowed=False)


def test_a_valid_query_still_works_after_that(ctx):
    rows = run_sql(ctx, sql="SELECT account, amount FROM golden_ledger_postings LIMIT 3")
    assert rows["row_count"] == 3


def test_read_doc_on_a_directory_returns_a_listing(ctx):
    """A directory is not an error: list it so the seat can pick a file."""
    listing = read_doc(ctx, path="data/documents/inbox")
    assert listing["kind"] == "directory"
    assert listing["file_count"] == 4
    names = {e["name"] for e in listing["entries"]}
    assert "2025-10-14-cbp-refund-notice.md" in names
    assert all(e["bytes"] > 0 for e in listing["entries"] if e["kind"] == "file")
    # and the paths it hands back are readable
    first = next(e for e in listing["entries"] if e["kind"] == "file")
    assert read_doc(ctx, path=first["path"])["kind"] == "file"


def test_read_doc_lists_nested_directories_too(ctx):
    top = read_doc(ctx, path="data/documents")
    assert {e["name"] for e in top["entries"]} == {"contracts", "inbox"}
    assert all(e["kind"] == "directory" for e in top["entries"])
    assert {e["name"] for e in read_doc(ctx, path="data/reference")["entries"]} == {
        "hts_programs.csv", "refund_policy.md"}


@pytest.mark.parametrize("path", ["data/truth", "data/truth/", "data/answer_key"])
def test_the_guard_still_refuses_forbidden_directories(ctx, conn, path):
    """Listing is no way round the ban."""
    with pytest.raises(Refused, match="may never read"):
        read_doc(ctx, path=path)
    ok, content = dispatch(ctx, "read_doc", {"path": path})
    assert not ok and "may never read" in content


def test_a_directory_outside_the_allowed_folders_is_refused(ctx):
    for path in ("systems", "personas", "data", "."):
        with pytest.raises(Refused, match="may only read"):
            read_doc(ctx, path=path)


def test_an_unexpected_exception_in_a_tool_does_not_crash_the_run(conn, item, cfg, monkeypatch):
    """Fix A2: any exception becomes a logged failed tool call, handed back to the seat."""
    from desk import agent, llm
    from desk.llm import LLMResponse, ToolCall
    from desk.tools import REGISTRY

    import dataclasses as dc

    def explode(ctx, **kwargs):
        raise RuntimeError("the disk caught fire")

    monkeypatch.setitem(REGISTRY, "match_deposit",
                        dc.replace(REGISTRY["match_deposit"], fn=explode))

    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "match_deposit", {"bank_txn_id": "BNK-0026"})
    assert not ok
    assert "RuntimeError" in content and "the disk caught fire" in content
    assert "try again" in content

    failed = events(conn, action="tool:match_deposit", allowed=False)
    assert len(failed) == 1
    evidence = json.loads(failed[0]["evidence"])
    assert evidence["exception"] == "RuntimeError"
    assert "traceback" in evidence

    # and the agent loop survives it and carries on to the next turn
    client = llm.FakeLLM(script=[
        LLMResponse(text="trying", tool_calls=(
            ToolCall("x1", "match_deposit", {"bank_txn_id": "BNK-0026"}),)),
        LLMResponse(text="that tool is broken; stopping and reporting it"),
    ])
    run = agent.run_seat(conn, "seat1", item["id"], client, "record the deposit", cfg=cfg)
    assert run.turns == 2
    assert run.tool_calls == [{"name": "match_deposit", "ok": False,
                               "arguments": {"bank_txn_id": "BNK-0026"}}]
    assert "broken" in run.final_text
