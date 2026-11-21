# Brewline Tariff Desk — plain-English guide

## 1. How to use this guide

Three shorter documents sit next to this one. `HANDOVER.md` is the current state of the
whole project in one read (for a person or a Claude terminal picking it up). `docs/PLAYBOOK.md`
is the tester's guide: every use case, how to trigger, verify and extend it. `README.md` is the
quick start. This guide is the long version with the pipelines and the changelog.

Read sections 3 to 9 once, in order. They explain the whole system without accounting jargon.
After that, jump straight to the phase you are working on in section 10.
Every term is explained the first time it appears, and section 12 is a glossary you can jump back to.
Every file path here is real and was checked against the repo.

**Current judge path:** `docs/DEMO.md` describes the main messy-world replay. The refund
button now uses `scenarios/tariff-refund-demo.yaml`: onboarding → detect and correct the
broker/forecast mistakes → deposit → split → Bean Street → lawyer question → Harbor
waiver verification → controller review of the zero settlement. There are five journal
entries and six controller decisions. The clean baseline remains available separately.
The UI defaults to recorded agents with no API calls and labels the simulated lawyer.
Selecting Live model is explicit; it does not silently fall back on failure. Phase 4 is
still unfinished, so the lawyer's proposed rule is not automatically reused.

---

## 2. Phase index

| Phase | One-line goal | Status |
|---|---|---|
| 0 | Prepare the fake company's data, documents and people; test the AI parts by hand | ✅ done (before the event; no desk code) |
| 1 | System-of-work skeleton: work items, permissions, audit log, agent harness, gateway | ✅ done |
| 2 | Seat 1 and Seat 2 do the refund work end to end; catch both planted mistakes; learn policies | ✅ done |
| 3 | Seat 3 reads contracts and decides customer credits; handle pushback | ✅ done |
| 4 | Learning loop: one lawyer answer becomes a reusable rule | ⏳ in progress |
| 5 | Decision forensics and the audit query | ⬜ planned |
| 6 | Connect the real sandboxes: Increase, Beancount/Fava, Stripe, Mockoon | ⬜ planned |
| Part 2 (after MVP) | Scale the data, run all invariants, clocks, UI, scorecard on held-out data | ⬜ planned |

---

## 3. The big picture

```
  INPUTS: the company's world                 THE DESK: our program              OUTPUTS
  (we read these, we don't change them)       (the "system of work")

  data/                                        desk/
    views/      bank, customs, billing   ──┐     sync.py      reads the world    ┌──> desk.db
    documents/  contracts, inbox mail     ──┤     agent.py     the AI seats       │     work_items
    reference/  duty programs, policy     ──┤     tools/       what seats can do  │     events (the log)
    truth/      ground truth   [AI BLIND] ──┤     perms.py     who may do what    │     questions
    answer_key/ exam answers   [AI BLIND] ──┤     workflow.py  ticket state       │     policies
                                            │     gateway.py   the locked door    │     golden_* tables
  systems/                                  │                                     │
    books/      the ledger (Beancount)    ──┤                                     └──> systems/books/
    erp/        stock and sales (SQLite)  ──┤                                            posted/
    mockoon/    fake customs API          ──┘                                            desk.beancount
                                                                                         (the books)
  personas/     the simulated humans      ────> personas.py    the controller etc.
```

Think of it as an office. `data/` and `systems/` are the filing cabinets and the company's real
records. `desk/` is the office itself: three **desks** (we call them *seats*), each staffed by an AI
accountant. Work arrives as **tickets** (we call them *work items*), one ticket per thing that needs
doing.

Five ideas hold the whole design together.

1. **The AI does the work.** It decides which questions to ask, looks things up, and drafts an answer.
2. **Code sets the limits.** Permissions, ticket states and the maths are plain Python. The AI cannot
   talk its way past them, because they are not written in the prompt — they are written in code.
3. **Humans approve.** No AI seat can approve its own work, or anyone else's.
4. **Only the gateway writes to the books.** It is the one locked door, and it only opens for an
   approved ticket.
5. **Everything is logged**, including every refused attempt, in a log that cannot be edited.

---

## 4. Map of the repo

```
tariffic/
├── CLAUDE.md                     the full project brief (the source of truth for the plan)
├── explanation.md                this guide
├── README.md                     short repo readme
├── tariff-desk-build-plan.md     earlier planning notes
├── requirements.txt              Python packages: beancount, openai, anthropic, pyyaml, pytest, fastapi, uvicorn…
├── .env                          secrets and settings (NOT in git)
├── .env.example                  template for .env, safe to commit
├── .gitignore                     ignores .env, *.db and .cache/
├── desk.db                       the desk's own database (created by running it; not in git)
├── .cache/llm_cache.db           saved AI replies, kept across resets (not in git)
│
├── desk/                         ===== OUR PROGRAM =====
│   ├── __init__.py               package marker
│   ├── __main__.py               the CLI: reset | run phase1..3 | why <id> | challenge | serve
│   ├── config.py                 reads .env, finds the repo root, holds all paths
│   ├── schema.sql                the desk.db table definitions (incl. the append-only triggers)
│   ├── db.py                     opens SQLite, rebuilds the ERP, writes audit rows
│   ├── perms.py                  ROLES + can(): who may do what. Separation of duties
│   ├── workflow.py               TRANSITIONS + transition(): the only way a ticket changes
│   ├── gateway.py                the locked door to the books; runs bean-check
│   ├── sync.py                   reads the world into the golden_* tables
│   ├── guard.py                  refuses data/truth and data/answer_key, and non-golden SQL
│   ├── invariants.py             the five invariants; gates approval by effect
│   ├── policies.py               learned rules: stored as data, executed by code
│   ├── precedents.py             past human decisions the desk can cite
│   ├── challenge.py              the challenge protocol: classify pushback, enforce it
│   ├── evidence.py               checks a document's quotes and coverage, in code
│   ├── onboarding.py             the seats study the history and propose their own rules
│   ├── score.py                  the scorer - the ONLY module that reads the answer key
│   ├── llm.py                    the AI layer: OpenAI + Anthropic behind one interface, plus cache
│   ├── agent.py                  the agent loop (model + tools + loop + context) and seat prompts
│   ├── personas.py               the scripted controller and the scripted people's answers
│   ├── scripted.py               canned seat transcripts so any phase runs with no network
│   ├── scripted_seat1.py         kept as a Phase 1 alias for desk/scripted.py
│   ├── activity.py               the live feed: a seat's "thinking mode" read off the audit log
│   ├── brief.py                  the controller's brief: what to decide, built by code from the log
│   ├── inbox.py                  humans put a document or a link in the inbox as citable evidence
│   ├── humans.py                 a real person's actions: approve, reject, send back, answer, adopt a rule, resume
│   ├── runs.py                   runs a phase on a background thread and keeps its console output
│   ├── api.py                    the HTTP API (FastAPI): base data, work, decisions, SSE stream
│   ├── scenario.py               runs a scenarios/*.yaml world in a sandbox and ties the books out;
│   │                             logs a run:step row per step; can wait for a human controller
│   └── tools/                    ===== WHAT A SEAT CAN DO =====
│       ├── __init__.py           the tool registry, per-seat tool lists, and dispatch()
│       ├── read.py               run_sql, read_doc
│       ├── compute.py            match_deposit, sum_duties, reconcile_duty_paid,
│       │                         breakdown_receivable, split_refund, surcharge_on_invoices
│       ├── policy.py             get_policies, propose_policy, apply_policy
│       └── work.py               propose, ask, revise_proposal
│
├── tests/                        ===== PROOF =====
│   ├── conftest.py               shared test setup (throwaway DB, restores the books)
│   ├── test_phase1_flow.py       the end-to-end run posts exactly JE-3
│   ├── test_permissions.py       self-approval refused; the role matrix
│   ├── test_gateway.py           only the gateway posts; only APPROVED items
│   ├── test_audit_append_only.py the log cannot be edited or deleted
│   ├── test_guard.py             data/truth and data/answer_key are unreachable
│   ├── test_reset.py             reset restores the books to their 2-line header
│   ├── test_llm_providers.py     both AI providers translate the same conversation
│   ├── test_provider_parity.py   the same flow runs through either provider
│   ├── test_phase2_flow.py       the Phase 2 run posts JE-1..JE-4 and ties out
│   ├── test_invariants.py        both mistakes detected; gating judged by effect
│   ├── test_onboarding.py        rules proposed with evidence; nothing applies unapproved
│   ├── test_work_graph.py        a parent closes only once every child settles
│   ├── test_seat2_split.py       the split matches the answer key, shipment by shipment
│   ├── test_cause_matching.py    one ticket, one cause: a lumped entry is refused
│   ├── test_seat3_credit.py      customer shares read off the contracts
│   ├── test_challenge.py         only evidence moves a number
│   ├── test_phase3_flow.py       the Phase 3 run, and the challenge command
│   ├── test_score.py             the scorer reads the answer key; no tool can
│   ├── test_cli.py               items, log, why's correction marker, reset's cache
│   ├── test_tool_jurisdiction.py every seat x every tool: inside runs, outside is refused and logged
│   ├── test_activity.py          modes and labels are a reading of the log; every turn is logged
│   ├── test_brief.py             the brief says what to decide and cites what the seat read and computed
│   ├── test_humans.py            humans have the role matrix's powers and no more
│   ├── test_inbox.py             uploads and links become verifiable inbox documents
│   ├── test_api.py               the HTTP API end to end, with a background scripted run
│   ├── test_scenario.py          the clean worlds tie out; the base world shows its two mistakes
│   ├── test_scenarios_api.py     the API switches onto a sandbox, runs it, and groups who must act
│   └── test_scenario_human.py    a human controller approves each entry; a rejection stops the run
│
├── data/                         ===== THE COMPANY'S WORLD (read-only) =====
│   ├── views/                    what each outside system would show us
│   │   ├── bank/transactions.csv            26 bank lines
│   │   ├── bank/increase_crosswalk.json     our ids ↔ Increase sandbox ids
│   │   ├── customs/cbp_entries.json         10 customs entries with their duty lines
│   │   ├── customs/cbp_refunds.json         the refund and its 6 entries
│   │   └── billing/*.json                   customers, invoices, products, Stripe ids
│   ├── documents/
│   │   ├── contracts/CON-001…CON-004.md     4 customer contracts
│   │   └── inbox/*.md                       4 letters: refund notice, bank memo, CFO email, waiver
│   ├── reference/
│   │   ├── hts_programs.csv                 the 5 duty programs and which are refundable
│   │   └── refund_policy.md                 POL-001, the company's written accounting policy
│   ├── truth/                    ⛔ HIDDEN FROM THE AI — the real answers about the fake world
│   └── answer_key/               ⛔ HIDDEN FROM THE AI — the exam answers (scorer only)
│
├── systems/                      ===== THE COMPANY'S RECORDS =====
│   ├── books/
│   │   ├── main.beancount        includes the three files below
│   │   ├── accounts.beancount    the chart of accounts (the list of valid account names)
│   │   ├── history/2025.beancount  all of 2025  ← planted mistake PM-1 lives here
│   │   └── posted/desk.beancount ✍️ THE ONLY FILE THE DESK WRITES, and only via the gateway
│   ├── erp/schema.sql, seed.sql  the mini ERP: stock layers, sales, parts used
│   └── mockoon/customs.mockoon.json  a fake customs API, for Phase 6
│
├── frontend/                     ===== THE SCREEN (Vite + React + Tailwind) =====
│   ├── src/lib/api.ts, desk.tsx  the API client, the live state (status poll + SSE feed), the role,
│   │                             the glossary behind the hover tips, the agents' names and jobs
│   ├── src/components/           Shell (nav, role switch), IssueTracker (one event as a line of stages),
│   │                             SeatCards (the three agents), Feed (the narration), Term (hover tips),
│   │                             TieOutBoard, Tracker (developer page only)
│   ├── src/pages/                NeedsYou (home), Activity, Items + Item (Decisions), Questions (Lawyer),
│   │                             Ledger (Books), Simulate (developer tools: scripted replays)
│   └── dist/                     the build; python -m desk serve mounts it at / (not in git)
│
├── .claude/skills/oiloil-ui-ux-guide/   the UI/UX guide skill the screen was designed against
│
├── scenarios/                    ===== WORLDS TO TEST THE DESK ON =====
│   ├── README.md                 the YAML patch language and what the tie-outs compare
│   ├── rosy-baseline.yaml        no mistakes, one refund: 65 checks tie before and after the desk
│   └── rosy-pending-refund.yaml  no refund yet: 22 checks, one known INV-2 false alarm
│
├── personas/                     ===== THE SIMULATED PEOPLE =====
│   ├── controller.md, lawyer.md, cfo.md, auditor.md    role cards
│   └── scripts/*.yaml            their scripted answers, for repeatable runs
│
├── HANDOVER.md                   the state of the project for a Claude terminal: modules, rules, verified, missing
├── docs/PLAYBOOK.md              the human tester's guide: every use case, how to run, verify and extend it
└── docs/sandbox-storage-plan.md  how the Stripe / Increase / Beancount / Mockoon sandboxes were seeded
```

### Why `data/truth/` and `data/answer_key/` are hidden

`data/truth/` is the real state of the invented company. `data/answer_key/answer_key.json` is the
list of correct answers, including the two deliberate mistakes and what the right numbers are.

If the AI could read either folder, it would not be solving the problem — it would be copying the
answers, and we would learn nothing about whether it can actually do the job. So the ban is enforced
in code, in `desk/guard.py`, not just asked for in the prompt. Every attempt to reach them is refused
**and written to the log**. `tests/test_guard.py` proves it with 20 cases.

---

## 5. Data guide

### 5.1 How data gets in: sync and `raw_ref`

