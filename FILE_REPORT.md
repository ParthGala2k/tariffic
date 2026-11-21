# Tariffic: full file report

> Historical setup snapshot at 3030c45. The demo has since been updated on codex/judge-demo; see [the current demo guide](docs/DEMO.md).

Source inventory checked October 24, 2026, at commit `3030c4557c015da959e011fdd4f7e4d99a54acc5`.

**180 tracked files; 1,507,240 bytes.** Every source file was retrieved from your fork and checked against its Git hash. This inventory excludes generated environments, credentials, databases, builds, caches, scenario copies, and these two local reports.

Start with [the setup and status report](LOCAL_STATUS_REPORT.md), [your playbook](docs/PLAYBOOK.md), and [the handover](HANDOVER.md).

## Area totals

| Area | Tracked files | Role |
|---|---:|---|
| (root) | 8 | Project guides and setup configuration |
| desk | 35 | Python backend, agents, permissions, workflow, tools, API, and CLI |
| frontend | 24 | React/Vite screen and its configuration |
| tests | 31 | 30 test modules plus shared fixtures |
| data | 36 | Synthetic documents, allowed views/references, and restricted grading fixtures |
| systems | 7 | Ledger, ERP seeds, and mock API configuration |
| scenarios | 3 | Alternative benchmark worlds |
| personas | 11 | Simulated human role cards and answers |
| docs | 2 | Human playbook and sandbox notes |
| .claude | 23 | Bundled UI/UX guide and references |

The suite expands to 315 passing test cases. The frontend contains 19 source files. A directory containing many files is not evidence that its planned feature is complete; Phase 4–6 gaps are recorded in the status report.

## (root)

| File | Lines | Bytes | Purpose |
|---|---:|---:|---|
| [.env.example](.env.example) | 29 | 695 | Configuration template with provider/model fields and placeholder sandbox credentials. |
| [.gitignore](.gitignore) | 41 | 403 | Excludes credentials, virtual environments, databases, builds, caches, and scenario copies. |
| [CLAUDE.md](CLAUDE.md) | 363 | 30,988 | Original project brief, hard rules, expected results, and phased build plan. |
| [HANDOVER.md](HANDOVER.md) | 228 | 21,926 | Current agent handover: implemented modules, verification history, gaps, and conventions. |
| [README.md](README.md) | 94 | 5,322 | Quick start, current status, run commands, and repository map. |
| [explanation.md](explanation.md) | 1,514 | 117,140 | Long owner guide, architecture/data walkthroughs, and development changelog. |
| [requirements.txt](requirements.txt) | 14 | 113 | Python runtime and test dependencies; Beancount/Fava pinned, most others unpinned. |
| [tariff-desk-build-plan.md](tariff-desk-build-plan.md) | 288 | 17,657 | Historical pre-event plan; current status belongs in the handover. |

## desk

