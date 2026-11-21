"""The gateway. The only component that writes to the books.

It posts APPROVED work items and nothing else, appends to
systems/books/posted/desk.beancount with work_item and approved_by metadata,
then runs bean-check. If bean-check fails the appended entry is removed, the
failure is logged and the item stays APPROVED.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .config import CFG, Config
from .db import log_event_now
from .perms import Denied, require
from .workflow import APPROVED, get_item, transition

ACTOR = "gateway"
ACCOUNT_WIDTH = 40

# The gateway's own attestation. These two keys say which approved work item an
# entry belongs to and who approved it, so nothing else may set them - not the
# seat that drafted the entry, and not its metadata.
RESERVED_METADATA = ("work_item", "approved_by")


class PostFailed(Exception):
    """bean-check rejected the appended entry; the books were left unchanged."""

    def __init__(self, reason: str, *, item_id: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.item_id = item_id


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    output: str


def bean_check(cfg: Config = CFG) -> CheckResult:
    """Run bean-check over the main ledger."""
    binary = shutil.which("bean-check")
    cmd = [binary, str(cfg.beancount_file)] if binary else [
        sys.executable, "-c",
        "import sys; from beancount.scripts.check import main; sys.argv=['bean-check', sys.argv[1]]; main()",
        str(cfg.beancount_file),
    ]
    done = subprocess.run(cmd, capture_output=True, text=True)
    output = (done.stdout + done.stderr).strip()
    return CheckResult(done.returncode == 0 and not output, output)


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def render_entry(item: dict[str, Any]) -> str:
    """Render a work item's journal entry as Beancount text."""
    entry = (item.get("payload") or {}).get("journal_entry")
    if not entry:
        raise PostFailed(f"{item['id']} has no journal_entry in its payload", item_id=item["id"])

    postings = entry.get("postings") or []
    if len(postings) < 2:
        raise PostFailed(f"{item['id']} needs at least two postings", item_id=item["id"])

    total = sum((_money(p["amount"]) for p in postings), Decimal("0.00"))
    if total != Decimal("0.00"):
        raise PostFailed(
            f"{item['id']} postings do not balance: they sum to {total}", item_id=item["id"]
        )

    # Drafted metadata first, the gateway's stamps last, so a proposal can never
    # overwrite the record of which work item it came from or who approved it.
    metadata = {
        k: str(v) for k, v in (entry.get("metadata") or {}).items()
        if k not in RESERVED_METADATA
    }
    metadata["work_item"] = item["id"]
    metadata["approved_by"] = item["approved_by"]

    lines = [f'{entry["date"]} * "{entry["narration"]}"']
    lines += [f'  {key}: "{metadata.pop(key)}"' for key in RESERVED_METADATA]
    lines += [f'  {key}: "{value}"' for key, value in metadata.items()]
    for posting in postings:
        amount = f'{_money(posting["amount"]):,.2f} {posting.get("currency", "USD")}'
        lines.append(f'  {posting["account"]:<{ACCOUNT_WIDTH}}{amount:>18}')
    return "\n".join(lines) + "\n"


def post(
    conn: sqlite3.Connection,
    item_id: str,
    actor: str = ACTOR,
    cfg: Config = CFG,
) -> dict[str, Any]:
    """Post an APPROVED work item to the books. Only the gateway may call this."""
    item = get_item(conn, item_id)
    if item is None:
        raise Denied(f"no such work item {item_id}", actor=actor, action="post", subject=item_id)

    # Permission first: a non-gateway actor never gets as far as the file.
    decision = require(conn, actor, "post", item, subject=item_id)

    if item["state"] != APPROVED:
        reason = f"refused: {item_id} is {item['state']}; the gateway posts APPROVED items only"
        log_event_now(conn, actor=actor, action="post", subject=item_id, allowed=False,
                      reason=reason)
        raise Denied(reason, actor=actor, action="post", subject=item_id)

    text = render_entry(item)
    path = cfg.posted_file
    before_bytes = path.stat().st_size
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + text)

    result = bean_check(cfg)
    if not result.ok:
        with path.open("r+", encoding="utf-8") as handle:
            handle.truncate(before_bytes)
        reason = f"refused: bean-check failed, appended entry removed: {result.output[:500]}"
        log_event_now(conn, actor=actor, action="post", subject=item_id, allowed=False,
                      reason=reason, diff={"before": None, "after": text})
        raise PostFailed(reason, item_id=item_id)

    posted = transition(
        conn, item_id, "post", actor,
        reason="posted to systems/books/posted/desk.beancount; bean-check passed",
        evidence=(item.get("evidence") or []) + [path.relative_to(cfg.root).as_posix()],
        decision=decision,
    )
    return {"item": posted, "entry": text}
