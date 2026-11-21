# Handover: Brewline Tariff Desk (Cal Hacks 13.0, Hyphenate track)

> **For Claude Code.** This file is the full project context. Read it before making changes. If you save it as `CLAUDE.md` in the repo root, Claude Code loads it automatically.
>
> **Hackathon rule:** all project code must be written during the event (Oct 23–25, 2026). Data files, documents and personas were prepared beforehand.
>
> **Current state:** read `HANDOVER.md` first (what exists, what is verified, what is missing, the conventions). `docs/PLAYBOOK.md` is the human tester's guide to every use case; `explanation.md` is the long guide with the changelog.

---

## 1. The project in one paragraph

We are building the **Tariff Desk**: an autonomous *system of work* for the finance team of a fictional manufacturer, **Brewline Equipment Co.**, which imports espresso-machine parts and sells machines to coffee chains and distributors. Tariff rules keep changing: some 2025 tariffs (IEEPA) were struck down and are being refunded, some (Section 232 steel, Section 301 China) are never refunded, and new ones keep arriving. Each change creates the same chain of finance work: classify duties, put the cost in the right place, decide what customers are owed, watch deadlines, match and unwind refunds, and close the books with every number agreeing. The desk fills **three accounting seats**. It picks up work from events, proposes answers, asks humans when judgment is needed, and writes to the books **only through a gateway** after approval. Every step is logged; it can show **which facts decided** each call; it **holds firm under pushback** unless given evidence; and it **learns**, turning human decisions into approved, reusable rules.

**Seat line (for the submission):** "Brewline's Tariff Desk fills three seats (trade-compliance, cost and revenue accountant), handles every tariff change from import to close, learns from every decision, and posts nothing without the controller."

---

## 2. Hackathon context: what wins

**Track:** "The Hyphenated Enterprise: Autonomous Systems for Finance" (Hyphenate). Prizes $8k / $5k / $3k; top 5 teams get expedited interviews.

**Judging questions:**
1. **Is it real enough?** Realistic data, a messy problem, proof that it works.
2. **Would you hire it?** Named seats; limited access; a clean record of its work; knowing when to ask.
3. **Did we see something new?**
4. **The "wow" moment:** catches what others missed, recovers, defends a decision when questioned.

**Required deliverables:** demo video opening with the moment; live demo (judges may bring their own scenario); one line on the seats; proof: what's real, what's simulated, how we know.

**Hyphenate's own checklist:**

| Strong | Weak (avoid) |
|---|---|
| A real company with a messy corner, and an answer key | A chatbot over a spreadsheet, or a one-shot extractor |
| A few roles done deep, named in one line | Ten workflows, none deep |
| Asks at the right moment, and handles a no | Clean happy-path data that only works on the script |
| Limits enforced in code; a record of every call | An admin key; "the prompt tells it not to" |
| A moment on stage, and a score to back it | Memory that ends with the context window; "trust me" |

**Hyphenate's architecture (from their workshop slides; follow it):**
- **Systems of record** (bank, ERP, ledger, contracts, CRM, inbox) hold the truth about the business. We integrate with them; we don't replace them.
- **System of work** (what we build): work items, decisions and reasons, questions and approvals, learned policies, the log. Agents and people share it.
- **Gateway:** one door; every write is checked; only the gateway posts to systems of record.
- **Build steps:** (1) sync records into one shape (raw → standardized → golden, each golden row traceable to its raw row, stable IDs, change-only sync with a cursor per source); (2) map actors × actions × resources with a `can()` check, including separation of duties; (3) the agent's tools are CRUD on the work, and every change goes through `transition()`; (4) log every transition as a diff, append-only, in the same commit as the change, including denied attempts.

---

## 3. Core concepts