| File | Lines | Bytes | Purpose |
|---|---:|---:|---|
| [desk/__init__.py](desk/__init__.py) | 7 | 269 | Brewline Tariff Desk - the system of work. |
| [desk/__main__.py](desk/__main__.py) | 880 | 40,367 | desk CLI: reset \| run phase1\|phase2\|phase3 \| why \| items \| log \| score \| challenge \| serve \| scenario. |
| [desk/activity.py](desk/activity.py) | 468 | 21,008 | Live activity for the UI, read straight off the audit log. |
| [desk/agent.py](desk/agent.py) | 219 | 9,910 | The agent harness: model + tools + loop + context. |
| [desk/api.py](desk/api.py) | 1,083 | 45,741 | HTTP API over the desk: the base data, the work, the people's actions, and the live feed the UI draws its "thinking" view from. |
| [desk/brief.py](desk/brief.py) | 344 | 15,043 | The brief: what a human needs to read before approving or denying. |
| [desk/challenge.py](desk/challenge.py) | 427 | 18,904 | The challenge protocol (CLAUDE.md 6.8). |
| [desk/config.py](desk/config.py) | 86 | 2,600 | Configuration. Everything comes from .env in the repo root; nothing is hardcoded. |
| [desk/db.py](desk/db.py) | 180 | 5,405 | SQLite connections, desk schema, ERP rebuild. |
| [desk/evidence.py](desk/evidence.py) | 268 | 10,865 | Evidence checking for the challenge protocol (CLAUDE.md 6.8). |
| [desk/gateway.py](desk/gateway.py) | 139 | 5,243 | The gateway. The only component that writes to the books. |
| [desk/guard.py](desk/guard.py) | 125 | 4,161 | The hard guard: the desk must never read data/truth/ or data/answer_key/. |
| [desk/humans.py](desk/humans.py) | 233 | 10,817 | What a real human does through the API: approve, reject, send back, answer, adopt a rule, resume a seat. |
| [desk/inbox.py](desk/inbox.py) | 175 | 6,862 | The inbox: how a human puts a document, or a link, in front of the desk. |
| [desk/invariants.py](desk/invariants.py) | 606 | 25,303 | The five invariants (CLAUDE.md 6.6). |
| [desk/llm.py](desk/llm.py) | 394 | 14,535 | Provider-neutral LLM layer. |
| [desk/onboarding.py](desk/onboarding.py) | 167 | 7,328 | Onboarding: the seats study the company's history and propose their own rules. |
| [desk/perms.py](desk/perms.py) | 142 | 4,270 | Actors x actions x resources, enforced in code (CLAUDE.md section 6.2). |
| [desk/personas.py](desk/personas.py) | 268 | 11,496 | Simulated humans. Phase 1 and 2 run them in scripted mode. |
| [desk/plain.py](desk/plain.py) | 268 | 14,061 | Plain words for the screen. |
| [desk/policies.py](desk/policies.py) | 375 | 16,191 | Learned policies: stored as data, executed by code. |
| [desk/precedents.py](desk/precedents.py) | 71 | 2,529 | Precedents: past human decisions the desk can cite in a matching case. |
| [desk/runs.py](desk/runs.py) | 211 | 8,253 | Background runs for the API: start a phase, watch it, read its output. |
| [desk/scenario.py](desk/scenario.py) | 803 | 42,962 | Scenario runner: a sandboxed copy of the world, patched, tied out, and worked. |
| [desk/schema.sql](desk/schema.sql) | 291 | 10,014 | Desk SQLite tables, golden records, workflow records, and append-only event triggers. |
| [desk/score.py](desk/score.py) | 476 | 20,105 | The scorer. The ONLY module that may read data/answer_key/answer_key.json. |
| [desk/scripted.py](desk/scripted.py) | 564 | 28,320 | Scripted seat transcripts, so every phase runs with no network. |
| [desk/scripted_seat1.py](desk/scripted_seat1.py) | 1 | 68 | Recorded trade-compliance transcripts for deterministic offline runs. |
| [desk/sync.py](desk/sync.py) | 279 | 13,095 | Minimal sync layer (Phase 1). |
| [desk/tools/__init__.py](desk/tools/__init__.py) | 338 | 13,496 | Tool registry and per-seat permissions. |
| [desk/tools/compute.py](desk/tools/compute.py) | 492 | 18,608 | Compute tools. The arithmetic lives here, never in the model. |
| [desk/tools/policy.py](desk/tools/policy.py) | 172 | 6,497 | Policy tools: get_policies, propose_policy, apply_policy. |
| [desk/tools/read.py](desk/tools/read.py) | 177 | 5,791 | Read tools: run_sql over the golden tables, read_doc over the allowed folders. |
| [desk/tools/work.py](desk/tools/work.py) | 783 | 32,095 | Work tools: propose and ask. Both go through transition(). |
| [desk/workflow.py](desk/workflow.py) | 314 | 11,580 | Work-item state machine. The only way a work item changes. |