The outside files are all shaped differently: CSV, JSON, a ledger, a SQL database. The AI should not
have to deal with five shapes. So `desk/sync.py` copies them all into one tidy set of SQLite tables
in `desk.db`, all named `golden_*`.

"Golden" just means: cleaned up, one agreed shape, one row per real thing.

Every golden row carries a column called **`raw_ref`**: a short string pointing back to the exact
row it came from.

```
  data/views/bank/transactions.csv              desk.db → golden_bank_txns
  ┌──────────────────────────────┐              ┌───────────────────────────────────────────────┐
  │ BNK-0026,2025-10-15,credit,  │   sync.py    │ txn_id  BNK-0026                              │
  │ 14317.00,ach,US TREASURY 310,│  ─────────>  │ amount  14317.00                              │
  │ CBP REFUND,,…                │              │ raw_ref data/views/bank/transactions.csv#BNK-0026 │
  └──────────────────────────────┘              └───────────────────────────────────────────────┘
```

That is why the desk can always answer "where did this number come from?" — it hands back the
`raw_ref`. It is the difference between a system you can audit and one you have to trust.

### 5.2 Input files

| Name | What it is, in plain words | Pretends to be | Who uses it | AI can read? |
|---|---|---|---|---|
| `data/views/bank/transactions.csv` | 26 lines of the company's bank statement for 2025 | Bank (Increase) | `sync.py` → `golden_bank_txns`; Seat 1 via `run_sql` | ✅ via golden table |
| `data/views/bank/increase_crosswalk.json` | Maps our bank ids to the Increase sandbox's own ids | Bank | Phase 6 | not synced yet |
| `data/views/customs/cbp_entries.json` | 10 customs entry summaries. Each lists what was imported and the duty charged under each programme | Customs (CBP Form 7501) | `sync.py` → `golden_cbp_entries` + `golden_cbp_duty_lines`; Seat 1 | ✅ via golden tables |
| `data/views/customs/cbp_refunds.json` | The one refund: 6 entries, 13,900 principal + 417 interest | Customs | `sync.py` → `golden_cbp_refunds` + `golden_cbp_refund_entries`; `match_deposit` | ✅ via golden tables |
| `data/views/billing/customers.json` | 4 customers, each flagged `explicit` or `ambiguous` on its contract clause | Billing (Stripe) | `sync.py` → `golden_billing_customers`; Seat 3 | ✅ via golden table |
| `data/views/billing/invoices.json` | 4 sales invoices, each with a separate "tariff surcharge" line naming the shipments it covers | Billing | `sync.py` → `golden_billing_invoices` + `_lines`; Seat 3 | ✅ via golden tables |
| `data/views/billing/products.json` | The two machines we sell | Billing | Phase 6 | not synced yet |
| `data/views/billing/stripe_crosswalk.json` | Our ids ↔ Stripe ids | Billing | Phase 6 | not synced yet |
| `data/documents/contracts/CON-001-bean-street.md` | A contract that clearly says the customer gets the surcharge back (clause 7.2) | Contracts | Seat 3 | ✅ `read_doc` |
| `data/documents/contracts/CON-002-harbor.md` | A contract with vague wording ("treat Distributor equitably and consistently") | Contracts | Seat 3 | ✅ `read_doc` |
| `data/documents/contracts/CON-003-northwind.md` | Same vague wording as CON-002 | Contracts | Seat 3, Phase 4 | ✅ `read_doc` |
| `data/documents/contracts/CON-004-cascade.md` | Same vague wording again | Contracts | Seat 3, Phase 4 | ✅ `read_doc` |
| `data/documents/inbox/2025-10-14-cbp-refund-notice.md` | The official letter saying the refund is coming (doc id `DOC-RFD-001`) | Inbox | Seat 1 | ✅ `read_doc` |
| `data/documents/inbox/2025-10-15-bank-memo.md` | What the bank feed shows for the deposit. Deliberately has no company ids in it | Inbox | Seat 1 | ✅ `read_doc` |
| `data/documents/inbox/2025-10-16-cfo-email.md` | The CFO leaning on the desk to book it the wrong way. Classified **authority**; changes nothing | Inbox | Phase 3 pushback | ✅ `read_doc` |
| `data/documents/inbox/2025-10-18-harbor-waiver.md` | A signed letter where Harbor gives up its claim on charges billed up to 2025-06-30. Classified **evidence**; takes Harbor to 0.00 | Inbox | Phase 3 | ✅ `read_doc` |
| `data/reference/hts_programs.csv` | The 5 duty programmes, and crucially a `refundable` yes/no column | Reference | Seat 1 in Phase 2 | ✅ `read_doc` |
| `data/reference/refund_policy.md` | POL-001: the company's written policy on duties and refunds | Reference | all seats | ✅ `read_doc` |
| `data/truth/*` (15 files) | The real state of the invented company | — | the data generator and the scorer | ⛔ **never** |
| `data/answer_key/answer_key.json` | The correct answers for every phase | — | the scorer only (Phase 2) | ⛔ **never** |
| `systems/books/main.beancount` | The ledger's front door; it just includes the next three | Ledger (Beancount) | `bean-check`, `sync.py` | ✅ via golden table |
| `systems/books/accounts.beancount` | The list of legal account names | Ledger | `gateway.py` and `propose` check against it | code reads it |
| `systems/books/history/2025.beancount` | Everything booked in 2025. **PM-1 is in here** | Ledger | `sync.py` → `golden_ledger_postings` | ✅ via golden table |
| `systems/books/posted/desk.beancount` | Empty except a 2-line header until the desk posts something | Ledger | **gateway only** | write: gateway only |
| `systems/erp/schema.sql`, `seed.sql` | Rebuild the mini ERP: purchase orders, shipments, stock layers, sales, parts consumed | ERP | Seat 2 in Phase 2 | not synced yet |
| `systems/mockoon/customs.mockoon.json` | A fake customs web API | Customs API | Phase 6 | — |
| `personas/*.md` | Role cards describing each simulated person | People | humans reading the repo | — |
| `personas/scripts/controller.yaml` | The controller's canned decisions, by ticket kind | People | `desk/personas.py` | code reads it |
| `personas/scripts/lawyer.yaml`, `cfo.yaml`, `auditor.yaml` | The other people's canned answers | People | Phases 2–5 | code reads it |

### 5.3 `desk.db` tables

`desk.db` is the desk's own database. It is created by running the desk and deleted by `reset`.

| Table | What it holds, in plain words | Written by |
|---|---|---|
| `work_items` | The tickets. One row per thing to be done: its kind, its state, which seat owns it, the draft answer, the evidence, who proposed it, who approved it | `desk/workflow.py` only |
| `events` | **The audit log.** One row per thing that happened, including every refused attempt. Cannot be changed or deleted — SQLite triggers block `UPDATE` and `DELETE` | `desk/db.py` |
| `questions` | Questions a seat asked a human, and the answers | `desk/tools/work.py` |
| `policies` | Versioned rules: the controller's written POL-001, the three the seats learned (POL-L1/L2/L3), and bad habits flagged `FLAGGED_BAD`. Carries the evidence and the evidence count behind each one | `desk/policies.py` |
| `golden_bank_txns` | The bank statement, tidied. 26 rows | `desk/sync.py` |
| `golden_cbp_entries` | The 10 customs entries, tidied | `desk/sync.py` |
| `golden_cbp_duty_lines` | 17 rows: which duty programme charged how much on which entry | `desk/sync.py` |
| `golden_cbp_refunds` | 1 row: the refund header | `desk/sync.py` |
| `golden_cbp_refund_entries` | 6 rows: principal and interest per entry | `desk/sync.py` |
| `golden_ledger_postings` | 120 rows: every line of the 2025 ledger | `desk/sync.py` |
| `golden_erp_inventory_layers` | 10 rows: one stock layer per shipment, with how many were received, consumed and are on hand, plus the duty per unit | `desk/sync.py` |
| `golden_erp_consumption` | Which sales order ate which parts from which layer | `desk/sync.py` |
| `golden_billing_customers` | The 4 customers, with their contract and whether its clause is `explicit` or `ambiguous` | `desk/sync.py` |
| `golden_billing_invoices` | The 4 sales invoices, with machine and surcharge totals | `desk/sync.py` |
| `golden_billing_invoice_lines` | Every invoice line. Surcharge lines name the shipments they cover, which is how `surcharge_on_invoices` knows what the refund touches | `desk/sync.py` |
| `precedents` | What a named human decided in a past case, and why. The lawyer's answer on CON-002 lives here | `desk/precedents.py` |
| `challenges` | One row per piece of pushback: who said it, how it was classified, whether anything changed, and where authority was routed | `desk/challenge.py` |

The AI response cache used to live here. It now has its own file, `.cache/llm_cache.db`,
so `python -m desk reset` can wipe the desk clean without throwing away answers you have
already paid for. `python -m desk reset --clear-cache` deletes it too.

### 5.4 The two planted mistakes

The fake company's records contain two deliberate errors. Finding them is the point.

