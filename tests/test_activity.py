"""The live-activity view is a reading of the audit log, nothing more."""
from __future__ import annotations

import json

from desk import activity, agent, llm
from desk.__main__ import TASK
from desk.scripted_seat1 import script

from conftest import BANK_TXN, events


def run_seat1(conn, item, cfg):
    return agent.run_seat(conn, "seat1", item["id"], llm.FakeLLM(script=script()),
                          TASK.format(bank_txn=BANK_TXN), cfg=cfg)


def test_idle_seats_before_anything_happens(conn):
    status = activity.seat_status(conn)
    assert set(status) == {"seat1", "seat2", "seat3"}
    assert all(s["mode"] == "idle" and s["work_item"] is None for s in status.values())
    snap = activity.snapshot(conn)
    assert snap["work_items"] == {} and snap["awaiting_controller"] == []
    assert snap["events"] >= 0


def test_the_feed_reads_modes_off_the_log(conn, item, cfg):
    run_seat1(conn, item, cfg)
    feed = activity.feed(conn, subject=item["id"])
    modes = [e["mode"] for e in feed]
    for expected in ("picking_up", "thinking", "computing", "proposing", "waiting_approval"):
        assert expected in modes, f"{expected} missing from {modes}"
    assert all(e["label"] and e["who"] for e in feed)
    assert all(not e["noise"] for e in feed)                       # can: rows hidden by default
    with_checks = activity.feed(conn, subject=item["id"], include_permission_checks=True)
    assert len(with_checks) > len(feed)
    assert any(e["mode"] == "permission" for e in with_checks)


def test_the_feed_pages_by_event_id(conn, item, cfg):
    run_seat1(conn, item, cfg)
    first = activity.feed(conn, limit=3)
    assert len(first) == 3
    rest = activity.feed(conn, after=first[-1]["event_id"])
    assert rest and rest[0]["event_id"] > first[-1]["event_id"]
    assert activity.last_event_id(conn) == max(e["event_id"] for e in first + rest)


def test_seat_status_shows_who_is_waiting_for_the_controller(conn, item, cfg):
    run = run_seat1(conn, item, cfg)
    status = activity.seat_status(conn)["seat1"]
    assert status["mode"] == "waiting_approval"
    assert status["work_item"] == item["id"]
    assert status["turns"] == run.turns >= 1
    assert activity.seat_status(conn)["seat2"]["mode"] == "idle"
    snap = activity.snapshot(conn)
    assert snap["awaiting_controller"] == [item["id"]]
    assert snap["work_items"] == {"PROPOSED": 1}


def test_a_seat_is_idle_again_once_its_item_is_posted(conn, item, cfg, clean_books):
    from desk import gateway, personas

    run_seat1(conn, item, cfg)
    assert activity.seat_status(conn)["seat1"]["mode"] == "waiting_approval"
    personas.ScriptedController.load(cfg).review(conn, item["id"])
    gateway.post(conn, item["id"], cfg=cfg)
    status = activity.seat_status(conn)["seat1"]
    assert status["mode"] == "idle" and status["work_item_state"] == "POSTED"
    assert item["id"] in status["label"]
    assert activity.snapshot(conn)["awaiting_controller"] == []


def test_every_model_turn_is_logged_with_its_tool_calls(conn, item, cfg):
    run = run_seat1(conn, item, cfg)
    turns = events(conn, action="agent:turn", subject=item["id"])
    assert len(turns) == run.turns
    called = [c["name"] for t in turns for c in json.loads(t["evidence"])["tool_calls"]]
    assert called == [c["name"] for c in run.tool_calls]
    assert all(t["actor"] == "ai_agent.seat1" for t in turns)