| Term | Meaning here |
|---|---|
| **Seat** | A Claude tool-use agent (model + tools + loop + context) with role-scoped tools; code enforces its limits |
| **Work item** | One unit of work with a state, an owner seat, evidence and a parent/children (the work graph) |
| **Gateway** | The only component that writes to Beancount (journal entries) or Stripe (credit notes), and only for APPROVED items |
| **Policy / rule** | A versioned, executable rule (e.g. RULE-001). Proposed by a seat agent (from history or a precedent) or by a human (e.g. the lawyer); approved by the controller (and the lawyer for contract rules); applied by agents only through `apply_policy` |
| **Precedent** | A past human decision the desk can reuse in matching cases |
| **Invariant** | A rule that must always hold; a violation opens a work item |
| **Clock** | A timer on an entry or question (deadline, response SLA) that triggers alerts or escalation |
| **Decision forensics** | For any decision, remove one fact at a time and re-run the decider to find which facts were decisive |
| **Challenge protocol** | Pushback is classified as evidence / opinion / authority; only evidence (an uploaded document with matching parties and dates) can change a decision |

---

## 4. The company and scenario (tiny world v1)

**Brewline Equipment Co.** (importer no. 12-3456789AB), banks with Increase, and pays its import duty directly to CBP.

- **Vendors:** Shenzhen Boardworks (CN, boards), Ningbo Steelform (CN, frames), Milano Grind (IT, burrs).
- **Parts:** PRT-BOARD $200, PRT-FRAME $300, PRT-BURR $80.
- **Machines:** Espresso X1 ($4,800, BOM: 2 boards + 1 frame + 1 burr, 12% tariff surcharge); Grinder Pro ($1,250, BOM: 1 board + 1 burr, 9.6% surcharge).
- **10 shipments** (SHP-001…SHP-010), each a CBP entry. Duties by program:
  - Boards (CN): IEEPA_CN 20% + S301_CN 25%
  - Frames (CN): IEEPA_CN 20% + S232_STEEL 50%
  - Burrs (IT): IEEPA_EU 15%
- **Total duties per CBP:** 59,790, and the books agree: Brewline pays CBP directly, so duty paid equals the 7501 line for line.
- **4 sales invoices:** INV-1001 and INV-1002 (Bean Street), INV-1003 (Harbor Roasters), INV-1004 (Northwind). Each has a separately stated tariff surcharge line. All parts consumed came from SHP-001/002/003 (FIFO).
- **Customers and contracts:**
  - CON-001 Bean Street: **explicit**. Clause 7.2 credits the full Tariff Surcharge on affected machines when duties are refunded; interest is retained by Brewline.
  - CON-002 Harbor, CON-003 Northwind, CON-004 Cascade: **distributor template, ambiguous** clause 7.2 ("treat Distributor equitably and consistently…").
- **Refund RFD-001:** CBP notice 2025-10-14; deposit 2025-10-15 of **14,317.00** = principal 13,900 (IEEPA only, entries 1–6) + interest 417. Entries 7–10 are unliquidated.
- **Bad forecast (PM-1):** on 2025-09-30 the controller booked a 24,400 refund receivable that wrongly includes Section 232 steel (10,500), which is never refunded. The correct figure is 13,900. This is the one planted mistake.
- **Policy POL-001** (`data/reference/refund_policy.md`): duties expensed to `Expenses:COGS:Tariff` until 2025-09-30; from 2025-10-01 duties on parts on hand belong in `Assets:Inventory:Parts`. CBP 7501 is the source of truth for duty amounts. Refund split = principal × (qty consumed / qty received) per entry. Interest goes to `Income:Interest:TariffRefund` and is never passed to customers.

---

## 5. Seats and people

| Seat | Owns | Read tools | Compute tools | Work tools |
|---|---|---|---|---|
| **1. Trade-compliance accountant** | Classify duties by program (via `hts_programs.csv`); reconcile duty paid vs the CBP 7501; correct the refund forecast; match deposits to entries; split principal and interest; track entry clocks | `run_sql` over golden tables (ERP, books, customs), `read_doc`, `search_precedents`, `get_policies` | `sum_duties`, `match_deposit`, `reconcile_duty_paid`, `breakdown_receivable` | `propose`, `ask`, `propose_policy`, `apply_policy` |
| **2. Cost accountant** | Split each entry's refund between parts consumed (COGS) and on hand (inventory) using ERP layers; draft journal entries | `run_sql` over golden tables (ERP, books, customs), `read_doc`, `search_precedents`, `get_policies` | `split_refund` | `propose`, `ask`, `propose_policy`, `apply_policy`, `revise_proposal` |
| **3. Revenue accountant** | Decide customer shares per contract; draft credit notes; route ambiguous clauses to the lawyer; apply approved rules | `run_sql` over golden tables (ERP, books, customs), `read_doc`, `search_precedents`, `get_policies` | `surcharge_on_invoices` | `propose`, `ask`, `propose_policy`, `apply_policy`, `revise_proposal` |

