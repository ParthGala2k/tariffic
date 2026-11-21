# Handover: the state of the Tariff Desk (for a Claude terminal)

> Read this before touching anything. It is the map of what exists, what it does, what is
> verified, what is half-built and what is missing, as of 2026-10-25.
> `CLAUDE.md` is the original brief and the design rules; `explanation.md` is the long
> plain-English owner's guide with a changelog; `docs/PLAYBOOK.md` is the human tester's
> guide. This file is the shortest accurate description of the whole system.

## 1. What this is

A **system of work** for the finance team of a fictional espresso-machine importer,
Brewline Equipment Co. Tariff refunds arrive, duties must be classified, costs put in the
right place, customers credited or not per their contracts, and the books must agree with
the bank, Customs, the stock records and billing. Three AI "seats" (Claude
tool-use agents) do the work; humans approve; one gateway posts to the books. Everything is
logged, including refusals.

The three seats, named by role everywhere a person reads:

| Seat id | Role name on screen | Owns |
|---|---|---|
| `seat1` | Trade compliance | classify duties by programme, reconcile duty paid vs the CBP 7501, correct the refund forecast, match the deposit to the notice, split principal from interest |
| `seat2` | Cost accounting | split each refunded entry between parts sold (COGS) and parts on hand (inventory) |
| `seat3` | Revenue accounting | decide customer shares per contract, route ambiguous clauses to the lawyer, apply approved rules |

**Two humans** at the screen (the role switch top right): the **CFO** approves, declines,
sends back, adopts rules, and may push back; the **Lawyer** answers contract questions,
proposes rules, and may ask why. In recorded runs the scripted stand-ins in
`personas/scripts/*.yaml` play them (`human_controller` is the scripted CFO, shown as
"CFO (scripted)"; the auditor script is kept). Brewline pays CBP directly: there is no
customs broker.

**The CFO is asked only when the desk is genuinely stuck.** The CFO adopted a **standing
authorization** once (`POL-A1`, `desk/authorization.py`): a refund deposit that matches its
notice, a forecast correction under an adopted rule, a refund split under an adopted rule,
and a customer credit under an explicit contract clause post on their own, each under a cap,
and only when every figure was computed by code, all five checks pass, nothing is open on
the item, and it was never sent back or revised. Everything else (an ambiguous clause, a
document that changes a decision, pushback, a send-back, an amount above the cap) waits
for the CFO, and the reason is logged and shown ("Why you: …"). Code approves on the
CFO's behalf as actor `standing_authorization`; the gateway still posts, and the log reads
"routine … posts on the CFO's standing authorization".

## 2. The five rules the code enforces (never the prompt)

1. The desk never reads `data/truth/` or `data/answer_key/` (`desk/guard.py`; `tests/test_guard.py`).
2. Agents propose and ask; they have no `approve` or `post` tool (`desk/tools/__init__.py` SEAT_TOOLS; `desk/perms.py` ROLES). A routine entry is approved by the CFO's standing authorization (`desk/authorization.py`), never by the agent.
3. Only `gateway` writes to the books (`desk/gateway.py`), only for `APPROVED` items, and `bean-check` runs after every write.
4. The audit log is append-only in the same transaction as the change (`desk/schema.sql` triggers; `desk/workflow.py` `transition`).
5. A number changes under pushback only on a document with matching parties and dates (`desk/challenge.py`, `desk/evidence.py`, `revise_proposal` in `desk/tools/work.py`).

Also: the LLM never does arithmetic (compute tools in `desk/tools/compute.py` use `Decimal`),
every proposal cites sources, and the seats never talk to each other (they coordinate
through the work graph: parent and child work items).

## 3. Where everything lives

