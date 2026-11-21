"""The read-only CLI commands, and reset's cache handling."""
from __future__ import annotations

import pytest

from desk import db
from desk.__main__ import POSTED_HEADER, main
from desk.config import CFG


def test_items_lists_every_work_item(conn, cfg, item, capsys, monkeypatch):
    import argparse

    import desk.__main__ as cli

    monkeypatch.setattr(cli, "CFG", cfg)
    assert cli.cmd_items(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    for column in ("id", "kind", "state", "seat", "parent", "proposed_by", "approved_by"):
        assert column in out
    assert item["id"] in out
    assert "WI-REFUND-DEPOSIT" in out
    assert "1 work items, 0 settled" in out


def test_items_shows_the_whole_graph_after_a_phase2_run(conn, cfg, capsys, monkeypatch):
    import argparse

    import desk.__main__ as cli
    from desk.workflow import create_work_item

    parent = create_work_item(conn, kind="WI-REFUND-EVENT", seat=None, actor="sync")
    child = create_work_item(conn, kind="WI-REFUND-SPLIT", seat="seat2", actor="sync",
                             parent_id=parent["id"])
    monkeypatch.setattr(cli, "CFG", cfg)
    assert cli.cmd_items(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert parent["id"] in out and child["id"] in out
    assert f"{parent['id']}" in out.split(child["id"])[1]   # parent column filled in


def test_items_is_friendly_when_there_is_nothing(tmp_path, monkeypatch, capsys):
    import dataclasses

    import desk.__main__ as cli

    empty = dataclasses.replace(CFG, desk_db=tmp_path / "empty.db")
    monkeypatch.setattr(cli, "CFG", empty)
    assert cli.cmd_items(None) == 0
    assert "no work items yet" in capsys.readouterr().out


def test_log_shows_the_latest_events_and_respects_limit(conn, cfg, item, capsys, monkeypatch):
    import argparse

    import desk.__main__ as cli

    monkeypatch.setattr(cli, "CFG", cfg)
    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert total > 2

    assert cli.cmd_log(argparse.Namespace(limit=2)) == 0
    out = capsys.readouterr().out
    assert "action" in out and "reason" in out
    assert f"showing 2 of {total} events" in out

    assert cli.cmd_log(argparse.Namespace(limit=100)) == 0
    assert f"showing {total} of {total} events" in capsys.readouterr().out


def test_log_is_friendly_when_there_is_nothing(tmp_path, monkeypatch, capsys):
    import argparse
    import dataclasses

    import desk.__main__ as cli

    empty = dataclasses.replace(CFG, desk_db=tmp_path / "empty.db")
    monkeypatch.setattr(cli, "CFG", empty)
    assert cli.cmd_log(argparse.Namespace(limit=10)) == 0
    assert "no events yet" in capsys.readouterr().out


def test_why_marks_a_refused_attempt_and_its_correction(conn, cfg, item, capsys, monkeypatch):
    """The unbalanced-entry refusal must read as a refusal followed by a fix."""
    import dataclasses

    import desk.__main__ as cli
    from desk.tools import Ctx, dispatch

    ctx = Ctx(conn=conn, actor="ai_agent.seat1", seat="seat1",
              work_item_id=item["id"], cfg=cfg)
    unbalanced = {
        "rationale": "signs the wrong way round",
        "journal_entry": {
            "date": "2025-10-15", "narration": "unbalanced",
            "postings": [
                {"account": "Assets:Bank:Increase:Operating", "amount": "14317.00"},
                {"account": "Assets:Receivable:TariffRefund", "amount": "13900.00"},
                {"account": "Income:Interest:TariffRefund", "amount": "417.00"},
            ],
        },
        "policy_ref": "POL-001@1",
        "evidence": ["data/documents/inbox/2025-10-14-cbp-refund-notice.md"],
    }
    ok, content = dispatch(ctx, "propose", unbalanced)
    assert not ok and "28634.00" in content

    fixed = dict(unbalanced)
    fixed["journal_entry"] = dict(unbalanced["journal_entry"])
    fixed["journal_entry"]["postings"] = [
        {"account": "Assets:Bank:Increase:Operating", "amount": "14317.00"},
        {"account": "Assets:Receivable:TariffRefund", "amount": "-13900.00"},
        {"account": "Income:Interest:TariffRefund", "amount": "-417.00"},
    ]
    ok, _ = dispatch(ctx, "propose", fixed)
    assert ok

    monkeypatch.setattr(cli, "CFG", cfg)
    import argparse
    assert cli.cmd_why(argparse.Namespace(work_item_id=item["id"])) == 0
    out = capsys.readouterr().out
    assert "REF" in out
    assert "refused attempt, corrected at event" in out
    assert "of those corrected" in out


def test_reset_keeps_the_cache_by_default(clean_books, capsys):
    cache = db.cache_conn(CFG)
    cache.execute(
        "INSERT OR REPLACE INTO llm_cache VALUES ('k','openai','m','{}','now')")
    cache.close()
    assert CFG.cache_db.exists()

    assert main(["reset"]) == 0
    out = capsys.readouterr().out
    assert "cache kept" in out
    assert CFG.cache_db.exists()
    kept = db.cache_conn(CFG)
    assert kept.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] >= 1
    kept.close()


def test_reset_clear_cache_wipes_it(clean_books, capsys):
    cache = db.cache_conn(CFG)
    cache.execute(
        "INSERT OR REPLACE INTO llm_cache VALUES ('k2','openai','m','{}','now')")
    cache.close()

    assert main(["reset", "--clear-cache"]) == 0
    assert "cache cleared" in capsys.readouterr().out
    assert not CFG.cache_db.exists()


def test_the_cache_lives_outside_the_desk_db(conn, cfg):
    """llm_cache moved to its own gitignored file (Phase 1 fix 3)."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "llm_cache" not in tables
    assert cfg.cache_db.name == "llm_cache.db"
    assert "/.cache/" in str(CFG.cache_db) or CFG.cache_db.parent.name == ".cache"
