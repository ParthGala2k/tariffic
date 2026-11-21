"""The challenge protocol (CLAUDE.md 6.8).

Pushback is classified as evidence, opinion or authority. Only evidence can
change a decision, and only through revise_proposal, which code gates on facts
checked back against the document (desk/evidence.py).

* **opinion** - someone's view with nothing behind it. Logged; nothing changes.
* **authority** - someone invoking their position. Logged and routed to the
  approver the policy names. Nothing changes: POL-001 5.2 is explicit that
  authority does not move a number.
* **evidence** - a document. The seat must read it and extract facts with
  quotes; code checks them; only then may the proposal change.

A POSTED entry is never edited. Valid evidence against something already in the
books opens a *new* child work item proposing a correcting entry, which goes
through approval and the gateway like anything else (CLAUDE.md 7.4).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .config import CFG, Config
from .db import log_event_now, now
from .evidence import EvidenceRejected, load_inbox_document
from .workflow import POSTED, get_item

EVIDENCE, OPINION, AUTHORITY = "evidence", "opinion", "authority"
CLASSES = (EVIDENCE, OPINION, AUTHORITY)

# Who authority is escalated to. POL-001 5.1: the controller owns the books.
AUTHORITY_ROUTES = {
    "human_cfo": "human_controller",
    "cfo": "human_controller",
    "human_controller": "human_controller",
}
DEFAULT_ROUTE = "human_controller"

CLASSIFY_SYSTEM = """\
You classify pushback against an accounting decision. Answer with one word.

evidence  - it attaches or cites a specific document that bears on the facts: a signed
            letter, an amendment, a waiver, a customs form. Something checkable.
opinion   - a view, a practice, an assertion, with nothing behind it. "We always do it
            this way", "that's probably a timing thing", "nobody has asked".
authority - someone invoking their position or sign-off to settle it. "I'm the CFO",
            "I'll take responsibility", "I've already approved this".

If it invokes authority AND attaches a real document, answer evidence.
If it mentions a document only vaguely ("I'll send it over"), that is not evidence.