def test_labels_are_human_readable():
    def ev(action, allowed=True, reason="", evidence=None, actor="ai_agent.seat1", subject="WI-0001"):
        return {"event_id": 1, "at": "now", "actor": actor, "action": action, "subject": subject,
                "allowed": 1 if allowed else 0, "reason": reason,
                "evidence": json.dumps(evidence) if evidence is not None else None}

    mode, label = activity.classify(ev("tool:read_doc", evidence={"arguments": {"path": "data/reference/refund_policy.md"}}))
    assert mode == "investigating" and "refund_policy.md" in label and "Trade compliance" in label
    mode, label = activity.classify(ev("tool:split_refund", allowed=False, reason="refused: seat1 has no tool"))
    assert mode == "refused" and "refused" in label
    mode, label = activity.classify(ev("tool:ask", evidence={"arguments": {"to_role": "human_lawyer"}}))
    assert mode == "asking" and "Lawyer" in label
    assert activity.classify(ev("approve", actor="human_controller"))[0] == "approved"
    assert activity.classify(ev("post", actor="gateway"))[0] == "posted"
    assert activity.classify(ev("agent:finish", reason="seat1 finished with WI-0001 in PROPOSED"))[0] == "waiting_approval"
    assert activity.classify(ev("agent:finish", reason="seat3 finished with WI-0002 in NEEDS_INFO"))[0] == "waiting_answer"
    assert activity.classify(ev("can:approve", allowed=False, actor="ai_agent.seat1"))[0] == "permission"
    assert activity.who("ai_agent.seat3") == "Revenue accounting"
    assert activity.who("human_cfo") == "CFO"


def test_the_plain_reading_has_no_ids_paths_or_tool_names():
    """The feed a finance person reads: names instead of ids, titles instead of paths."""
    names = {"WI-0007": "‘Record the refund deposit’", "POL-L1@1": "the rule ‘refundable programmes’",
             "POL-L1": "the rule ‘refundable programmes’"}

    def ev(action, allowed=True, reason="", evidence=None, actor="ai_agent.seat1", subject="WI-0007"):
        return {"event_id": 1, "at": "now", "actor": actor, "action": action, "subject": subject,
                "allowed": 1 if allowed else 0, "reason": reason,
                "evidence": json.dumps(evidence) if evidence is not None else None}

    row = activity.enrich(ev("tool:read_doc", evidence={"arguments": {"path": "data/documents/inbox/2025-10-14-cbp-refund-notice.md"}}), names)
    assert row["plain"] == "Trade compliance is reading CBP refund notice" and not row["technical_only"]
    row = activity.enrich(ev("tool:run_sql", evidence={"arguments": {"sql": "SELECT 1"}}), names)
    assert row["plain"] == "Trade compliance is querying the records"
    row = activity.enrich(ev("tool:match_deposit"), names)
    assert row["plain"] == "Trade compliance is matching the deposit to the refund notice"
    row = activity.enrich(ev("tool:propose_policy", evidence={"arguments": {"policy_id": "POL-L1"}}), names)
    assert row["plain"] == "Trade compliance proposed the rule ‘refundable programmes’"
    row = activity.enrich(ev("approve", actor="human_controller", reason="scripted controller: approve (personas/scripts/controller.yaml)"), names)
    assert row["plain"] == "CFO (scripted) approved ‘Record the refund deposit’"
    row = activity.enrich(ev("approve", actor="ai_agent.seat1", allowed=False, reason="separation of duties: seat1 proposed WI-0007"), names)
    assert row["plain"].startswith("Trade compliance may not approve") and "WI-0007" not in row["plain"] and "seat1" not in row["plain"]
    row = activity.enrich(ev("invariant_gate", actor="code", reason="no new or worsened invariant violations"), names)
    assert row["plain"] == "Checks passed on ‘Record the refund deposit’"
    row = activity.enrich(ev("post", actor="gateway"), names)
    assert row["plain"] == "Posted to the books: ‘Record the refund deposit’"
    # rows only a developer reads are marked, and the model turn that only chose tools is one of them
    assert activity.enrich(ev("run:step", actor="sync", reason="deposit started"), names)["technical_only"]
    assert activity.enrich(ev("agent:turn", reason="turn 1", evidence={"tool_calls": [{"name": "run_sql"}]}), names)["technical_only"]
    assert not activity.enrich(ev("agent:turn", reason="The deposit matches the notice."), names)["technical_only"]
    for text in (activity.humanize("seat2 applied POL-L1@1 on WI-0007 per data/reference/refund_policy.md", names),):
        assert text == "Cost accounting applied the rule ‘refundable programmes’ on ‘Record the refund deposit’ per Accounting policy"
