# Tariffic: local setup and status

> Historical setup snapshot at 3030c45. The demo has since been updated on codex/judge-demo; see [the current demo guide](docs/DEMO.md).

Checked on October 24, 2026. Source: `likhithramesha/tariffic`, branch `main`, commit `3030c4557c015da959e011fdd4f7e4d99a54acc5`.

The project is ready for local, scripted use. All 315 tests pass, the frontend builds, and the clean scripted scenario passes 66/66 checks. The local screen is running at [http://127.0.0.1:8000](http://127.0.0.1:8000). No application code, existing documentation, dependency declarations, or lockfiles were changed.

## What was set up

- Retrieved all 180 tracked files through the authenticated GitHub plugin; every file matches its original Git object hash.
- Created a shallow local Git checkout at the exact original commit, with `main` tracking `origin/main`. Only the current commit is present in local history. Git's object-integrity check passes.
- Set `origin` to your fork and `upstream` to `ParthGala2k/tariffic`. Rechecked the fork after setup: its head still matches this checkout.
- Created `.venv` with Python 3.12.11 and installed the 63 resolved Python packages from `requirements.txt`.
- Installed the frontend from `package-lock.json` with Node 22.23.1 / npm 10.9.8 and built `frontend/dist`.
- Copied `.env.example` to the ignored local `.env`. Model names and API keys remain blank; the app reports no live model configured.
- Rebuilt the ignored local ERP database, ran the tests, and checked the scripted clean scenario.
- Prepared a fresh `rosy-baseline` sandbox for the preview. Its 31 initial checks pass; no agent run is active, and there are no pending decisions.
- Added this report and [the full file inventory](FILE_REPORT.md) as local, uncommitted documents.

The playbook is your testing reference: [docs/PLAYBOOK.md](docs/PLAYBOOK.md). The handover was read and checked against the current code: [HANDOVER.md](HANDOVER.md). Both are preserved.

## What the project does

Tariffic is a finance-workflow prototype for Brewline, a fictional espresso-machine importer. Three agents handle trade compliance, cost accounting, and revenue accounting. They read records, use deterministic arithmetic tools, propose journal entries, and ask for judgment. Human roles approve decisions; a single gateway writes the Beancount ledger. Work items, questions, rules, challenges, and the audit log are stored in SQLite. A React screen sits over the FastAPI backend.

The current demonstration uses synthetic records and local system views. Approving a customer credit updates the local books; it does not create a Stripe credit note or move money through a bank.

## Current build status

| Area | Current state | Evidence and limit |
|---|---|---|
| Phases 1–3 | Implemented and locally passing | Existing tests cover the three seats, work graph, approvals, posting, invariants, contracts, and challenges. Live-model behavior was not retested. |
| Human approval and gateway | Implemented | Permission, gateway, workflow, and append-only audit tests pass. |
| Evidence-based pushback | Implemented | The challenge tests pass: opinion and authority alone cannot revise amounts; admitted evidence is checked. |
| Screen | Built and locally viewable | The actual home screen loads, shows all five checks holding, 31/31 tie-outs, and the recorded-run disclosure. |
| Phase 4: reusable learning | Incomplete | The lawyer can propose RULE-001. `desk/policies.py`'s distributor executor still raises `PolicyError`; automatic Northwind handling is absent. |
| Phase 5: decision forensics | Partial foundations | History, briefs, and waiver decisive facts exist. Removing facts and rerunning policies to establish causality is not implemented. |
| Phase 6: external integrations | Not connected | `desk/sync.py` reads local files. The gateway writes Beancount. Stripe/Increase crosswalks and Mockoon configuration are fixtures, not active integrations. |

The next feature in the handover is Phase 4. No Phase 4–6 work was started.

## Verification performed here

| Check | Result |
|---|---|
| Complete pytest suite | **315 passed in 14.40 seconds** |
| Frontend TypeScript and Vite build | **Passed**; 1,956 modules transformed |
| Base Beancount ledger validation | **Passed** |
| Clean scripted `rosy-baseline` scenario | **PASS: 66/66 checks**, three journal entries posted inside its sandbox |
| Fresh preview sandbox | **31/31 initial checks**, zero failures |
| Local HTTP smoke check | `/`, `/health`, `/status`, `/issues`, `/attention`, `/invariants`, `/scenarios/active`, and `/openapi.json` all return 200 |
| Browser inspection | Home screen rendered and visually inspected |
| Source integrity | All 180 imported file hashes verified; original commit reproduced exactly; no tracked-file differences |

The baseline scenario's legacy scorer reports three informational misses because it is not scenario-aware: the clean world has no planted forecast mistake to correct, and Harbor remains at the question stage. The scenario's own checks pass. The README/handover still quote an older 65-check total.

The handover's claims about earlier live-model runs are historical evidence. This setup used scripted clients and did not make paid model calls.

## Issues to keep in view

1. **Terminal Git authentication is still missing.** Plugin access works, and the checkout is current with your fork. Ordinary `git fetch` / `git pull` cannot authenticate yet. The original private upstream also returns 404 through the connection, so newer upstream changes cannot be independently compared. This was a plugin-based checkout, not a successful conventional clone/pull.
2. **Frontend dependency audit: four affected packages, one high and three moderate.** The packages are Vite, esbuild, react-router, and react-router-dom. The package manager proposes major-version upgrades. No upgrade or automatic audit fix was applied. Advisory examples: [Vite path traversal](https://github.com/advisories/GHSA-4w7w-66w2-5vf9), [esbuild development server](https://github.com/advisories/GHSA-67mh-4wv8-2f99), [React Router redirect](https://github.com/advisories/GHSA-wrjc-x8rr-h8h6). These are dependency findings, not a demonstrated exploit of this app.
3. **Vite development proxy omissions.** `frontend/src/lib/api.ts` calls `/issues` and `/simulate/refund-arrives`, but `frontend/vite.config.ts` does not proxy those prefixes. The built UI served by FastAPI works; the separate `npm run dev` path needs this addressed before relying on it for the event screen. This is a static code finding; the Vite dev path was not exercised.
4. **The screen's simulate flow stops at Harbor's question.** The waiver-close step exists in the Phase 3 CLI flow. The lawyer is scripted even with a human controller.
5. **Two documented correctness limits remain.** The pending-refund scenario intentionally expects an INV-2 false alarm; `score.py` is not scenario-aware. Live onboarding can miss the bad-forecast flag, according to the handover; that behavior was not reproduced here.
6. **External sandbox cleanup is still pending.** The handover records a pre-existing Increase refund deposit, a leftover Stripe credit note, and illustrative dates needing alignment. No external account was changed, and those remote records were not inspected.
7. **Reproducibility is uneven.** Frontend versions have a lockfile. Most Python requirements are unpinned, so a future installation may resolve different versions.

## Using this checkout on macOS

The preview is already running on port 8000. To start it again after stopping it:

```bash
cd tariffic
.venv/bin/python -m desk serve --host 127.0.0.1 --port 8000
```

The active scenario is held in memory; restarting the server does not retain that selection. Use the screen's scenario controls or event simulation to prepare another demo world. Without a configured model and key, the app identifies its run as a recorded replay.

For checks, stop the preview first because the tests and server use shared local scenario folders:

```bash
.venv/bin/python -m pytest -q tests
cd frontend
npm run build
cd ..
.venv/bin/bean-check systems/books/main.beancount
.venv/bin/python -m desk scenario scenarios/rosy-baseline.yaml --llm scripted
```

To enable a live model later, fill the provider, model, optional fast model, and matching API key in the ignored `.env`, then restart the server. No credentials were retrieved or saved during setup.

## Scope preserved

No features implemented, fixes applied, commits created, pushes made, financial actions taken, or external sandbox records changed. Existing source files, the playbook, and the handover match the fork. This report and the inventory are the only new reviewable project documents; environments, databases, build output, and scenario copies are local generated files.