```
CLAUDE.md                  the brief: company, seats, features, expected results, phases
HANDOVER.md                this file
explanation.md             owner's guide (1,400+ lines) with pipelines, data guide, changelog
README.md                  quick start
docs/PLAYBOOK.md           human guide: every use case, how to test it, how to extend it
docs/sandbox-storage-plan.md   how the Stripe / Increase / Beancount / Mockoon sandboxes were seeded
tariff-desk-build-plan.md  the pre-event plan (historical)
scenarios/                 YAML worlds run in a sandbox: rosy-baseline, rosy-pending-refund; README
personas/                  role cards + scripts/*.yaml (scripted answers keyed by question id)
data/
  reference/               hts_programs.csv (programmes, Chapter 99 codes, refundable flag), refund_policy.md (POL-001)
  documents/contracts/     CON-001 Bean Street (explicit clause), CON-002 Harbor, CON-003 Northwind, CON-004 Cascade (distributor template, ambiguous)
  documents/inbox/         2025-10-14 CBP refund notice, 10-15 bank memo, 10-16 CFO email (authority), 10-18 Harbor waiver (evidence)
  views/                   per-system views the sync reads: bank/transactions.csv, customs/*.json, billing/*.json (+ sandbox id crosswalks)
  truth/, answer_key/      ground truth and the exam answers; only desk/score.py and scenario patches touch them
systems/
  books/                   Beancount: main.beancount -> accounts, history/2025.beancount, posted/desk.beancount (gateway-only)
  erp/                     schema.sql + seed.sql -> erp.db (rebuilt by reset; gitignored)
  desk/desk.db             the desk DB (gitignored)
  mockoon/                 customs.mockoon.json (fake customs API, port 3001; not read by the desk yet)
desk/                      the program (see section 4)
frontend/                  the screen: Vite + React + Tailwind; dist/ served at / by `python -m desk serve`
tests/                     336 tests, no network (pytest)
.sandbox/<scenario>/       built copies of data/ + systems/ + personas/ for a scenario (gitignored)
.cache/llm_cache.db        LLM response cache keyed by request hash (gitignored; kept across resets)
.env                       secrets (gitignored); see .env.example
```

## 4. The program, module by module (`desk/`)

