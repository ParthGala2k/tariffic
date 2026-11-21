"""Scripted seat transcripts, so every phase runs with no network.

These stand in for the model's replies. The real run uses whichever provider
LLM_PROVIDER names; the agent, tools, permissions, invariants and audit log
behave identically either way.
"""
from __future__ import annotations

from .llm import LLMResponse, ToolCall

BANK_TXN = "BNK-0026"
REFUND = "RFD-001"
NOTICE = "data/documents/inbox/2025-10-14-cbp-refund-notice.md"
POLICY = "data/reference/refund_policy.md"
HTS = "data/reference/hts_programs.csv"
CON1 = "data/documents/contracts/CON-001-bean-street.md"

REFUND_ENTRIES = ["BRK-2500101-1", "BRK-2500128-2", "BRK-2500211-3",
                  "BRK-2500225-4", "BRK-2500311-5", "BRK-2500325-6"]

# What split_refund returns for RFD-001, per CLAUDE.md 10.2.
SPLIT_ROWS = [
    ("SHP-001", "BRK-2500101-1", "4000.00", 12, 100, 88, "480.00", "3520.00"),
    ("SHP-002", "BRK-2500128-2", "2400.00", 3, 40, 37, "180.00", "2220.00"),
    ("SHP-003", "BRK-2500211-3", "1200.00", 9, 100, 91, "108.00", "1092.00"),
    ("SHP-004", "BRK-2500225-4", "3000.00", 0, 75, 75, "0.00", "3000.00"),
    ("SHP-005", "BRK-2500311-5", "1800.00", 0, 30, 30, "0.00", "1800.00"),
    ("SHP-006", "BRK-2500325-6", "1500.00", 0, 125, 125, "0.00", "1500.00"),
]

SPLIT_DETAIL = {
    "refund_id": REFUND,
    "by_shipment": {
        shipment: {"entry_no": entry, "principal": principal, "consumed": consumed,
                   "received": received, "on_hand": on_hand,
                   "to_cogs": to_cogs, "to_inventory": to_inventory}
        for shipment, entry, principal, consumed, received, on_hand, to_cogs, to_inventory
        in SPLIT_ROWS
    },
    "to_cogs_total": "768.00",
    "to_inventory_total": "13132.00",
    "principal_total": "13900.00",
}


def _je(date, narration, postings, metadata=None):
    return {"date": date, "narration": narration,
            "metadata": metadata or {},
            "postings": [{"account": a, "amount": m, "currency": "USD"} for a, m in postings]}


# --------------------------------------------------------------------------- #
# Phase 1: Seat 1 records the deposit
# --------------------------------------------------------------------------- #

def seat1_deposit() -> list[LLMResponse]:
    return [
        LLMResponse(
            text="Let me look at the deposit itself first.",
            tool_calls=(ToolCall("d1", "run_sql", {
                "sql": f"SELECT * FROM golden_bank_txns WHERE txn_id = '{BANK_TXN}'"}),),
        ),
        LLMResponse(
            text="A 14,317.00 CBP refund credit. Checking the policy and the refund notice.",
            tool_calls=(ToolCall("d2", "read_doc", {"path": POLICY}),
                        ToolCall("d3", "read_doc", {"path": NOTICE})),
        ),
        LLMResponse(
            text="Now matching the deposit so the split is computed in code.",
            tool_calls=(ToolCall("d4", "match_deposit", {"bank_txn_id": BANK_TXN}),),
        ),
        LLMResponse(
            text="The figures tie out. Proposing the entry.",
            tool_calls=(ToolCall("d5", "propose", {
                "rationale": (
                    "Deposit BNK-0026 of 14,317.00 on 2025-10-15 matches CBP refund RFD-001 "
                    "(notice 2025-10-14) on amount and date. match_deposit splits it into "
                    "13,900.00 principal across the six liquidated IEEPA entries and 417.00 "
                    "interest. POL-001 2.3 clears the receivable on receipt; POL-001 2.4 "
                    "books refund interest to Income:Interest:TariffRefund."),
                "journal_entry": _je(
                    "2025-10-15", "Record CBP refund RFD-001 deposit",
                    [("Assets:Bank:Increase:Operating", "14317.00"),
                     ("Assets:Receivable:TariffRefund", "-13900.00"),
                     ("Income:Interest:TariffRefund", "-417.00")],
                    {"refund": REFUND, "bank_txn": BANK_TXN, "policy": "POL-001",
                     "entries": ",".join(REFUND_ENTRIES)}),
                "policy_ref": "POL-001@1",
                "evidence": [NOTICE, POLICY, "DOC-RFD-001",
                             "data/views/bank/transactions.csv#BNK-0026",
                             "data/views/customs/cbp_refunds.json#RFD-001"] + REFUND_ENTRIES,
            }),),
        ),
    ]