## frontend

| File | Lines | Bytes | Purpose |
|---|---:|---:|---|
| [frontend/index.html](frontend/index.html) | 15 | 592 | Vite HTML entry page. |
| [frontend/package-lock.json](frontend/package-lock.json) | 2,458 | 82,032 | Exact frontend dependency resolution used by npm ci. |
| [frontend/package.json](frontend/package.json) | 27 | 621 | Frontend scripts and dependency declarations. |
| [frontend/src/components/Feed.tsx](frontend/src/components/Feed.tsx) | 76 | 3,963 | Audit-event narration with a technical-detail toggle. |
| [frontend/src/components/IssueTracker.tsx](frontend/src/components/IssueTracker.tsx) | 93 | 5,393 | Event work stages and status visualization. |
| [frontend/src/components/SeatCards.tsx](frontend/src/components/SeatCards.tsx) | 42 | 2,299 | The three accounting agents and their current activity. |
| [frontend/src/components/Shell.tsx](frontend/src/components/Shell.tsx) | 95 | 4,929 | Application navigation, page frame, role switch, and connection status. |
| [frontend/src/components/Term.tsx](frontend/src/components/Term.tsx) | 26 | 1,083 | Glossary and raw-value hover explanations. |
| [frontend/src/components/TieOutBoard.tsx](frontend/src/components/TieOutBoard.tsx) | 60 | 3,152 | Ledger-to-system reconciliation checks. |
| [frontend/src/components/Tracker.tsx](frontend/src/components/Tracker.tsx) | 129 | 6,903 | Developer replay step tracker. |
| [frontend/src/components/ui.tsx](frontend/src/components/ui.tsx) | 117 | 5,409 | Shared interface primitives. |
| [frontend/src/index.css](frontend/src/index.css) | 149 | 3,831 | Application styling, layout rules, and shared visual tokens. |
| [frontend/src/lib/api.ts](frontend/src/lib/api.ts) | 177 | 11,096 | HTTP client, API calls, role names, labels, glossary, and money formatting. |
| [frontend/src/lib/desk.tsx](frontend/src/lib/desk.tsx) | 145 | 5,966 | Shared status, SSE updates, role/scenario state, polling, and action notices. |
| [frontend/src/main.tsx](frontend/src/main.tsx) | 33 | 1,200 | React application entry point and page routing. |
| [frontend/src/pages/Activity.tsx](frontend/src/pages/Activity.tsx) | 73 | 3,840 | Event stages, agent status, and live activity narration. |
| [frontend/src/pages/Item.tsx](frontend/src/pages/Item.tsx) | 243 | 16,996 | Decision detail, evidence, history, human actions, and pushback. |
| [frontend/src/pages/Items.tsx](frontend/src/pages/Items.tsx) | 36 | 2,038 | Decision/work-item listing. |
| [frontend/src/pages/Ledger.tsx](frontend/src/pages/Ledger.tsx) | 63 | 3,371 | Ledger balances, posted entries, tie-outs, and audit log. |
| [frontend/src/pages/NeedsYou.tsx](frontend/src/pages/NeedsYou.tsx) | 188 | 12,388 | Role-specific home queue, approval cards, health checks, and refund trigger. |
| [frontend/src/pages/Questions.tsx](frontend/src/pages/Questions.tsx) | 113 | 7,424 | Lawyer questions, answers, proposed rules, and precedents. |
| [frontend/src/pages/Simulate.tsx](frontend/src/pages/Simulate.tsx) | 67 | 5,324 | Developer controls for scenario setup and step-by-step replay. |
| [frontend/tsconfig.json](frontend/tsconfig.json) | 18 | 433 | TypeScript compiler settings. |
| [frontend/vite.config.ts](frontend/vite.config.ts) | 18 | 834 | React/Tailwind build and API proxy configuration; issues/simulate prefixes are missing. |

## tests

