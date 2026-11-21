"""reset rebuilds the ERP, restores the posted book and drops the desk DB."""
from __future__ import annotations

import sqlite3

from desk import db, gateway
from desk.__main__ import POSTED_HEADER, main
from desk.config import CFG


def test_reset_restores_the_posted_book_to_its_header(clean_books):
    clean_books.write_text(POSTED_HEADER + '\n2025-10-15 * "junk entry"\n  Assets:X 1 USD\n')
    assert main(["reset"]) == 0

    text = clean_books.read_text()
    assert text == POSTED_HEADER
    assert len(text.strip().splitlines()) == 2
    assert text.startswith("; Gateway-only file.")
    assert "junk entry" not in text
    assert gateway.bean_check(CFG).ok


def test_reset_drops_the_desk_db_and_rebuilds_the_erp(clean_books):
    conn = db.desk_conn(CFG)
    conn.execute("BEGIN IMMEDIATE")
    db.log_event(conn, actor="sync", action="marker", subject=None, allowed=True)
    conn.execute("COMMIT")
    conn.close()
    assert CFG.desk_db.exists()

    assert main(["reset"]) == 0

    assert not CFG.desk_db.exists()
    assert CFG.erp_db.exists()

    erp = sqlite3.connect(CFG.erp_db)
    try:
        assert erp.execute("SELECT COUNT(*) FROM shipments").fetchone()[0] == 10
        assert erp.execute("SELECT COUNT(*) FROM inventory_layers WHERE duty_per_unit > 0"
                           ).fetchone()[0] == 10
    finally:
        erp.close()


def test_reset_then_a_fresh_desk_db_has_no_events(clean_books):
    assert main(["reset"]) == 0
    conn = db.desk_conn(CFG)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == 0
    finally:
        conn.close()
    db.drop_desk_db(CFG)