# --------------------------------------------------------------------------- #
# Phase 2: onboarding
# --------------------------------------------------------------------------- #

def onboard_refundable() -> list[LLMResponse]:
    return [
        LLMResponse(
            text="Starting with the programme reference table and the refund notice.",
            tool_calls=(ToolCall("o1", "read_doc", {"path": HTS}),
                        ToolCall("o2", "read_doc", {"path": NOTICE})),
        ),
        LLMResponse(
            text=(
                "The table flags IEEPA_CN and IEEPA_EU refundable; Section 232, Section 301 "
                "and Section 122 not. The notice says the same in its closing line."),
            tool_calls=(ToolCall("o3", "propose_policy", {
                "policy_id": "POL-L1",
                "name": "Refundable duty programmes",
                "verdict": "adopt",
                "rule": {"kind": "program_refundability",
                         "refundable": ["IEEPA_CN", "IEEPA_EU"],
                         "not_refundable": ["S232_STEEL", "S301_CN", "S122"]},
                "scope": "every refund forecast and refund receivable",
                "reason": (
                    "hts_programs.csv carries a refundable flag per programme: IEEPA_CN and "
                    "IEEPA_EU are yes because the court vacated them; S232_STEEL, S301_CN "
                    "and S122 are no. The CBP refund notice confirms it: Section 232 and "
                    "Section 301 duties 'are not affected by this notice and remain due'."),
                "evidence": [HTS, NOTICE, "IEEPA_CN", "IEEPA_EU", "S232_STEEL", "S301_CN",
                             "S122"],
                "evidence_count": 6,
            }),),
        ),
    ]


def onboard_split() -> list[LLMResponse]:
    return [
        LLMResponse(
            text="Reading the written policy on how a refund is split.",
            tool_calls=(ToolCall("s1", "read_doc", {"path": POLICY}),),
        ),
        LLMResponse(
            text=(
                "POL-001 3.1 gives the formula: principal x (qty consumed / qty received) "
                "per shipment, from the ERP's FIFO layers."),
            tool_calls=(ToolCall("s2", "propose_policy", {
                "policy_id": "POL-L2",
                "name": "Refund split between parts sold and parts on hand",
                "verdict": "adopt",
                "rule": {"kind": "refund_split", "numerator": "qty_consumed",
                         "denominator": "qty_received", "per": "shipment",
                         "basis": "golden_erp_inventory_layers at the refund date"},
                "scope": "every refunded CBP entry with an ERP stock layer",
                "reason": (
                    "POL-001 3.1: to_cogs = principal x (qty consumed / qty received) per "
                    "entry, using the ERP's FIFO layers at the refund date; to_inventory is "
                    "the remainder. 3.3 requires the two to add back to the principal."),
                "evidence": [POLICY, "systems/erp/erp.db#inventory_layers"],
                "evidence_count": 11,
            }),),
        ),
    ]