| File | Lines | Bytes | Purpose |
|---|---:|---:|---|
| [tests/conftest.py](tests/conftest.py) | 110 | 3,433 | Shared fixtures. No test touches the network or data/. |
| [tests/test_activity.py](tests/test_activity.py) | 135 | 7,972 | The live-activity view is a reading of the audit log, nothing more. |
| [tests/test_api.py](tests/test_api.py) | 258 | 13,430 | The HTTP API: base data, work, people's actions, and the live feed. |
| [tests/test_audit_append_only.py](tests/test_audit_append_only.py) | 67 | 2,679 | The audit log is append-only, and the change rides in the same transaction. |
| [tests/test_brief.py](tests/test_brief.py) | 90 | 4,209 | The brief is built by code from the log, and it says what the human must decide. |
| [tests/test_cause_matching.py](tests/test_cause_matching.py) | 243 | 9,871 | One work item, one cause: each entry must settle its own ticket, for its own amount. |
| [tests/test_challenge.py](tests/test_challenge.py) | 370 | 17,726 | The challenge protocol: only evidence moves a number. |
| [tests/test_cli.py](tests/test_cli.py) | 160 | 5,749 | The read-only CLI commands, and reset's cache handling. |
| [tests/test_gateway.py](tests/test_gateway.py) | 298 | 13,109 | Only the gateway posts, and only APPROVED items. |
| [tests/test_guard.py](tests/test_guard.py) | 194 | 7,665 | The hard guard: data/truth and data/answer_key are unreachable. |
| [tests/test_humans.py](tests/test_humans.py) | 168 | 7,832 | Humans at the screen have exactly the powers the role matrix gives them. |
| [tests/test_inbox.py](tests/test_inbox.py) | 97 | 4,197 | Uploads and links become inbox documents the verifier can cite. |
| [tests/test_invariants.py](tests/test_invariants.py) | 269 | 12,122 | The five invariants: detection, and gating by effect. |
| [tests/test_llm_providers.py](tests/test_llm_providers.py) | 112 | 4,659 | The provider adapters translate the same canonical conversation both ways. |
| [tests/test_onboarding.py](tests/test_onboarding.py) | 124 | 5,377 | Onboarding: the seats propose rules, and nothing applies before approval. |
| [tests/test_permissions.py](tests/test_permissions.py) | 111 | 4,921 | Permissions and separation of duties, enforced in code. |
| [tests/test_phase1_flow.py](tests/test_phase1_flow.py) | 102 | 4,151 | The Phase 1 pass criteria, end to end, with a fake LLM client. |
| [tests/test_phase2_flow.py](tests/test_phase2_flow.py) | 181 | 7,158 | Phase 2 end to end, with a fake LLM: four entries, two mistakes caught. |
| [tests/test_phase3_flow.py](tests/test_phase3_flow.py) | 170 | 6,555 | Phase 3 end to end, plus the challenge CLI. |
| [tests/test_plain.py](tests/test_plain.py) | 90 | 4,298 | The plain-language layer: a CFO can read what they are approving without the jargon. |
| [tests/test_policy_executors.py](tests/test_policy_executors.py) | 23 | 1,251 | Approved rules are executable: the explicit-clause credit rule computes the credit itself. |
| [tests/test_provider_parity.py](tests/test_provider_parity.py) | 183 | 5,530 | The same Phase 1 flow runs through both provider adapters, no network. |
| [tests/test_reset.py](tests/test_reset.py) | 53 | 1,721 | reset rebuilds the ERP, restores the posted book and drops the desk DB. |
| [tests/test_scenario.py](tests/test_scenario.py) | 113 | 5,253 | Scenarios run in a sandbox, tie out, and never touch the base data. |
| [tests/test_scenario_human.py](tests/test_scenario_human.py) | 101 | 4,602 | A run with a human controller pauses at every proposal until a person decides. |
| [tests/test_scenarios_api.py](tests/test_scenarios_api.py) | 85 | 3,856 | The API can switch onto a scenario's sandbox, work it in the background, and tell the CFO who has to act. |
| [tests/test_score.py](tests/test_score.py) | 201 | 8,275 | The scorer: it alone reads the answer key, and agent tools still cannot. |
| [tests/test_seat2_split.py](tests/test_seat2_split.py) | 116 | 5,400 | Seat 2: the refund split, computed by the approved rule. |
| [tests/test_seat3_credit.py](tests/test_seat3_credit.py) | 329 | 14,976 | Seat 3: customer shares read off the contracts. |
| [tests/test_tool_jurisdiction.py](tests/test_tool_jurisdiction.py) | 98 | 4,210 | Every seat gets exactly the tools in its jurisdiction (CLAUDE.md section 5), and nothing outside it runs, however it is asked for. |
| [tests/test_work_graph.py](tests/test_work_graph.py) | 136 | 5,922 | The work graph: a parent closes only once every child has settled. |