**No agent has `post` or `approve` tools.**

**People**: two on screen. The **CFO** approves, declines, sends back, adopts rules and may push back; the **Lawyer** answers contract questions, proposes rules and may ask why. Simulated stand-ins (see `personas/`): controller (the scripted CFO), lawyer, CFO (authority pushback lines), auditor. There is no customs broker: Brewline pays CBP directly. Each has a role card (`*.md`) and a scripted answer file (`scripts/*.yaml`). **Personas (the simulated humans) run in scripted mode for scoring and repeatable runs; live LLM personas are used only in the demo. Seat agents always use the LLM in both modes.**

**Seats do not talk to each other.** They coordinate through the work graph: finishing one work item creates the next.

---

## 6. Features (MVP)

### 6.1 Work items and states

States: `NEW → PROPOSED → (NEEDS_INFO ⇄ PROPOSED) → APPROVED → POSTED`, plus `REJECTED`. Nothing is deleted; a reversal is a new transition.

Work item kinds (as used in `personas/scripts/controller.yaml`):
`WI-FORECAST-CORRECTION`, `WI-DUTY-RECONCILIATION`, `WI-REFUND-DEPOSIT`, `WI-REFUND-SPLIT`, `WI-CUST-001-CREDIT`, `WI-CUST-002-QUESTION`, `WI-CUST-002-CLOSE`, `WI-RULE-001`, `WI-CUST-003-CREDIT`.

### 6.2 Roles and permissions (enforced in code)

| Actor | May do |
|---|---|
| `sync` | create work items |
| `ai_agent` (any seat) | propose, ask on work items; propose policies |
| `human_cfo` | approve / reject / send back work items, adopt policies, answer, push back |
| `human_lawyer` | answer questions; propose contract rules; push back |
| `human_controller` | the scripted stand-in for the CFO in recorded runs (same powers) |
| `standing_authorization` | approve only, and only a routine entry inside the CFO's adopted caps and conditions (POL-A1, `desk/authorization.py`) |
| `gateway` | post (the only actor that writes to Beancount or Stripe) |

**Separation of duties:** an actor can never approve an item it proposed. No role except the gateway can post. Every `can()` check, allowed or denied, is logged. **The CFO is asked only when the desk is stuck:** an ambiguous clause, a document that changes a decision, pushback, a send-back, or an amount above the standing authorization's cap; the reason is logged and shown.

### 6.3 Gateway

Writes only APPROVED items: journal entries appended to `systems/books/posted/desk.beancount` (each with `work_item` and `approved_by` metadata), and credit notes in Stripe. After every write, run `bean-check`.

### 6.4 Audit log

Append-only events table: `at, actor, action, subject, allowed, reason, diff (before/after), evidence (source row IDs), policy_version`. Written in the same DB transaction as the change. "Why?" must be answerable with one query.

### 6.5 Sync layer

Read each system of record (Mockoon customs API, Increase, Stripe, Beancount, ERP SQLite, document folders) into raw → standardized → golden tables with stable IDs and `raw_ref`. Entry number is the crosswalk key between customs and ERP.

### 6.6 Invariants (each violation opens a work item)

1. Duties per entry: CBP lines sum per program; duty paid per the books must equal the CBP 7501.
2. Refund forecast includes only programs with `refundable = yes` (catches PM-1).
3. Refund received ≤ refundable duty on the refunded entries; deposit = notice total.
4. Refund split adds up: `to_cogs + to_inventory = principal` per entry and in total.
5. Ledger inventory ties to the ERP landed value on hand (see 10.3).

