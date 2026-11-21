# The Tariff Desk playbook: test it, break it, extend it

This is the human's guide. It lists every use case the desk handles today, how to run and
verify each one from the screen, the API, the command line and the tests, and how each maps
to the agents, the work graph, the gateway and the log underneath. The last section is how
to add your own cases. For the design and the project state, read `HANDOVER.md`; for the
long guide with the changelog, `explanation.md`.

**Current demo:** follow `docs/DEMO.md` for the judge walkthrough. The main button now
uses the messy `tariff-refund-demo` world and includes Harbor's waiver review. Recorded
replay is the default and makes no model calls. Live mode is an explicit, paid choice;
an unavailable model produces an error instead of quietly switching modes. The lawyer
remains simulated and automatic distributor-rule reuse remains unimplemented.

## 0. Set up once

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt          # Windows; use .venv/bin/pip elsewhere
copy .env.example .env                                   # fill in the keys you have
cd frontend && npm install && npm run build && cd ..     # the screen, served at / by the API
python -m desk reset                                     # rebuild the ERP, restore the books, drop the desk DB
python -m pytest -q tests                                # 336 tests, no network
python -m desk serve --port 8000                         # http://127.0.0.1:8000 ; API docs at /docs
```

Keys. With `LLM_PROVIDER=creao` the seats run on the three Brewline agents on Creao;
their app-scoped keys go in `.env` as `CREAO_TARIFFS_REFUNDS_KEY`, `CREAO_STOCK_CUSTOMERS_KEY`
and `CREAO_SOURCING_KEY`. `LLM_MODEL_FAST` with an `ANTHROPIC_API_KEY` (or OpenAI key)
still serves the small classification calls. Set `LLM_PROVIDER=anthropic` with `LLM_MODEL`
to run the desk's own model-backed seats instead. Without any of these every flow still
runs on recorded transcripts ("scripted"), and the screen says so. `CREAO_DRY_RUN=1` sends
every Creao turn through the real routing and parsing but answers from the recorded
transcripts: no network, no credit. Stripe and Increase keys are only used by the one-off
seeders; the desk does not call those APIs yet.

Vocabulary used below. A **work item** is one unit of work with a state
(`NEW → PROPOSED → APPROVED → POSTED`, side states `NEEDS_INFO`, `REJECTED`, `CLOSED`). A
**seat** is an agent (Trade compliance = `seat1`, Cost accounting = `seat2`, Revenue
accounting = `seat3`). The **gateway** is the only thing that writes to the books. The
**log** is the `events` table in `systems/desk/desk.db` (or the sandbox's copy), append-only.

Two worlds. The **base world** (`data/`, `systems/`) carries two planted mistakes. A
**scenario** (`scenarios/*.yaml`) is a patched copy of the world built under `.sandbox/`
and tied out before and after the desk works it; the screen's "Simulate an event" button
uses `tariff-refund-demo`, preserving both mistakes and labeling their exact starting
discrepancies. `rosy-baseline` remains the clean benchmark. Stop the server before running
the test suite: both use the scenario folders.

Roles on the screen. The switch at the top right changes who you are. The **CFO**
approves, declines, sends back, adopts rules, and can push back. The **Lawyer** answers
contract questions, proposes rules, and reads every decision. Nobody else exists on screen;
the scripted stand-ins in recorded runs show as "CFO (scripted)".

The standing authorization. The CFO adopted one rule once (`POL-A1`): routine entries post
on their own, inside caps and conditions code checks, and the CFO is asked only when the
desk is stuck. Every card on the home page that needs the CFO says **why**.

---

## 1. Use cases, one by one

Each case says: how to trigger it, what you should see, how to verify it, and what runs
underneath.

### U1. A tariff refund lands in the bank

**Trigger.** Role CFO → "Simulate an event: a tariff refund arrives" (home page, or
Activity). API: `POST /simulate/refund-arrives` with `{"controller": "human"}` (add
`"llm": "scripted"` to force the replay).

**What you see.** Activity shows the issue "A tariff refund arrived: 14,317.00 from U.S.
Customs" with the forecast correction first, being worked by Trade compliance; the agent
card narrates (reading the CBP refund notice, the bank memo, the policy; matching the deposit
to the notice). After 10–40 s the stage says "needs the controller".

**Underneath.** The endpoint builds `.sandbox/tariff-refund-demo`, syncs the views into the
`golden_*` tables, runs the tie-outs, opens the parent `WI-REFUND-EVENT`, then runs the
scenario's steps on a background thread (`desk/scenario.py` `run_desk`): onboarding (the
three rules, scripted controller), invariants scan (the two planted mistakes), then
`deposit`: a child `WI-REFUND-DEPOSIT` for `seat1` with the `TASK` prompt. The seat's tool
calls (`read_doc`, `run_sql`, `match_deposit`, `breakdown_receivable`, `get_policies`) are
each checked by `can()` and logged; `propose` moves the item to `PROPOSED` and the run waits
(`_await_human`).

**Verify.** `curl localhost:8000/issues` shows the stage `PROPOSED` with `awaiting:
"controller"`. `curl localhost:8000/work-items/WI-0006/brief` shows `plain.summary` with
14,317.00, 13,900.00 and 417.00 and `computations` from `match_deposit`. Tests:
`tests/test_plain.py`, `tests/test_scenario_human.py`.

### U2. Routine entries post on the standing authorization; the CFO decides the rest

**Trigger.** Nothing: it follows U1. The deposit is proposed, code checks it against the
CFO's standing authorization, and it posts. Home page → "Posted on your standing
authorization" lists it with its amount and a link. To see the manual path instead, revoke
the authorization in the sandbox (`UPDATE policies SET status='REVOKED' WHERE
policy_id='POL-A1'`) before running; then the card appears on the home page with "Why you:
no standing authorization is in force" and the CFO approves it:
`POST /work-items/{id}/approve` with `{"actor": "human_cfo", "reason": "approved on screen"}`.

**What you see.** Home: operating cash rises by 14,317.00, "Refund still due from Customs"
drops to 0.00. Activity: the stage says "posted" and the narration reads "‘Record the refund
deposit’ is routine and posts on the CFO's standing authorization: 14,317.00 is under the
25,000.00 cap …". Books page: the entry in the posted book with `approved_by:
standing_authorization`.

**Underneath.** `desk/authorization.py` `check`: the kind is covered (POL-A1 covers the
forecast correction, the deposit, the split and explicit-clause credits), the amount is under
the kind's cap, the brief has compute calls, the invariant gate passes, no question or
pushback is open, it was never sent back or revised; `apply` logs
`authorization:applied` and calls `desk/humans.py` `approve` as actor
`standing_authorization` (a role that may approve and nothing else), then
`desk/gateway.py` `post` appends to the posted book, runs `bean-check`, and transitions to
`POSTED`. When any condition fails, `authorization:needs_human` is logged with the reason,
the run waits, and the home page shows the card with "Why you".

**Verify.** `bean-check` is silent in the sandbox. `python -m desk why WI-0006` prints the
whole history, including the authorization verdict. Tests: `tests/test_authorization.py`,
`tests/test_scenario_human.py`, `tests/test_gateway.py`, `tests/test_permissions.py`.

**Try to break it.** Switch to Lawyer and click Approve on a Decision page: the API answers
403 "role human_lawyer may not approve" and the refusal is logged in that item's history
(Decision page → Details → technical feed, red row). Push back on a proposal before it
posts, or lower a cap in POL-A1: the entry waits for the CFO and says why.

### U3. The refund is split between parts sold and parts in stock

**Trigger.** Follows U2 automatically. Cost accounting picks up `WI-REFUND-SPLIT`.

**What you see.** Card "Split the refund between parts sold and parts still in stock",
13,132.00, summary "Of the 13,900.00 refunded, 768.00 relates to parts already sold and
13,132.00 to parts still on the shelf". Money movement: Tariff cost in, Parts inventory out.

**Underneath.** `seat2` runs `get_policies` and `apply_policy` on `POL-L2@1` (rule kind
`refund_split`, `desk/policies.py`), which computes per shipment principal × consumed /
received from the ERP layers; `split_refund` is the compute tool. INV-4 (the split adds up)
gates the approval.

**Verify.** `CLAUDE.md` §10.2 table: 480/180/108 to COGS on SHP-001/002/003, the rest to
inventory. After approval, home "Parts inventory" drops by 13,132.00 to 172.8K. Tests:
`tests/test_seat2_split.py`, `tests/test_invariants.py`.

### U4. A customer with an explicit clause is credited (Bean Street)

**Trigger.** Follows U3. Revenue accounting picks up `WI-CUST-001-CREDIT`.

**What you see.** Card "Credit Bean Street Coffee Co. for the tariff surcharge", 1,632.00,
"If you approve: a credit of 1,632.00 is booked and credit notes are issued on the invoices".
Decision page → Checked against: Bean Street contract (CON-001), Accounting policy.

**Underneath.** `seat3` reads `data/documents/contracts/CON-001-bean-street.md`, runs
`surcharge_on_invoices` (INV-1001 1,152.00 and INV-1002 480.00, both built from refunded
shipments), may apply `POL-L3@1` (`explicit_clause_credit`, which now computes the same
credit), and proposes Dr Income:Sales:TariffSurcharge / Cr Liabilities:CustomerCredits.

**Verify.** Home "Owed to customers" shows 1,632.00 after approval. The Stripe credit notes
are **not** created yet (Phase 6). Tests: `tests/test_seat3_credit.py`,
`tests/test_policy_executors.py`.

### U5. An ambiguous clause goes to the lawyer (Harbor Roasters)

**Trigger.** Follows U4. Revenue accounting picks up `WI-CUST-002-QUESTION`.

**What you see.** Activity: stage "Harbor: ask the lawyer". Lawyer page: the question
(quoting clause 7.2 of CON-002 and the 576.00 surcharge on INV-1003). In scripted mode the
lawyer answers at once ("Yes, on the consistency reading…"), a precedent is recorded, and
RULE-001 appears under Rules as "proposed" with an **Adopt** button for the controller.

**Underneath.** The seat must `ask`, not decide: the item parks in `NEEDS_INFO`, the question
row is matched to `Q-CON-002-SHARE` in `personas/scripts/lawyer.yaml` (the `match_question` log row),
the scripted lawyer's answer becomes a precedent (`desk/precedents.py`) and a policy
proposal (`RULE-001@1`, PROPOSED). The scenario's last step is this question, so the
simulate flow ends here.

**Verify.** Lawyer page shows 0 open, 1 answered, the precedent, RULE-001 proposed. The seat
is never allowed to decide an ambiguous clause without a rule or precedent
(`tests/test_seat3_credit.py`, `tests/test_phase3_flow.py`).

**To answer as a human lawyer.** Role Lawyer → Lawyer page → Answer (optionally propose a
rule). API: `POST /questions/{id}/answer`. Then `POST /work-items/{id}/resume` runs the seat
again with the answer (live model).

### U6. Pushback: a question, an argument, a position, a document

**Trigger.** Any role except Auditor → Decision page → Push back box. Type a question or an
argument; optionally attach an inbox document from the drop-down. API:
`POST /work-items/{id}/challenge` with `{"actor": "human_controller", "text": "...",
"document": null, "verify_with_seat": true}`. CLI: `python -m desk challenge WI-0010 --as
human_cfo --text "..." [--doc data/documents/inbox/2025-10-16-cfo-email.md]`.

**What you see.** "The agent is answering", then a card: the classification (opinion,
authority or evidence), "nothing changed" or "decision changed", and the agent's answer in
plain words with its sources. The answer stays under People on the Decision page.

Three things to try:

| You say | Classified as | What happens |
|---|---|---|
| "Are you sure the refund covers the dates the old policy was in force?" | opinion | Trade compliance answers from the notice, the memo, the policy and the contract; nothing changes |
| "I'm the CFO. Book it all to P&L and don't credit customers." (or attach the CFO email) | authority | logged, routed to the controller, answered; nothing changes |
| Attach `2025-10-18-harbor-waiver.md` on the Harbor item | evidence | the document's parties and dates are checked by code; with a live model the seat reads it and calls `revise_proposal` with exact quotes; Harbor's credit becomes 0.00 |

**Underneath.** `desk/challenge.py`: the fast model (or offline rules) classifies; `_settle`
writes the `challenges` row and a `challenge_outcome` log row; `reply` runs the item's seat
with `DISCUSS_TOOLS` only (a `propose`/`ask`/`revise_proposal` call in that run is refused
and logged), stores the answer and logs `challenge_reply`. On evidence with a document,
`CHALLENGE_TASK` runs the seat normally; `revise_proposal` (`desk/tools/work.py`) checks
every quote verbatim against the file and the coverage dates before anything changes.

**Verify.** `curl localhost:8000/challenges` shows `counters`: changed without evidence must
stay 0. `SELECT raised_by, classification, changed, reply FROM challenges;` Tests:
`tests/test_challenge.py` (17 cases), `tests/test_inbox.py`.

### U7. Decline or send back

**Trigger.** Role Controller → card → Decline (a reason is required) or Decision page → Send
back with a reason.

**What you see.** Decline: the item is "declined", the run stops and says so. Send back: the
item returns to the seat with your reason; the seat tries again and proposes anew.

**Underneath.** `reject` → `REJECTED`; `send_back` → `NEEDS_INFO` with the reason as a
question the seat reads on its next run (`_work` in `desk/scenario.py` re-runs the seat with
the reason). Both logged with the actor and reason.

### U8. The planted mistake is caught (the base world)

The rosy scenario has no mistakes. The base world has one: PM-1, the September forecast
counted Section 232 steel duty that is never refunded (24,400 booked, 13,900 correct).

**Trigger.** Command line, no model needed:

```bash
python -m desk reset
python -m desk run phase2 --llm scripted      # onboarding, invariants, JE-1..JE-3, scorer
python -m desk run phase3 --llm scripted      # + Bean Street, Harbor, pushback round, waiver
python -m desk score --phase 3
```

**What you see.** INV-2 opens `WI-FORECAST-CORRECTION` (JE-1: 10,500 out of the receivable),
then JE-2 the deposit, JE-3 the split and JE-4 Bean Street's credit; Harbor ends at 0.00
after the waiver; the scorer prints PASS for phase 3 with JE-5 pending. INV-1 stays quiet:
Brewline pays CBP directly, so duty paid and the 7501 already agree.

**Underneath.** `desk/invariants.py` `scan_and_open_work_items` after the parent opens; each
child is worked by `seat1` with `FORECAST_TASK`; the gate refuses a proposal
that would add a violation and the controller sends it back (`NEEDS_INFO`) with the
invariant named.

**Verify.** `python -m desk items`, `python -m desk log --limit 60`,
`cat systems/books/posted/desk.beancount`. Tests: `tests/test_phase2_flow.py`,
`tests/test_phase3_flow.py`, `tests/test_score.py`, `tests/test_cause_matching.py`.

### U9. Learning: rules from the policy and the books

**Trigger.** Every run starts with onboarding. On the screen: Activity → "Learning the rules
from the policy and the books" (three rules adopted, one review). Lawyer page → Rules.

**What you see.** POL-L1 programme refundability (IEEPA yes; 232/301/122 no), POL-L2 refund
split, POL-L3 explicit-clause credit, each "in force" with its scope; the 2025-09-30 forecast
method is flagged as a bad habit (scripted transcript) or, with the live model's cached
answer, left unflagged and reported as a failing check.

**Underneath.** `desk/onboarding.py`: one `WI-ONBOARD-*` item per rule; the seat reads
`data/reference/refund_policy.md`, the programme table and the ledger history and calls
`propose_policy` with evidence counts; the scripted controller approves (`approve_policy`);
`apply_policy` only ever runs APPROVED policies through `desk/policies.py` EXECUTORS.

**Verify.** `curl localhost:8000/policies`; `tests/test_onboarding.py`.

### U10. Invariants: the five checks on the home page

**Trigger.** Always on. Home → "Five checks the books must always pass"; API `/invariants`.

**Underneath.** INV-1 duty paid = the CBP 7501; INV-2 receivable holds refundable duty only; INV-3 refund
≤ refundable duty and deposit = notice; INV-4 split adds up; INV-5 ledger inventory = ERP
landed value. A violation opens a work item; the gate on approval refuses only a new or
worse violation (`tests/test_invariants.py`).

### U11. Alternative worlds and the tie-outs

**Trigger.** `python -m desk scenario scenarios/rosy-baseline.yaml [--llm live]`;
`python -m desk scenario scenarios/rosy-pending-refund.yaml --no-run`. On the screen:
Developer tools → build a sandbox, run it step by step.

**What you see.** A report (`.sandbox/<name>/report.txt`) of every place the ledger must
agree with another system (bank, Customs, ERP, billing), before and after the desk.
Rosy baseline: 65/65. The Books page shows the same board.

**Underneath.** `desk/scenario.py` copies `data/`, `systems/`, `personas/` into
`.sandbox/<name>`, applies the YAML patches (`json_set`, `csv_set`, `replace`, …), rebuilds
the ERP there, and points a `Config` at it. The repo's data is never written.

### U12. Why? The audit trail

**Trigger.** `python -m desk why WI-0006`; Decision page → Details for the controller and
the auditor; API `/work-items/{id}/history`; Books page → Audit log.

**Underneath.** One query over `events` for the subject; every `can()` check, tool call,
refusal, transition, gate, post, question, answer, challenge and reply is a row, in the
order it happened, with `diff` and `evidence`. `UPDATE events …` fails: triggers make the
log append-only (`tests/test_audit_append_only.py`).

---

## 2. Reading the screen against the internals

| On the screen | Where it comes from |
|---|---|
| Home numbers | `/books/balances` (Beancount balances) and the plain account names in `frontend/src/lib/api.ts` |
| A card to approve | `/attention` → `for_controller[]` with `plain` from `desk/plain.py` over `desk/brief.py` |
| Issue tracker | `/issues`: parent work item + children; each stage's visual is the child's state |
| Agent cards | `/status` → `seat_status`: the seat's own last log row, the item's state, and a `plain` reading |
| "As it happens" | `/stream` (SSE) → `desk/activity.py` `enrich`: `plain` when the technical toggle is off, `label` when on; `technical_only` rows hidden |
| Decision page | `/work-items/{id}/brief` (+ `plain`); Money movement = the proposal's postings; Checked against = evidence classified as documents |
| Push back answer | `challenges.reply` (stored) and `challenge_reply` log row |
| Lawyer page | `/questions`, `/policies`, `/precedents` |
| Books page | the scenario report's tie-outs, `/books/balances`, `/books/posted`, `/events` |

---

## 3. Extending the desk

Every extension follows the same shape: data in the world → a work item kind with a task
prompt → a seat with the right tools → a plain title → tests. Keep code enforcing limits and
the agent doing judgement.

### 3.1 A new world or a messy case: a scenario

1. Copy `scenarios/rosy-baseline.yaml`; change `name`, `description`, the `patches` (see
   `scenarios/README.md` for `json_set`, `csv_set`, `replace`, `recompute_running_balance`),
   and the `run` block (`steps`, `expect`).
2. Patch the truth and the answer key in the same file so the scorer and tie-outs know the
   new numbers (the desk still cannot read them).
3. `python -m desk scenario scenarios/<name>.yaml --no-run` until every tie-out holds before
   the run; then run it with `--llm live` (scripted transcripts only fit the base amounts).
4. Add a test next to `tests/test_scenario.py`.

### 3.2 A new kind of event or work item

1. Pick the seat and write the task prompt in `desk/__main__.py` next to `TASK`,
   `SPLIT_TASK`, `CREDIT_TASK`. Say what to read, which compute tool settles the number, when
   to ask instead of deciding.
2. Add the kind to `KIND_TITLES` in `desk/brief.py` and a plain template in
   `desk/plain.py` (`plain()`), and a short tracker label in
   `frontend/src/components/IssueTracker.tsx` `SHORT`.
3. Open it from where the event arrives: a scenario step in `desk/scenario.py`
   `_run_step`, an invariant in `desk/invariants.py`, or the sync.
4. If the controller's scripted answer matters, add it to `personas/scripts/controller.yaml`
   keyed by kind.
5. Test with a `FakeLLM` script (see `tests/test_phase1_flow.py`) and, if it should run
   with no network, a segment in `desk/scripted.py`.

### 3.3 A new tool

1. Implement it in `desk/tools/read.py`, `compute.py`, `work.py` or `policy.py` with a
   `ctx` first argument; compute tools return `Decimal`-exact strings and `raw_refs`.
2. Register it in `desk/tools/__init__.py` `REGISTRY` with its `kind` and JSON schema, and
   add it to the seats that may use it in `SEAT_TOOLS`. A seat outside the list is refused
   and logged automatically.
3. Add the plain wording of what it does to `COMPUTE_PLAIN` in `desk/activity.py` and, if it
   may run in a pushback discussion, to `DISCUSS_TOOLS` in `desk/challenge.py`.
4. Extend `tests/test_tool_jurisdiction.py`.

### 3.4 A new rule kind (executable policy)

1. Write `_exec_<kind>(conn, rule, inputs, cfg)` in `desk/policies.py` and register it in
   `EXECUTORS`. Raise `PolicyError` for out-of-scope inputs so the seat has to ask.
2. Teach a seat to propose it (an onboarding task in `desk/onboarding.py` `TASKS`, or a
   lawyer answer that carries a `rule` in `personas/scripts/lawyer.yaml`).
3. Test it directly as `tests/test_policy_executors.py` does.

### 3.5 A new invariant

1. Add `INV-6` in `desk/invariants.py`: a `check` that returns violations with a magnitude and
   evidence rows, and the work-item kind it opens.
2. Add its plain title in `desk/api.py` `/attention` → `invariants.titles` and the home-page
   tile in `frontend/src/pages/NeedsYou.tsx`.
3. Plant a mistake for it in a scenario and assert it is caught.

### 3.6 A new person or a live persona

- Scripted: a role card in `personas/<role>.md` and answers in `personas/scripts/<role>.yaml`
  keyed by question id; `desk/personas.py` loads them. Add the role to `ROLES` in
  `desk/perms.py` with exactly the actions it may take.
- Live: use the role card as the system prompt for a model client in `desk/personas.py`
  (demo only; scripted mode stays for scoring).

### 3.6b The Creao agents

- The seam is `desk/creao.py`. `AGENTS` holds the three app ids and their forms;
  `agent_for` routes a ticket kind to its agent; `render_context` writes the desk's rules,
  the seat's tools and the whole transcript into the agent's form field; `parse_output`
  reads the agent's tool calls back out of its reply.
- A new ticket kind: add it to the owning agent's `ticket_kind` select on Creao and to
  that agent's `kinds` here (or let `select_kind` map it to the nearest listed kind; the
  real kind is always in the context).
- A new agent: add it to `AGENTS`, its key name to `CREAO_KEY_NAMES` in `desk/config.py`,
  and a route in `agent_for`.
- Test it with `creao.DryRunTransport` (see `tests/test_creao.py`): it records what the
  desk would send and answers from a script.
- One real turn takes about 75 seconds and costs Creao credit; the recorded replay stays
  the demo default. `CREAO_DRY_RUN=1 python -m desk scenario scenarios/tariff-refund-demo.yaml --llm live`
  exercises the whole path for free.

### 3.7 Wiring the real systems (Phase 6)

- **Increase**: add `sync_bank_increase` in `desk/sync.py` reading transactions through the
  SDK with a cursor per source, writing `golden_bank_txns` with `raw_ref` = the Increase id
  (crosswalk in `data/views/bank/increase_crosswalk.json`); the demo trigger becomes a
  simulated inbound ACH.
- **Stripe**: in `desk/gateway.py` `post`, for a customer-credit item, create the credit
  notes on the invoices from `data/views/billing/stripe_crosswalk.json` with an idempotency
  key = the work item id, and log the Stripe ids as evidence. Void the leftover test credit
  note first (`CLAUDE.md` §12.2).
- **Mockoon**: read `/cbp/entries` and `/cbp/refunds` from `MOCKOON_URL`
  instead of the JSON views.
- Keep every tie-out in `desk/scenario.py` green against the live sources.

### 3.8 A new screen

Pages live in `frontend/src/pages`, routes in `frontend/src/main.tsx`, the client in
`frontend/src/lib/api.ts`. Read `plain` fields from the API; never show ids, paths, seat
numbers or tool names in copy; put terms behind `<Term k="...">` (glossary in `api.ts`).
`npm run build` then restart the server.

---

## 4. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Nothing needs you right now" while the agents work | normal; the next card appears when a stage reaches PROPOSED. Watch Activity |
| The trigger says "the agents replay a recorded run" | no model key with credit; the API pinged the model and fell back. Set the keys in `.env`, restart |
| A live run finishes in a second | the LLM cache answered; `python -m desk reset --clear-cache` |
| Tests fail with "database is locked" or sandbox errors | the server is running; stop it, rerun |
| `git status` shows `systems/books/posted/desk.beancount` modified after tests | `git checkout -- systems/books/posted/desk.beancount` |
| 403 on an action | the role matrix refused it; the refusal is logged in the item's history, by design |
| A stage is "being worked" forever after the lawyer answered | the simulate flow ends at the question; Phase 4 adds the resume |