## data

| File | Lines | Bytes | Purpose |
|---|---:|---:|---|
| [data/answer_key/answer_key.json](data/answer_key/answer_key.json) | 113 | 9,021 | Hidden expected-results fixture, read by the scorer only. |
| [data/documents/contracts/CON-001-bean-street.md](data/documents/contracts/CON-001-bean-street.md) | 30 | 1,505 | Synthetic customer contract supplied as evidence to the revenue agent. |
| [data/documents/contracts/CON-002-harbor.md](data/documents/contracts/CON-002-harbor.md) | 30 | 1,302 | Synthetic customer contract supplied as evidence to the revenue agent. |
| [data/documents/contracts/CON-003-northwind.md](data/documents/contracts/CON-003-northwind.md) | 30 | 1,305 | Synthetic customer contract supplied as evidence to the revenue agent. |
| [data/documents/contracts/CON-004-cascade.md](data/documents/contracts/CON-004-cascade.md) | 30 | 1,314 | Synthetic customer contract supplied as evidence to the revenue agent. |
| [data/documents/inbox/2025-10-14-cbp-refund-notice.md](data/documents/inbox/2025-10-14-cbp-refund-notice.md) | 26 | 1,105 | Synthetic inbox evidence: cbp refund notice. |
| [data/documents/inbox/2025-10-15-bank-memo.md](data/documents/inbox/2025-10-15-bank-memo.md) | 23 | 665 | Synthetic inbox evidence: bank memo. |
| [data/documents/inbox/2025-10-16-cfo-email.md](data/documents/inbox/2025-10-16-cfo-email.md) | 21 | 688 | Synthetic inbox evidence: cfo email. |
| [data/documents/inbox/2025-10-18-harbor-waiver.md](data/documents/inbox/2025-10-18-harbor-waiver.md) | 21 | 996 | Synthetic inbox evidence: harbor waiver. |
| [data/reference/hts_programs.csv](data/reference/hts_programs.csv) | 6 | 887 | Allowed policy/program reference used by the desk. |
| [data/reference/refund_policy.md](data/reference/refund_policy.md) | 56 | 3,416 | Allowed policy/program reference used by the desk. |
| [data/truth/bom.csv](data/truth/bom.csv) | 6 | 126 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/consumption.csv](data/truth/consumption.csv) | 11 | 217 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/contracts.csv](data/truth/contracts.csv) | 5 | 389 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/customers.csv](data/truth/customers.csv) | 5 | 344 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/deposits.csv](data/truth/deposits.csv) | 2 | 242 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/duty_lines.csv](data/truth/duty_lines.csv) | 18 | 889 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/invoice_lines.csv](data/truth/invoice_lines.csv) | 5 | 145 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/invoices.csv](data/truth/invoices.csv) | 5 | 481 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/machines.csv](data/truth/machines.csv) | 3 | 124 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/parts.csv](data/truth/parts.csv) | 4 | 174 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/refund_entries.csv](data/truth/refund_entries.csv) | 7 | 365 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/refunds.csv](data/truth/refunds.csv) | 2 | 126 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/shipments.csv](data/truth/shipments.csv) | 11 | 1,145 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/vendors.csv](data/truth/vendors.csv) | 4 | 123 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/truth/world.json](data/truth/world.json) | 49 | 6,810 | Hidden synthetic ground-truth fixture; inaccessible to desk tools. |
| [data/views/bank/increase_crosswalk.json](data/views/bank/increase_crosswalk.json) | 248 | 7,986 | External sandbox ID mapping; not a live connection. |
| [data/views/bank/transactions.csv](data/views/bank/transactions.csv) | 27 | 3,111 | Local system-of-record view read by the sync layer. |
| [data/views/billing/customers.json](data/views/billing/customers.json) | 42 | 960 | Local system-of-record view read by the sync layer. |
| [data/views/billing/invoices.json](data/views/billing/invoices.json) | 150 | 3,837 | Local system-of-record view read by the sync layer. |
| [data/views/billing/products.json](data/views/billing/products.json) | 17 | 294 | Local system-of-record view read by the sync layer. |
| [data/views/billing/stripe_crosswalk.json](data/views/billing/stripe_crosswalk.json) | 43 | 978 | External sandbox ID mapping; not a live connection. |
| [data/views/customs/cbp_entries.json](data/views/customs/cbp_entries.json) | 32 | 7,120 | Local system-of-record view read by the sync layer. |
| [data/views/customs/cbp_refunds.json](data/views/customs/cbp_refunds.json) | 22 | 1,199 | Local system-of-record view read by the sync layer. |