| Module | Responsibility |
|---|---|
| `config.py` | `CFG` from `.env`: `LLM_PROVIDER` (creao/anthropic/openai), `LLM_MODEL`, `LLM_MODEL_FAST`, keys, the three Creao `capi_` keys (`creao_keys`, found under their canonical names or however they were pasted), `CREAO_BASE_URL`, `CREAO_DRY_RUN`, `BEANCOUNT_FILE`, `ERP_DB`, `DESK_DB`, `root`; `live_ready`, `fast_provider`. A scenario replaces `root` and the db paths with the sandbox copy (`dataclasses.replace`). |
| `db.py` | `desk_conn` (applies `schema.sql`, then `LATER_COLUMNS` adds columns a table gained after it shipped), `desk_conn_ro` (for `run_sql`), `cache_conn`, `log_event_now`, `rebuild_erp`, `drop_desk_db`. Connections are autocommit (`isolation_level=None`). |
| `schema.sql` | `work_items`, `events` (append-only: UPDATE/DELETE triggers raise), `questions`, `policies`, `precedents`, `challenges` (+ `reply`, `replied_by`), the `golden_*` tables. |
| `sync.py` | Reads `data/views/*` and the Beancount ledger into `golden_*` tables, each row with `raw_ref`. **No Increase, Stripe or Mockoon calls.** |
| `workflow.py` | States `NEW → PROPOSED → (NEEDS_INFO ⇄ PROPOSED) → APPROVED → POSTED`, plus `REJECTED`, `CLOSED`; `TRANSITIONS` (state, action) → state; `create_work_item`, `transition` (does `can()` and writes the change and the log row together), `children`, `close_parent_if_settled`. |
| `perms.py` | `ROLES` (sync, ai_agent, human_cfo, human_lawyer, human_controller [scripted CFO], human_auditor [scripted], standing_authorization [approve only], gateway) → allowed actions; `can(actor, action, resource)` with separation of duties (an actor never approves what it proposed); post is gateway-only. Every check is logged as `can:<action>`. |
| `authorization.py` | the CFO's standing authorization: `in_force` (the APPROVED `standing_authorization` policy, seeded as POL-A1), `check` (kind covered, cap, computed figures, gate, nothing open, never sent back or revised, explicit clause for credits) → `Verdict`, `apply` (logs `authorization:applied` or `authorization:needs_human`, then approves as `standing_authorization` and the gateway posts), `last_verdict` (what the brief and the screen show as "why you"). |
| `guard.py` | `check_doc_path`: refuses `data/truth` and `data/answer_key`, directory listing allowed elsewhere. |
| `tools/__init__.py` | `REGISTRY` of 15 tools with kind read/compute/work, `SEAT_TOOLS` per seat, `schemas_for`, `Ctx`, `dispatch` (jurisdiction, `can()`, guard, run, log allowed or refused). |
| `tools/read.py` | `run_sql` (read-only connection, golden tables only; a bad query returns the real column names), `read_doc`, `get_policies`, `search_precedents`. |
| `tools/compute.py` | `match_deposit`, `sum_duties`, `reconcile_duty_paid`, `breakdown_receivable`, `split_refund`, `surcharge_on_invoices` (raises `PartialRefund` when a surcharge line is only partly covered). All `Decimal`; every result carries `raw_refs`. |
| `tools/work.py` | `propose` (balanced entry required), `ask` (opens a question, parks the item in NEEDS_INFO), `revise_proposal` (needs an inbox document; quotes checked verbatim; coverage checked by code). |
| `tools/policy.py` | `propose_policy` (verdict adopt or reject → PROPOSED or FLAGGED_BAD), `apply_policy` (runs an APPROVED policy's executor). |
| `agent.py` | `run_seat(conn, seat, item, llm, task, cfg, max_turns, allowed_tools)`: system prompt per seat, loop of model turns, one `agent:turn` log row per turn, stops when the item becomes PROPOSED (only if it was not already) or parks on an open question. `allowed_tools` narrows the tool set for a discussion run. A brain with `for_ticket` (the Creao client) is bound to the item first. |
| `llm.py` | `LLMResponse`/`ToolCall`, `AnthropicClient`, `OpenAIClient`, `CachingClient` (memoises by request hash), `FakeLLM` (scripted), `build_client` (provider `creao` → `desk/creao.py`; `fast=True` → the model provider in `Config.fast_provider`). |
| `creao.py` | the Creao agents as the seats' brains: `AGENTS` (app ids, forms, ticket kinds), `agent_for` (route by kind), `form_inputs`, `render_context` (rules, tools, task, transcript in one field), `parse_output` (tool calls as JSON in the reply), `HttpTransport` (create a run, poll it), `DryRunTransport` (records requests, answers from the recorded transcripts), `CreaoClient` (`for_ticket`, `for_segment`), `build`. |
| `scripted.py`, `scripted_seat1.py` | Recorded transcripts per segment (onboard_*, deposit, split, credit, harbor…) so every flow runs with no network. |
| `onboarding.py` | One work item per rule to learn: POL-L1 refundable programmes, POL-L2 refund split, POL-L3 explicit-clause credit, plus a review of the 2025-09-30 forecast method that must be flagged `BAD-2025-09-30-FORECAST`. The scripted controller approves. A missing result closes the item with "nothing to adopt". |
| `policies.py` | versioned policies (`policy_id@version`), statuses PROPOSED / APPROVED / FLAGGED_BAD, `EXECUTORS` by rule kind: `document`, `program_refundability`, `refund_split`, `explicit_clause_credit` (computes the credit from the invoices), `distributor_passthrough` (**raises: Phase 4 not done**). |
| `invariants.py` | INV-1 duty paid = the CBP 7501 per entry; INV-2 receivable holds refundable programmes only; INV-3 refund ≤ refundable duty and deposit = notice; INV-4 split adds up; INV-5 ledger inventory = ERP landed value. `scan_and_open_work_items` opens a child per violation; `gate` judges a proposal by effect (refuses only a new or worse violation). |
| `gateway.py` | `post`: renders the entry with `work_item` and `approved_by` metadata, appends to `posted/desk.beancount`, runs `bean-check`, rolls back the append on failure. |
| `personas.py` | `ScriptedController` (approve or reject per work-item kind) and `answer_question` for the lawyer, CFO and auditor scripts in `personas/scripts/*.yaml`; `_match_question` maps a seat's free-text question to a scripted id and logs `match_question`. |
| `precedents.py` | record a human decision; search by contract/clause. |
| `challenge.py` | `classify` (fast model or offline rules), `raise_challenge` (opinion: nothing changes; authority: routed; evidence: document admitted or refused), `reply` (the seat answers with `DISCUSS_TOOLS` only), `fallback_reply` (code answers from the brief), `store_reply`, `counters`. |
| `evidence.py` | inbox documents with front matter (parties, dates, relates_to); `EvidenceRejected` when parties or dates do not match. |
| `inbox.py` | uploads and fetched links become inbox documents the verifier can cite. |
| `humans.py` | the people's actions through code: `approve` (the gateway posts), `reject`, `send_back`, `answer` (a lawyer's answer becomes a precedent and may carry a rule proposal), `approve_policy`, `resume` a seat after its question was answered. The parent closes only when no background run is still adding stages. |
| `brief.py` | `brief(conn, item, cfg)`: everything a human needs before deciding, built from the log: headline, decision needed, key points, the entry, policy, evidence (classified document / source row / identifier), documents read, computations, invariant gate, questions, challenges, precedents, decisive facts, refused attempts, timeline. `KIND_TITLES`. |
| `plain.py` | the plain-language layer over a brief: sentence title per kind, summary with figures, reasons, "if approved", account names in words, document titles, seat role names. |
| `activity.py` | the live feed: `classify`/`enrich` one log row into mode + technical `label` + `plain` reading + `technical_only`; `names_for`, `humanize`; `feed`, `seat_status`, `snapshot`. |
| `runs.py` | `MANAGER`: background threads for phase and scenario runs, captured output, `RUN-000N` ids. |
| `scenario.py` | `load`, `materialize` (copy data/systems/personas into `.sandbox/<name>` and apply YAML patches), `tie_outs` (ledger vs bank, Customs, ERP, billing; `expected_differences` mark known starting discrepancies), `run_desk` (steps: onboarding, invariants, deposit, split, credit:<cust>, question:<cust>, harbor-close), human mode: a proposal first goes through `authorization.apply`, posts if routine, otherwise waits for the CFO (`run:step` "waiting" rows), report. |
| `score.py` | the only reader of `data/answer_key/answer_key.json`; grades JE-1…JE-6, customer shares, planted mistakes, by phase. Not scenario-aware. |
| `api.py` | FastAPI; routes in section 6; serves `frontend/dist` at `/`; `app.state.scenario` switches the whole API onto a sandbox. |
| `__main__.py` | CLI (section 6) and the seat task prompts: `TASK` (deposit), `SPLIT_TASK`, `CREDIT_TASK`, `FORECAST_TASK`, `CHALLENGE_TASK`, `DISCUSS_TASK`, `HARBOR_CLOSE_TASK`. |