def onboard_clause() -> list[LLMResponse]:
    return [
        LLMResponse(
            text="Reading the policy on customer pass-through and the explicit contract.",
            tool_calls=(ToolCall("c1", "read_doc", {"path": POLICY}),
                        ToolCall("c2", "read_doc", {"path": CON1})),
        ),
        LLMResponse(
            text=(
                "CON-001 clause 7.2 is explicit, and POL-001 4.2 says what to do with an "
                "explicit clause. Writing it down for Seat 3."),
            tool_calls=(ToolCall("c3", "propose_policy", {
                "policy_id": "POL-L3",
                "name": "Explicit-clause contracts: credit the full tariff surcharge",
                "verdict": "adopt",
                "rule": {"kind": "explicit_clause_credit",
                         "applies_when": "the contract explicitly promises the surcharge back",
                         "credit": "full tariff surcharge billed on affected machines",
                         "accounts": {"debit": "Income:Sales:TariffSurcharge",
                                      "credit": "Liabilities:CustomerCredits"},
                         "interest": "never passed to the customer"},
                "scope": "contracts whose clause 7.2 is explicit, e.g. CON-001",
                "reason": (
                    "POL-001 4.2: for explicit pass-through wording, credit the full tariff "
                    "surcharge billed on machines whose parts came from refunded entries. "
                    "CON-001 clause 7.2 is that wording. POL-001 2.4 keeps the interest."),
                "evidence": [POLICY, CON1],
                "evidence_count": 2,
            }),),
        ),
    ]


def onboard_review() -> list[LLMResponse]:
    return [
        LLMResponse(
            text="Looking at what was booked to the refund receivable, and at the policy.",
            tool_calls=(ToolCall("r1", "run_sql", {
                "sql": ("SELECT date, narration, account, amount, raw_ref FROM"
                        " golden_ledger_postings WHERE account ="
                        " 'Assets:Receivable:TariffRefund' ORDER BY date")}),
                        ToolCall("r2", "read_doc", {"path": POLICY})),
        ),
        LLMResponse(
            text="24,700.00 was booked. Totalling the duty on those entries by programme.",
            tool_calls=(ToolCall("r3", "sum_duties", {"refund_id": REFUND}),),
        ),
        LLMResponse(
            text=(
                "Only 13,900.00 of that is refundable. The estimate swept in Section 232 steel "
                "duty, which is never refunded. That contradicts POL-001 2.1, so I will "
                "flag it rather than learn it."),
            tool_calls=(ToolCall("r4", "propose_policy", {
                "policy_id": "BAD-2025-09-30-FORECAST",
                "name": "Bad habit: forecasting the refund with Section 232 duty included",
                "verdict": "reject",
                "rule": {"kind": "bad_practice",
                         "observed": ("receivable estimated at 24,400.00 including "
                                      "Section 232 steel duty"),
                         "correct": ("13,900.00: refundable programmes only, from the CBP 7501"),
                         "where": "systems/books/history/2025.beancount, 2025-09-30"},
                "scope": "do not reuse this estimation method",
                "reason": (
                    "The 2025-09-30 entry booked 24,400.00. Only 13,900.00 is refundable: the "
                    "excess is 10,500.00 of S232_STEEL on BRK-2500128-2 and BRK-2500311-5. "
                    "This breaks POL-001 2.1: only programmes flagged refundable in "
                    "hts_programs.csv may be forecast."),
                "evidence": [POLICY, HTS, "systems/books/history/2025.beancount",
                             "BRK-2500128-2", "BRK-2500311-5", "BRK-2500225-4"],
                "evidence_count": 4,
            }),),
        ),
        LLMResponse(text=(
            "Flagged as a bad habit, not adopted as a rule. The correct method is POL-L1: "
            "refundable programmes only, taken from the CBP 7501.")),
    ]


# --------------------------------------------------------------------------- #
# Phase 2: the four journal entries
# --------------------------------------------------------------------------- #