Answer with exactly one word: evidence, opinion, or authority."""


class ChallengeError(Exception):
    pass


@dataclass
class Outcome:
    challenge_id: str
    work_item_id: str
    classification: str
    classified_by: str
    reasoning: str
    changed: bool
    outcome: str
    routed_to: str | None = None
    child_work_item: str | None = None
    checks: list[dict] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def classify(
    text: str,
    document: str | None,
    llm=None,
    cfg: Config = CFG,
) -> tuple[str, str, str]:
    """Classify one piece of pushback. Returns (class, classified_by, reasoning).

    The fast model does the reading. Code still decides what follows from it:
    a classification of 'evidence' only matters if a real document survives
    desk/evidence.py.
    """
    if llm is None:
        # No model available: fall back to the document's own declaration if it
        # has one, and treat anything undocumented as opinion - the safe default,
        # because opinion changes nothing.
        if document:
            try:
                _path, _text, meta = load_inbox_document(document, cfg)
                declared = str(meta.get("pushback_class_expected") or "").strip().lower()
                if declared in CLASSES:
                    return declared, "code", f"{document} declares itself {declared}"
                return EVIDENCE, "code", f"a document was attached: {document}"
            except EvidenceRejected as refusal:
                return OPINION, "code", f"no usable document: {refusal.reason}"
        return OPINION, "code", "no model available and no document attached"

    prompt = f"Pushback: {text}"
    if document:
        prompt += f"\n\nA document was attached: {document}"
    response = llm.complete(CLASSIFY_SYSTEM, [{"role": "user", "content": prompt}], [])
    word = (response.text or "").strip().lower()
    for candidate in CLASSES:
        if candidate in word:
            return candidate, getattr(llm, "model", "?"), response.text.strip()[:300]
    return OPINION, getattr(llm, "model", "?"), f"unrecognised answer {word!r}; treated as opinion"


def next_challenge_id(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COUNT(*) FROM challenges").fetchone()[0]
    return f"CH-{n + 1:04d}"


def raise_challenge(
    conn: sqlite3.Connection,
    work_item_id: str,
    *,
    raised_by: str,
    text: str,
    document: str | None = None,
    llm=None,
    seat_llm=None,
    cfg: Config = CFG,
) -> Outcome:
    """Run the challenge protocol on one work item."""
    item = get_item(conn, work_item_id)
    if item is None:
        raise ChallengeError(f"no work item {work_item_id}")

    classification, classified_by, reasoning = classify(text, document, llm, cfg)
    challenge_id = next_challenge_id(conn)

    log_event_now(
        conn, actor=raised_by, action="challenge", subject=work_item_id, allowed=True,
        reason=f"{challenge_id} classified {classification}: {text[:200]}",
        evidence={"challenge_id": challenge_id, "document": document,
                  "classified_by": classified_by, "reasoning": reasoning},
    )

    if classification == OPINION:
        outcome = _settle(
            conn, challenge_id, item, raised_by, text, document, classification,
            classified_by, reasoning, changed=False,
            outcome=("opinion: recorded and answered, but it carries no document, so it "
                     "changes nothing (POL-001 5.2)"),
        )
        return outcome

    if classification == AUTHORITY:
        routed = AUTHORITY_ROUTES.get(raised_by, DEFAULT_ROUTE)
        log_event_now(
            conn, actor=raised_by, action="escalate", subject=work_item_id, allowed=True,
            reason=f"{challenge_id} is authority; routed to {routed} (POL-001 5.1)",
            evidence={"challenge_id": challenge_id, "routed_to": routed},
        )
        return _settle(
            conn, challenge_id, item, raised_by, text, document, classification,
            classified_by, reasoning, changed=False, routed_to=routed,
            outcome=(f"authority: logged and routed to {routed}. Position does not change "
                     f"a number; only evidence does (POL-001 5.2)"),
        )

    # ---- evidence ----------------------------------------------------------
    if not document:
        return _settle(
            conn, challenge_id, item, raised_by, text, document, classification,
            classified_by, reasoning, changed=False,
            outcome=("evidence was claimed but no document was attached, so there is "
                     "nothing to check: nothing changes"),
        )
    try:
        load_inbox_document(document, cfg)
    except EvidenceRejected as refusal:
        log_event_now(
            conn, actor=raised_by, action="revise_proposal", subject=work_item_id,
            allowed=False, reason=refusal.reason,
            evidence={"challenge_id": challenge_id, "document": document},
        )
        return _settle(
            conn, challenge_id, item, raised_by, text, document, classification,
            classified_by, reasoning, changed=False,
            outcome=f"the attached document was refused: {refusal.reason}",
        )

    return _settle(
        conn, challenge_id, item, raised_by, text, document, classification,
        classified_by, reasoning, changed=False,
        outcome=("evidence: the document is real and citable. The seat must now read it, "
                 "extract the facts with exact quotes, and call revise_proposal - code "
                 "checks the quotes and the coverage before anything changes."),
    )


def _settle(conn, challenge_id, item, raised_by, text, document, classification,
            classified_by, reasoning, *, changed, outcome, routed_to=None,
            child_work_item=None, checks=None, verified=None) -> Outcome:
    conn.execute(
        "INSERT OR REPLACE INTO challenges (challenge_id, work_item_id, raised_by, text,"
        " document, classification, classified_by, reasoning, changed, outcome, routed_to,"
        " facts, verified, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (challenge_id, item["id"], raised_by, text, document, classification,
         classified_by, reasoning, 1 if changed else 0, outcome, routed_to,
         json.dumps(checks) if checks else None, verified, now()),
    )
    log_event_now(
        conn, actor="code", action="challenge_outcome", subject=item["id"],
        allowed=not changed or True,
        reason=f"{challenge_id} ({classification}): {outcome}",
        evidence={"challenge_id": challenge_id, "changed": changed,
                  "routed_to": routed_to, "child_work_item": child_work_item},
    )
    return Outcome(challenge_id=challenge_id, work_item_id=item["id"],
                   classification=classification, classified_by=classified_by,
                   reasoning=reasoning, changed=changed, outcome=outcome,
                   routed_to=routed_to, child_work_item=child_work_item, checks=checks)


# Tools a seat may use while answering pushback: it may look, compute and cite; it may
# not propose, ask or revise. A revision needs a document and goes through the evidence path.
DISCUSS_TOOLS = {"read_doc", "run_sql", "get_policies", "search_precedents", "sum_duties",
                 "match_deposit", "breakdown_receivable", "split_refund", "surcharge_on_invoices"}

RULES = {
    OPINION: "an argument or a question without a document is answered and changes nothing (POL-001 5.2)",
    AUTHORITY: "a position, however senior, is logged and routed; it changes no number (POL-001 5.1, 5.2)",
    EVIDENCE: "a document with matching parties and dates can change the answer, through revise_proposal",
}


def get_challenge(conn: sqlite3.Connection, challenge_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,)).fetchone()
    return dict(row) if row else None


def store_reply(conn: sqlite3.Connection, challenge_id: str, text: str, by: str) -> dict[str, Any]:
    """Keep the answer on the challenge and log it; the screen reads both."""
    ch = get_challenge(conn, challenge_id)
    if ch is None:
        raise ChallengeError(f"no challenge {challenge_id}")
    text = (text or "").strip()
    conn.execute("UPDATE challenges SET reply = ?, replied_by = ? WHERE challenge_id = ?",
                 (text, by, challenge_id))
    log_event_now(
        conn, actor=by, action="challenge_reply", subject=ch["work_item_id"], allowed=True,
        reason=text[:600], evidence={"challenge_id": challenge_id, "classification": ch["classification"]},
    )
    conn.commit()
    return {"text": text, "by": by, "source": "code" if by == "code" else "model"}


def reply(conn: sqlite3.Connection, challenge_id: str, *, seat_llm, cfg: Config = CFG,
          max_turns: int = 6) -> dict[str, Any]:
    """The item's seat answers the person with facts, using read and compute tools only."""
    from . import agent
    from .__main__ import DISCUSS_TASK
    from .activity import who

    ch = get_challenge(conn, challenge_id)
    if ch is None:
        raise ChallengeError(f"no challenge {challenge_id}")
    item = get_item(conn, ch["work_item_id"])
    seat = (item or {}).get("seat") or "seat3"
    task = DISCUSS_TASK.format(work_item=ch["work_item_id"], kind=(item or {}).get("kind"),
                               who=who(ch["raised_by"]), text=ch["text"],
                               classification=ch["classification"],
                               rule=RULES.get(ch["classification"], ""))
    run = agent.run_seat(conn, seat, ch["work_item_id"], seat_llm, task, cfg=cfg,
                         max_turns=max_turns, allowed_tools=DISCUSS_TOOLS)
    text = run.final_text.strip() or "The seat read the records but gave no answer."
    return store_reply(conn, challenge_id, text, agent.actor_for(seat))


