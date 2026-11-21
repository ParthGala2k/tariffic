"""The challenge protocol: only evidence moves a number."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from desk import challenge, gateway, llm, personas
from desk.agent import run_seat
from desk.challenge import AUTHORITY, EVIDENCE, OPINION
from desk.llm import LLMResponse
from desk.scripted import WAIVER_FACTS, segment
from desk.tools import Ctx, dispatch
from desk.workflow import POSTED, get_item

from conftest import REFUND, events

CFO_EMAIL = "data/documents/inbox/2025-10-16-cfo-email.md"
WAIVER = "data/documents/inbox/2025-10-18-harbor-waiver.md"
CON2 = "data/documents/contracts/CON-002-harbor.md"


class ClassifierLLM:
    """A stand-in for the fast model: answers with the word it is given."""

    provider, model = "fake", "fast-model"

    def __init__(self, answer: str):
        self.answer = answer
        self.prompts: list[str] = []

    def complete(self, system, messages, tools):
        self.prompts.append(messages[-1]["content"])
        return LLMResponse(text=self.answer)


@pytest.fixture
def harbor(conn, cfg, onboarded, customer_item):
    """Harbor's close ticket, after the lawyer has answered."""
    question = customer_item("CUST-002", kind="WI-CUST-002-QUESTION")
    run_seat(conn, "seat3", question["id"],
             llm.FakeLLM(script=segment("harbor_question")), "decide", cfg=cfg)
    return customer_item(
        "CUST-002", kind="WI-CUST-002-CLOSE",
        detail={"credit_notes": [{"invoice": "INV-1003", "amount": "576.00"}],
                "pre_waiver_amount": "576.00"},
    )


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #

def test_authority_is_logged_routed_and_changes_nothing(conn, cfg, harbor):
    outcome = challenge.raise_challenge(
        conn, harbor["id"], raised_by="human_cfo",
        text=("We are not crediting customers. The surcharge was pricing. "
              "I'm the CFO and I'm signing off."),
        document=CFO_EMAIL, llm=ClassifierLLM("authority"), cfg=cfg)

    assert outcome.classification == AUTHORITY
    assert outcome.changed is False
    assert outcome.routed_to == "human_controller"
    assert "does not change a number" in outcome.outcome
    assert events(conn, action="escalate", subject=harbor["id"])
    assert get_item(conn, harbor["id"])["payload"].get("revised_amount") is None


def test_opinion_is_logged_and_changes_nothing(conn, cfg, harbor):
    outcome = challenge.raise_challenge(
        conn, harbor["id"], raised_by="human_cfo",
        text="Our figure is right, we see this all the time. No action needed.",
        llm=ClassifierLLM("opinion"), cfg=cfg)

    assert outcome.classification == OPINION
    assert outcome.changed is False
    assert "changes nothing" in outcome.outcome
    assert not events(conn, action="escalate", subject=harbor["id"])


def test_evidence_is_admitted_but_does_not_change_anything_by_itself(conn, cfg, harbor):
    """Admitting a document is not the same as acting on it."""
    outcome = challenge.raise_challenge(
        conn, harbor["id"], raised_by="human_controller",
        text="Harbor signed a letter in June waiving this.",
        document=WAIVER, llm=ClassifierLLM("evidence"), cfg=cfg)

    assert outcome.classification == EVIDENCE
    assert outcome.changed is False
    assert "revise_proposal" in outcome.outcome
    assert get_item(conn, harbor["id"])["payload"].get("revised_amount") is None


def test_evidence_claimed_with_no_document_changes_nothing(conn, cfg, harbor):
    outcome = challenge.raise_challenge(
        conn, harbor["id"], raised_by="human_cfo",
        text="There's a waiver somewhere, I'll send it over later this week.",
        llm=ClassifierLLM("evidence"), cfg=cfg)
    assert outcome.changed is False
    assert "no document was attached" in outcome.outcome


def test_a_document_outside_the_inbox_is_refused_as_evidence(conn, cfg, harbor):
    outcome = challenge.raise_challenge(
        conn, harbor["id"], raised_by="human_controller",
        text="The contract itself says so.", document=CON2,
        llm=ClassifierLLM("evidence"), cfg=cfg)
    assert outcome.changed is False
    assert "not an inbox document" in outcome.outcome
    assert events(conn, action="revise_proposal", subject=harbor["id"], allowed=False)