def seat1_forecast() -> list[LLMResponse]:
    return [
        LLMResponse(
            text="Breaking the receivable down by cause before I touch anything.",
            tool_calls=(ToolCall("f0", "breakdown_receivable", {}),),
        ),
        LLMResponse(
            text="Running the approved refundability rule over the refunded entries.",
            tool_calls=(ToolCall("f1", "apply_policy", {
                "policy_ref": "POL-L1@1", "inputs": {"entry_nos": REFUND_ENTRIES}}),
                        ToolCall("f2", "sum_duties", {"refund_id": REFUND})),
        ),
        LLMResponse(
            text="Checking what the policy says about what may sit in the receivable.",
            tool_calls=(ToolCall("f3", "read_doc", {"path": POLICY}),),
        ),
        LLMResponse(
            text=(
                "Of the duty on those entries, 13,900.00 is refundable and 10,500.00 is "
                "Section 232, which is not. The 10,500.00 must come out of the receivable."),
            tool_calls=(ToolCall("f4", "propose", {
                "rationale": (
                    "breakdown_receivable shows the 10,500.00 excess in "
                    "Assets:Receivable:TariffRefund is all non-refundable duty. POL-L1@1 run "
                    "over the six refunded entries confirms the 10,500.00 is S232_STEEL "
                    "(6,000.00 on BRK-2500128-2 and 4,500.00 on BRK-2500311-5). POL-001 2.1 "
                    "allows only programmes flagged refundable in hts_programs.csv into a "
                    "refund forecast, and the CBP notice says Section 232 duties remain due. "
                    "That duty was a cost of the parts when it was paid, so it returns to "
                    "Expenses:COGS:Tariff."),
                "journal_entry": _je(
                    "2025-10-14", "Remove Section 232 duty from the refund forecast",
                    [("Expenses:COGS:Tariff", "10500.00"),
                     ("Assets:Receivable:TariffRefund", "-10500.00")],
                    {"policy": "POL-L1", "programs": "S232_STEEL",
                     "entries": "BRK-2500128-2,BRK-2500311-5"}),
                "policy_ref": "POL-L1@1",
                "evidence": [HTS, POLICY, NOTICE, "BRK-2500128-2", "BRK-2500311-5",
                             "systems/books/history/2025.beancount"],
            }),),
        ),
    ]


def seat2_split() -> list[LLMResponse]:
    return [
        LLMResponse(
            text="Running the approved split rule over the refund.",
            tool_calls=(ToolCall("p1", "split_refund", {"refund_id": REFUND}),),
        ),
        LLMResponse(
            text="Checking which account each side of the split belongs to.",
            tool_calls=(ToolCall("p2", "read_doc", {"path": POLICY}),),
        ),
        LLMResponse(
            text=(
                "768.00 belongs to parts already sold and 13,132.00 to parts still on hand. "
                "The on-hand duty was capitalised into inventory on 2025-10-01, so that "
                "portion comes back out of inventory."),
            tool_calls=(ToolCall("p3", "propose", {
                "rationale": (
                    "POL-L2@1 splits RFD-001's 13,900.00 principal per shipment by qty "
                    "consumed / qty received: 768.00 on parts already sold and 13,132.00 on "
                    "parts still on hand. POL-001 1.1 capitalised the duty on parts on hand "
                    "into Assets:Inventory:Parts on 2025-10-01, and the 2025-09-30 accrual "
                    "had credited the whole expected refund to Expenses:COGS:Tariff. So the "
                    "13,132.00 on-hand portion must move out of inventory and into COGS. "
                    "POL-001 3.3: 768.00 + 13,132.00 = 13,900.00."),
                "journal_entry": _je(
                    "2025-10-15", "Refund split: on-hand portion out of inventory",
                    [("Expenses:COGS:Tariff", "13132.00"),
                     ("Assets:Inventory:Parts", "-13132.00")],
                    {"policy": "POL-L2", "refund": REFUND,
                     "to_cogs": "768.00", "to_inventory": "13132.00"}),
                "policy_ref": "POL-L2@1",
                "evidence": [POLICY, NOTICE, "systems/erp/erp.db#inventory_layers",
                             "data/views/customs/cbp_refunds.json#RFD-001"] + REFUND_ENTRIES,
                "detail": SPLIT_DETAIL,
            }),),
        ),
    ]


# --------------------------------------------------------------------------- #

CON2 = "data/documents/contracts/CON-002-harbor.md"
WAIVER = "data/documents/inbox/2025-10-18-harbor-waiver.md"