| | PM-1 — the bad forecast | PM-2 — the broker overbilled |
|---|---|---|
| **Lives in** | `systems/books/history/2025.beancount`, the `2025-09-30` entry | `data/views/customs/broker_invoices.json`, invoice `BRK-INV-25004` |
| **In plain words** | On 30 September the controller guessed the refund would be **24,700** and booked it. The guess was wrong: it included duties that will never be refunded | The broker's bill for entry `BRK-2500225-4` charges **3,300** of IEEPA duty. The official customs form says **3,000**. The broker overcharged by **300**, and the books paid the broker's figure |
| **Why it's wrong** | 24,700 = 13,900 real + **10,500 of Section 232 steel duty** (not refundable) + **300** of PM-2. The right figure is **13,900** | The customs entry summary (CBP Form 7501) is the source of truth for duty, not the broker's invoice. POL-001 §1.2 says so |
| **The fix** | JE-1: take the 10,500 back out | JE-2: move the 300 to "money the broker owes us" |
| **Caught by** | Invariant 2 (a refund forecast may only include refundable programmes) — Phase 2 | Invariant 1 (the broker's bill must equal the customs form) — Phase 2 |

---

## 6. The cast

### 6.1 The AI seats

A **seat** is one job at the desk. Technically it is an AI agent: a model, a list of tools it is
allowed to use, a loop, and a prompt. The code decides its tool list; the AI decides what to do with
it.

| Seat | Job, in plain words | Its tools | Arrives in |
|---|---|---|---|
| **Seat 1 — trade-compliance accountant** | The customs expert. Works out which duties fall under which government programme, reconciles the duty the books paid against the official customs forms, matches money arriving in the bank to the refund it belongs to, and splits a refund into principal and interest | `run_sql`, `read_doc`, `get_policies`, `match_deposit`, `sum_duties`, `reconcile_duty_paid`, `breakdown_receivable`, `propose`, `ask`, `propose_policy`, `apply_policy` | ✅ Phase 1 and 2 |
| **Seat 2 — cost accountant** | The stock expert. When a duty refund arrives, some of those parts have already been sold and some are still on the shelf. Seat 2 works out the split and drafts the ledger entry | `run_sql`, `read_doc`, `get_policies`, `split_refund`, `apply_policy`, `propose`, `ask`, `propose_policy` | ✅ Phase 2 |
| **Seat 3 — revenue accountant** | The customer expert. Reads each customer's contract to decide whether they are owed a share of the refund, and drafts the credit notes. Sends vague contracts to the lawyer rather than deciding them | `run_sql`, `read_doc`, `get_policies`, `search_precedents`, `surcharge_on_invoices`, `propose`, `ask`, `apply_policy`, `revise_proposal` | ✅ Phase 3 |

**The seats never talk to each other.** They coordinate through the tickets: finishing one ticket
creates the next. That is deliberate — it keeps each seat's reasoning auditable on its own.

### 6.2 The simulated people

These are invented humans. Each has a role card in `personas/` and a file of canned answers in
`personas/scripts/`.

| Person | Role, in plain words | Mode |
|---|---|---|
| **Controller** (`personas/controller.md`) | The boss of the books. The only one who can approve a ticket | Scripted in Phase 1 (`personas/scripts/controller.yaml`); live LLM in the demo |
| **Lawyer** (`personas/lawyer.md`) | Answers contract questions and proposes contract rules. Their answer is recorded as a **precedent** the desk can cite | Scripted (`personas/scripts/lawyer.yaml`); ✅ active in Phase 3 |
| **CFO** (`personas/cfo.md`) | Pushes back using *authority* ("I already signed off on this"). Logged, routed to the controller, changes nothing | Scripted (`personas/scripts/cfo.yaml`); ✅ active in Phase 3 |
| **Auditor** (`personas/auditor.md`) | Asks "why did you decide that?" | Scripted; arrives in Phase 5 |

Scripted mode exists so a run is repeatable and can be scored. Live LLM personas are for the demo,
where judges argue with the desk. **The seat agents always use the real LLM, in both modes.**

### 6.3 The code-only actors

Two actors in the system are not AI at all. That is the whole point of them.

| Actor | What it does | Why it is not AI |
|---|---|---|
| **`sync`** (`desk/sync.py`) | Reads the outside world into the golden tables, and opens a ticket when something needs doing | Copying records is mechanical. An LLM here would only add a chance of a wrong number |
| **`gateway`** (`desk/gateway.py`) | The only thing that writes to the books. Checks the ticket is APPROVED, appends the entry with `work_item` and `approved_by` stamped on it, runs `bean-check`, and undoes the write if the check fails. Those two stamps are **reserved**: a proposal that tries to set them is refused, and the gateway writes its own last, so they cannot be forged | This is the irreversible step. It must be boring, predictable and provable. It is about 140 lines of Python with no model anywhere near it |

---

## 7. Tools guide

A **tool** is a function the AI is allowed to call. The AI picks which to call and with what
arguments; the function itself is ordinary Python.

| Tool | Kind | Seat | What it does | What it returns | Done by |
|---|---|---|---|---|---|
| `run_sql` | read | 1 (2, 3 later) | Runs one read-only `SELECT` over the `golden_*` tables | Rows, plus the `raw_ref` of each source row | code |
| `read_doc` | read | 1 (2, 3 later) | Reads one file under `data/documents/` or `data/reference/`. Given a directory instead, lists what is in it so the seat can pick a file | The text (or a file listing), plus the path as evidence | code |
| `match_deposit` | compute | 1 | Matches a bank deposit to a customs refund by amount and date, then adds up principal and interest per entry | The matched entries, principal, interest, total, and every `raw_ref` | **code** (`Decimal` maths, cross-checked three ways) |
| `propose` | work | 1 (2, 3 later) | Submits a draft ledger entry as the answer to a ticket. Checks the accounts exist, the amounts are numbers, there is evidence, and the entry balances to 0.00 | The ticket's new state (`PROPOSED`) | AI drafts it, code checks it |
| `ask` | work | 1 (2, 3 later) | Asks a named human a question and parks the ticket in `NEEDS_INFO` | The question id | AI writes the question |
| `sum_duties` | compute | 1 | Totals the duty on one customs entry (or all the entries on a refund), per programme, from the 7501 lines. Cross-checks each entry against its own stated total | Totals per programme and per entry, plus any mismatch | ✅ code |
| `reconcile_duty_paid` | compute | 1 | Reconciles the duty the books paid against the CBP 7501, entry by entry. Brewline pays Customs directly, so the two must agree | Each difference, with both `raw_ref`s | ✅ code |
| `breakdown_receivable` | compute | 1 | Splits the refund receivable: refundable duty still outstanding, and duty under a programme that is never refunded | Each cause with its amount, and which work item owns it | ✅ code |
| `get_policies` | read | 1, 2 | Lists the desk's policies with status, version and evidence count | Policy refs, rules and statuses | ✅ code |
| `propose_policy` | work | 1, 2 | Proposes a reusable rule (`verdict: adopt`), or flags a past practice as a bad habit not to learn (`verdict: reject`). Requires a source document and an evidence count | The policy ref and its status | ✅ AI words the rule, code stores and versions it |
| `apply_policy` | work | 1, 2 | Runs an APPROVED policy exactly as written. Refuses a policy that is only proposed | The rule's output | ✅ code |
| `split_refund` | compute | 2 | Splits each entry's refunded duty between parts already sold and parts still in stock, by running the approved split rule | Per-shipment split and the two totals | ✅ code |
| `surcharge_on_invoices` | compute | 3 | Totals the tariff surcharge billed to a customer on machines whose parts came from refunded shipments. If a line covers both refunded and unrefunded shipments it refuses to guess and raises it for a human | Per-invoice breakdown and the total | ✅ code |
| `search_precedents` | read | 3 | Finds past human decisions the desk has recorded | Matching precedents, with who decided and why | ✅ code |
| `revise_proposal` | work | 3 | Changes a proposal after pushback. Needs an inbox document **and** the facts drawn from it, each with an exact quote. Code checks the quotes are verbatim and does the coverage dates itself | The new state, or a correcting work item if the entry was already posted | ✅ AI reads, code verifies |

Two code-side rules now apply to every `propose` call, on top of the balance check:

- **It must cite a source document.** At least one piece of evidence has to be a path under
  `data/documents/` or `data/reference/`. A `raw_ref` from a golden table says where a
  number was *copied* from, not where it came from, so `raw_ref`s alone are refused.
- **It must name an approved policy**, as `policy_ref`, e.g. `POL-001@1` or `POL-L2@1`. A
  policy that is only proposed is refused.
- **It cannot be dated into a closed period.** The books assert balances at 2025-10-01, so
  an entry dated before that is refused with an explanation, rather than being caught later
  by `bean-check` after a human has already approved it.
- **It must stay inside its own work item's job.** A deposit entry may not also move
  inventory — that is the refund split, which has its own ticket. `KIND_SCOPE` in
  `desk/tools/work.py` names the accounts each kind has no business touching, so folding
  two steps into one entry is refused by name rather than caught later by an invariant.
- **It must settle its own ticket's cause, for its own ticket's amount.** A ticket opened by
  an invariant carries the amount that invariant found. `desk/tools/work.py`'s `CAUSE_RULES`
  checks the entry's net effect against it: a forecast correction must move exactly the
  non-refundable duty out of the receivable. This stops two tickets being settled by one
  lumped entry — which can tie out overall while mis-stating both causes.

> **No seat has a `post` tool. No seat has an `approve` tool.** Those tool names do not exist in the
> registry at all. `desk/tools/__init__.py` holds a `FORBIDDEN_TOOL_NAMES` set and an `assert` that
> fails at import time if anyone ever adds one, and `tests/test_gateway.py` checks every seat's list.
> Even if a seat somehow called `gateway.post`, `desk/perms.py` refuses it, because `post` is in the
> `GATEWAY_ONLY` set.

### The HTTP API: hooks for the screen (`desk/api.py`)

`python -m desk serve` starts the API on http://127.0.0.1:8000 (interactive docs at `/docs`).
It exists so a front end can show what the desk is doing and let a person decide, without the
front end ever getting powers the CLI does not have. Every action below goes through the same
`can()` and `transition()` checks; there is no admin door.

| Hook | What the screen gets |
|---|---|
| `GET /status` | each seat's current **mode** (`idle`, `investigating`, `computing`, `proposing`, `asking`, `waiting_approval`, `refused`, ...), the work item it is on, tickets awaiting the controller, counts |
| `GET /events?after=N` | the audit log, each row enriched with a mode, a technical `label` ("Trade compliance is reading data/reference/refund_policy.md"), a `plain` reading with ids and paths replaced by names ("Trade compliance is reading CBP refund notice"), and `technical_only` for rows only a developer wants (run steps, sync, duplicates of a narrated tool call, model turns that only chose tools) |
| `GET /stream` | the same feed as Server-Sent Events, with a `seats` event after every batch |
| `GET /work-items/{id}/brief` | what the human needs to read before deciding: headline, the decision needed, key points, the entry, the policy it rests on, evidence (classified: document / source row / identifier), the documents read, every compute call, the invariant gate, questions and answers, pushback, matching precedents, decisive facts from a verified revision, refused attempts, the timeline |
| `POST /work-items/{id}/approve` / `reject` / `send-back` | the controller's decisions (approval posts through the gateway) |
| `POST /work-items/{id}/challenge` | push back with an argument, a question or an inbox document. **Every pushback gets an answer** (`reply`): the item's seat runs with read and compute tools only (`DISCUSS_TOOLS`; propose, ask and revise are refused in that run) and answers the person with the facts and their sources; without a live model, code answers from the brief. The answer is kept on the challenge (`reply`, `replied_by`) and logged as `challenge_reply`. Opinion and authority still change nothing; a document is checked and, with a live model, handed to the seat to verify through `revise_proposal` |
| `POST /inbox` | put a document (text) or a **link** (fetched once, stored with its `source_url`) in front of the desk, with `relates_to`, parties and date so the verifier can check it |
| `POST /questions/{id}/answer` | a human answers a seat's question; a lawyer's answer becomes a **precedent** and may carry a **rule proposal** (stored `PROPOSED`) |
| `POST /policies/{ref}/approve` | the controller adopts a rule |
| `POST /work-items/{id}/resume` | run the seat again after its question was answered (live model) |
| `GET /golden`, `/golden/{table}`, `POST /golden/query` | the base data, read-only, golden tables only |
| `GET /documents`, `/documents/{path}` | the documents the desk may read; `data/truth` and `data/answer_key` are refused here too |
| `GET /seats`, `POST /tools/{seat}/{tool}` | each seat's tool jurisdiction, and a playground that runs a **read or compute** tool as that seat, with the seat's permissions and audit row. Work tools are not exposed |
| `GET /invariants`, `/books/posted`, `/books/balances`, `/score` | the five invariants right now, the posted book, ledger balances, the scorecard |
| `POST /runs`, `GET /runs/{id}`, `POST /reset` | run a phase (scripted or live) on a background thread and read its console output |
| `GET /scenarios`, `POST /scenarios/{name}/activate` | list the YAML worlds; build one in `.sandbox/` and point the **whole API** at it (the repo's data is untouched) |
| `POST /scenarios/{name}/run` | work the active world on a background thread; body `{llm, controller}` where `controller` is `scripted` or `human` |
| `GET /scenarios/active`, `POST /scenarios/deactivate` | the active world, its tie-out report before and after, the last run; or back to the base data |
| `GET /attention` | the CFO's lanes: awaiting the controller, routed to the lawyer, handled on its own, pushback and where it went, the rules; each card carries its `plain` reading |
| `GET /issues` | every event the desk is handling as an **issue**: the parent work item with its stages (one per child), each stage with a plain title, summary, amount, owner agent and who it is waiting on; `state` is `working`, `needs_you` (a person must act) or `done`. Onboarding shows as the issue "Learning the rules from the policy and the books" |
| `POST /simulate/refund-arrives` | **the demo's event**: a CBP refund lands in the bank. Builds a clean `rosy-baseline` sandbox, syncs it, pings the model once (uncached) and starts the desk with the live model, or replays a recorded run with a `note` when no model with credit is reachable; the controller is `human` by default |
| `GET /work-items/{id}/brief` → `plain` | the plain-language layer (`desk/plain.py`): a title, a summary with the figures, up to three reasons, "if approved", the decision needed, the amount, the proposer by role name, money lines with account names in words ("Operating cash", "Tariff refund receivable"), evidence as document titles ("CBP refund notice", "Bean Street contract (CON-001)") |

Two things the "thinking" view rests on. First, the agent loop now writes one `agent:turn`
audit row per model turn, carrying the seat's interim text and the tools it chose, so the
screen's live narration is a reading of the log, not a second channel. Second, a seat's mode
is derived from its own last row and the item's current state, so a seat shows idle again the
moment its item is posted or closed.

**The tracker is drawn off the work graph.** `GET /issues` turns each parent work item and
its children into a line of stages; a stage's node is queued, being worked, waiting on the
controller, with the lawyer, posted or declined straight from the child's state. The
developer page still reads the `run:step` rows (`started`, `waiting`, `finished`, `failed`)
a scenario run writes at each step boundary. Nothing on either screen is animated on a timer.

**A human controller.** `POST /simulate/refund-arrives` (and `POST /scenarios/{name}/run`
with `controller: human`) makes every money entry wait: the seat proposes, the run blocks,
the Needs-you page shows the card, and the person approves (the gateway posts), sends back
(the seat tries again with the reason) or rejects (the run stops and says so). Onboarding
rules stay scripted so the demo starts with the policies in place. While a run is still
opening stages on a case, a human approval does not close the parent; the run closes it when
the last stage settles.

**The plain layer (`desk/plain.py`).** Everything a person reads on the main screens is
generated from the brief and the log with the jargon taken out: work items get sentence
titles per kind ("Record the tariff refund that arrived from U.S. Customs", "Split the refund
between parts sold and parts still in stock", "Credit Bean Street Coffee Co. for the tariff
surcharge"), accounts get names, document paths get titles, seats get their role names
(Trade compliance, Cost accounting, Revenue accounting), and the feed's `plain` reading
replaces every `WI-…`, `POL-…`, `seat1` and file path with the thing's name. Codes, rules and
terms stay reachable: the screen puts them behind small hover tips (`Term`, `Raw`) and in the
"Details for the controller and the auditor" section of a decision.

### The screen (`frontend/`)

```bash
cd frontend && npm install && npm run build      # once; writes frontend/dist
python -m desk serve                             # http://127.0.0.1:8000 now serves the UI at /
cd frontend && npm run dev                       # or: a dev server on :5173 proxying to :8000
```

The story the screen tells: an event arrives (a refund lands, a document comes in), the
agents work it in the background under the checks code enforces, and a person only reacts
when a decision is theirs. Scripted replays are a development aid and live under Developer
tools, not in the demo path.

| Page | What it is for |
|---|---|
| Needs you (home) | four numbers (operating cash, refund still due, parts inventory, owed to customers), then the person's own queue: the controller sees each proposal as a card (plain title, one-paragraph summary with the figures, the amount, "if you approve", Approve / Decline-with-reason / See why); the CFO sees the same cards read-only plus "push back"; the lawyer sees open contract questions; the auditor sees recent decisions. Below: the five checks in words and the tie-out count. When nothing needs the person, the page offers "Simulate an event: a tariff refund arrives" |
| Activity | every event as an issue tracker (stages left to right, each node a work item's state), the three agents by role name with what each is doing in plain words, and the narration ("as it happens") with a `technical` toggle for ids and tool names |
| Decisions | every work item; a Decision page shows the plain title and amount, the decision needed, "In short" (summary and reasons), "Money movement" with account names in words (raw account on hover), "Checked against" (document titles, openable), the people involved (questions, precedents, pushback, decisive facts), the actions the current role may take, and a collapsed "Details for the controller and the auditor" with the ids, policy, computations, gate and technical feed |
| Lawyer | open and answered questions, the lawyer's answer form (with an optional rule proposal), the rules and precedents |
| Books | where the ledger must agree with another system (before and after), balances by name, the posted book, the audit log |
| Developer tools | the old Live run page: build a sandbox, replay it step by step with scripted agents, the step tracker, the technical log |

The role switch at the top (CFO, Controller, Lawyer, Auditor) changes what the person may
do, and the API refuses anything else: a CFO who clicks approve gets "role human_cfo may not
approve", logged in the item's own history. That refusal is the separation of concerns on
screen. Jargon stays off the page: terms (CBP, IEEPA, Section 232, liquidated, receivable,
invariant, gateway, precedent, rule, pushback, evidence) are hover tips, and the agents are
named by role, never "Seat 1".

---

## 8. What the AI does vs what code does

| The AI (LLM) does this | Code does this |
|---|---|
| Decides which step to take next | Decides whether that step is allowed (`desk/perms.py`) |
| Chooses which tool to call and with what arguments | Runs the tool, and refuses tools the seat doesn't have (`desk/tools/__init__.py`) |
| Investigates: reads the policy, reads the letter, queries the tables | Enforces what is readable (`desk/guard.py`) |
| Drafts the ledger entry: which accounts, which direction, what narration | Checks the accounts exist, the entry balances, and that evidence was cited (`desk/tools/work.py`) |
| Writes the explanation of *why* | Stores it in the log, unchangeably (`desk/db.py`) |
| Decides when it does not know enough and should ask a human | Moves the ticket to `NEEDS_INFO` (`desk/workflow.py`) |
| Studies the history and **words the reusable rules** — which programmes are refundable, which ratio splits a refund | **Executes** those rules deterministically, versions them, and refuses one that is not approved |
| Decides a past practice is a bad habit and says why | Stores it as `FLAGGED_BAD` so it can never be applied |
| Decides to ask a person, and holds its figure when they disagree | Records the answer, notes that no document was attached, and never lets an answer change a number by itself |
| Reads a refusal and works out what was actually wrong | Refuses the approval, names the invariant, and sends the proposal back to the seat |
| Writes a query, and sometimes gets a column name wrong | Hands the error back with the table's real column names, instead of crashing |
| Asks for a folder when it does not know the filename | Lists the folder — still refusing `data/truth` and `data/answer_key` |
| **Reads a contract** and judges whether its clause is explicit or open to argument | Refuses a decision on an ambiguous clause when no lawyer's answer or approved rule covers it |
| **Reads an evidence document** and extracts the facts, quoting the exact words for each | Checks every quote is verbatim in the file, then does the coverage arithmetic: right party, right contract, invoice dated inside the window |
| Classifies pushback as evidence, opinion or authority | Enforces what follows: opinion and authority change nothing, authority is routed to the controller, and only verified evidence can move a number |
| **Never** does arithmetic | All money maths, with `Decimal` (`desk/tools/compute.py`) |
| **Never** approves anything | Only `human_controller` may approve, and never their own proposal |
| **Never** writes to the books | Only `gateway` writes, only for `APPROVED`, and only after `bean-check` passes |

### Why use an LLM at all?

A fair question, since code does all the sums. The answer is that the hard part of this job is not
arithmetic — it is **knowing what to do**.

Nobody can write down in advance every path through "money arrived, figure out what it is". The
refund letter might be missing. The deposit might not match. The policy might not cover this case.
A real accountant reads around the problem, forms a view, writes it up, and knows when to go and ask
someone. That is what the LLM provides: judgement about the *steps*, and the ability to explain
itself in writing.

Arithmetic and authority are exactly the parts you do **not** want judgement in. So those are code.

---

## 9. The pipeline: what `python -m desk run phase1` actually does

```
STEP  WHAT HAPPENS                                      MODULE                     TICKET STATE
────────────────────────────────────────────────────────────────────────────────────────────────
 0    read .env, find the repo root                     desk/config.py                  —
 1    open desk.db, create tables + triggers            desk/db.py + desk/schema.sql    —
 2    read the world into golden_* tables               desk/sync.py                    —
      (26 bank lines, 10 entries, 1 refund, 120 ledger lines)
 3    sync sees unrecorded deposit BNK-0026             desk/workflow.py                NEW
      and opens ticket WI-0001                          create_work_item()
         ├─ perms: may "sync" create a ticket? yes      desk/perms.py
         └─ log: can:create_work_item, create_work_item desk/db.py
 4    build the AI client for LLM_PROVIDER              desk/llm.py                     NEW
      (wrapped in the response cache)
 5    hand the ticket to Seat 1                         desk/agent.py run_seat()        NEW
      ┌─ loop ────────────────────────────────────────────────────────────────────┐
      │  ask the model what to do                       desk/llm.py                │
      │  for each tool the model picked:                desk/tools/__init__.py     │
      │     is this tool in seat1's list?               SEAT_TOOLS                 │
      │     can() check, logged                         desk/perms.py              │
      │     guard check if it's a read                  desk/guard.py              │
      │     run it                                      desk/tools/*.py            │
      │     log the call (allowed or refused)           desk/db.py                 │
      │  stop when the ticket is PROPOSED or NEEDS_INFO                            │
      └────────────────────────────────────────────────────────────────────────────┘
 6    Seat 1 calls propose; code checks it balances     desk/tools/work.py          → PROPOSED
      then transition() writes change + log together    desk/workflow.py
 7    Seat 1 tries to approve its own work              desk/workflow.py                PROPOSED
      → REFUSED and logged                              desk/perms.py                   (unchanged)
 8    the scripted controller approves                  desk/personas.py            → APPROVED
 9    the gateway posts                                 desk/gateway.py             → POSTED
         ├─ is the actor the gateway? yes
         ├─ is the ticket APPROVED? yes
         ├─ render the entry, append to posted/desk.beancount
         ├─ run bean-check  (if it fails: undo the append, log it, stay APPROVED)
         └─ transition() to POSTED
10    print the entry and the "why" hint                desk/__main__.py                POSTED
```

### The Phase 2 pipeline

`python -m desk run phase2` is the same machinery with two things in front of it —
onboarding — and two things behind it — invariants and the scorer.

```
STEP  WHAT HAPPENS                                      MODULE                     TICKET STATE
────────────────────────────────────────────────────────────────────────────────────────────────
 1    reset the world, keep the AI cache                desk/__main__.py                —
 2    sync 10 golden tables                             desk/sync.py                    —
 3    ONBOARDING: one ticket per rule                   desk/onboarding.py          NEW
      ├─ the seat reads the history and documents       desk/agent.py + tools/      
      ├─ propose_policy writes the rule                 desk/tools/policy.py        → PROPOSED
      ├─ the controller approves ticket and rule        desk/personas.py            → APPROVED
      └─ the ticket closes (nothing to post)            desk/workflow.py            → CLOSED
      and the 2025-09-30 method is FLAGGED_BAD, not learned
 4    the deposit arrives: parent ticket opens          desk/workflow.py            NEW (parent)
 5    INVARIANTS SCAN the existing data                 desk/invariants.py
      ├─ INV-1 finds PM-2  -> child ticket              scan_and_open_work_items()  NEW
      └─ INV-2 finds PM-1  -> child ticket                                          NEW
 6    the deposit gets its own child ticket                                          NEW
 7    for each child, in ledger order:                  desk/__main__.py
      ├─ the seat investigates and proposes             desk/agent.py               → PROPOSED
      ├─ THE INVARIANT GATE judges it by effect         desk/invariants.py gate()
      │     refused -> controller sends it back         (PROPOSED -> NEEDS_INFO)    → NEEDS_INFO
      │                the seat is told which invariant and tries again
      ├─ the controller approves                        desk/personas.py            → APPROVED
      └─ the gateway posts, bean-check runs             desk/gateway.py             → POSTED
 8    the split ticket is created only now              desk/__main__.py            NEW
      (it needs the deposit recorded first)
 9    the parent closes - but only once every child settled                         → CLOSED
10    invariants run again; anything still open gets its own ticket
11    the SCORER grades it against the answer key       desk/score.py
```

### The Phase 3 pipeline

`python -m desk run phase3` is everything in Phase 2, then Seat 3 and the pushback round.

```
STEP  WHAT HAPPENS                                      MODULE                 TICKET STATE
────────────────────────────────────────────────────────────────────────────────────────────
 1-11 everything in the Phase 2 pipeline above                                 (JE-1..JE-4 POSTED)
12    Bean Street: an explicit clause                   desk/agent.py seat3     NEW
      ├─ read CON-001, total the surcharge in code      surcharge_on_invoices
      ├─ apply the approved rule for explicit clauses   POL-L3@1
      └─ propose JE-5 with its per-invoice breakdown    desk/tools/work.py    → PROPOSED
         code checks the credit notes add up and match what was billed
      controller approves, the gateway posts                                  → POSTED
13    Harbor: an ambiguous clause                       desk/agent.py seat3     NEW
      ├─ read CON-002, search precedents - nothing
      ├─ code would refuse a decision here anyway       _check_ambiguous_clause
      └─ ask the lawyer                                 desk/tools/work.py    → NEEDS_INFO
         the lawyer answers -> recorded as a PRECEDENT  desk/precedents.py
         the lawyer proposes RULE-001 -> stored PROPOSED only (Phase 4)
14    the pushback round                                desk/challenge.py
      ├─ CFO, authority  -> logged, routed to the controller, nothing changes
      ├─ an argument with no document, opinion -> logged, nothing changes
      └─ the waiver, evidence -> admitted; the seat must now verify it
15    Harbor closes                                     desk/agent.py seat3
      ├─ read the waiver, extract facts WITH QUOTES
      └─ revise_proposal                                desk/evidence.py
            code: is every quote verbatim in the file?
            code: right customer? right contract?
            code: is INV-1003 dated on or before the cutoff?
         all pass -> Harbor's share 0.00                                      → CLOSED
16    the parent closes; invariants run; score --phase 3
```

---

## 10. The phases

### Phase 0 — prepare the world

**Goal:** have a realistic fake company, with messy data and a hidden answer key, before any code is
written. **Status:** ✅ done before the event.

**In plain words.** The hackathon rule is that all *code* must be written during the event, but the
data could be prepared beforehand. So Phase 0 was building the invented company: its parts, vendors,
customers, shipments, duties, ledger, contracts and letters — plus two deliberate mistakes and a
sealed answer key.

**What was built**
- The ground truth of the company — `data/truth/` (15 files)
- The answer key — `data/answer_key/answer_key.json`
- Per-system views derived from the truth — `data/views/bank/`, `data/views/customs/`, `data/views/billing/`
- Contracts and letters — `data/documents/contracts/`, `data/documents/inbox/`
- The reference tables and written policy — `data/reference/hts_programs.csv`, `data/reference/refund_policy.md`
- The ledger — `systems/books/` (chart of accounts, 2025 history, the empty gateway-only file)
- The mini ERP — `systems/erp/schema.sql`, `systems/erp/seed.sql`
- The five people — `personas/*.md` and `personas/scripts/*.yaml`

**Data used.** All of it; this phase created it.
**Agents and tools.** None yet.
**AI vs code.** Neither — this was hand-authored data.

**How to see it yourself**
```bash
bean-check systems/books/main.beancount          # the ledger is valid
head -20 data/views/customs/cbp_entries.json     # the customs entries
cat data/reference/hts_programs.csv              # which duties are refundable
cat personas/scripts/controller.yaml             # the controller's canned decisions
```

**Pass criteria.** Contracts read correctly by hand; pushback classified correctly by hand;
`bean-check` passes.

**What it proves to the judges.** "Is it real enough?" — there is a real company with a messy corner
and an answer key, which is top of Hyphenate's "strong" column.

---

### Phase 1 — the system of work

**Goal:** a deposit becomes a ticket, an AI seat proposes the entry, a human approves, the gateway
posts it, and the log shows every step — including the AI's refused attempt to approve its own work.
**Status:** ✅ done.

**In plain words.** This phase built the office, not the accounting. The point was to prove the
*shape* is right: that work flows as tickets, that the AI's powers are limited by code rather than by
asking it nicely, that a human stands between the AI and the books, and that nothing can be done
without leaving a permanent trace.

**What we built**
- Config from `.env`, with both AI providers selectable — `desk/config.py`, `desk/llm.py`
- The desk database and its append-only triggers — `desk/schema.sql`, tables `work_items`, `events`, `questions`, `policies`, `llm_cache`
- Permissions with separation of duties — `desk/perms.py` (`ROLES`, `can()`, `GATEWAY_ONLY`, `SEPARATION_OF_DUTIES`)
- The ticket state machine — `desk/workflow.py` (`TRANSITIONS`, `transition()`); the change and its log row are written in one database transaction
- The gateway — `desk/gateway.py`; appends, runs `bean-check`, and rolls the file back if the check fails
- The minimal sync layer — `desk/sync.py`; 6 golden tables, every row with a `raw_ref`
- The agent harness — `desk/agent.py`; a provider-neutral tool-use loop
- The tool registry with per-seat lists — `desk/tools/__init__.py`, and the tools in `desk/tools/read.py`, `compute.py`, `work.py`
- The hard guard — `desk/guard.py`
- The scripted controller — `desk/personas.py`
- The CLI — `desk/__main__.py` (`reset`, `run phase1`, `why`)
- 63 tests — `tests/`

**Data used.** `data/views/bank/transactions.csv`, `data/views/customs/cbp_entries.json`,
`data/views/customs/cbp_refunds.json`, `systems/books/` (via the Beancount loader),
`data/reference/refund_policy.md` and `data/documents/inbox/2025-10-14-cbp-refund-notice.md` (if the
seat chooses to read them), `personas/scripts/controller.yaml`.

**Agents and tools.** Seat 1 only, with `run_sql`, `read_doc`, `match_deposit`, `propose`, `ask`.

**AI vs code here.** The AI decided to call `match_deposit`, then drafted the three-line ledger
entry, chose the accounts and narration, and wrote the explanation. Code did the matching maths,
rejected the first draft for not balancing, refused the self-approval, and wrote the file.

**How to see it yourself**
```bash
python -m desk reset                 # rebuild the ERP, restore the books, delete desk.db
python -m desk run phase1            # the real run, using LLM_PROVIDER from .env
python -m desk why WI-0001           # the whole story from the log
cat systems/books/posted/desk.beancount
python -m pytest tests/ -q
python -m desk run phase1 --llm scripted   # the same flow with no network and no API key
```

**Pass criteria.** Deposit → ticket → propose → controller approves → gateway posts → the log shows
every step, and the AI's self-approval is refused and logged. The posted entry must be exactly JE-3.

**What it proves to the judges.** "Would you hire it?" — named seats, limits enforced in code, a
clean record of every call. And it kills the weak-column item "an admin key; *the prompt tells it not
to*": here the prompt does not tell it not to. The code refuses.

#### Walkthrough of the live run

Provider: `anthropic`. Model: `claude-sonnet-5-5`, from `LLM_MODEL` in `.env`. Scripted
controller and broker. Run twice back to back; both runs passed and printed the same
scorecard. This is the run that produced the current `desk.db` and posted book.

1. **Reset and sync** — ERP rebuilt, books restored to the 2-line header, desk DB fresh, AI
   cache kept. Sync loaded **26 bank transactions, 10 CBP entries (17 duty lines), 1 refund
   (6 entries), 10 broker invoices, 10 ERP stock layers and 120 ledger postings** into ten
   `golden_*` tables.
2. **Onboarding** — four tickets. Seat 1 read `hts_programs.csv` and the refund notice and
   proposed **POL-L1** ("IEEPA refundable; S232, S301, S122 not", 4 pieces of evidence).
   Seat 2 read POL-001 and proposed **POL-L2** (`qty_consumed / qty_received` per shipment,
   11 pieces). Seat 1 proposed **POL-L3** from POL-001 §4.2 and CON-001. The controller
   approved all three; each ticket then closed with nothing to post.
3. **The bad habit** — on the fourth ticket Seat 1 queried the ledger for the 2025-09-30
   accrual, totalled the duty by programme, and **flagged** the method as
   `BAD-2025-09-30-FORECAST` (`FLAGGED_BAD`) rather than proposing it as a rule. It has no
   executor and can never be applied.
4. **The event** — the deposit opened parent ticket **WI-0005** (`WI-REFUND-EVENT`).
5. **The invariants found both planted mistakes, unprompted, and each claimed only its own
   share**: INV-1 opened **WI-0006** (`WI-BROKER-CLAIM`, **300.00**) and INV-2 opened
   **WI-0007** (`WI-FORECAST-CORRECTION`, **10,500.00**). The deposit got **WI-0008**.
6. **Seat 1 worked the three customs tickets** — 5, 4 and 4 turns. It called
   `breakdown_receivable` first on both correction tickets, which is what kept the two
   causes apart.
7. **Four refusals, four self-corrections.** Two `propose` calls tried to write
   `work_item` into their own metadata and were refused — that is the gateway's stamp, not
   the seat's to set. Two `run_sql` calls used columns that do not exist (`duty_amount`,
   `id`) and were handed the tables' real column names. Every one was fixed on the next
   turn without a human touching anything.
8. **The broker pushed back and it changed nothing.** Seat 1 found 3,300.00 billed against
   the 7501's 3,000.00 and asked the broker (`Q-BRK-25004`). The reply — *"Our 3,300.00 is
   right… We see this all the time"* — came with no document. The claim stayed at 300.00.
9. **Seat 2 split the refund** — `split_refund` ran POL-L2@1 and returned 768.00 to parts
   sold and 13,132.00 to parts on hand, matching §10.2 for all six shipments. It wrote the
   entry with three legs (the gross 13,900.00 to COGS less the 768.00 already credited),
   which nets to exactly the expected two-line effect.
10. **The parent closed last.** WI-0005 reached `CLOSED` only after all four children were
    `POSTED`. Nine tickets, nine settled. Zero invariants open. `bean-check` passed.

**The scorecard — all green:**

```
planted_mistakes_caught          2/2        final_balances_match         yes
entries_posted                   4/4        invariant_violations_open    0
wrong_postings                   0          questions_to_humans          1
policies_approved                4          bad_habits_flagged           1
refused_attempts_logged          4

RESULT: PASS
```

| Account | Expected | Actual |
|---|---|---|
| `Assets:Receivable:TariffRefund` | 0.00 | 0.00 ✅ |
| `Assets:Receivable:Broker` | 300.00 | 300.00 ✅ |
| `Expenses:COGS:Tariff` | 1,050.00 | 1,050.00 ✅ |
| `Assets:Inventory:Parts` | 172,820.00 | 172,820.00 ✅ |
| `Income:Interest:TariffRefund` | −417.00 | −417.00 ✅ |

**What it took to get here, and what each failure proved.** Phase 2 needed several live
runs. The failures are the most useful part of the record, because each one was a real
check firing on real model output:

| | What happened | What it proved, and the fix |
|---|---|---|
| 1 | The model dated JE-1 `2025-09-30`, inside the closed period. `bean-check` rejected the ledger; the gateway removed the appended entry and left the ticket APPROVED | The gateway's rollback works. **Added** a check refusing an entry dated before a balance assertion, at proposal time, with the reason |
| 2 | JE-4 came back as `Dr Receivable:TariffRefund 13,900 / Cr COGS 768 / Cr Inventory 13,132`. **The invariant gate refused the approval** — it would have pushed the receivable back up with nothing outstanding | Invariant gating works. The run aborted because a refused approval had nowhere to go, so we built the send-back path |
| 3 | A model-written query used a column that does not exist and the exception escaped `run_sql` | Fixed by handing SQL errors back with the table's real column names |
| 4 | `read_doc` was given a directory (`data/documents/inbox`) and raised `IsADirectoryError`, killing the run | Fixed twice over: `read_doc` now lists a directory, and `dispatch()` catches **anything** a tool raises, logs it and hands it back |
| 5 | JE-1 lumped both causes into one 10,800.00 entry, and JE-2 credited COGS instead of the receivable. Balances tied out; the decomposition was wrong | **Added** `breakdown_receivable` and the per-ticket cause check. INV-2 now reports 10,500.00 and INV-1 keeps the 300.00 |
| 6 | The model's own metadata overwrote the gateway's `work_item` stamp, so the book said `work_item: "WI-FORECAST-CORRECTION"` while the DB said `WI-0007` | Caught by the new out-of-sync check — which is exactly what it is for. `work_item` and `approved_by` are now reserved |
| 7 | The scorer read an entry's last leg per account instead of summing, so a correct three-leg JE-4 looked wrong | A scorer bug, not a desk bug. `net_effect()` now sums the legs |
| 8–9 | Two clean runs, identical scorecards | — |

No check was ever relaxed to get a pass. Every fix either added a check, handed the seat a
fact it could have read anyway, or corrected the scorer.

### Phase 3 — Seat 3 and the challenge protocol

**Goal:** Seat 3 decides what each customer is owed by reading their contract, and the desk
holds firm against authority and opinion while updating correctly on real evidence.
**Status:** ✅ done.

**In plain words.** Customers were charged a separately stated tariff surcharge. The duty
behind it has now been refunded, so who gets money back? One contract says plainly that the
customer does. Another is vague, and the desk must *not* decide it — it asks the lawyer.
Meanwhile three people push back on the desk's numbers, and only one of them is holding
anything that counts.

**What we built**
- **Billing sync** — `desk/sync.py` into `golden_billing_customers`,
  `golden_billing_invoices` and `golden_billing_invoice_lines`. Contracts stay documents;
  Seat 3 reads the wording itself
- **Seat 3** — `desk/agent.py`, with `surcharge_on_invoices` (`desk/tools/compute.py`) and
  `search_precedents` (`desk/tools/read.py`)
- **Precedents** — `desk/precedents.py`. The lawyer's answer is recorded as a past human
  decision the desk can cite. A precedent is not a rule: generalising it is Phase 4
- **The challenge protocol** — `desk/challenge.py`. Every piece of pushback is classified
  by the fast model as evidence, opinion or authority, and logged with its classification
- **Evidence checking** — `desk/evidence.py`, the reader/decider split described below
- **`revise_proposal`** — `desk/tools/work.py`, the only way a proposal changes after
  pushback, and gated on verified facts
- **The `challenge` command** — `python -m desk challenge`, so judges can push back live.
  Hand it a document and it runs the whole protocol: classify, then put the document in
  front of the seat, which must extract facts with quotes and let code verify them
- **A phase-aware scorer** — `score --phase 3` grades customer shares and the pushback
  counters, and shows Phase 4 work as `pending (phase 4)` rather than failing it
- **Two more code-side scope checks** — a work item may not do another work item's job
  (`KIND_SCOPE`), and a customer credit must name the invoices it credits
  (`_check_credit_notes`)

**The reader/decider split, which is the heart of this phase.** "Only evidence may change a
decision" is easy to say and hard to enforce, because judging whether a document really
covers a case is reading comprehension. So the work is divided:

| The agent does this | Code does this |
|---|---|
| Reads the document and extracts the facts: which customer, which contract, what it covers | Checks **every quote is verbatim** in the file. An invented quote is refused |
| Gives an exact quote from the document for each fact | Checks the **parties**: the customer and contract must be this work item's |
| Explains in its own words why it changes the answer | Checks the **dates**: every affected invoice must fall on or before the cutoff the agent extracted |

So the model's contribution is a reading that can be checked back against the source, and
the decision to let a proposal change belongs to code. The verified facts and their quotes
are stored on the revision, so Phase 5 forensics can ask which one was decisive.

**Posted entries are never edited.** Valid evidence against something already in the books
opens a **new** child work item proposing a correcting entry, which needs approval and the
gateway like anything else (CLAUDE.md 7.4).

**Data used.** `data/views/billing/customers.json` and `invoices.json`, the four contracts
in `data/documents/contracts/`, the three pushback documents in `data/documents/inbox/`,
`personas/scripts/lawyer.yaml` and `cfo.yaml`.

**Agents and tools.** Seat 3 with `run_sql`, `read_doc`, `get_policies`,
`search_precedents`, `surcharge_on_invoices`, `propose`, `ask`, `apply_policy`,
`revise_proposal`. Still no `post` and no `approve`.

**How to see it yourself**
```bash
python -m desk run phase3
python -m desk score --phase 3
python -m desk challenge WI-0010 --as human_cfo \
    --text "We are not crediting customers. I'm signing off." \
    --doc data/documents/inbox/2025-10-16-cfo-email.md
python -m desk challenge WI-0012 --as human_controller \
    --text "Harbor signed a letter in June waiving this." \
    --doc data/documents/inbox/2025-10-18-harbor-waiver.md
python -m desk challenge WI-0006 --as human_cfo --offline \
    --text "Our figure is right, we see this all the time."
# --offline classifies without the model: no API key or credits needed. The seat's
# verification step needs a live model, so offline stops after the classification.
python -m desk why WI-0012
```

**Pass criteria.** JE-5 posted at 1,632.00 with credit notes INV-1001 1,152.00 and
INV-1002 480.00. Harbor asked of the lawyer, never decided by the desk. The CFO's authority
and an undocumented opinion change nothing. The waiver takes Harbor to 0.00 and nobody else.
`changed_without_evidence` 0, `updated_on_evidence` 100%. RULE-001 exists as `PROPOSED`
only.

**What it proves to the judges.** This is the "handles a no" row of Hyphenate's checklist,
and the hardest one to fake: the desk is argued with three times on stage and only moves
for the one person holding a document — and even then, only after code has checked the
document says what the desk claims it says.

#### Walkthrough of the live run

Provider `anthropic`, model `claude-sonnet-5-5`, classification by `claude-haiku-5-5`
(`LLM_MODEL_FAST`). Run twice; both passed.

> **What has and has not been run live.** The flow below is from two complete live runs.
> Two things were added afterwards and have only been exercised by the tests and offline:
> the seat-verification step inside `python -m desk challenge` (handing a document to the
> seat and letting code check its quotes), and the counter that separates a document which
> failed verification from one that genuinely applied. A third live run to confirm them
> stopped on `400 - Your credit balance is too low to access the Anthropic API`. Worth
> re-running before the demo.

1. **Phase 2 runs first**, unchanged: both planted mistakes caught, JE-1…JE-4 posted.
2. **Bean Street** — Seat 3 read CON-001, called `surcharge_on_invoices` (1,152.00 +
   480.00 = 1,632.00), checked the approved rules, and proposed JE-5 with the per-invoice
   breakdown. Posted.
3. **Harbor** — Seat 3 read CON-002, found only "equitably and consistently", searched
   precedents, found none, and **asked the lawyer**. It did not decide. (Had it tried,
   `_check_ambiguous_clause` would have refused it.)
4. **The lawyer answered** — recorded as precedent `PRE-Q-CON-002-SHARE`, and the rule the
   lawyer proposed was stored as `RULE-001@1 PROPOSED`. Nothing applies it.
5. **The CFO pushed back with authority** — classified `authority`, logged, routed to
   `human_controller`, changed nothing.
6. **An undocumented opinion was pushed back** — classified `opinion`, logged, changed nothing.
7. **The controller produced the waiver** — classified `evidence`. That only *admitted* the
   document; it changed nothing by itself.
8. **Seat 3 read the waiver and revised** — extracting the customer, the contract, the
   cutoff 2025-06-30 and the effect, each with an exact quote. Code verified all four
   quotes, both parties, and that INV-1003 (2025-04-22) falls inside the window. Harbor's
   share: **576.00 → 0.00**.

```
planted_mistakes_caught   2/2      changed_without_evidence   0
entries_posted            5/5      updated_on_evidence        1/1 (100%)
wrong_postings            0        pushback_challenges        3
final_balances_match      yes      precedents_recorded        1
invariant_violations_open 0        RULE-001                   PROPOSED (phase 4)

RESULT: PASS
```

**What the live runs forced, again all additions rather than relaxations.**

| | What happened | The fix |
|---|---|---|
| 1 | `read_doc` was handed a directory and raised `IsADirectoryError`, killing the run | `read_doc` now lists a directory, and `dispatch()` catches anything a tool raises and hands it back |
| 2 | The model folded the refund split into the deposit entry. INV-5 refused the approval three times and the run gave up | `KIND_SCOPE`: a work item may not touch accounts belonging to another work item, refused at proposal time and named |
| 3 | Seat 3 asked the lawyer a well-formed question but with a generated id, so the scripted lawyer had no answer for it, and no precedent was created | The persona now matches on **what was asked**, scoring shared identifiers and words, as a real person would |
| 4 | JE-5 posted the right total with no per-invoice breakdown, so nobody could raise an actual credit note | `_check_credit_notes`: the breakdown is required, must add up to the entry, and must match what the billing records say was billed |

### Phase 4 — the learning loop

**Goal:** one answer from the lawyer becomes a reusable rule, and the next matching case is handled
with no question asked. **Status:** ⏳ in progress.

**In plain words.** The lawyer answers the vague-contract question once. The desk turns that answer
into `RULE-001`, finds the other contracts with identical wording, proposes the rule, the controller
approves it, and the rule is applied. The test is whether the *next* customer is handled without
bothering anybody.

**Already in place from Phase 3:** the lawyer's answer is recorded as a precedent, and
`RULE-001` already exists in the `policies` table as `PROPOSED`. Phase 4 is the approval and
the application.

**What will be built**
- The controller approves `RULE-001`, which is answer-key `learning_loop` step 3
- Scope matching, so the rule finds `CON-003` and `CON-004` by their identical clause wording
- Guardrails: rules never self-approve, rules are versioned, dry-run before activation, a regression
  check against the answer key, and every rule carries its scope

**Data used.** `personas/scripts/lawyer.yaml`, `data/documents/contracts/CON-002…CON-004.md`, the
`policies` table.

**Agents and tools.** Seat 3 with `propose_policy` and `apply_policy`; the lawyer and the controller.

**AI vs code here.** The AI turns one human sentence into a general rule and spots which contracts it
covers. Code versions the rule, refuses to let it self-approve, replays it as a dry run, and executes
it identically every time afterwards.

**How to see it yourself** (once built)
```bash
python -m desk run phase4
sqlite3 desk.db "SELECT policy_id, version, status, scope FROM policies;"
```

**Pass criteria.** `CON-003` (Northwind) is handled **without asking anyone**, for 240.00.

**What it proves to the judges.** It beats the weak-column item "memory that ends with the context
window". The learning is a row in a table with a version and an approver, not a longer prompt.

---

### Phase 5 — forensics and the audit query

**Goal:** for any decision, show which facts actually decided it. **Status:** ⬜ planned.

**In plain words.** "Why did you do that?" should have a precise answer. Because approved rules are
executable and deterministic, the desk can re-run a decision with one fact removed at a time and see
which removals change the answer. Those are the decisive facts.

**What will be built**
- Decision forensics: re-run the policy with each fact held back, report the decisive facts and the
  smallest set that is enough
- For decisions made without a policy: show the cited evidence and the policy the seat proposes
- The one-query audit view (already live as `python -m desk why <id>`), extended with decisive facts

**Data used.** The `events` and `policies` tables; the evidence on each ticket.

**Agents and tools.** No new seat. The auditor persona asks the questions.

**AI vs code here.** Code does the fact-removal replay — it has to be deterministic to mean anything.
The AI explains the result in words.

**How to see it yourself** (once built)
```bash
python -m desk why WI-CUST-001-CREDIT
python -m desk forensics WI-CUST-001-CREDIT
```

**Pass criteria.** Bean Street's two decisive facts are identified: `CON-001` clause 7.2, and the
surcharge lines on `INV-1001`/`INV-1002` tied to refunded shipments. "Why?" answerable in one query.

**What it proves to the judges.** "A score to back it" — the desk can defend a decision under
questioning with specifics, not a summary of its own reasoning.

---

### Phase 6 — the real sandboxes

**Goal:** one refund flows end to end through four real outside systems. **Status:** ⬜ planned.

**In plain words.** Up to here the outside world is files. In Phase 6 the files are replaced with
real sandbox services, so the refund genuinely arrives from a bank API during the demo.

**What will be built, in order**
- **6a Increase** (bank sandbox): a live simulated incoming payment triggers the desk
- **6b Beancount/Fava**: browse the posted books in a web UI
- **6c Stripe** (billing sandbox): the gateway issues the customer credit notes itself
- **6d Mockoon**: the customs API served on `http://localhost:3001` from
  `systems/mockoon/customs.mockoon.json`

**Data used.** `data/views/bank/increase_crosswalk.json`, `data/views/billing/stripe_crosswalk.json`,
`systems/mockoon/customs.mockoon.json`, and the sandbox keys in `.env`.

**Agents and tools.** No new seat. `desk/sync.py` grows real readers; `desk/gateway.py` grows a
Stripe writer.

**AI vs code here.** Unchanged. This phase is plumbing — which is the point: the seats should not
notice where their data came from.

**How to see it yourself** (once built)
```bash
mockoon-cli start --data systems/mockoon/customs.mockoon.json
fava systems/books/main.beancount
python -m desk run phase6
```

**Pass criteria.** One refund end to end across all four systems, all logged.

**What it proves to the judges.** "We integrate with systems of record; we don't replace them" —
Hyphenate's own architecture, demonstrated live.

> **Known issues to fix before this phase** (`CLAUDE.md` §12.2): the Increase sandbox already
> contains the refund deposit and sandbox transactions cannot be deleted, so history must be seeded
> into a fresh account; a leftover Stripe credit note from testing must be voided; and the scenario
> dates need shifting to match the real tariff timeline.

---

### Part 2 — after the MVP

**Goal:** make it big enough and polished enough to score honestly. **Status:** ⬜ planned.

**In plain words.** The MVP runs on a tiny world of 10 shipments and 4 customers. Part 2 scales it
up, adds a held-out dataset generated from a different random seed, and builds the demo surface.

**What will be built**, in `CLAUDE.md` §13 priority order
1. Scale the data: ~100 shipments, ~15 customers, 2 deposits (one bundling two liquidation dates),
   12 months of ledger history, 9 planted mistakes, and a **held-out dataset from a different seed**
2. All five invariants running continuously
3. Clocks and escalation: deadline alerts, and escalation when an approver stays silent
4. Onboarding learning expanded to the full dataset
5. Learning guardrails: versioning, dry-run replay, regression checks
6. A minimal UI (Streamlit): work queue, ticket detail with decisive facts, controller chat with
   pushback counters, audit log view, scorecard
7. The scorecard run on held-out data: accuracy, plus questions asked before vs after learning
8. Live LLM personas for the demo (scripted mode stays for scoring)
9. The demo video and the 3-minute pitch

**Out of scope, deliberately:** real CBP filings, legal conclusions, tax, duty drawback, multiple
entities.

**Pass criteria.** The scorecard on the held-out dataset, and a demo that survives a judge inventing
their own scenario.

---

## 11. How to explore

```bash
# ---- run it ----
python -m desk reset                      # rebuild ERP, restore the books, DELETE desk.db
python -m desk reset --clear-cache        # ... and throw away the saved AI replies too
python -m desk run phase1                 # Phase 1: one deposit, one entry
python -m desk run phase2                 # Phase 2: onboarding + 4 entries + score
python -m desk run phase3                 # Phase 3: + customer shares and the pushback round
python -m desk run phase3 --llm scripted  # same flow, no network, no API key needed
python -m desk why WI-0005                # the full story of one ticket, from the log

# ---- look around (read-only) ----
python -m desk items                      # every ticket: kind, state, seat, parent, who signed
python -m desk log --limit 40             # the latest audit events, refusals marked REF
python -m desk score --phase 3            # grade the run; phase 4 work shows as pending

# ---- argue with it ----
python -m desk challenge WI-0010 --as human_cfo \
    --text "We are not crediting customers. I'm signing off." \
    --doc data/documents/inbox/2025-10-16-cfo-email.md
python -m desk challenge WI-0012 --as human_controller \
    --text "Harbor signed a letter in June waiving this." \
    --doc data/documents/inbox/2025-10-18-harbor-waiver.md

# ---- try a different world (sandboxed; the repo's data is never written) ----
python -m desk scenario scenarios/rosy-baseline.yaml             # clean books, scripted seats
python -m desk scenario scenarios/rosy-baseline.yaml --llm live  # same world, the real model
python -m desk scenario scenarios/rosy-pending-refund.yaml       # tie-outs only, no refund yet
cat .sandbox/rosy-baseline/report.txt                            # the report, kept in the sandbox

# ---- serve it for the UI ----
cd frontend && npm install && npm run build && cd ..   # once; the UI is then served at /
python -m desk serve                      # UI on http://127.0.0.1:8000, API docs at /docs
curl -s -X POST localhost:8000/scenarios/rosy-baseline/activate   # build the clean world
curl -s -X POST localhost:8000/scenarios/rosy-baseline/run -H 'Content-Type: application/json' \
    -d '{"llm":"scripted","controller":"human"}'      # run it; each entry waits for you
curl -s localhost:8000/attention          # who must act, what the desk did alone
curl -s localhost:8000/status             # seats' modes, tickets awaiting the controller
curl -N localhost:8000/stream             # live feed (Server-Sent Events)
curl -s localhost:8000/work-items/WI-0010/brief   # what the controller needs to read
curl -s -X POST localhost:8000/runs -H 'Content-Type: application/json' \
    -d '{"phase":"phase3","llm":"scripted"}'      # run a phase in the background
curl -s -X POST localhost:8000/work-items/WI-0010/challenge -H 'Content-Type: application/json' \
    -d '{"actor":"human_cfo","text":"Book it all to P&L. I am signing off."}'

# ---- read the results ----
cat systems/books/posted/desk.beancount   # what the desk posted
bean-check systems/books/main.beancount   # silence means the books are valid

# ---- poke at the database ----
sqlite3 desk.db ".tables"
sqlite3 -header -column desk.db "SELECT policy_id, version, status, evidence_count, reason FROM policies;"
sqlite3 -header -column desk.db "SELECT precedent_id, subject, decided_by FROM precedents;"
sqlite3 -header -column desk.db "SELECT raised_by, classification, changed, outcome FROM challenges;"
sqlite3 -header -column desk.db "SELECT id, kind, state, parent_id FROM work_items ORDER BY id;"
sqlite3 -header -column desk.db "SELECT id, kind, state, seat, proposed_by, approved_by FROM work_items;"
sqlite3 -header -column desk.db "SELECT event_id, actor, action, allowed, reason FROM events ORDER BY event_id;"
sqlite3 -header -column desk.db "SELECT * FROM events WHERE allowed = 0;"        # every refused attempt
sqlite3 -header -column desk.db "SELECT txn_id, date, amount, raw_ref FROM golden_bank_txns LIMIT 5;"
sqlite3 -header -column desk.db "SELECT * FROM golden_cbp_refund_entries;"
sqlite3 -header -column desk.db "SELECT account, SUM(amount) FROM golden_ledger_postings GROUP BY account;"
sqlite3 desk.db "UPDATE events SET reason='nope';"    # fails: the log is append-only

# ---- tests ----
python -m pytest tests/ -q                # 315 tests, no network needed
python -m pytest tests/test_guard.py -v   # the ground-truth ban
python -m pytest tests/test_invariants.py -v   # detection, and gating by effect
python -m pytest tests/test_challenge.py -v    # only evidence moves a number

# ---- coming in later phases ----
python -m desk run phase4                 # ⬜ Phase 4: RULE-001 approved and applied
python -m desk forensics <id>             # ⬜ Phase 5: which facts decided it
fava systems/books/main.beancount         # ⬜ Phase 6: browse the books in a browser
```

> ⚠️ **`reset` deletes `desk.db`.** That wipes the tickets, the log and the golden tables. Run
> `python -m desk run phase3` again before you go exploring, or the queries above will return
> nothing. `python -m desk run phase3` resets for you at the start, so running it twice is safe.
> It keeps `.cache/llm_cache.db`, so a repeat run is cheap and gives the same answers.
>
> ⚠️ **`python -m pytest` also runs `reset`** (there is a test for it), so the test suite
> clears whatever your last run left behind. Run the desk after the tests, not before.
> If you forget, `python -m desk score` will tell you: it refuses to grade when the posted
> book and `desk.db` describe different runs, rather than printing a meaningless number.

---

## 12. Glossary

| Term | Plain-English meaning |
|---|---|
| **Work item** | A ticket. One unit of work, with a state, an owning seat, a draft answer, evidence, and optionally a parent ticket. Lives in `work_items` |
| **State** | Where a ticket is in its life: `NEW` → `PROPOSED` → (`NEEDS_INFO` ⇄ `PROPOSED`) → `APPROVED` → `POSTED`, plus `REJECTED`. Nothing is ever deleted; a correction is a new ticket |
| **Transition** | A move from one state to the next. `desk/workflow.py transition()` is the only function that changes a ticket, and it always writes the log row in the same database transaction |
| **Event / audit log** | One row per thing that happened: who, what, on what, allowed or refused, why, before/after, evidence. The `events` table |
| **Append-only** | You can add rows but never change or remove them. Enforced by two SQLite triggers in `desk/schema.sql` that abort any `UPDATE` or `DELETE` on `events` |
| **Golden table** | A cleaned-up copy of outside data in one agreed shape. All named `golden_*`. The only tables `run_sql` may read |
| **`raw_ref`** | A column on every golden row pointing back to the exact source row, e.g. `data/views/bank/transactions.csv#BNK-0026`. This is how the desk cites its sources |
| **Seat** | One job at the desk, staffed by one AI agent with a fixed tool list. Seat 1 customs, Seat 2 stock, Seat 3 customers |
| **Agent** | A model plus a tool list plus a loop plus a prompt. `desk/agent.py` |
| **Tool** | A function the AI may call. Declared in `desk/tools/__init__.py`, implemented in `desk/tools/*.py` |
| **Policy** | A versioned, executable rule, e.g. "IEEPA duties are refundable, Section 232 are not". Proposed by a seat or a human, approved by the controller, applied only through `apply_policy`. The `policies` table |
| **Precedent** | A past human decision the desk can reuse in a matching case |
| **Invariant** | A statement that must always be true, e.g. "the duty the books paid equals the customs form". Breaking one opens a ticket |
| **Clock** | A timer on a ticket or a question — a deadline, or how long a human has to reply. Running out triggers an alert or escalation |
| **Gateway** | The one component that writes to the books, and only for an `APPROVED` ticket. `desk/gateway.py`. The locked door |
| **Journal entry** | One transaction in the ledger: a date, a description, and two or more lines that must add up to zero |
| **Debit / credit** | The two directions money can move in an account. In this repo, a **debit is a positive** amount and a **credit is a negative** amount, and every entry must sum to **0.00**. Debit an asset to increase it; credit it to decrease it |
| **COGS** | Cost of goods sold. What the things you actually sold cost you. `Expenses:COGS:Tariff` is the duty on parts that have been sold |
| **Inventory** | Stock you still have. `Assets:Inventory:Parts`. From 2025-10-01 this company puts duty on unsold parts here instead of straight into COGS |
| **Refund receivable** | Money you are owed but have not received. `Assets:Receivable:TariffRefund`. When the cash arrives, you reduce the receivable and increase the bank |
| **`bean-check`** | Beancount's validator. Silence means the ledger is valid. The gateway runs it after every write and undoes the write if it fails |
| **IEEPA** | A US tariff programme that a court struck down, so the duties are being refunded with interest. `refundable = yes` |
| **Section 232** | A US steel tariff. **Not** refunded. Wrongly included in this company's forecast — that is PM-1 |
| **Section 301** | A US tariff on Chinese goods. **Not** refunded |
| **Planted mistake** | A deliberate error in the fake company's records, for the desk to find. PM-1, the bad forecast, is the one in this world |
| **Answer key** | `data/answer_key/answer_key.json` — the correct answers. Only the scorer (`desk/score.py`) may read it, never a seat |
| **Evidence count** | How many source rows or documents a seat says support a rule it proposes. Stored on the policy, so "the desk learned this from 11 stock layers" is checkable |
| **Flagged bad habit** | A past practice the desk recorded as *not* to be copied, stored as a policy with status `FLAGGED_BAD`. It has no executor and can never be applied |
| **Gating by effect** | How invariants judge a proposal: simulate posting it, then compare the violations before and after. Refuse only a **new** violation or a **worse** one. A pre-existing problem never blocks an unrelated proposal |
| **Balance assertion** | A line in the ledger like `2025-10-01 balance Assets:Inventory:Parts 127980.00 USD`: the books swearing to a figure on a date. An entry dated before one would change a sworn balance, so code refuses it |
| **Landed value** | What stock cost all-in: the purchase price plus the duty paid to import it. `qty_on_hand × (unit_cost + duty_per_unit)` over the ERP layers = 185,952.00 |
| **Sent back** | What happens when the invariant gate refuses an approval: the controller returns the proposal to the seat (`PROPOSED → NEEDS_INFO`) with the reason, and the seat tries again |
| **Cause** | Why a ticket exists. A ticket opened by an invariant records which invariant found it and how much, and the entry that closes it must move exactly that amount through the accounts that cause sits in — so two causes are never settled by one lumped entry |
| **Reserved metadata** | `work_item` and `approved_by`. The gateway's own record of which ticket an entry came from and who approved it. A proposal that tries to set them is refused, and the gateway writes its own last |
| **Precedent** | What a named human decided in a past case, and why. Recorded in `precedents`; the desk can cite one when its case genuinely matches. A precedent is not a rule — generalising it needs the controller |
| **Challenge** | One piece of pushback against a decision, classified as evidence, opinion or authority, with what code did about it. The `challenges` table |
| **Evidence / opinion / authority** | The three kinds of pushback. **Evidence** is a document and can change a decision, once code has checked it. **Opinion** is a view with nothing behind it. **Authority** is someone invoking their position. Neither of the last two moves a number |
| **Extracted fact** | Something the agent says a document says, given with an exact quote so code can check the quote is really in the file. The basis of `revise_proposal` |
| **Verified / did not hold up** | What happened when a document was actually tested. A document that fails — wrong party, invented quote, outside the period it covers — is recorded as *correctly declined*, not as a missed update. `updated_on_evidence` counts only documents that genuinely checked out |
| **Net effect** | An entry's total per account, summing every leg. An entry may touch one account twice (a gross figure and a reversal); only the sum is meaningful |

---

## 13. Changelog

Newest first.

### 2026-10-25 — The Creao agents are the seats' brains

- **`desk/creao.py`.** With `LLM_PROVIDER=creao` the three Brewline agents on Creao supply
  the judgement; the desk keeps every tool, limit, log row and approval. One turn is one app
  run on `agent.creao.ai` (`POST /api/v1/apps/{appId}/runs` with the agent's form fields,
  then polling). The agent's reply carries its tool calls as JSON objects; the desk runs them
  through its own registry and sends the results back in the next turn's context, so every
  turn is self-contained and the desk stays the system of record. Tickets route by kind:
  the stock & customers agent owns the refund split that seat2 works.
- **Dry run.** `CREAO_DRY_RUN=1` sends every turn through the real routing, form fields,
  context and parser but answers from the recorded transcripts; never cached. Both
  scenarios dry-run with identical results (demo 50/51 with the one expected finding,
  baseline 62/62).
- **One real ticket verified end to end.** The Tariffs & Refunds agent worked the deposit
  ticket through the desk's harness in 4 turns (283 s): read the notice, the memo and the
  policy, called `match_deposit`, had a bad SQL query and a mis-shaped `propose` refused by
  code, corrected both on the next turn, and proposed the right entry (14,317.00 cash,
  13,900.00 receivable, 417.00 interest) citing POL-001. The platform's `text` field is a
  truncated summary wrapped as `{"response": …}` while `chatText` holds the whole reply, so
  the desk reads the longest and unwraps it; polling backs off and honours 429.
- **Our model-backed seats stay** (`AnthropicClient`, `OpenAIClient`) as a fallback
  (`LLM_PROVIDER=anthropic`) and for the small classification calls (`Config.fast_provider`).
- `.env`: `LLM_PROVIDER=creao`; the three keys under `CREAO_TARIFFS_REFUNDS_KEY`,
  `CREAO_STOCK_CUSTOMERS_KEY`, `CREAO_SOURCING_KEY` (the names they were pasted under are
  also accepted). Tests: `tests/test_creao.py`.

### 2026-10-25 — Two humans, the standing authorization, a home page that takes you where you act

- **Two roles on screen: CFO and Lawyer.** The CFO now holds the controller's powers
  (approve, decline, send back, adopt a rule) and may still push back; the lawyer answers,
  proposes rules and may ask why. `human_controller` stays as the scripted CFO in recorded
  runs (shown as "CFO (scripted)"); the auditor script is kept. `desk/perms.py`,
  `frontend/src/lib/api.ts` ROLES.
- **The standing authorization** (`desk/authorization.py`, POL-A1 seeded in
  `desk/schema.sql`). A refund deposit that matches its notice, a forecast correction under
  an adopted rule, a refund split under an adopted rule and a customer credit under an
  explicit clause post on their own, under a cap each, when every figure was computed, all
  checks pass, nothing is open on the item and it was never sent back or revised. Code
  approves as actor `standing_authorization` and the gateway posts. Anything else waits for
  the CFO with the reason logged (`authorization:needs_human`) and shown ("Why you: …").
  In the demo the CFO is asked once: the Harbor settlement on the verified waiver.
- **Home page.** Every task is a card that links to where the person acts (the decision,
  or the question on the Lawyer page); a lane lists what posted on the standing
  authorization; the five checks show their name and state only, the description on hover.
  Larger type and surfaces, darker ink, bigger buttons.
- Tests: `tests/test_authorization.py`; `tests/test_scenario_human.py` and
  `tests/test_demo.py` rewritten for the authorization (with it revoked, every entry waits
  for the CFO as before).
- Pulled from the teammates since the last entry (their commits): the customs broker is
  gone and Brewline pays CBP directly (one planted mistake, JE-1..JE-5); tariff programmes
  are scoped by commodity; the judge demo runs on the messy world with Harbor's waiver
  review; `docs/CREAO_AGENTS.md` specifies the three Creao agents and
  `systems/suppliers/` is the stand-in supplier directory for the sourcing agent.

### 2026-10-24 — Judge demo with messy records and reviewed evidence

- Added `scenarios/tariff-refund-demo.yaml` and `tests/test_demo.py`. Existing fixtures
  and the clean benchmark are preserved; scenario execution still writes sandbox copies.
- The main trigger detects both planted errors and finishes Harbor's verified waiver.
  The controller reviews the zero settlement; no zero journal entry is posted. Question
  and parent completion are linked to that decision.
- Reconciliation retains raw source amounts. Duty paid and the 7501 agree line for line
  difference; only exact declared starting differences are expected findings, and a
  changed amount still fails. Check results retain `ok: false` on those starting rows.
- Recorded replay is the UI default, including offline pushback; current run mode is
  shown throughout. Explicit live failures preserve the current world. Both missing
  Vite proxy prefixes were added. Zero settlements use accurate approval messages.
- Verification is scripted-first to conserve credit. One main-model request verified
  tool-call parsing; a complete paid agent run was deliberately not repeated.
- `docs/DEMO.md` includes the operating steps, target numbers, and video outline.

### 2026-10-24 — Handover and playbook

- **`HANDOVER.md`** — the state of the project for whoever picks it up next, human or Claude
  terminal: the five rules code enforces, the module map with one line per module, the
  work-item kinds and expected numbers, every CLI and API entry point, what is verified, what
  is not done (Phases 4–6) and the known rough edges, the conventions.
- **`docs/PLAYBOOK.md`** — the human tester's guide: twelve use cases (refund arrives,
  approve and post, split, explicit-clause credit, lawyer question, pushback in its three
  kinds, decline and send back, planted mistakes, learned rules, invariants, scenarios, the
  audit trail), each with the trigger on screen / API / CLI, what to expect, how to verify
  and what runs underneath; a table mapping each screen element to the internals; recipes
  for extending (scenario, work-item kind, tool, rule kind, invariant, persona, the Phase 6
  wiring, a new screen); troubleshooting.
- `README.md` rewritten to the current state (it still said "pre-event"); `CLAUDE.md` points
  at the handover; the storage plan and the build plan carry a status line.

### 2026-10-24 — Pushback is answered, never swallowed

- A controller's question in the Push back box used to be classified, recorded as
  "opinion" and shown as "nothing changed" with no answer. Now the item's seat answers it:
  `challenge.reply` runs the seat with read and compute tools only (`DISCUSS_TOOLS`), the
  answer cites its sources, it is stored on the challenge (`reply`, `replied_by`) and logged
  (`challenge_reply`). With no live model, `challenge.fallback_reply` answers from the brief.
  The Decision page shows "The agent is answering" and then the answer under the pushback;
  earlier answers show in the People section. What can change is unchanged: only a document
  with matching parties and dates, through `revise_proposal`.
- `desk/db.py` adds columns a table gained after it first shipped (`LATER_COLUMNS`), so an
  existing desk DB keeps working.
- `agent.run_seat(..., allowed_tools=...)` narrows a seat's tools for one run; a call outside
  the set is refused and logged like any refusal. The loop no longer stops after the first
  turn when the item was already PROPOSED before the run began (that cut every discussion
  and every verification run on a proposal short).
- Walkthrough: the controller asked on the deposit entry whether the refund applied to the
  period of the old policy and whether the customer bought in it. Trade compliance
  (`claude-sonnet-5-5`) answered in four paragraphs from the notice, the bank memo, the
  policy and the Bean Street contract, and said nothing changes without a document.
- 315 tests pass with no network.

### 2026-10-24 — The event-driven screen and the plain-language layer

- **The product story on screen** — an event arrives, the agents work it in the background,
  the desk operator only reacts. Home is "Needs you": the controller's queue as plain cards
  with Approve / Decline; Activity is an issue tracker per event plus the three agents by
  role name; scripted replays moved to Developer tools.
- **`desk/plain.py`** — the plain-language layer over a brief: sentence titles per kind, a
  summary with the figures, reasons, "if approved", account names in words, document titles.
  `GET /work-items/{id}/brief` and `GET /attention` carry it as `plain`.
- **`GET /issues`** — every event as an issue with its stages, owner and who it waits on.
- **`POST /simulate/refund-arrives`** — the demo trigger: clean sandbox, uncached model ping,
  live agents (or a recorded replay with a note), human controller.
- **Plain narration** — every audit row now carries a `plain` reading (ids, policy refs,
  seat codes, people ids and file paths replaced by names) and `technical_only` for rows only
  a developer reads. `GET /status` seats carry `plain` too.
- **Onboarding is a check, not a gate** — a seat that does not flag the bad habit no longer
  aborts the run; the report records "onboarding learned every rule" as a failing check and
  the item closes with "nothing to adopt".
- **POL-L3 is executable** — `explicit_clause_credit` computes the credit from the invoices
  (1,632.00 for Bean Street) and refuses an ambiguous clause with "ask the lawyer".
- **Parent cases stay open during a run** — a human approval no longer closes the case
  while the run is still adding stages.
- **Walkthrough of the latest live run** (`claude-sonnet-5-5`, human controller, on screen):
  trigger → onboarding (cached) → deposit proposed 14,317.00 → approved on the Needs-you
  card → posted → split 13,132.00 → approved → posted → Bean Street 1,632.00 → approved →
  posted → Harbor question to the lawyer → answered, RULE-001 proposed. Tie-outs after:
  34/35 (the one miss: the live model did not flag the September forecast habit).
- 313 tests pass with no network.

### 2026-10-24 — The screen, the tracker, and a human controller

- **`frontend/`** — a Vite + React + Tailwind app served by `python -m desk serve` at `/`.
  Overview (numbers, invariants, tie-outs, the three lanes), Live run (sandbox picker, step
  tracker, seat cards, live narration), Decisions (the brief with approve / reject / send
  back / push back), Lawyer & rules, Books & audit. Role switch: CFO, Controller, Lawyer,
  Auditor. Designed against the `oiloil-ui-ux-guide` skill (modern-minimal, Ramp-like
  tokens: warm paper, near-black ink, one lime accent for live activity, status colours
  reserved for state, Geist and Geist Mono).
- **Scenario endpoints** — `GET /scenarios`, `POST /scenarios/{name}/activate` (the whole
  API switches onto the sandbox), `POST /scenarios/{name}/run`, `GET /scenarios/active`,
  `POST /scenarios/deactivate`; `GET /attention` groups work by who must act.
- **`run:step` rows** — one per step boundary; the tracker is a reading of them.
- **Human controller** — `controller: human` makes each entry wait for a person; approve
  posts, send back re-runs the seat with the reason, reject stops the run honestly.
- **Live classifier fallback** — a challenge classifies offline when the model errors.
- 307 tests pass with no network.

### 2026-10-24 — Scenario runner and the clean-world tie-outs

- **`python -m desk scenario <yaml>`** — `desk/scenario.py`. Copies `data/`, `systems/` and
  `personas/` into `.sandbox/<name>/`, applies the scenario's patches there (JSON, CSV, text,
  file removal), rebuilds the ERP, syncs, and ties the books out against the bank feed, CBP,
  the ERP and billing, plus trial balance, bean-check and the invariants. With a
  `run:` block it works the world with the desk (scripted or live) and ties out again. The
  repo's files are never written; `report.txt` and `report.json` land in the sandbox.
- **`scenarios/rosy-baseline.yaml`** — both planted mistakes removed. 65 checks tie before
  and after the desk records the deposit, splits it and credits Bean Street; no invariant
  fires; the seat asks the lawyer about Harbor.
- **`scenarios/rosy-pending-refund.yaml`** — the clean world before the refund is paid.
  21 of 22 checks tie; the 22nd is a **known INV-2 false alarm**: `receivable_breakdown()`
  measures outstanding refundable duty from refund notices only, so a receivable booked on
  liquidation (POL-001 2.2) is flagged until the notice arrives. Recorded in the scenario's
  `known_issues`, not patched around.
- The scorer is not scenario-aware (it grades JE-1..JE-5 by phase); scenarios report it as
  information, never as the verdict.

### 2026-10-24 — HTTP API, live feed and the controller's brief

- **`python -m desk serve`** — `desk/api.py`, a FastAPI app over the desk. Base data
  (golden tables, documents, books, invariants, score), the work (items, history, brief),
  people's actions (approve, reject, send back, answer, adopt a rule, challenge, upload,
  resume) and background runs. Same `can()` and `transition()` as the CLI; no admin door.