## systems

| File | Lines | Bytes | Purpose |
|---|---:|---:|---|
| [systems/books/accounts.beancount](systems/books/accounts.beancount) | 16 | 829 | Chart of accounts. |
| [systems/books/history/2025.beancount](systems/books/history/2025.beancount) | 356 | 13,302 | Synthetic historical transactions and balance assertions. |
| [systems/books/main.beancount](systems/books/main.beancount) | 6 | 169 | Ledger entry point including accounts, history, and posted entries. |
| [systems/books/posted/desk.beancount](systems/books/posted/desk.beancount) | 51 | 1,978 | Committed sample postings; at runtime the gateway appends approved entries here. |
| [systems/erp/schema.sql](systems/erp/schema.sql) | 101 | 3,213 | Mini-ERP schema for shipments, inventory, sales, BOM, and consumption. |
| [systems/erp/seed.sql](systems/erp/seed.sql) | 90 | 5,222 | Synthetic initial ERP records used to rebuild the local database. |
| [systems/mockoon/customs.mockoon.json](systems/mockoon/customs.mockoon.json) | 189 | 21,957 | Mock customs API configuration; the desk does not consume it yet. |

## scenarios

| File | Lines | Bytes | Purpose |
|---|---:|---:|---|
| [scenarios/README.md](scenarios/README.md) | 91 | 5,064 | Scenario format, sandbox behavior, running worlds, and known limits. |
| [scenarios/rosy-baseline.yaml](scenarios/rosy-baseline.yaml) | 94 | 4,275 | Clean scripted benchmark world; verified here with 66/66 checks. |
| [scenarios/rosy-pending-refund.yaml](scenarios/rosy-pending-refund.yaml) | 74 | 3,603 | Pre-refund benchmark documenting the known INV-2 false alarm. |

## personas