### 6.7 Clocks

Per-entry liquidation status and dates; per-question response timers; escalate to the next approver when a timer expires. Simulated time is fine.

### 6.8 Challenge protocol

Classify every pushback as evidence / opinion / authority. **Agents may discuss pushback, but `revise_proposal` requires a valid inbox document id with matching parties and dates; without one, code refuses the change and logs the refused attempt.** Every pushback, including a plain question, gets an answer from the item's seat (read and compute tools only; stored on the challenge and logged as `challenge_reply`); code answers from the brief when no model is available. Track two counters: changed without evidence (target 0) and correctly updated on evidence (target 100%).

### 6.9 Decision forensics

Approved policies are executable and deterministic, so forensics re-runs the policy with each fact removed to find the decisive facts and the minimal sufficient set. Decisions the agent reasoned without a policy show their cited evidence and the policy they propose. Expected for Bean Street: CON-001 clause 7.2, and the surcharge lines on INV-1001/1002 tied to refunded shipments.

### 6.10 Learning

- **Self-learning at onboarding (MVP):** the agent studies 2025 history and POL-001 and proposes three policies with evidence counts: refundable programs (IEEPA yes; 232/301/122 no), refund split by consumed/received per shipment, and full-surcharge credit for explicit-clause contracts. It flags the 2025-09-30 forecast method as a bad habit instead of learning it. The controller approves each policy.
- **Self-improving:** the lawyer's answer on CON-002 becomes RULE-001. The desk finds matching contracts (CON-003, CON-004), proposes the rule, the controller approves, and the rule is applied. Pass if CON-003 is handled **without asking anyone**.
- **Guardrails:** rules never self-approve; rules are versioned; dry-run before activation; regression check against the answer key; rules carry their scope.

### 6.11 Scorecard

Run the desk, then compare against `data/answer_key/answer_key.json` (only the scorer reads it). Report planted mistakes caught, decisions matching the key, wrong postings (target 0), unresolved invariant violations (target 0), pushback flips without evidence (target 0), and questions to humans before vs after learning.

### 6.12 Minimal UI

Work queue; item detail (proposal, evidence, decisive facts); controller chat with pushback counters; audit log view; scorecard. Built in `frontend/` (Vite + React + Tailwind, served by `python -m desk serve` at `/`). **The screen tells one story: an event arrives, the agents work it in the background under the checks code enforces, the desk operator only reacts.** Home is "Needs you" (the person's queue as plain cards: title, summary with the figures, amount, "if you approve", Approve / Decline); Activity is an issue tracker per event plus the three agents by role name; Decisions, Lawyer and Books follow. **Plain language everywhere people read:** no work-item ids, policy refs, seat numbers, file paths or tool names in the copy; agents are named by role (Trade compliance, Cost accounting, Revenue accounting), accounts by name, documents by title; codes and terms sit behind hover tips and a collapsed "Details" section. Scripted replays are a development aid under Developer tools, not the demo path. The backend hooks are in `desk/api.py`: `GET /status`, `GET /events` and `GET /stream` (each row with a `plain` reading), `GET /issues`, `GET /attention`, `GET /work-items/{id}/brief` (with `plain` from `desk/plain.py`), `POST /simulate/refund-arrives`, and the approve / reject / send-back / challenge / answer / inbox / resume endpoints.

---

## 7. Hard rules for implementation

1. **The desk must never read `data/truth/` or `data/answer_key/`.** They're ground truth and the exam answers. Only the scorer and data-generation scripts may read them.
2. **Agents do the work; code sets the limits.** Business rules are learned by agents as versioned policies, approved by humans, and applied via `apply_policy`. Code enforces `can()`, `transition()`, the gateway (the only actor that posts), append-only logging, evidence-required revisions, invariant checks before approval, and exact money math via compute tools. The LLM never does arithmetic and has no `post` or `approve` tools.
3. **Only the gateway writes** to the books and Stripe, and only for APPROVED items.
4. **Append-only** log and books; corrections are new entries.
5. **Every number in a proposal cites a source** (document ID, entry number, invoice ID, ledger line).
6. **Secrets live in `.env`** (gitignored). Use Stripe **test** keys and Increase **sandbox** keys only.
7. **Repeatable runs:** scripted personas plus a reset command that rebuilds the ERP DB, resets `posted/desk.beancount`, and clears the desk DB. Alternative worlds (clean or messy) are YAML scenarios run in `.sandbox/` by `python -m desk scenario`, never edits to the base data.
8. Keep `bean-check systems/books/main.beancount` passing at all times.

---

## 7a. Documentation rule (explanation.md)

`explanation.md` is the owner's plain-English guide. At the end of every phase, and after any change
that adds or renames files, tables, tools, agents or commands, update `explanation.md` before
reporting done: update the phase index status, fill the finished phase's section (including a
walkthrough of the latest real run), update the repo map, data guide, tools guide, pipeline and
cheat sheet, and add a changelog entry. Keep it simple and use real paths. **A phase is not done
until `explanation.md` is updated.**