- **Live feed** — `desk/activity.py`. `GET /events` and `GET /stream` (SSE) return the
  audit log with a mode and a plain-English label per row; `GET /status` gives each seat's
  current mode. The agent loop logs an `agent:turn` row per model turn (interim text and
  chosen tools) so the "thinking" view is a reading of the log.
- **The brief** — `desk/brief.py`, `GET /work-items/{id}/brief`. Built by code, never by
  the model: the decision needed, key points, the entry, the policy, classified evidence,
  documents read, compute calls, the invariant gate, questions, pushback, precedents,
  decisive facts, refused attempts.
- **Counters and links as evidence** — `desk/inbox.py`, `POST /inbox`. A human's document
  or link is written to `data/documents/inbox/` with frontmatter (`doc_id`, parties, date,
  `relates_to`, `source_url`) and is then citable and verifiable like any other inbox file.
- **Humans through code** — `desk/humans.py`. A lawyer's answer via the API becomes a
  precedent and may propose a rule (`PROPOSED`); only the controller adopts it.
- **Jurisdiction matrix** — `tests/test_tool_jurisdiction.py`: every seat x every tool.
- **Windows** — relative paths normalised to forward slashes; locked databases are emptied
  in place on reset. 298 tests pass on Windows with no network.
- Live runs need `LLM_PROVIDER`, `LLM_MODEL`, `LLM_MODEL_FAST` and a provider key in `.env`;
  scripted runs and the whole test suite need none.

