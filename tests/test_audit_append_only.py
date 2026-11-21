"""The audit log is append-only, and the change rides in the same transaction."""
from __future__ import annotations

import sqlite3

import pytest

from desk.perms import Denied
from desk.workflow import transition

from conftest import events


def test_events_cannot_be_updated(conn, item):
    event_id = conn.execute("SELECT MAX(event_id) FROM events").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE events SET reason = 'rewritten' WHERE event_id = ?", (event_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE events SET allowed = 1")


def test_events_cannot_be_deleted(conn, item):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM events WHERE event_id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM events")
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] > 0


def test_a_refused_attempt_survives_the_rollback_of_its_transaction(conn, item):
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    with pytest.raises(Denied):
        transition(conn, item["id"], "post", "ai_agent.seat1")
    after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert after > before
    refused = events(conn, action="post", subject=item["id"], allowed=False)
    assert len(refused) == 1
    # ... and the item itself did not move.
    assert conn.execute("SELECT state FROM work_items WHERE id=?",
                        (item["id"],)).fetchone()[0] == "NEW"


def test_the_change_and_its_event_are_one_transaction(conn, item, monkeypatch):
    """If the audit write fails, the state change is rolled back with it."""
    import desk.workflow as workflow

    real = workflow.log_event

    def explode(conn_, **kw):
        if kw.get("action") == "propose":
            raise sqlite3.OperationalError("disk full")
        return real(conn_, **kw)

    monkeypatch.setattr(workflow, "log_event", explode)
    with pytest.raises(sqlite3.OperationalError):
        transition(conn, item["id"], "propose", "ai_agent.seat1",
                   patch={"payload": {"x": 1}, "evidence": ["e"]})

    assert conn.execute("SELECT state FROM work_items WHERE id=?",
                        (item["id"],)).fetchone()[0] == "NEW"
    assert not events(conn, action="propose", subject=item["id"])


def test_every_event_carries_an_actor_action_and_allowed_flag(conn, item):
    for row in events(conn):
        assert row["actor"] and row["action"] and row["at"]
        assert row["allowed"] in (0, 1)