| File | Lines | Bytes | Purpose |
|---|---:|---:|---|
| [personas/README.md](personas/README.md) | 17 | 920 | Explains simulated human role cards and scripted answers. |
| [personas/auditor.md](personas/auditor.md) | 10 | 390 | Role description for the simulated auditor. |
| [personas/cfo.md](personas/cfo.md) | 10 | 569 | Role description for the simulated cfo. |
| [personas/controller.md](personas/controller.md) | 12 | 811 | Role description for the simulated controller. |
| [personas/lawyer.md](personas/lawyer.md) | 11 | 690 | Role description for the simulated lawyer. |
| [personas/scripts/auditor.yaml](personas/scripts/auditor.yaml) | 16 | 932 | Deterministic answers and actions for the simulated auditor. |
| [personas/scripts/cfo.yaml](personas/scripts/cfo.yaml) | 15 | 514 | Deterministic answers and actions for the simulated cfo. |
| [personas/scripts/controller.yaml](personas/scripts/controller.yaml) | 53 | 3,040 | Deterministic answers and actions for the simulated controller. |
| [personas/scripts/lawyer.yaml](personas/scripts/lawyer.yaml) | 38 | 2,306 | Deterministic answers and actions for the simulated lawyer. |

## docs

| File | Lines | Bytes | Purpose |
|---|---:|---:|---|
| [docs/PLAYBOOK.md](docs/PLAYBOOK.md) | 394 | 22,607 | Human testing and extension guide; preserved for you to view. |
| [docs/sandbox-storage-plan.md](docs/sandbox-storage-plan.md) | 66 | 8,639 | Describes external sandbox seeding and storage choices; not an active integration. |

## .claude