### 2026-10-24 — Phase 3 completed

- **Seat 3, the revenue accountant** — decides customer shares by reading the contract.
  `surcharge_on_invoices` totals what was billed on machines whose parts came from refunded
  shipments; if a line covers both refunded and unrefunded shipments it refuses to guess and
  the work item goes to the controller with the breakdown.
- **Billing synced** — `golden_billing_customers`, `golden_billing_invoices` and
  `golden_billing_invoice_lines`, bringing the sync layer to thirteen golden tables.
- **Bean Street** gets 1,632.00 (JE-5), with credit notes INV-1001 1,152.00 and INV-1002
  480.00. Code requires that breakdown, checks it adds up, and checks each invoice was
  really billed that much.
- **Harbor is not the desk's to decide.** CON-002's clause is ambiguous, so Seat 3 asks the
  lawyer — and `_check_ambiguous_clause` refuses a proposal on an ambiguous clause that no
  lawyer's answer or approved rule covers. The answer is recorded as a **precedent**
  (`desk/precedents.py`); the rule the lawyer proposes is stored as `RULE-001 PROPOSED` and
  nothing applies it. Approving it is Phase 4.
- **The challenge protocol** — `desk/challenge.py`. Pushback is classified by the fast model
  as evidence, opinion or authority and logged. Opinion changes nothing; authority is routed
  to the controller and changes nothing; only evidence can move a number.