### The screen (`frontend/src`)

| File | What |
|---|---|
| `lib/api.ts` | the HTTP client, `ROLES`, `AGENTS` (names and jobs), `ACCOUNT_NAMES`, `GLOSSARY` (hover-tip text), `STATE_WORD`, `MODE_LABEL`, `money`/`compact` |
| `lib/desk.tsx` | `DeskProvider`: status poll, SSE `/stream` feed, scenario, health, role switch, notices, `act()` (runs an action, toasts the result or the refusal), `useLive(fetcher, deps, everyMs)` |
| `pages/NeedsYou.tsx` | home: four numbers, then the person's queue. Each task is a card that links to the page where the person acts: the CFO's cards (title, summary, "Why you: …", amount, Approve / Decline, link to the decision), the lawyer's questions (link to the question on the Lawyer page). A lane lists what posted on the standing authorization. The five checks show name and state only (description on hover), then the tie-out count and the "Simulate an event" trigger |
| `pages/Activity.tsx` + `components/IssueTracker.tsx` | each event as a line of stages (from `/issues`), the three agents (`SeatCards`), the narration (`Feed`, technical toggle) |
| `pages/Items.tsx`, `pages/Item.tsx` | every decision; one decision in plain words, money movement, documents, people (questions, precedents, pushback and the agent's answer), role actions, push back box, collapsed technical details |
| `pages/Questions.tsx` | the lawyer: open and answered questions, answer form with optional rule proposal, rules, precedents |
| `pages/Ledger.tsx` | tie-outs before/after, balances by name, the posted book, the audit log |
| `pages/Simulate.tsx` + `components/Tracker.tsx` | developer tools: build a sandbox and replay it step by step (not the demo path) |
| `components/Term.tsx` | `<Term k>` glossary hover tips, `<Raw label raw>` raw-value hover |

## 5. The work graph and the kinds

A parent `WI-REFUND-EVENT` is opened when a refund lands; each piece of work is a child.
Kinds (`desk/brief.py` KIND_TITLES; plain titles in `desk/plain.py`):

| Kind | Seat | What it is |
|---|---|---|
| `WI-ONBOARD-POLICY`, `WI-ONBOARD-REVIEW` | seat1/seat2 | learn a rule from the policy and the books; review a past habit |
| `WI-FORECAST-CORRECTION` | seat1 | INV-2 found non-refundable duty in the receivable (PM-1) → JE-1 |
| `WI-DUTY-RECONCILIATION` | seat1 | INV-1 found the books and the 7501 disagree on an entry (clean in the base world) |
| `WI-REFUND-DEPOSIT` | seat1 | record the deposit: principal clears the receivable, interest is income → JE-2 |
| `WI-REFUND-SPLIT` | seat2 | consumed/received per shipment → JE-3 |
| `WI-CUST-001-CREDIT` | seat3 | Bean Street, explicit clause → JE-4 and credit notes |
| `WI-CUST-002-QUESTION` | seat3 | Harbor, ambiguous clause → ask the lawyer → RULE-001 proposed |
| `WI-CUST-002-CLOSE` | seat3 | after the lawyer and the waiver → 0.00, nothing to post |
| `WI-RULE-001`, `WI-CUST-003-CREDIT` | seat3 | **Phase 4**: adopt RULE-001, Northwind 240.00 without a question (not built) → JE-5 |
| `WI-CORRECTION` | any | a correcting entry against a posted item after a verified document |

Brewline pays CBP directly, so there is one planted mistake (PM-1) and the entries are
numbered JE-1..JE-5.

Expected numbers (answer key): deposit 14,317.00 = 13,900.00 principal +
417.00 interest; split 768.00 to COGS / 13,132.00 to inventory; Bean Street 1,632.00
(INV-1001 1,152.00 + INV-1002 480.00); Harbor 0.00 after the waiver; Northwind 240.00.

## 6. Entry points

CLI (`python -m desk …`): `reset [--clear-cache]`, `run phase1|phase2|phase3 [--llm live|scripted]`,
`why <WI-id>`, `items`, `log [--limit N]`, `score [--phase N]`,
`challenge <WI-id> --as <actor> --text "…" [--doc <inbox path>] [--offline]`,
`scenario <yaml> [--llm …] [--no-run] [--sandbox-dir …]`, `serve [--host] [--port] [--reload]`.

API (`python -m desk serve`, docs at `/docs`): `/health`, `/status`, `/events`, `/stream` (SSE),
`/work-items[/{id}[/history|/brief]]`, `POST /work-items/{id}/approve|reject|send-back|challenge|resume`,
`/questions`, `POST /questions/{id}/answer`, `/policies`, `POST /policies/{ref}/approve`, `/precedents`,
`/challenges`, `/golden[/{table}]`, `POST /golden/query`, `/books/posted`, `/books/balances`, `/invariants`,
`/documents[/{path}]`, `POST /inbox`, `/score`, `/seats`, `POST /tools/{seat}/{tool}`, `/attention`,
`/issues`, `POST /simulate/refund-arrives`, `/scenarios`, `/scenarios/active`,
`POST /scenarios/{name}/activate|run`, `POST /scenarios/deactivate`, `POST /runs`, `/runs[/{id}]`, `POST /reset`.
Request bodies are Pydantic models at the top of `desk/api.py`; the OpenAPI page shows them.

The demo path is `POST /simulate/refund-arrives` (the screen's "Simulate an event" button):
materialise `scenarios/tariff-refund-demo.yaml` (the messy base world, one planted mistake)
into `.sandbox/tariff-refund-demo`, sync, tie out, then run the desk with the mode the
header's "Next run" selector chose (recorded replay by default; live pings the model once
without the cache and fails loudly if it is unreachable), `controller: human`. Routine
entries post on the standing authorization; the CFO is asked for the Harbor settlement.

## 7. What is verified

- 336 tests pass with no network (`python -m pytest -q tests`, ~2.5 min on Windows).
- `python -m desk run phase3 --llm scripted` on the base world: JE-1…JE-5 posted, both planted mistakes caught, Harbor 0.00 after the waiver, scorer PASS through phase 3.
- Live runs (`claude-sonnet-5-5`) of phase 2 and phase 3 matched the answer key (see `explanation.md` changelog).
- `scenarios/rosy-baseline.yaml` scripted: 65/65 tie-outs before and after.
- The screen, live model, human controller, 2026-10-24: trigger → deposit proposed → approved on the Needs-you card → posted → split → approved → posted → Bean Street → approved → posted → Harbor question answered by the scripted lawyer → RULE-001 proposed. Tie-outs after: 34/35 (the miss: the cached live onboarding answer did not flag the forecast habit).
- The screen, recorded replay, the CFO at the screen, 2026-10-25: trigger → the forecast correction, the deposit, the split and the Bean Street credit post on the standing authorization with no click → the Harbor settlement is the one card on the home page, with "Why you" → approved → every stage done, 30/30 checks agree after the run (20/21 before, with the one known starting discrepancy).
- Pushback on a proposal: the seat answers in plain words from the notice, memo, policy and contract; nothing changes without a document.
- The Creao path, 2026-10-25: both scenarios dry-run through it with identical results (`CREAO_DRY_RUN=1 python -m desk scenario … --llm live`: demo 50/51 with the one expected finding, baseline 62/62); the real Tariffs & Refunds agent worked the deposit ticket end to end through the desk's harness in 4 turns (283 s): it read the notice, the memo and the policy, called match_deposit, had a bad SQL query and a mis-shaped propose refused by code, corrected both on the next turn, and proposed the right entry (14,317.00 cash; 13,900.00 receivable; 417.00 interest) citing POL-001.

## 8. What is not done, and the known rough edges

**Phase 4 (learning loop, in progress).** RULE-001 is proposed by the lawyer's answer and
stored PROPOSED; `_exec_distributor_passthrough` raises; nothing adopts the rule or handles
Northwind (`WI-RULE-001`, `WI-CUST-003-CREDIT` kinds exist only as titles). The scorer
treats JE-6 as pending before phase 4.

**Phase 5 (forensics).** Decisive facts exist only for a verified revision (the waiver case).
There is no remove-one-fact re-run of a policy yet. `python -m desk why` and
`/work-items/{id}/history` answer "why" from the log.

**Phase 6 (real systems).** The sync reads local view files; the gateway writes Beancount
only. Stripe and Increase sandboxes are **seeded** (`data/views/*/..._crosswalk.json`) but
never read or written by the desk; Mockoon is configured but unused. No credit note is created
in Stripe when a customer credit is approved. Nothing moves money anywhere.

**Creao agents (wired, 2026-10-25).** The three Brewline agents on Creao are now the
seats' brains when `LLM_PROVIDER=creao` (`desk/creao.py`; the contract is in
`brewline_agents_integration.md` and the API trigger docs). Each turn is one app run:
`POST https://agent.creao.ai/api/v1/apps/{appId}/runs` with the agent's form fields, then
polling until it completes; the agent's reply carries its tool calls as JSON objects, the
desk runs them through its own registry and sends the results back in the next turn's
context. Tickets route by kind (`agent_for`): the stock & customers agent owns the refund
split that seat2 works; the sourcing agent has no ticket in the refund flow yet. Every
limit, log row and approval is unchanged. The desk's own model-backed seats
(`AnthropicClient`, `OpenAIClient`) are kept for fallback and for the small classification
calls (`Config.fast_provider`). `CREAO_DRY_RUN=1` answers from the recorded transcripts and
never caches, so the whole path runs with no network. A real turn takes about 75 seconds;
the recorded replay stays the demo default.