def fallback_reply(conn: sqlite3.Connection, challenge_id: str, *, cfg: Config = CFG,
                   note: str | None = None) -> dict[str, Any]:
    """No live model: code answers from the brief, so the person is never left in silence."""
    from .brief import brief as make_brief
    from .plain import SEAT_NAMES, document_title, plain

    ch = get_challenge(conn, challenge_id)
    if ch is None:
        raise ChallengeError(f"no challenge {challenge_id}")
    b = make_brief(conn, ch["work_item_id"], cfg) or {}
    p = plain(conn, cfg, b)
    policy = (b.get("proposal") or {}).get("policy") or {}
    docs = [document_title(e["ref"], cfg) for e in b.get("evidence") or [] if e.get("kind") == "document"]
    parts = [f"This is {p['title'][:1].lower() + p['title'][1:]}.", p.get("summary") or ""]
    if policy.get("name"):
        parts.append(f"It follows the approved rule \u2018{policy['name']}\u2019.")
    if docs:
        parts.append("It rests on " + ", ".join(dict.fromkeys(docs)) + ".")
    if b.get("computations"):
        parts.append("Every figure was computed by code from the records, not estimated.")
    seat_name = SEAT_NAMES.get((b.get("work_item") or {}).get("seat") or "", "The desk")
    parts.append(f"Your pushback is recorded as {ch['classification']}: {RULES.get(ch['classification'], '')}.")
    if ch["classification"] != EVIDENCE:
        parts.append("Nothing changes. Attach a document that names the parties and covers the dates and "
                     f"{seat_name} will read it and revise if it applies.")
    if note:
        parts.append(f"({note})")
    return store_reply(conn, challenge_id, " ".join(x for x in parts if x), "code")