WAIVER_FACTS = {
    "customer": {"value": "Harbor Roasters",
                 "quote": 'Harbor Roasters ("Distributor")'},
    "contract": {"value": "CON-002", "quote": "relates_to: [CON-002, INV-1003]"},
    "coverage_cutoff_date": {
        "value": "2025-06-30",
        "quote": "billed by Brewline on or before June 30, 2025"},
    "covered_invoices": {"value": ["INV-1003"],
                         "quote": "relates_to: [CON-002, INV-1003]"},
    "effect": {
        "value": "waives any claim to a credit of tariff charges billed on or before 2025-06-30",
        "quote": ("Distributor waives any claim to a credit, refund or other adjustment "
                  "of any Tariff Surcharge")},
}


# --------------------------------------------------------------------------- #
# Phase 3: Seat 3, the revenue accountant
# --------------------------------------------------------------------------- #

def seat3_bean_street() -> list[LLMResponse]:
    """CON-001 is explicit: apply the approved rule and propose the credit."""
    return [
        LLMResponse(
            text="Reading Bean Street's contract and totalling what they were billed.",
            tool_calls=(ToolCall("x1", "read_doc", {"path": CON1}),
                        ToolCall("x2", "surcharge_on_invoices",
                                 {"customer_id": "CUST-001", "refund_id": REFUND})),
        ),
        LLMResponse(
            text="Clause 7.2 is explicit. Checking the approved rule for it.",
            tool_calls=(ToolCall("x3", "get_policies", {"status": "APPROVED"}),
                        ToolCall("x4", "read_doc", {"path": POLICY})),
        ),
        LLMResponse(
            text=(
                "CON-001 7.2 says Brewline credits the full surcharge on affected "
                "machines: 1,152.00 on INV-1001 and 480.00 on INV-1002, 1,632.00 in all."),
            tool_calls=(ToolCall("x5", "propose", {
                "rationale": (
                    "CON-001 clause 7.2 is explicit: 'Brewline shall credit Customer the "
                    "full Tariff Surcharge billed on the affected machines within thirty "
                    "(30) days of Brewline\'s receipt of the refund.' surcharge_on_invoices "
                    "gives 1,152.00 on INV-1001 and 480.00 on INV-1002, both billed on "
                    "machines built from SHP-001, SHP-002 and SHP-003, all of which are in "
                    "RFD-001. POL-L3@1 is the approved rule for explicit clauses: debit "
                    "Income:Sales:TariffSurcharge, credit Liabilities:CustomerCredits. "
                    "Clause 7.2 and POL-001 2.4 both keep the interest with Brewline."),
                "journal_entry": _je(
                    "2025-10-15", "Bean Street tariff surcharge credit (CON-001 clause 7.2)",
                    [("Income:Sales:TariffSurcharge", "1632.00"),
                     ("Liabilities:CustomerCredits", "-1632.00")],
                    {"customer": "CUST-001", "contract": "CON-001", "refund": REFUND,
                     "credit_notes": "INV-1001 1152.00, INV-1002 480.00"}),
                "policy_ref": "POL-L3@1",
                "evidence": [CON1, POLICY, "INV-1001", "INV-1002",
                             "data/views/billing/invoices.json#INV-1001",
                             "data/views/billing/invoices.json#INV-1002"],
                "detail": {
                    "customer_id": "CUST-001",
                    "contract_id": "CON-001",
                    "total_surcharge": "1632.00",
                    "credit_notes": [{"invoice": "INV-1001", "amount": "1152.00"},
                                     {"invoice": "INV-1002", "amount": "480.00"}],
                },
            }),),
        ),
    ]