def test_the_classifier_falls_back_to_opinion_without_a_model(conn, cfg, harbor):
    """No model and no document: the safe default, because opinion changes nothing."""
    outcome = challenge.raise_challenge(
        conn, harbor["id"], raised_by="human_cfo", text="Just do it my way.",
        llm=None, cfg=cfg)
    assert outcome.classification == OPINION
    assert outcome.changed is False


def test_an_unrecognised_answer_is_treated_as_opinion(conn, cfg, harbor):
    outcome = challenge.raise_challenge(
        conn, harbor["id"], raised_by="human_cfo", text="...",
        llm=ClassifierLLM("I'm not sure, possibly something else"), cfg=cfg)
    assert outcome.classification == OPINION
    assert outcome.changed is False


# --------------------------------------------------------------------------- #
# revise_proposal: the gate
# --------------------------------------------------------------------------- #

def test_the_waiver_takes_harbor_to_nil(conn, cfg, harbor):
    run = run_seat(conn, "seat3", harbor["id"],
                   llm.FakeLLM(script=segment("harbor_close")),
                   "settle Harbor on the waiver", cfg=cfg)
    item = get_item(conn, harbor["id"])
    assert item["payload"]["revised_amount"] == "0.00"
    revision = item["payload"]["revision"]
    assert revision["doc_id"] == "DOC-WAIVER-001"
    assert len(revision["checks"]) >= 7
    assert all(c["passed"] for c in revision["checks"])
    # the facts and their quotes are kept, for Phase 5 forensics
    assert revision["facts"]["coverage_cutoff_date"]["value"] == "2025-06-30"
    assert "June 30, 2025" in revision["facts"]["coverage_cutoff_date"]["quote"]


def test_the_waiver_changes_harbor_only(conn, cfg, onboarded, customer_item):
    """A document only counts for the party it names."""
    bean = customer_item("CUST-001", kind="WI-CUST-001-CREDIT",
                         detail={"credit_notes": [{"invoice": "INV-1001",
                                                   "amount": "1152.00"}]})
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3",
              work_item_id=bean["id"], cfg=cfg)
    ok, content = dispatch(ctx, "revise_proposal", {
        "evidence_doc": WAIVER,
        "extracted_facts": WAIVER_FACTS,
        "rationale": "trying to apply Harbor's waiver to Bean Street",
        "revised_amount": "0.00",
    })
    assert not ok
    assert "Harbor Roasters" in content
    assert "CUST-001" in content or "Bean Street" in content
    assert get_item(conn, bean["id"])["payload"].get("revised_amount") is None


def test_a_fabricated_quote_is_refused(conn, cfg, harbor):
    """The model's reading is checked back against the document."""
    facts = json.loads(json.dumps(WAIVER_FACTS))
    facts["effect"]["quote"] = "Distributor waives absolutely every claim of any kind"
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3",
              work_item_id=harbor["id"], cfg=cfg)
    ok, content = dispatch(ctx, "revise_proposal", {
        "evidence_doc": WAIVER, "extracted_facts": facts,
        "rationale": "invented", "revised_amount": "0.00",
    })
    assert not ok
    assert "does not appear in" in content
    assert get_item(conn, harbor["id"])["payload"].get("revised_amount") is None
    assert events(conn, action="revise_proposal", subject=harbor["id"], allowed=False)


def test_an_invoice_outside_the_coverage_window_is_refused(conn, cfg, onboarded,
                                                            customer_item):
    """Code does the date arithmetic, not the model."""
    conn.execute("UPDATE golden_billing_invoices SET invoice_date = '2025-09-01'"
                 " WHERE invoice_id = 'INV-1003'")
    item = customer_item("CUST-002", kind="WI-CUST-002-CLOSE",
                         detail={"credit_notes": [{"invoice": "INV-1003",
                                                   "amount": "576.00"}]})
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "revise_proposal", {
        "evidence_doc": WAIVER, "extracted_facts": WAIVER_FACTS,
        "rationale": "the waiver covers it", "revised_amount": "0.00",
    })
    assert not ok
    assert "covers charges up to 2025-06-30" in content
    assert "INV-1003 dated 2025-09-01" in content


def test_revise_proposal_needs_a_document_at_all(conn, cfg, harbor):
    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3",
              work_item_id=harbor["id"], cfg=cfg)
    ok, content = dispatch(ctx, "revise_proposal", {
        "evidence_doc": "data/documents/inbox/2025-10-16-cfo-email.md",
        "extracted_facts": WAIVER_FACTS,
        "rationale": "the CFO says so", "revised_amount": "0.00",
    })
    assert not ok and "does not appear in" in content