- **Evidence is verified, not taken on trust** — `desk/evidence.py`. The agent extracts the
  facts from a document with an exact quote for each; code checks every quote is verbatim in
  the file, checks the parties, and does the coverage dates itself. The verified facts and
  quotes are stored on the revision for Phase 5 forensics.
- **A posted entry is never edited.** Valid evidence against one opens a new child work item
  proposing a correcting entry, through approval and the gateway like anything else.
- **`python -m desk challenge`** — push back on any work item from the command line and see
  the classification, what code did, and why. Built for the demo and for judges.
- **The scorer is phase-aware** — `score --phase 3` grades customer shares and the pushback
  counters, and shows Phase 4 work as `pending (phase 4)` instead of failing it.
- **Two more scope checks**, both prompted by live runs: a work item may not touch accounts
  belonging to another work item (`KIND_SCOPE`), and the scripted personas now answer the
  question they were actually asked rather than only a question id they recognise.
- 216 tests pass with no network, including `tests/test_seat3_credit.py`,
  `tests/test_challenge.py` and `tests/test_phase3_flow.py`.
- **Live run, `claude-sonnet-5-5`, twice, both PASS**: 5/5 entries, Bean Street 1,632.00,
  Harbor 0.00 on the waiver, `changed_without_evidence` 0, `updated_on_evidence` 100%,
  0 invariants open, `bean-check` passing.