def seat3_harbor_question() -> list[LLMResponse]:
    """CON-002 is ambiguous: ask the lawyer, do not decide."""
    return [
        LLMResponse(
            text="Reading Harbor's contract before I do anything else.",
            tool_calls=(ToolCall("h1", "read_doc", {"path": CON2}),
                        ToolCall("h2", "surcharge_on_invoices",
                                 {"customer_id": "CUST-002", "refund_id": REFUND})),
        ),
        LLMResponse(
            text="Checking whether anyone has already decided this wording.",
            tool_calls=(ToolCall("h3", "search_precedents", {"query": "CON-002"}),),
        ),
        LLMResponse(
            text=(
                "CON-002 7.2 only promises to treat the distributor 'equitably and "
                "consistently'. That is open to more than one reading and no precedent "
                "covers it, so it is not mine to decide."),
            tool_calls=(ToolCall("h4", "ask", {
                "question": ("Does clause 7.2 of CON-002 (Harbor Roasters) oblige Brewline "
                             "to pass through refunded IEEPA duties billed as a tariff "
                             "surcharge?"),
                "to_role": "human_lawyer",
                "question_id": "Q-CON-002-SHARE",
                "evidence": [CON2, "INV-1003",
                             "data/views/billing/invoices.json#INV-1003"],
            }),),
        ),
        LLMResponse(text=(
            "Asked human_lawyer (Q-CON-002-SHARE). Harbor's share is 576.00 on INV-1003 "
            "if the consistency reading holds, but that is the lawyer's call, not mine. "
            "Stopping here until the answer is on file.")),
    ]


def seat3_harbor_close() -> list[LLMResponse]:
    """The lawyer has answered; the waiver then takes Harbor to nil."""
    return [
        LLMResponse(
            text="Taking the lawyer's answer and the waiver that was uploaded.",
            tool_calls=(ToolCall("w1", "search_precedents", {"query": "CON-002"}),
                        ToolCall("w2", "read_doc", {"path": WAIVER})),
        ),
        LLMResponse(
            text=(
                "The lawyer says Harbor is owed the surcharge on the consistency reading, "
                "unless they waived it in writing. Paragraph 2 of the 30 June letter does "
                "exactly that, and INV-1003 is dated 2025-04-22, inside the period."),
            tool_calls=(ToolCall("w3", "revise_proposal", {
                "evidence_doc": WAIVER,
                "extracted_facts": WAIVER_FACTS,
                "rationale": (
                    "The lawyer's answer (PRE-Q-CON-002-SHARE) makes Harbor's share the "
                    "full surcharge on affected machines - 576.00 on INV-1003 - unless a "
                    "written waiver covers it. The letter agreement of 2025-06-30 waives "
                    "any claim to a credit of tariff charges billed on or before that "
                    "date, and INV-1003 is dated 2025-04-22. Harbor's share is therefore "
                    "0.00. The waiver covers Harbor only; it says nothing about any other "
                    "distributor."),
                "revised_amount": "0.00",
            }),),
        ),
    ]


def onboarding_script() -> list[LLMResponse]:
    """The four onboarding transcripts, in the order desk/onboarding.py runs them."""
    return onboard_refundable() + onboard_split() + onboard_clause() + onboard_review()


# Each work item gets its own slice of transcript. A real model decides when to
# stop; a scripted one would otherwise run on into the next item's turns.
SEGMENTS = {
    "onboard_refundable": onboard_refundable,
    "onboard_split": onboard_split,
    "onboard_clause": onboard_clause,
    "onboard_review": onboard_review,
    "forecast": seat1_forecast,
    "deposit": seat1_deposit,
    "split": seat2_split,
    "bean_street": seat3_bean_street,
    "harbor_question": seat3_harbor_question,
    "harbor_close": seat3_harbor_close,
}


def segment(name: str) -> list[LLMResponse]:
    if name not in SEGMENTS:
        raise ValueError(f"no scripted segment {name!r}; have {', '.join(SEGMENTS)}")
    return SEGMENTS[name]()


def phase_script(phase: str = "phase1") -> list[LLMResponse]:
    if phase == "phase1":
        return seat1_deposit()
    if phase == "phase2":
        return (onboarding_script() + seat1_forecast()
                + seat1_deposit() + seat2_split())
    if phase == "phase3":
        return (phase_script("phase2") + seat3_bean_street()
                + seat3_harbor_question() + seat3_harbor_close())
    raise ValueError(f"no script for {phase!r}")


# Phase 1 compatibility.
def script() -> list[LLMResponse]:
    return seat1_deposit()