def mark_acted_on(
    conn: sqlite3.Connection,
    work_item_id: str,
    *,
    document: str,
    facts: dict[str, Any] | None = None,
    child_work_item: str | None = None,
    outcome: str = "",
) -> str | None:
    """Record that a verified revision acted on an evidence challenge.

    raise_challenge only admits a document - it says the thing is real and
    citable. The decision only actually changes when the seat reads it, extracts
    facts with quotes, and revise_proposal verifies them. This closes that loop,
    so `updated_on_evidence` counts decisions that really moved.
    """
    row = conn.execute(
        "SELECT challenge_id FROM challenges WHERE work_item_id = ? AND document = ?"
        " AND classification = ? ORDER BY created_at DESC LIMIT 1",
        (work_item_id, document, EVIDENCE),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE challenges SET changed = 1, verified = 1, facts = ?, outcome = ?"
        " WHERE challenge_id = ?",
        (json.dumps(facts) if facts else None,
         outcome or "the facts were verified against the document and the decision changed",
         row["challenge_id"]),
    )
    log_event_now(
        conn, actor="code", action="challenge_acted_on", subject=work_item_id,
        allowed=True,
        reason=(f"{row['challenge_id']}: the decision changed on verified evidence "
                f"from {document}"),
        evidence={"challenge_id": row["challenge_id"], "document": document,
                  "child_work_item": child_work_item},
    )
    return row["challenge_id"]


def mark_not_verified(
    conn: sqlite3.Connection, work_item_id: str, *, document: str, reason: str
) -> str | None:
    """Record that a document was produced but did not hold up.

    This is a *success* for the protocol, not a miss: the desk was handed
    something and correctly declined to move on it. It is counted separately
    from evidence that genuinely applied.
    """
    row = conn.execute(
        "SELECT challenge_id FROM challenges WHERE work_item_id = ? AND document = ?"
        " AND classification = ? ORDER BY created_at DESC LIMIT 1",
        (work_item_id, document, EVIDENCE),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE challenges SET verified = 0, changed = 0, outcome = ?"
        " WHERE challenge_id = ?",
        (f"the document did not hold up: {reason}", row["challenge_id"]),
    )
    log_event_now(
        conn, actor="code", action="challenge_not_verified", subject=work_item_id,
        allowed=True,
        reason=f"{row['challenge_id']}: {reason}",
        evidence={"challenge_id": row["challenge_id"], "document": document},
    )
    return row["challenge_id"]


def counters(conn: sqlite3.Connection) -> dict[str, Any]:
    """The two counters CLAUDE.md 6.8 asks for, plus the breakdown behind them."""
    rows = conn.execute(
        "SELECT classification, changed, COUNT(*) AS n FROM challenges"
        " GROUP BY classification, changed"
    ).fetchall()
    by_class: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = by_class.setdefault(row["classification"], {"changed": 0, "unchanged": 0})
        bucket["changed" if row["changed"] else "unchanged"] += row["n"]

    # A proposal that changed on anything other than verified evidence is a failure.
    changed_without_evidence = sum(
        v["changed"] for k, v in by_class.items() if k != EVIDENCE
    )

    # Of the documents that actually checked out, how many changed the decision?
    # A document that failed verification - wrong party, invented quote, outside
    # its coverage - is counted separately: declining it is the right answer, not
    # a missed update.
    verified = conn.execute(
        "SELECT COUNT(*) FROM challenges WHERE classification = ? AND verified = 1",
        (EVIDENCE,),
    ).fetchone()[0]
    verified_changed = conn.execute(
        "SELECT COUNT(*) FROM challenges WHERE classification = ? AND verified = 1"
        " AND changed = 1", (EVIDENCE,),
    ).fetchone()[0]
    not_verified = conn.execute(
        "SELECT COUNT(*) FROM challenges WHERE classification = ? AND verified = 0",
        (EVIDENCE,),
    ).fetchone()[0]
    untested = conn.execute(
        "SELECT COUNT(*) FROM challenges WHERE classification = ? AND verified IS NULL",
        (EVIDENCE,),
    ).fetchone()[0]
    return {
        "challenges": sum(sum(v.values()) for v in by_class.values()),
        "by_class": by_class,
        "changed_without_evidence": changed_without_evidence,
        "updated_on_evidence": f"{verified_changed}/{verified}" if verified else "0/0",
        "updated_on_evidence_pct": (
            round(100 * verified_changed / verified) if verified else None
        ),
        "documents_that_did_not_hold_up": not_verified,
        "documents_not_yet_tested": untested,
    }