---

## 8. Repository and data map

```
data/
  truth/            ground truth (DESK MUST NOT READ): world.json, shipments, duty_lines, invoices,
                    consumption, refunds, refund_entries, deposits, customers, contracts, …
  answer_key/       answer_key.json (DESK MUST NOT READ; scorer only)
  reference/        hts_programs.csv (programs, Chapter 99 codes, refundable flag), refund_policy.md (POL-001)
  documents/
    contracts/      CON-001-bean-street.md, CON-002-harbor.md, CON-003-northwind.md, CON-004-cascade.md
    inbox/          CBP refund notice, bank memo, CFO email (authority), Harbor waiver (evidence)
  views/            per-system views derived from truth, used to seed the systems:
    bank/           transactions.csv, increase_crosswalk.json (Increase sandbox IDs)
    billing/        customers.json, invoices.json, products.json, stripe_crosswalk.json (Stripe IDs)
    customs/        cbp_entries.json, cbp_refunds.json
personas/           role cards (controller, lawyer, cfo, auditor) + scripts/*.yaml (scripted answers)
systems/
  books/            main.beancount → accounts.beancount, history/2025.beancount, posted/desk.beancount (gateway only)
  erp/              schema.sql, seed.sql (SQLite mini ERP: POs, shipments, inventory_layers, BOM, sales, consumption)
  mockoon/          customs.mockoon.json (port 3001; CRUD routes /cbp/entries, /cbp/refunds; key entry_no)
```

---

## 9. External systems and setup

| System | Role | Notes |
|---|---|---|
| **Increase sandbox** | Bank | Account and account-number IDs in `increase_crosswalk.json`. The demo trigger is a **simulated inbound ACH** (CBP refund) |
| **Stripe sandbox** | Billing | Customers, products, invoices with surcharge lines; credit notes issued by the gateway. IDs in `stripe_crosswalk.json` |
| **Beancount + Fava** | Ledger | `bean-check` to validate; `fava systems/books/main.beancount` to view |
| **Mockoon CLI** | Customs API | `mockoon-cli start --data systems/mockoon/customs.mockoon.json` → http://localhost:3001 |
| **SQLite** | Mini ERP and desk DB | ERP rebuilt from `schema.sql` + `seed.sql` |
| **Creao agents** | The three Brewline agents (Tariffs & Refunds, Sourcing, Stock & Customers) are the seats' brains when `LLM_PROVIDER=creao` | `desk/creao.py`; app-scoped `capi_` keys in `.env`; `brewline_agents_integration.md` |
| **Claude API** | The small classification calls (pushback), and the desk's own model-backed seats as a fallback (`LLM_PROVIDER=anthropic`) | Model name from env |
| **Desk API** | Frontend hooks and scripted/live runs | `python -m desk serve` -> http://127.0.0.1:8000, docs at `/docs`, SSE at `/stream` |
| **Scenario runner** | Alternative worlds in a sandbox, tied out across every system of record | `python -m desk scenario scenarios/<name>.yaml [--llm live]`; see `scenarios/README.md`. Repo data is never written |
| **The screen** | `frontend/` (Vite + React + Tailwind), served at `/` by `python -m desk serve` after `npm run build` | Role switch CFO / Controller / Lawyer / Auditor; home is the person's queue, Activity the issue tracker per event; `POST /simulate/refund-arrives` is the demo trigger (live agents, human controller); the tracker, agent cards and narration are read off the audit log |

