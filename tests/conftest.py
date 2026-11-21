"""Shared fixtures. No test touches the network or data/."""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desk import db, llm, onboarding, personas, sync   # noqa: E402
from desk.__main__ import POSTED_HEADER        # noqa: E402
from desk.config import CFG                    # noqa: E402
from desk.scripted import onboarding_script, phase_script, segment   # noqa: E402
from desk.workflow import create_work_item      # noqa: E402

BANK_TXN = "BNK-0026"
REFUND = "RFD-001"


@pytest.fixture(autouse=True)
def clean_books():
    """Start every test from the 2-line header; leave the repo file as it was."""
    original = CFG.posted_file.read_text(encoding="utf-8")
    CFG.posted_file.write_text(POSTED_HEADER, encoding="utf-8")
    yield CFG.posted_file
    CFG.posted_file.write_text(original, encoding="utf-8")


@pytest.fixture
def cfg(tmp_path):
    """The real repo, but a throwaway desk DB and LLM cache."""
    return dataclasses.replace(
        CFG, desk_db=tmp_path / "desk.db", cache_db=tmp_path / "llm_cache.db"
    )


@pytest.fixture
def conn(cfg):
    connection = db.desk_conn(cfg)
    sync.run(connection, cfg)
    yield connection
    connection.close()


@pytest.fixture
def cache(cfg):
    """The LLM response cache, in its own throwaway file."""
    connection = db.cache_conn(cfg)
    yield connection
    connection.close()


@pytest.fixture
def onboarded(conn, cfg):
    """Policies proposed by the seats and approved by the controller."""
    client = llm.FakeLLM(script=onboarding_script())
    return onboarding.run(conn, client, cfg)


@pytest.fixture
def customer_item(conn):
    """Factory for a customer work item, the shape Seat 3 works on."""
    from desk.workflow import create_work_item

    def make(customer_id, kind="WI-CUST-CREDIT", **payload):
        row = conn.execute(
            "SELECT contract_id FROM golden_billing_customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        return create_work_item(
            conn, kind=kind, seat="seat3", actor="sync",
            payload={"customer_id": customer_id,
                     "contract_id": row["contract_id"] if row else None,
                     "refund_id": REFUND, **payload},
            evidence=["test"],
        )
    return make


@pytest.fixture
def item(conn):
    """A NEW WI-REFUND-DEPOSIT work item for BNK-0026, opened by sync."""
    txn = conn.execute(
        "SELECT * FROM golden_bank_txns WHERE txn_id = ?", (BANK_TXN,)
    ).fetchone()
    return create_work_item(
        conn, kind="WI-REFUND-DEPOSIT", seat="seat1", actor="sync",
        payload={"bank_txn_id": txn["txn_id"], "amount": f"{txn['amount']:.2f}",
                 "date": txn["date"]},
        evidence=[txn["raw_ref"]],
    )


def events(conn, *, action=None, subject=None, allowed=None):
    sql = "SELECT * FROM events WHERE 1=1"
    params: list = []
    if action is not None:
        sql += " AND action = ?"
        params.append(action)
    if subject is not None:
        sql += " AND subject = ?"
        params.append(subject)
    if allowed is not None:
        sql += " AND allowed = ?"
        params.append(1 if allowed else 0)
    return [dict(r) for r in conn.execute(sql + " ORDER BY event_id", params).fetchall()]


@pytest.fixture(autouse=True)
def no_creao_network(monkeypatch):
    """The suite never spends Creao credit: a real app run from inside a test is a bug."""
    from desk import creao

    def refuse(self, agent, inputs, key, **kw):
        raise AssertionError(f"a test tried to run the Creao agent {agent.name!r} over the network")

    if not hasattr(creao.HttpTransport, "run_unguarded"):
        creao.HttpTransport.run_unguarded = creao.HttpTransport.run     # for the transport's own test
    monkeypatch.setattr(creao.HttpTransport, "run", refuse)
