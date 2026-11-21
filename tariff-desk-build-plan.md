# Tariff Desk: Build Plan

> **Status 2026-10-24.** This is the pre-event plan, kept for the record. The current state of
> the build is in `HANDOVER.md`; the brief that superseded this plan is `CLAUDE.md`.

*Part 1 is the MVP: the smallest version that proves the idea works, plus the tools, sandboxes and data it needs. Part 2 is what we add once the MVP and data are running. For the full vision see `tariff-refund-usecase.md`; for the plain-English walkthrough see `tariff-desk-explained.md`; for weekend priorities see `tariff-desk-hackathon-scope.md`.*

> **Hackathon rule:** all project code must be written during the event. Phase 0 is research and setup only (no project code). Phases 1–6 are the first ~16–18 hours of the hackathon.

---

# Part 1: The MVP

## 1.1 What the MVP must prove

| # | Question | Why it's risky |
|---|---|---|
| 1 | Can we build a small, consistent Brewline world with a known answer key? | Everything depends on the data |
| 2 | Do the system-of-work mechanics hold (states, permissions, gateway, log)? | It's the backbone Hyphenate judges |
| 3 | Do the rule-based seats get exactly the right answers? | Matching and splitting money must be correct |
| 4 | Can the AI reliably read contracts and sort pushback? | The LLM parts are the least predictable |
| 5 | Does one learning loop visibly change behavior? | It's the "not a one-time thing" claim |
| 6 | Does the "which facts decided it" test work? | It's our edge |
| 7 | Does it run on real systems (bank, books, billing)? | It's our strongest "real enough" moment |

## 1.2 The phases

| Phase | What | Time | When |
|---|---|---|---|
| 0 | Test the AI parts by hand; set up accounts and tools | 2–3 h | Before the event |
| 1 | Tiny world + system-of-work skeleton | ~4 h | Event |
| 2 | Seats 1 and 2 (rule-based) | 3–4 h | Event |
| 3 | Seat 3 + the AI parts | ~3 h | Event |
| 4 | One learning loop | ~2 h | Event |
| 5 | Proof: audit query + "which facts decided it" | 1–2 h | Event |
| 6 | Connect real systems | 3–4 h | Event |

### Phase 0: before the event, no code (2–3 hours)

**Test the AI parts by hand in a chat:**
- Paste Bean Street's and Harbor's contract text; ask for the tariff facts as structured fields. Is it right every time?
- Paste 8–10 pushback lines ("the CFO says so," "here's the signed waiver," "they'll never ask"); ask it to classify each as evidence, opinion or authority. How accurate?
- Paste a messy refund notice and bank memo; can it pull out entry numbers and amounts?

**Set up** the tools in section 1.4 (accounts, keys, installs).

**Hand-write** the tiny dataset for Phase 1 in a spreadsheet, with its answer key.

✅ **Pass if:** the AI reads both contracts correctly and sorts pushback reliably. If not, narrow the AI's role before the event (see go/no-go, 1.3).

### Phase 1: tiny world + system-of-work skeleton (~4 hours)

**Tiny world:**
- 10 shipments, 3 kinds of part, one refund deposit covering 6 of them
- 2 customers: Bean Street (clear share) and Harbor Roasters (ambiguous)
- 1 planted mistake: steel tariffs counted in the refund forecast