**`.env` keys (never commit):** `STRIPE_SECRET_KEY` (sk_test_…), `INCREASE_API_KEY`, `INCREASE_BASE_URL` (sandbox), `ANTHROPIC_API_KEY`, `LLM_MODEL`, `MOCKOON_URL=http://localhost:3001`, `BEANCOUNT_FILE=systems/books/main.beancount`, `ERP_DB`, `DESK_DB`.

**Suggested stack:** Python 3.11+, FastAPI, SQLite, `beancount`, the `stripe` and `increase` Python SDKs, the Anthropic SDK, Streamlit for the MVP UI.

---

## 10. Expected results (from the answer key)

### 10.1 Journal entries the desk should propose (gateway posts after approval)

| ID | Purpose | Postings |
|---|---|---|
| JE-1 | Remove Section 232 from the forecast (PM-1) | Dr Expenses:COGS:Tariff 10,500 / Cr Assets:Receivable:TariffRefund 10,500 |
| JE-2 | Record deposit RFD-001 | Dr Assets:Bank:Increase:Operating 14,317 / Cr Assets:Receivable:TariffRefund 13,900 / Cr Income:Interest:TariffRefund 417 |
| JE-3 | Split refund: on-hand portion out of inventory | Dr Expenses:COGS:Tariff 13,132 / Cr Assets:Inventory:Parts 13,132 |
| JE-4 | Bean Street share (CON-001 §7.2) | Dr Income:Sales:TariffSurcharge 1,632 / Cr Liabilities:CustomerCredits 1,632; credit notes on INV-1001 (1,152) and INV-1002 (480) |
| JE-5 | Northwind share after RULE-001 | Dr Income:Sales:TariffSurcharge 240 / Cr Liabilities:CustomerCredits 240; credit note on INV-1004 |

The answer key records each entry's former id in `was`: the old JE-2 was a broker claim and
is gone, so JE-3…JE-6 became JE-2…JE-5.

**Why JE-3 works:** the 2025-09-30 accrual credited the whole expected refund to `COGS:Tariff`. On 2025-10-01 duties on parts on hand were capitalized into inventory (see 12.1). So the refund portion for parts still on hand must move from COGS to inventory.

### 10.2 Refund split by shipment

| Shipment | Principal | Consumed / received | To COGS | To inventory |
|---|---|---|---|---|
| SHP-001 | 4,000 | 12 / 100 | 480 | 3,520 |
| SHP-002 | 2,400 | 3 / 40 | 180 | 2,220 |
| SHP-003 | 1,200 | 9 / 100 | 108 | 1,092 |
| SHP-004 | 3,000 | 0 / 75 | 0 | 3,000 |
| SHP-005 | 1,800 | 0 / 30 | 0 | 1,800 |
| SHP-006 | 1,500 | 0 / 125 | 0 | 1,500 |
| **Total** | **13,900** | | **768** | **13,132** |

### 10.3 Final balances after JE-1…JE-5

- `Expenses:COGS:Tariff` = **1,050**: duty on parts sold (1,818) minus refund on parts sold (768).
- `Assets:Inventory:Parts` = **172,820**: purchase cost on hand (127,980) + duty on hand (57,972) − refunded duty on hand (13,132).
- `Assets:Receivable:TariffRefund` = 0; `Liabilities:CustomerCredits` = 1,872 (credit balance).

### 10.4 Customers, pushback and learning

| Customer | Outcome |
|---|---|
| CUST-001 Bean Street | 1,632 credit (explicit clause) |
| CUST-002 Harbor | Ask the lawyer → RULE-001 created → then the June 30 waiver makes INV-1003 credit **0.00** |
| CUST-003 Northwind | 240 credit after RULE-001, **without a question** |
| CUST-004 Cascade | Rule applies; no 2025 invoices → 0 |