def test_the_counters_say_what_happened(conn, cfg, harbor):
    challenge.raise_challenge(conn, harbor["id"], raised_by="human_cfo",
                              text="I'm signing off on no credits.", document=CFO_EMAIL,
                              llm=ClassifierLLM("authority"), cfg=cfg)
    challenge.raise_challenge(conn, harbor["id"], raised_by="human_cfo",
                              text="We see this all the time.",
                              llm=ClassifierLLM("opinion"), cfg=cfg)
    challenge.raise_challenge(conn, harbor["id"], raised_by="human_controller",
                              text="Here is the signed waiver.", document=WAIVER,
                              llm=ClassifierLLM("evidence"), cfg=cfg)
    run_seat(conn, "seat3", harbor["id"], llm.FakeLLM(script=segment("harbor_close")),
             "settle it", cfg=cfg)

    counters = challenge.counters(conn)
    assert counters["challenges"] == 3
    assert counters["changed_without_evidence"] == 0
    assert counters["updated_on_evidence"] == "1/1"
    assert counters["updated_on_evidence_pct"] == 100
    assert counters["by_class"][AUTHORITY]["changed"] == 0
    assert counters["by_class"][OPINION]["changed"] == 0
    assert counters["by_class"][EVIDENCE]["changed"] == 1


# --------------------------------------------------------------------------- #
# a posted entry is never edited
# --------------------------------------------------------------------------- #

def test_evidence_against_a_posted_entry_opens_a_correcting_work_item(
        conn, cfg, onboarded, customer_item, clean_books):
    """The books are append-only: a correction is a new entry, not an edit."""
    from test_invariants import make_proposal

    item = customer_item("CUST-002", kind="WI-CUST-002-CLOSE",
                         detail={"credit_notes": [{"invoice": "INV-1003",
                                                   "amount": "576.00"}]})
    payload = dict(item["payload"])
    payload["journal_entry"] = {
        "date": "2025-10-15", "narration": "Harbor credit",
        "postings": [{"account": "Income:Sales:TariffSurcharge", "amount": "576.00"},
                     {"account": "Liabilities:CustomerCredits", "amount": "-576.00"}],
    }
    payload["policy_ref"] = "POL-001@1"
    from desk.workflow import transition
    transition(conn, item["id"], "propose", "ai_agent.seat3",
               patch={"payload": payload, "evidence": ["x"]})
    personas.ScriptedController.load(cfg).review(conn, item["id"])
    posted = gateway.post(conn, item["id"], cfg=cfg)
    assert posted["item"]["state"] == POSTED
    book_before = clean_books.read_text()

    ctx = Ctx(conn=conn, actor="ai_agent.seat3", seat="seat3",
              work_item_id=item["id"], cfg=cfg)
    ok, content = dispatch(ctx, "revise_proposal", {
        "evidence_doc": WAIVER, "extracted_facts": WAIVER_FACTS,
        "rationale": "the waiver zeroes this", "revised_amount": "0.00",
    })
    assert ok, content
    result = json.loads(content)

    # the posted entry is untouched
    assert clean_books.read_text() == book_before
    assert get_item(conn, item["id"])["state"] == POSTED
    assert result["changed"] is False

    # and a child work item exists to propose the correction
    child = get_item(conn, result["correcting_work_item"])
    assert child["kind"] == "WI-CORRECTION"
    assert child["parent_id"] == item["id"]
    assert child["state"] == "NEW"
    assert child["payload"]["corrects"] == item["id"]
    assert child["payload"]["revised_amount"] == "0.00"
    assert "append-only" in result["note"]