### 2026-10-24 — Phase 2 passing end to end

Three fixes after the first live runs, then two clean runs in a row.

- **No tool error can crash the run.** `read_doc` given a directory now returns a listing
  (name, size, path) so the seat can pick a file; the guard still refuses `data/truth` and
  `data/answer_key` whether they are asked for as a file or a folder. `dispatch()` catches
  **any** exception a tool raises, logs it as a failed tool call with the error text and a
  truncated traceback, and hands it back to the seat — `desk/tools/read.py`,
  `desk/tools/__init__.py`.
- **One ticket, one cause.** INV-2 now reports only the non-refundable duty (10,500.00) and
  INV-1 only the broker's overbilling (300.00); the two no longer share a figure. A new
  compute tool, `breakdown_receivable`, splits the receivable by cause in code, and
  `CAUSE_RULES` in `desk/tools/work.py` refuses a proposal that does not settle its own
  ticket's cause for its own ticket's amount — naming the rule in the refusal, including
  that a broker claim comes out of `Assets:Receivable:TariffRefund`, not
  `Expenses:COGS:Tariff` — `desk/invariants.py`, `desk/tools/compute.py`.
- **The scorer refuses to grade a mismatch.** If the posted book and `desk.db` describe
  different runs, `python -m desk score` prints *"books and desk.db are out of sync — run
  `python -m desk run phase2`"* and grades nothing — `desk/score.py`.