**Pushback:** CFO email = authority (no change); an argument with no document = opinion (no change); Harbor waiver = evidence (changes CUST-002 to 0.00).

---

## 11. MVP build phases

| Phase | Goal | Pass if |
|---|---|---|
| 0 | (Pre-event) AI parts tested by hand; tools set up | Contracts read correctly; pushback classified correctly |
| 1 | System-of-work skeleton + agent harness (tool-use loop, tool registry with per-seat permissions) + gateway + log | Deposit → work item → propose → controller approves → gateway posts → log shows every step; the AI's self-approval is refused and logged |
| 2 | Seat 1 and Seat 2 agents with read/compute/work tools + invariants + onboarding policies approved | Matches answer key exactly; PM-1 caught |
| 3 | Seat 3 agent (`read_doc` on contracts, `surcharge_on_invoices`, `propose`/`ask`) + challenge protocol with evidence-required `revise_proposal` | Contracts read correctly; zero flips on opinion/authority; waiver changes Harbor to 0 |
| 4 | Learning loop (RULE-001) | CON-003 handled without asking |
| 5 | Forensics + audit query | Bean Street's two decisive facts identified; "why?" in one query |
| 6 | Real systems: 6a Increase (live inbound ACH triggers the desk) → 6b Beancount/Fava → 6c Stripe credit notes → 6d Mockoon | One refund end to end across all four, all logged |

**Go / no-go:** if contract reading is unreliable, the LLM suggests facts and a human confirms. If pushback classification is shaky, only uploaded documents with matching parties and dates count as evidence. If time is short, cut Seat 3 to Bean Street only. Never cut the gateway, log, refund moment, forensics or scorecard.

---

## 12. Known data issues

### 12.1 Being fixed now (see the change request)

- Capitalize duties on parts on hand on 2025-10-01 (57,972); add `duty_per_unit` to ERP `inventory_layers`; update answer-key tie-outs and add JE-6.
- Align CON-003's clause 7.2 wording with the distributor template so RULE-001's scope matches.
- Make the learning-loop sequence explicit: the lawyer answers Harbor's share question first (creating RULE-001), then the waiver zeroes INV-1003.
- Fix `truth/contracts.csv` path for CON-001; use "Brewline Equipment Co." consistently.

**Done:** (2026-10-23, data fixes only; no desk code written yet)
- `systems/books/history/2025.beancount` — added one 2025-10-01 transaction "Capitalize duties on parts on hand at policy adoption (POL-001 §1.1)" (`source: controller`, `policy: POL-001`): ten `Assets:Inventory:Parts` debits, one per shipment with `shipment` metadata, against a single `Expenses:COGS:Tariff` credit of 57,972.00. Per-unit duty is CBP-7501-based (boards 90.00, frames 210.00, burrs 12.00), so SHP-004 uses 90.00. 2025 history above it is untouched.
- `systems/erp/schema.sql` — `inventory_layers.duty_per_unit REAL NOT NULL DEFAULT 0`.
- `systems/erp/seed.sql` — `duty_per_unit` populated on all ten layers (90 / 210 / 12).
- `data/reference/refund_policy.md` — §1.1 records the 2025-10-01 capitalization of 57,972.00.
- `data/truth/world.json` — `company.policy_2025` note matches §1.1.
- `data/answer_key/answer_key.json` — JE-6 added (Northwind 240.00, credit note on INV-1004); JE-5 credit notes made explicit; tie-outs `inventory_parts_2025-10-01_after_reclass` and `erp_on_hand_landed_value` = 185,952.00, plus `final_balances_after_JE-1_to_JE-6`; `learning_loop.sequence` (5 ordered steps) and a full-text `scope_predicate` for RULE-001; `notes` explaining why JE-4 is correct.
- `data/documents/contracts/CON-003-northwind.md`, `CON-004-cascade.md` — clause 7.2's last sentence is now byte-identical to CON-002.
- `personas/scripts/lawyer.yaml` — Q-CON-003-SHARE quotes the new identical wording; RULE-001 scope written out in full.
- `personas/scripts/controller.yaml` — PB-C3 moved to `on: WI-CUST-002-CLOSE` with the new expected_effect; JE-4/JE-6 comments clarified.
- `data/truth/contracts.csv` — CON-001 path → `CON-001-bean-street.md`.
- `.env.example` — rewritten to the §9 key names (was `STRIPE_SEED_API` / `INCREASE_BANK_SEED_API`); `.gitignore` — added bare `*.db`.
- Still open: the bank memo `data/documents/inbox/2025-10-15-bank-memo.md` carries the ACH receiver name as `BREWLINE EQUIPMENT CO` (uppercase, no period). Left as-is deliberately — that is how ACH receiver fields arrive, and it is useful realism for the sync layer to normalize. Everything else in the repo reads "Brewline Equipment Co."