def test_opinion_and_authority_on_a_posted_entry_do_nothing(conn, cfg, onboarded,
                                                            customer_item, clean_books):
    from desk.workflow import transition

    item = customer_item("CUST-001", kind="WI-CUST-001-CREDIT")
    payload = dict(item["payload"])
    payload["journal_entry"] = {
        "date": "2025-10-15", "narration": "Bean Street credit",
        "postings": [{"account": "Income:Sales:TariffSurcharge", "amount": "1632.00"},
                     {"account": "Liabilities:CustomerCredits", "amount": "-1632.00"}],
    }
    payload["policy_ref"] = "POL-001@1"
    transition(conn, item["id"], "propose", "ai_agent.seat3",
               patch={"payload": payload, "evidence": ["x"]})
    personas.ScriptedController.load(cfg).review(conn, item["id"])
    gateway.post(conn, item["id"], cfg=cfg)
    book_before = clean_books.read_text()

    for raised_by, text, answer in (("human_cfo", "Reverse it, I'm signing off.", "authority"),
                                    ("human_cfo", "Nobody does this.", "opinion")):
        outcome = challenge.raise_challenge(
            conn, item["id"], raised_by=raised_by, text=text,
            llm=ClassifierLLM(answer), cfg=cfg)
        assert outcome.changed is False
        assert outcome.child_work_item is None

    assert clean_books.read_text() == book_before
    assert not conn.execute(
        "SELECT 1 FROM work_items WHERE kind = 'WI-CORRECTION'"
    ).fetchone()


def test_pushback_without_a_model_still_gets_an_answer(conn, item, cfg):
    """A question from the controller never disappears: code answers from the brief,
    the answer is kept on the challenge and logged, and nothing changes."""
    from desk import agent, llm
    from desk.__main__ import TASK
    from desk.scripted_seat1 import script
    from conftest import BANK_TXN

    agent.run_seat(conn, "seat1", item["id"], llm.FakeLLM(script=script()), TASK.format(bank_txn=BANK_TXN), cfg=cfg)
    outcome = challenge.raise_challenge(
        conn, item["id"], raised_by="human_controller",
        text="Are you sure the refund applies to the period the old policy covered?", llm=None, cfg=cfg)
    answer = challenge.fallback_reply(conn, outcome.challenge_id, cfg=cfg)
    assert answer["by"] == "code" and "Nothing changes" in answer["text"]
    assert "Record the tariff refund" in answer["text"] or "14,317.00" in answer["text"]
    row = challenge.get_challenge(conn, outcome.challenge_id)
    assert row["reply"] == answer["text"] and row["replied_by"] == "code"
    logged = conn.execute("SELECT reason FROM events WHERE action = 'challenge_reply' AND subject = ?",
                          (item["id"],)).fetchone()
    assert logged is not None and logged["reason"].startswith(answer["text"][:60])
    assert conn.execute("SELECT state FROM work_items WHERE id = ?", (item["id"],)).fetchone()["state"] == "PROPOSED"


def test_the_seat_answers_pushback_on_an_already_proposed_item(conn, item, cfg):
    """A discussion run must not stop after its first look just because the item is
    already PROPOSED; the seat reads, then answers, with read tools only."""
    from desk import agent
    from desk.__main__ import TASK
    from desk.llm import FakeLLM, ToolCall
    from desk.scripted_seat1 import script
    from conftest import BANK_TXN

    agent.run_seat(conn, "seat1", item["id"], FakeLLM(script=script()), TASK.format(bank_txn=BANK_TXN), cfg=cfg)
    assert get_item(conn, item["id"])["state"] == "PROPOSED"
    outcome = challenge.raise_challenge(conn, item["id"], raised_by="human_controller",
                                        text="Does the refund cover the period the old policy was in force?", llm=None, cfg=cfg)
    seat = FakeLLM(script=[
        LLMResponse(text="", tool_calls=(ToolCall("d1", "read_doc", {"path": "data/reference/refund_policy.md"}),
                                         ToolCall("d2", "propose", {"rationale": "x", "journal_entry": {}}))),
        LLMResponse(text="Yes. The refund covers entries liquidated under the policy in force; nothing changes without a document."),
    ])
    answer = challenge.reply(conn, outcome.challenge_id, seat_llm=seat, cfg=cfg)
    assert answer["by"] == "ai_agent.seat1" and answer["text"].startswith("Yes. The refund covers")
    assert seat.calls[0]["tools"] and "propose" not in seat.calls[0]["tools"] and "read_doc" in seat.calls[0]["tools"]
    refused = conn.execute("SELECT reason FROM events WHERE action = 'tool:propose' AND allowed = 0 AND subject = ?",
                           (item["id"],)).fetchall()
    assert refused and "not available in this run" in refused[-1]["reason"]
    assert get_item(conn, item["id"])["state"] == "PROPOSED"
    assert challenge.get_challenge(conn, outcome.challenge_id)["reply"] == answer["text"]