**Skeleton** (Hyphenate's slides 16–18 show this pattern):
- Work items with states: New → Proposed → Needs info → Approved → Posted (plus Rejected)
- Permission checks: the AI can propose and ask, never approve or post
- A gateway: the only path that writes to the books
- An append-only log, including refused attempts

✅ **Pass if:** a deposit creates a work item → the AI proposes → a simulated controller approves → the gateway posts → the log shows every step. When the AI tries to approve its own work, it's refused and logged.

### Phase 2: Seats 1 and 2, rule-based (3–4 hours)

- **Seat 1 (trade-compliance):** classify duties by tariff code; match the deposit to shipments; separate principal and interest.
- **Seat 2 (cost):** split the refund between machines sold and parts on hand; draft the journal entry.
- **Two invariants:** "the refund split adds up," and "the forecast only counts refundable tariffs."

✅ **Pass if:** results match the answer key exactly, and the steel-tariff mistake is caught.

### Phase 3: Seat 3 + the AI parts (~3 hours)

- **Seat 3 (revenue):** the AI reads the two contracts into facts; rule-based code decides each customer's share.
- Harbor's ambiguous contract goes to the simulated lawyer as a question.
- **Pushback classifier** wired into the controller chat. Only uploaded documents can change a decision.

✅ **Pass if:** both contracts read correctly; zero decisions change on opinion or authority alone; one decision correctly changes when a real document is uploaded.

### Phase 4: one learning loop (~2 hours)

Add 2 distributor contracts with Harbor's wording. The lawyer decides Harbor's case → the desk finds the similar contracts → proposes a rule → the controller approves → the rule applies.

✅ **Pass if:** the next similar contract is handled correctly without asking anyone.

### Phase 5: proof (1–2 hours)

- The "which facts decided it" test on Bean Street's decision
- One audit-log query answering "why did we credit Bean Street?"

✅ **Pass if:** it identifies the two decisive facts (the contract clause and the tariff invoices), and the audit query returns the full history.

### Phase 6: connect real systems (3–4 hours)

Swap the most important made-up systems for real software.

| Step | Connect | What it proves |
|---|---|---|
| 6a | **Increase sandbox (bank):** the refund deposit arrives as a simulated incoming transfer; the desk detects it | The desk reacts to a real banking event |
| 6b | **Beancount + Fava (books):** the gateway writes approved entries to the Beancount file; Fava shows them | Judges watch the entry appear in real double-entry books |
| 6c | **Stripe sandbox (billing):** Bean Street's invoices, with surcharge lines, live in Stripe; an approved share becomes a credit note | The revenue seat's output lands in real billing software |
| 6d | **Mockoon (customs records):** shipments and refund notices are served from a mock API | The desk syncs from separate systems, as Hyphenate's slide 15 describes |

✅ **Pass if:** one refund runs end to end: deposit in Increase → desk proposes → controller approves → entry in Fava → credit note in Stripe → all logged.

**If short on time:** 6a → 6b → 6c → 6d, in that order.

## 1.3 Go / no-go: what the results tell us

| Result | Meaning | Action |
|---|---|---|
| All phases pass | The idea works | Move to Part 2 |
| Contract reading unreliable | AI can't be trusted there | AI suggests facts; a human confirms; decision stays rule-based |
| Pushback classification shaky | Defense moment at risk | Only an uploaded document with a matching source counts as evidence |
| Learning loop feels staged | "Learns" claim is weak | Lead the pitch with the defense moment; show learning as one honest example |
| Backbone slow to build | Time risk | Cut Seat 3 to Bean Street only; protect backbone and the refund moment |
| A sandbox won't cooperate | Integration risk | Fall back to the mock for that system; keep the others real |

## 1.4 Tools and sandboxes

### What Hyphenate's resource list actually is

Almost none of these **create** data. Most are empty, realistic software that we fill with our own data.

| Category | What it is | Creates data? |
|---|---|---|
| **Sandboxes** | Test versions of real business software with real APIs; no real money | No; they start empty. Increase can *simulate events* like incoming transfers |
| **Ledgers** | Engines that store bookkeeping entries so debits always equal credits | No; they store what we write |
| **Mocks** | Fake APIs we define, returning our data | No; they serve our data |
| **Data** (SEC EDGAR, GitHub) | Real public datasets | Yes, but about real companies, not Brewline |
| **Benchmarks** | Test sets for accounting AI | Yes, but general-purpose, not our problem |
| **People** | An LLM playing approver, colleague or auditor | Generates responses, not business data |

### What we use

| Tool | Plays which Brewline system | Required? |
|---|---|---|
| **Increase sandbox** | The bank: refund deposits in, duty payments out | Yes |
| **Beancount + Fava** | The books, with a web view | Yes |
| **Stripe sandbox** | Billing: invoices, surcharge lines, credit notes | Yes |
| **Mockoon** | Customs records (no real sandbox exists) | Yes |
| **LLM people** | Controller, lawyer, CFO, auditor | Yes |
| **SQLite** (we build) | A mini ERP: vendors, parts, machines, inventory, sales | Yes |
| **SEC EDGAR** | Real filings showing how companies booked tariff refunds (proof slide) | Optional |
| **stripe-mock** | Offline backup if Wi-Fi fails at judging | Optional |
| **BenchRec** | Real messy bank-memo patterns for realism | Optional |

**Skip:** Lago, Odoo, ERPNext (too heavy for 36 hours), Formance, Blnk, TigerBeetle (one ledger is enough), Prism (Mockoon is quicker), and the other benchmarks (our planted mistakes and answer key are our benchmark).

### How they fit together

- **Systems of record:** Increase (bank), Stripe (billing), Beancount (books), Mockoon (customs), SQLite (ERP), text files (contracts and inbox).
- **The desk reads from all of them** and does its work in its own database.
- **The gateway writes to only two places:** journal entries into Beancount and credit notes into Stripe, only after approval.

### Setup

| Tool | Setup in plain terms | Effort |
|---|---|---|
| **Increase** | Sign up for a sandbox; get a sandbox API key; create a test account; use its "simulate an incoming transfer" API | 30–45 min |
| **Stripe** | Create an account; use test mode or a sandbox; get test API keys; create customers and invoices via the API | ~30 min |
| **Beancount + Fava** | `pip install beancount fava`; the books are a plain-text file; run `fava` on it to open the books in a browser | ~15 min |
| **Mockoon** | Install the desktop app or CLI; define a few endpoints returning our JSON; run it | 30–60 min |
| **LLM people** | One prompt per person (role and how they respond), plus a scripted mode with fixed answers for repeatable runs | ~1 h |

**Owner:** the "Data + evaluation" teammate, since they load the simulator's data into these tools.

## 1.5 Data

### The key idea: build the true story first, then the views

1. Generate **one true story** of Brewline: what actually happened to every shipment, part, machine, payment and contract.
2. From it, produce **each system's view** (customs entries, bank, ERP, invoices), adding small disagreements and planted mistakes on purpose.
3. The true story **is the answer key.**

That's how sources disagree realistically while we still know the right answer.

### Where each piece of data comes from

| Data | Source | Lives in |
|---|---|---|
| Tariff codes and refund rules | **Real:** US tariff schedule (USITC HTS: IEEPA 9903.01/9903.02; Section 122 9903.03.01; Sections 232 and 301), Customs refund guidance | Rules tables |
| Accounting policy (how refunds are split; interest) | **Real:** published guidance (PwC, Deloitte, others) | One written policy |
| Shipments, tariff lines, refund notices | **Generated** | Mockoon |
| Bank payments and refund deposits | **Generated**, created via the sandbox | Increase |
| Customer invoices with surcharge lines | **Generated**, created via the API | Stripe |
| Past journal entries | **Generated** | Beancount |
| Parts, machines, inventory, sales | **Generated** | SQLite |
| Contracts, letters, memos | **Hand-written** templates, lightly varied by an LLM | Text files |
| How people respond | **Written by us** as scripts or personas | Our app |
| Real refund-accounting examples | **Real:** SEC filings (optional) | Proof slide |

### MVP data size

| Thing | MVP |
|---|---|
| Shipments | 10 |
| Kinds of part | 3 (steel frames, electronic boards, grinder parts) |
| Refund deposits | 1 (covers 6 shipments) |
| Customers / contracts | 2 (Bean Street, Harbor) + 2 distributor contracts for Phase 4 |
| Planted mistakes | 2 |
| Answer key | One file |

### How we create it

1. **Define the world on paper** (before the event): parts, vendors, customers, contract wording, illustrative tariff rates, planted mistakes.
2. **Write the simulator** (at the event; Python with a fixed random seed):
   - Buy parts → customs entries with duties by program → duty paid direct to CBP
   - Receive parts at full cost → build machines → sell them (with surcharges where they apply)
   - Entries liquidate → refunds arrive bundled, with interest
   - Record everything in the ledger
3. **Inject planted mistakes** with code that also writes each one to the answer key.
4. **Write documents** from templates.
5. **Load each source separately** into its tool, each with its own IDs.
6. **Check it ties out:** bank reconciles, duties add up, inventory rolls forward. The only inconsistencies are the planted ones.

## 1.6 Before the event (setup only)

- [ ] Phase 0 hand tests of the AI parts
- [ ] Increase sandbox account + API key
- [ ] Stripe account + test API keys
- [ ] `pip install beancount fava`; confirm Fava opens a sample file
- [ ] Mockoon installed; a hello-world endpoint running
- [ ] LLM API key
- [ ] Tiny dataset + answer key written in a spreadsheet
- [ ] World definition on paper (parts, vendors, customers, contract wording)

---

# Part 2: After the MVP

Once Phases 1–6 pass, the backbone, seats, AI parts, learning loop, proof and real systems all work on a tiny world. Part 2 grows that into the demo we present. Roughly hours ~18–30 of the hackathon; feature freeze at ~hour 30.

## 2.1 Order of work

| Priority | Functionality | Done means |
|---|---|---|
| **1** | **Scale the data** | ~100 shipments, ~15 customers, 2 deposits (one bundling two batches), 12 months of ledger history, all 9 planted mistakes, a held-out dataset from a different seed |
| **2** | **Full invariants** | All five checks running continuously; any violation opens a work item |
| **3** | **Clocks and escalation** | Deadline alerts for the 23 at-risk shipments; escalation when an approver doesn't answer in time |
| **4** | **Self-learning at onboarding** | The desk studies 12 months of history and proposes 2 rules (how tariff costs were recorded; what TRF-6 means), and flags the bad habit instead of learning it |
| **5** | **Learning guardrails** | Rules are versioned; a dry run shows what a new rule would change before it goes live; a rule that lowers the score is blocked |
| **6** | **Minimal UI** | Work queue, item detail with decisive facts, controller chat with pushback counters, audit log view, scorecard |
| **7** | **Scorecard on held-out data** | Accuracy numbers, plus questions to humans before vs after learning |
| **8** | **LLM people for the live demo** | Controller, lawyer and CFO personas judges can talk to (scripted mode still used for scoring) |
| **9** | **Demo video + pitch** | Video opens with the moment; 3-minute pitch rehearsed |

## 2.2 Should-have additions (if priorities 1–9 are green)

| Functionality | What it adds |
|---|---|
| **Work-graph view** | A visual of one event fanning out across the three seats |
| **"Delivered duty paid" supplier case** | A refund that goes to the supplier, not Brewline; shows importer-of-record judgment |
| **Full escalation chain** | Controller silent → CFO |
| **SEC EDGAR examples** | Real filings on the proof slide showing companies split refunds the same way |
| **stripe-mock backup** | Offline fallback for judging |

## 2.3 Stretch (only if everything above works)

| Functionality | What it adds | Risk |
|---|---|---|
| **Section 122 tracking** | Classify its tariff code and track its deadlines while the appeal is pending | Low–medium |
| **Judge-invented tariff** | Judges invent a tariff; the desk handles it with fewer questions by reusing precedents | High: one bug kills the demo |
| **Learning curve across tariff periods** | Fast-forward replay through several tariff periods, showing questions dropping | High: must be measured honestly, not scripted |
| **Cash forecast** | Expected remaining refunds and customer obligations | Medium |
| **More real systems** | ERPNext or Odoo instead of SQLite | High setup cost |

## 2.4 What we present

| Deliverable | Content |
|---|---|
| **Seat line** | "Brewline's Tariff Desk fills three seats (trade-compliance, cost and revenue accountant), handles every tariff change from import to close, learns from every decision, and posts nothing without the controller." |
| **Demo video (~2 min)** | Opens with the CFO's request and the refusal; then the catches, the approved posting, one decision's audit trail and decisive facts, the learning moment, the proof slide |
| **Live demo (3 min)** | Hook (15 s) → refund moment (60 s) → open a decision (30 s) → learning (45 s) → proof + seat line (30 s); then judges try to argue the desk into a bad decision |
| **Proof slide** | Real: tariff codes, rulings, refund rules, accounting guidance. Simulated: Brewline's books and people. How we know: 9 planted mistakes, hidden answer key, held-out scores, zero wrong postings, zero cave-ins to pushback, questions before vs after learning |

## 2.5 If we fall behind

Cut in this order:
1. Stretch items
2. Should-have additions
3. LLM people for the live demo (keep scripted)
4. Escalation (keep deadline alerts)
5. Seat 3 down to Bean Street only

**Never cut:** the gateway, the audit log, the refund moment, decision forensics, the scorecard, and at least one real system (Increase or Beancount).