**Rough edges.**
- The demo scenario now includes `harbor-close`; the clean baseline still ends at Harbor's question.
- The lawyer is scripted even in human-controller mode, so the question is answered at once.
- The live model's cached onboarding review does not flag `BAD-2025-09-30-FORECAST`; the scripted transcript does. The run no longer aborts on this; the report shows a failing check.
- `scenarios/rosy-pending-refund.yaml` documents an INV-2 false alarm (a receivable without a notice); a team decision is pending on widening INV-2.
- `score.py` is not scenario-aware (informational only inside a scenario report).
- Tests reset the base `systems/books/posted/desk.beancount`; after a suite run, `git checkout -- systems/books/posted/desk.beancount` restores the committed copy.
- The live server and the test suite share `.sandbox/rosy-baseline`; stop the server before running the suite.
- The LLM cache means a repeated live run replays earlier answers; `python -m desk reset --clear-cache` forgets them.
- Pending data fixes from `CLAUDE.md` §12.2: a leftover Stripe credit note from testing; dates predate the real 2025/2026 tariff timeline.

## 9. Conventions this repo follows

- **Small, composable commits**, one logical group each (user rule).
- **Documentation rule**: `explanation.md` is updated (repo map, tools, pipeline, changelog, walkthrough of the latest real run) before a change is reported done. Keep this file and `docs/PLAYBOOK.md` current too.
- **Plain language on every screen people read**: no work-item ids, policy refs, seat numbers, file paths or tool names in copy; agents by role name; codes behind hover tips or the collapsed details section (user rule). The backend provides `plain` fields; do not re-derive them in the UI.
- Two roles on screen, CFO and Lawyer; no "Controller" or "Auditor" in copy. The CFO decides only what the standing authorization does not cover; the home page says why.
- Scripted replays are a development aid (Developer tools page), not the demo.
- Secrets only in `.env`; Stripe test keys and Increase sandbox keys only; never print keys.
- Hackathon rule: project code written during the event (2026-10-23 to 25); seed scripts stay outside the repo.
- Windows: run tests from Git Bash or PowerShell with the venv's python; paths in code use `Path(...).as_posix()`; SQLite files are closed before deletion (`db._remove_db`).

## 10. How to pick up work

```bash
./.venv/Scripts/python -m pytest -q tests          # green first
./.venv/Scripts/python -m desk reset               # clean base world
./.venv/Scripts/python -m desk run phase3 --llm scripted
./.venv/Scripts/python -m desk serve --port 8000   # then open http://127.0.0.1:8000
```

Next pieces, in the order the plan wants them: Phase 4 (adopt RULE-001 after the lawyer's
answer, make `distributor_passthrough` executable, handle Northwind without a question,
resume the Harbor item after the answer in the simulate flow), Phase 5 (forensics by
re-running policies with facts removed), Phase 6 (Increase transactions with a cursor,
Stripe credit notes from the gateway, Mockoon for customs), then the Part 2 list in
`CLAUDE.md` §13.
