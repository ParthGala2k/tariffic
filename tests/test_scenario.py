"""Scenarios run in a sandbox, tie out, and never touch the base data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from desk import scenario
from desk.config import CFG

SCENARIOS = Path(CFG.root) / "scenarios"
BASE_FILES = (
    "data/views/customs/cbp_entries.json",
    "data/views/bank/transactions.csv",
    "systems/books/history/2025.beancount",
    "data/answer_key/answer_key.json",
    "data/truth/world.json",
)


def _digest(rel: str) -> str:
    return hashlib.sha256((CFG.root / rel).read_bytes()).hexdigest()


@pytest.fixture
def base_hashes():
    return {rel: _digest(rel) for rel in BASE_FILES}


def test_rosy_baseline_ties_out_before_and_after_the_desk(tmp_path, base_hashes, clean_books):
    code, report = scenario.run_file(SCENARIOS / "rosy-baseline.yaml", llm="scripted",
                                     sandbox_dir=tmp_path, log=lambda *_: None)
    failed = [c for c in report["checks"] if not c["ok"]]
    assert code == 0 and not failed, json.dumps(failed, indent=1)

    names = {c["name"] for c in report["checks"]}
    assert "[before] operating account" in names and "[after] operating account" in names
    assert "[after] inventory" in names
    assert report["run"]["opened_by_invariants"] == []
    assert report["run"]["posted_entries"] == 3
    assert report["run"]["questions"][0]["to_role"] == "human_lawyer"

    # the sandbox holds the patched world; the repo's files are byte-identical to before
    sandbox = Path(report["sandbox"])
    assert "3300.00" not in (sandbox / "systems/books/history/2025.beancount").read_text()
    assert (sandbox / "report.json").exists()
    assert {rel: _digest(rel) for rel in BASE_FILES} == base_hashes


def test_rosy_pending_refund_ties_out_with_no_refund(tmp_path, base_hashes):
    code, report = scenario.run_file(SCENARIOS / "rosy-pending-refund.yaml",
                                     sandbox_dir=tmp_path, log=lambda *_: None)
    failed = [c for c in report["checks"] if not c["ok"]]
    assert code == 0 and not failed, json.dumps(failed, indent=1)
    assert "run" not in report
    assert any(c["name"] == "[before] no refund, no Treasury deposit" for c in report["checks"])
    # the known INV-2 false alarm is expected and named; nothing else fires
    gate = next(c for c in report["checks"] if c["name"] == "[before] invariant violations")
    assert gate["left"] == "1" and "INV-2" in gate["note"]
    assert {rel: _digest(rel) for rel in BASE_FILES} == base_hashes


def test_the_base_world_fails_the_rosy_expectations(tmp_path):
    """Sanity: the same checks on the unpatched world report the planted mistakes."""
    spec = scenario.load(SCENARIOS / "rosy-baseline.yaml")
    spec["name"] = "base-world-check"
    spec["patches"] = []
    cfg = scenario.materialize(spec, CFG, tmp_path)
    from desk import db, sync

    db.rebuild_erp(cfg)
    conn = db.desk_conn(cfg)
    try:
        sync.run(conn, cfg)
        checks = scenario.tie_outs(conn, cfg, spec, "before")
    finally:
        conn.close()
    failed = {c.name for c in checks if not c.ok}
    assert "refund receivable" in failed                         # PM-1
    assert "invariant violations" in failed
    assert "trial balance" not in failed and "bean-check" not in failed


def test_patch_operations(tmp_path):
    root = tmp_path / "w"
    (root / "d").mkdir(parents=True)
    (root / "d" / "rows.json").write_text(json.dumps([{"id": "A", "lines": [{"amount": 1}]}, {"id": "B"}]))
    (root / "d" / "t.csv").write_text("id,direction,amount,bal\n1,credit,10.00,0\n2,debit,4.00,0\n3,credit,1.00,0\n")
    (root / "d" / "x.txt").write_text("alpha beta alpha")

    scenario.apply_patch(root, {"file": "d/rows.json", "json_set": [
        {"where": {"id": "A"}, "set": {"lines.0.amount": 5, "new.deep": "x"}}],
        "json_delete": [{"where": {"id": "B"}}]})
    rows = json.loads((root / "d" / "rows.json").read_text())
    assert rows == [{"id": "A", "lines": [{"amount": 5}], "new": {"deep": "x"}}]

    scenario.apply_patch(root, {"file": "d/t.csv", "csv_set": [{"where": {"id": "2"}, "set": {"amount": "3.00"}}],
                                "csv_delete": [{"where": {"id": "3"}}],
                                "recompute_running_balance": {"balance": "bal", "amount": "amount",
                                                              "direction": "direction", "credit_value": "credit"}})
    assert (root / "d" / "t.csv").read_text() == "id,direction,amount,bal\n1,credit,10.00,10.00\n2,debit,3.00,7.00\n"

    scenario.apply_patch(root, {"file": "d/x.txt", "replace": [{"old": "alpha", "new": "gamma", "count": 2}]})
    assert (root / "d" / "x.txt").read_text() == "gamma beta gamma"
    with pytest.raises(scenario.ScenarioError):
        scenario.apply_patch(root, {"file": "d/x.txt", "replace": [{"old": "alpha", "new": "z"}]})
    with pytest.raises(scenario.ScenarioError):
        scenario.apply_patch(root, {"file": "d/rows.json", "json_set": [{"where": {"id": "Z"}, "set": {"x": 1}}]})
    scenario.apply_patch(root, {"file": "d/x.txt", "remove_file": True})
    assert not (root / "d" / "x.txt").exists()