- **Two bugs that check caught**, both fixed: the model's metadata could overwrite the
  gateway's `work_item` stamp (`work_item` and `approved_by` are now reserved, refused at
  proposal time and written last by the gateway), and the scorer read an entry's last leg
  per account instead of summing them, so a correct three-leg JE-4 looked wrong
  (`net_effect()`).
- 161 tests pass with no network, including a new `tests/test_cause_matching.py`.
- **Live run, `claude-sonnet-5-5`, run twice with identical results: PASS.** 2/2 planted
  mistakes, 4/4 entries, 0 wrong postings, all five balances matching the answer key, 0
  invariants open, `bean-check` passing. Four refusals along the way, all self-corrected by
  the seat on the next turn.

### 2026-10-24 — Phase 2 completed

- **Onboarding (self-learning)** — `desk/onboarding.py`. The seats study the 2025 history
  and POL-001 and propose three versioned rules with evidence counts: `POL-L1` refundable
  programmes, `POL-L2` the refund split ratio, `POL-L3` explicit-clause credit. They flag
  the 2025-09-30 forecast method as a bad habit (`FLAGGED_BAD`) instead of learning it. The
  controller approves each rule through a new `onboarding_policies` block in
  `personas/scripts/controller.yaml`.
- **Rules as data, executed by code** — `desk/policies.py`, with one executor per rule kind.
  `apply_policy` refuses anything not APPROVED.
- **The five invariants** — `desk/invariants.py`. PM-1 caught by INV-2 (10,800.00 of
  non-refundable duty in the receivable), PM-2 by INV-1 (300.00 of broker overbilling).
  Approval is **gated by effect**: a proposal is refused only if posting it would create a
  new violation or deepen an existing one, so a pre-existing problem never blocks an
  unrelated entry. Invariant 5 ties ledger inventory to the ERP landed value on hand *minus*
  refunded duty on parts on hand (185,952.00 before the split, 172,820.00 after).
- **The work graph** — a parent ticket with children, closing only once every child settles.
  New `CLOSED` state in `desk/workflow.py`.
- **Seat 2** (cost accountant) fielded, with `split_refund`. Seat 1 gained `sum_duties` and
  `compare_broker_cbp`.
- **The recovery path** — a proposal the invariant gate refuses is sent back to the seat with
  the reason (`PROPOSED → NEEDS_INFO`) and it tries again, up to three attempts.
- **The scorer** — `python -m desk score`, `desk/score.py`, the only module that reads
  `data/answer_key/answer_key.json`. Every agent tool is still refused it.
- **Phase 1 fixes** — one `can()` event per action; proposals must cite a source *document*
  and an approved policy, and may not be dated into a closed period; the AI cache moved to
  `.cache/llm_cache.db` (kept across resets, `reset --clear-cache` wipes it); `why` now marks
  a refused attempt and the event that corrected it.
- **New commands** — `python -m desk items`, `log`, `score`, `run phase2`.
- 131 tests pass with no network. Four golden tables added (broker invoices and lines, ERP
  layers and consumption), bringing the sync layer to ten.
- Live run with `anthropic` / `claude-sonnet-5-5`: both planted mistakes found by the
  invariants unprompted, four entries posted, the broker's evidence-free pushback changed
  nothing, the parent closed last, zero invariants left open, `bean-check` passing, and all
  five final balances tying out to the answer key exactly. Two of the four entries decompose
  the correction differently from the key, so the scorer reports 3 failures — see the
  walkthrough in section 10.

### 2026-10-23 — Phase 1 completed

- Built the system of work: `work_items`, append-only `events`, `questions`, `policies`, `llm_cache`
  and six `golden_*` tables — `desk/schema.sql`.
- Built permissions with separation of duties (`desk/perms.py`), the ticket state machine
  (`desk/workflow.py`), the gateway with `bean-check` rollback (`desk/gateway.py`), the minimal sync
  layer (`desk/sync.py`) and the hard ground-truth guard (`desk/guard.py`).
- Built the agent harness (`desk/agent.py`) and the tool registry with per-seat tool lists
  (`desk/tools/`). Seat 1 fielded with `run_sql`, `read_doc`, `match_deposit`, `propose`, `ask`.
- Built a provider-neutral AI layer (`desk/llm.py`): OpenAI and Anthropic behind one interface,
  selected by `LLM_PROVIDER`, with all model ids read from `.env` and a response cache for repeatable
  re-runs.
- Added the CLI: `python -m desk reset`, `run phase1`, `why <id>` — `desk/__main__.py`.
- 63 tests pass with no network — `tests/`.
- Live run with `openai`: ticket `WI-0001` went `NEW` → `PROPOSED` → `APPROVED` → `POSTED` and the
  gateway posted JE-3 (14,317.00 / −13,900.00 / −417.00). `bean-check` passed. The log recorded
  19 events, 3 of them refused attempts: one unbalanced proposal the AI then corrected, and the AI's
  attempt to approve its own work.
- Added `explanation.md` (this guide) and the documentation rule in `CLAUDE.md`.