| File | Lines | Bytes | Purpose |
|---|---:|---:|---|
| [.claude/skills/oiloil-ui-ux-guide/LICENSE.txt](.claude/skills/oiloil-ui-ux-guide/LICENSE.txt) | 202 | 11,358 | License for the bundled UI/UX guide. |
| [.claude/skills/oiloil-ui-ux-guide/SKILL.md](.claude/skills/oiloil-ui-ux-guide/SKILL.md) | 220 | 14,968 | Bundled UI/UX skill instructions; not application runtime code. |
| [.claude/skills/oiloil-ui-ux-guide/evals/evals.json](.claude/skills/oiloil-ui-ux-guide/evals/evals.json) | 173 | 13,583 | Evaluation fixtures for the bundled UI/UX guide. |
| [.claude/skills/oiloil-ui-ux-guide/references/business-mockup-contract.md](.claude/skills/oiloil-ui-ux-guide/references/business-mockup-contract.md) | 149 | 9,082 | Bundled UI/UX guide reference: business mockup contract. |
| [.claude/skills/oiloil-ui-ux-guide/references/checklists.md](.claude/skills/oiloil-ui-ux-guide/references/checklists.md) | 111 | 4,638 | Bundled UI/UX guide reference: checklists. |
| [.claude/skills/oiloil-ui-ux-guide/references/design-interview.md](.claude/skills/oiloil-ui-ux-guide/references/design-interview.md) | 396 | 21,559 | Bundled UI/UX guide reference: design interview. |
| [.claude/skills/oiloil-ui-ux-guide/references/design-preview-template.html](.claude/skills/oiloil-ui-ux-guide/references/design-preview-template.html) | 1,813 | 105,864 | Bundled UI/UX guide reference: design preview template. |
| [.claude/skills/oiloil-ui-ux-guide/references/design-psych.md](.claude/skills/oiloil-ui-ux-guide/references/design-psych.md) | 97 | 3,716 | Bundled UI/UX guide reference: design psych. |
| [.claude/skills/oiloil-ui-ux-guide/references/design-spec-template.md](.claude/skills/oiloil-ui-ux-guide/references/design-spec-template.md) | 173 | 6,635 | Bundled UI/UX guide reference: design spec template. |
| [.claude/skills/oiloil-ui-ux-guide/references/extended-dimensions.md](.claude/skills/oiloil-ui-ux-guide/references/extended-dimensions.md) | 207 | 10,529 | Bundled UI/UX guide reference: extended dimensions. |
| [.claude/skills/oiloil-ui-ux-guide/references/icons.md](.claude/skills/oiloil-ui-ux-guide/references/icons.md) | 41 | 1,630 | Bundled UI/UX guide reference: icons. |
| [.claude/skills/oiloil-ui-ux-guide/references/interaction-psychology.md](.claude/skills/oiloil-ui-ux-guide/references/interaction-psychology.md) | 164 | 7,811 | Bundled UI/UX guide reference: interaction psychology. |
| [.claude/skills/oiloil-ui-ux-guide/references/review-template.md](.claude/skills/oiloil-ui-ux-guide/references/review-template.md) | 60 | 1,855 | Bundled UI/UX guide reference: review template. |
| [.claude/skills/oiloil-ui-ux-guide/references/style-families/brand-driven.md](.claude/skills/oiloil-ui-ux-guide/references/style-families/brand-driven.md) | 71 | 3,885 | Bundled UI/UX guide reference: brand driven. |
| [.claude/skills/oiloil-ui-ux-guide/references/style-families/brutal.md](.claude/skills/oiloil-ui-ux-guide/references/style-families/brutal.md) | 56 | 2,437 | Bundled UI/UX guide reference: brutal. |
| [.claude/skills/oiloil-ui-ux-guide/references/style-families/editorial.md](.claude/skills/oiloil-ui-ux-guide/references/style-families/editorial.md) | 58 | 2,337 | Bundled UI/UX guide reference: editorial. |
| [.claude/skills/oiloil-ui-ux-guide/references/style-families/index.md](.claude/skills/oiloil-ui-ux-guide/references/style-families/index.md) | 39 | 3,136 | Bundled UI/UX guide reference: index. |
| [.claude/skills/oiloil-ui-ux-guide/references/style-families/modern-minimal.md](.claude/skills/oiloil-ui-ux-guide/references/style-families/modern-minimal.md) | 58 | 2,556 | Bundled UI/UX guide reference: modern minimal. |
| [.claude/skills/oiloil-ui-ux-guide/references/style-families/playful.md](.claude/skills/oiloil-ui-ux-guide/references/style-families/playful.md) | 62 | 2,676 | Bundled UI/UX guide reference: playful. |
| [.claude/skills/oiloil-ui-ux-guide/references/style-families/premium-luxury.md](.claude/skills/oiloil-ui-ux-guide/references/style-families/premium-luxury.md) | 61 | 2,739 | Bundled UI/UX guide reference: premium luxury. |
| [.claude/skills/oiloil-ui-ux-guide/references/style-families/tech-cyberpunk.md](.claude/skills/oiloil-ui-ux-guide/references/style-families/tech-cyberpunk.md) | 64 | 2,958 | Bundled UI/UX guide reference: tech cyberpunk. |
| [.claude/skills/oiloil-ui-ux-guide/references/style-families/warm-content.md](.claude/skills/oiloil-ui-ux-guide/references/style-families/warm-content.md) | 62 | 2,349 | Bundled UI/UX guide reference: warm content. |
| [.claude/skills/oiloil-ui-ux-guide/references/system-principles.md](.claude/skills/oiloil-ui-ux-guide/references/system-principles.md) | 79 | 5,332 | Bundled UI/UX guide reference: system principles. |

## Generated local files

- `.venv/`: ignored Python environment.
- `.env`: ignored copy of the configuration template; no real keys added.
- `frontend/node_modules/`, `frontend/dist/`, and TypeScript build metadata: ignored dependencies and build output.
- `systems/erp/erp.db` and other local SQLite files: generated state, not source.
- `.sandbox/`: generated scenario copies. The preview uses a fresh `rosy-baseline` world.
- Python/pytest caches and any LLM cache database: disposable local state.
- `.git/`: verified shallow history and remote configuration; no GitHub credentials stored.
- `LOCAL_STATUS_REPORT.md` and `FILE_REPORT.md`: new, uncommitted reports.

Original source files and existing guides are unchanged. No implementation was started.
