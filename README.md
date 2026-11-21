# Tariffic — Tariff Desk

An agentic finance desk for Brewline Equipment Co., a fictional espresso-machine importer.
Tariff refunds, duty reconciliation and customer pass-through are worked by three AI seats
(Trade compliance, Cost accounting, Revenue accounting) that **propose** and **ask**; the
controller **approves or declines**; a single gateway **posts** to the books. Nothing is
posted without approval, every step is logged, and only a document with matching parties
and dates can change a number under pushback.

Cal Hacks 13.0, Hyphenate track ("The Hyphenated Enterprise: Autonomous Systems for Finance").

## Status (2026-10-24)

| Piece | State |
|---|---|
| Phases 1–3: system of work, three seats, invariants, learned policies, challenge protocol | done; 336 tests pass with no network |
| The screen: event-driven, plain language, two roles (CFO, Lawyer), every task links to where you act | verified in the browser on the recorded demo |
| The standing authorization: routine entries post on their own; the CFO is asked only when the desk is stuck, and told why | done (`desk/authorization.py`, POL-A1) |
| The Creao agents as the seats' brains (`LLM_PROVIDER=creao`) | wired (`desk/creao.py`); dry runs identical to the recorded ones; one real turn verified; our model-backed seats kept as fallback |
| Pushback | every pushback is answered by the seat with facts; nothing changes without a document |
| Phase 4: RULE-001 adoption and Northwind without a question | in progress (the rule is proposed, not yet adopted or applied) |
| Phase 5: decision forensics | planned |
| Phase 6: Increase, Stripe credit notes, Mockoon | planned; the sandboxes are seeded but the desk reads local views and writes Beancount only |

Read `HANDOVER.md` for the full state, `docs/PLAYBOOK.md` to test and extend every use
case, `explanation.md` for the long guide and the changelog, `CLAUDE.md` for the brief.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # LLM_PROVIDER=creao + the three CREAO_*_KEY app keys; LLM_MODEL_FAST + ANTHROPIC_API_KEY for the small classification calls
cd frontend && npm install && npm run build && cd ..
```

## Run

```
python -m desk reset                                   # rebuild the ERP, restore the books, drop the desk DB
python -m desk serve --port 8000                       # the screen at http://127.0.0.1:8000, API docs at /docs
python -m desk run phase3 --llm scripted               # the base world on the command line, no model needed
python -m desk scenario scenarios/tariff-refund-demo.yaml --llm scripted  # messy judge demo
python -m desk scenario scenarios/rosy-baseline.yaml   # clean benchmark (66 checks)
python -m pytest -q tests                              # 323 tests, no network; stop the server first
```

On the screen: leave **Next run** on **Recorded replay** to use no API credit, switch the
role to **Controller**, and press **Simulate an event: a tariff refund arrives**. The demo
preserves the two planted mistakes and runs through Harbor's signed waiver. Choose
**Live model** explicitly for a paid agent run; unavailable live access returns an error
before replacing the current sandbox. **Activity** shows the event as
a tracker of stages and the three agents by name. **Needs you** shows each proposal as a
plain card: the amount, "if you approve", Approve and Decline. The **CFO** sees the same
cards read-only and can push back; the agent answers with the facts. Harbor's contract
question goes to the **Lawyer**. Scripted step-by-step replays live under Developer tools.

See `docs/DEMO.md` for the six approvals, the CFO pushback moment, and the recording script.
The lawyer is simulated. RULE-001 stays proposed; Northwind rule reuse is not implemented.

## Layout

| Path | Holds |
|---|---|
| `desk/` | the program: sync, workflow, permissions, tools, agents, policies, invariants, gateway, challenge protocol, brief and plain layers, scenarios, API, CLI |
| `frontend/` | the screen (Vite + React + Tailwind); `dist/` is served at `/` |
| `tests/` | 323 tests, no network |
| `scenarios/` | YAML worlds run in `.sandbox/`: `tariff-refund-demo`, `rosy-baseline`, `rosy-pending-refund` |
| `personas/` | role cards and scripted answers for the controller, lawyer, CFO, auditor |
| `data/reference/` | `hts_programs.csv` (programmes, refundable flags), `refund_policy.md` (POL-001) |
| `data/documents/` | `contracts/` (4), `inbox/` (refund notice, bank memo, CFO email, Harbor waiver) |
| `data/views/` | each system's view of the world, read by the sync: bank, customs, billing (+ sandbox id crosswalks) |
| `data/truth/`, `data/answer_key/` | ground truth and the exam answers; the desk cannot read them, the scorer can |
| `systems/books/` | Beancount: chart of accounts, 2025 history, gateway-only `posted/desk.beancount` |
| `systems/erp/` | mini ERP: `schema.sql`, `seed.sql`; `erp.db` is rebuilt locally |
| `systems/mockoon/` | fake customs API (port 3001), not read by the desk yet |
| `docs/` | `PLAYBOOK.md`, `sandbox-storage-plan.md` |

## Local systems

```
.venv\Scripts\bean-check systems\books\main.beancount
.venv\Scripts\fava systems\books\main.beancount -p 5000
npx -y @mockoon/cli@latest start --data systems\mockoon\customs.mockoon.json --port 3001
```

## Sandboxes (seeded once, not yet wired)

| System | Contents | IDs |
|---|---|---|
| Stripe (test mode) | 4 customers, 3 products, 4 paid invoices with machine and tariff-surcharge lines, 1 leftover trial credit note | `data/views/billing/stripe_crosswalk.json` |
| Increase (sandbox) | 1 account, 26 transactions including the Treasury refund of 14,317 | `data/views/bank/increase_crosswalk.json` |

The desk's sync reads `data/views/*` (the same data) and the gateway writes Beancount only;
connecting the APIs is Phase 6.

## The tiny world

10 shipments, 3 part kinds, 4 customers, 4 invoices, 1 refund of 14,317.00 covering 6
entries, 2 planted mistakes: Section 232 steel counted in the refund forecast (booked
24,400, correct 13,900). Expected results are in
`CLAUDE.md` §10.