### 12.2 Still pending (fix before Phase 6 / the demo)

1. **Increase:** the CBP refund deposit (BNK-0026, 14,317) already exists in the sandbox. The demo needs it to arrive live. Sandbox transactions can't be deleted, so seed history into a fresh account and trigger the refund during the run.
2. **Stripe:** a leftover credit note (WI-0001, 1,152 on INV-1001) exists from testing. Void it or reseed, so the desk creates the credit notes itself.
3. **Timeline:** dates predate the real IEEPA tariffs (Feb 2025 China, Apr 2025 EU) and the refund predates the Supreme Court ruling (Feb 20, 2026; CAPE refunds from Apr 20, 2026). Shift entries to ~Jun–Sep 2025 and the refund to ~May–Jun 2026; shift policy dates with them. Rates stay labeled illustrative.

---

## 13. After the MVP (Part 2)

**Priority order (hours ~18–30, feature freeze ~30):**
1. **Scale the data:** ~100 shipments, ~15 customers, 2 deposits (one bundling two liquidation dates), 12 months of ledger history, 9 planted mistakes, and a **held-out dataset** from a different seed, generated by a "truth first, then views" simulator.
2. All five invariants running continuously.
3. Clocks and escalation (deadline alerts; escalation when an approver is silent).
4. Expand onboarding learning to the full dataset (more policies, more history).
5. Learning guardrails (versioning, dry-run replay, regression check).
6. Minimal UI polished for the demo.
7. Scorecard on held-out data (accuracy + questions before vs after learning).
8. LLM personas for the live demo (scripted mode stays for scoring).
9. Demo video and 3-minute pitch.

**Should-have:** work-graph visual; "delivered duty paid" vendor case (refund goes to the vendor); full escalation chain; SEC EDGAR examples of real refund accounting on the proof slide; stripe-mock offline backup.

**Stretch:** Section 122 deadline tracking; a judge-invented tariff handled by reusing precedents; learning curve across tariff periods; cash forecast; ERPNext instead of SQLite.

**Out of scope:** real CBP filings; legal conclusions; tax; duty drawback; multiple entities.

---

## 14. Demo (so the code supports it)

- **0:00–0:15** Hook: "When the Supreme Court struck down the tariffs, importers got refunds, and a finance mess."
- **0:15–1:15** The moment: refund deposit lands (live Increase event) → the CFO says "book the whole receivable to P&L, don't credit customers" → the desk refuses, splits correctly, catches PM-1 and PM-2, holds on authority and opinion, updates on the Harbor waiver.
- **1:15–1:45** Open a decision: audit trail plus decisive facts.
- **1:45–2:30** Learning: the lawyer's single answer becomes RULE-001 → Northwind handled without a question; before/after numbers.
- **2:30–3:00** Proof slide (real vs simulated, scores) and the seat line.
- **Q&A:** hand judges the controller chat to try to argue the desk into a bad decision.

**Real vs simulated:** real = tariff programs and codes, the ruling and refund process, published accounting guidance. Simulated = Brewline's books, documents and people, with planted mistakes and a hidden answer key.
