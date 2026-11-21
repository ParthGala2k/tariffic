# Sandbox Storage Plan

> **Status 2026-10-24.** The sandboxes were seeded as described below and the id crosswalks live in
> `data/views/*/`. The desk does not call Stripe, Increase or Mockoon yet: `desk/sync.py` reads
> the local views and `desk/gateway.py` writes Beancount only. Wiring them is Phase 6; see
> `HANDOVER.md` §8 and `docs/PLAYBOOK.md` §3.7.

How Brewline's data is stored in each tool. Companion to `tariff-desk-build-plan.md` (sections 1.4–1.5).

## The rule

1. A seeded simulator writes **one true story** to `data/truth/` (one CSV per entity plus `world.json`) and `data/answer_key/answer_key.json`.
2. A loader per tool pushes **that tool's view** (with planted disagreements) using the tool's **native IDs**.
3. The desk's `crosswalk` table is the only place truth IDs meet system IDs. The agent has **no read path** to `data/truth/` or `data/answer_key/`.
4. One sandbox set serves every scenario. A scenario is a data load tagged `scenario_id`; a reset is a re-run of the loader with the same seed.

## Per tool

| Tool | Brewline system | Where data lives | Objects stored | IDs carried | Load / reset |
|---|---|---|---|---|---|
| **Increase** sandbox | Bank | Increase-hosted sandbox; snapshot in `data/views/bank/transactions.csv` | 1 Entity, 1 Account "Brewline Operating"; outbound ACH per customs duty payment; 1 simulated inbound wire = refund deposit (principal + interest, 6 entries bundled) | Native `transaction_…`, `inbound_wire_…`. Memo carries only what Treasury would send: payer name, amount, short text. **No internal IDs** | Loader via API with idempotency keys; reset = new Account named `brewline-<seed>` |
| **Stripe** sandbox | Billing | Stripe-hosted sandbox; fixtures in `data/views/billing/*.json` | Customers (Bean Street, Harbor, 2 distributors), Products/Prices (machines, "Tariff surcharge"), Invoices with machine line + surcharge line; Credit Notes written **only by the gateway** | Native `cus_ / in_ / il_ / cn_`; `metadata`: `contract_id`, `shipment_ids`, `tariff_program`, `surcharge_basis` (Brewline tags its own invoices, so this is realistic) | Loader via API, idempotency key = truth ID; reset = new Stripe sandbox |
| **Beancount + Fava** | Books | Plain text in `systems/books/` (git-tracked) | `accounts.beancount` chart; `history/2025-MM.beancount` (12 months); `posted/desk.beancount` = gateway-only append file | Per-txn metadata `work_item`, `entry_no`, `shipment`, `tariff_program`, `source`; tags `#tariff #refund`; link `^WI-0001` | Simulator writes history; gateway appends; `bean-check` on every write; reset = `git checkout systems/books/`; view with `fava systems/books/main.beancount` |
| **Mockoon** | Customs (CBP) | `systems/mockoon/customs.mockoon.json`; data buckets fed from `data/views/customs/*.json` | `GET /cbp/entries` (7501-style: entry_no, HTS line, Ch.99 program, duty, liquidation date/status); `GET /cbp/refunds` (refund_id, entries[], principal, interest); | CBP 11-digit entry numbers | Simulator exports JSON; `mockoon-cli start --data systems/mockoon/customs.mockoon.json --port 3001`; reset = swap data file |
| **SQLite** (we build) | Mini ERP | `systems/erp/brewline.db` (gitignored, regenerated); `systems/erp/schema.sql` | vendors (with country), parts, purchase_orders, shipments (origin country, incoterm, entry_no), receipts, inventory_layers (FIFO), machines/BOM, builds, sales_orders, customers, contracts index | Truth IDs `SHP- PO- MCH- CUST-` (the ERP is Brewline's own, so it legitimately knows them) | Seeded by simulator; reset = delete + reseed |
| **Text files** | Contracts, inbox | `data/documents/contracts/*.md`, `data/documents/inbox/*.md` | Contracts (Bean Street explicit, Harbor ambiguous, 2 distributors); refund notice letter; bank memo; CFO email; signed waiver | YAML frontmatter: `doc_id`, `type`, `date`, `parties`, `sha256` | Templates + light LLM variation; reset = regenerate |
| **LLM people** | Controller, lawyer, CFO, auditor | `personas/<role>.md` prompts; `personas/scripts/<role>.yaml` fixed answers | Role prompt; scripted answers keyed by `question_id`; transcripts saved to desk DB | `persona_id`, `question_id` | Scripted mode for scoring; live mode for demo |
| **Desk DB** (we build) | The desk's own memory | `systems/desk/desk.db` (SQLite) | `work_items` (New → Proposed → Needs info → Approved → Posted / Rejected), `events` append-only incl. refused attempts, `facts` (source, pointer, value, hash), `decisions`, `rules` (versioned), `crosswalk`, `invariant_runs`, `conversations` | `WI- FACT- RULE-` | Only the app writes; append-only tables are never updated |
| **Truth + answer key** | Ground truth | `data/truth/` (CSVs + `world.json`), `data/answer_key/answer_key.json`, `heldout/` (different seed) | Every shipment, entry, payment, contract as it really happened; the planted mistakes; expected decisions | Truth IDs | Written once by the simulator; excluded from agent tools |
| **Rules tables** (real) | Reference | `data/reference/hts_programs.csv`, `data/reference/refund_policy.md` | Program → Ch.99 code (9903.01 / 9903.02 / 9903.03.01 / 232 / 301) → rate → effective dates → refundable?; accounting policy for split and interest | HTS code | Hand-curated from USITC / CBP / Big-4 guidance |
| **SEC EDGAR** (optional) | Proof slide | `data/reference/edgar/*.json` | Cached filing excerpts showing how real companies booked tariff refunds | CIK, accession no. | One-time fetch, read-only |
| **BenchRec** (optional) | Realism | `data/reference/benchrec/` | Real bank-memo string patterns used to format Increase memos | – | One-time download |
| **stripe-mock** (optional) | Offline billing | Runs from Stripe's spec fixtures | Stateless; returns spec-shaped responses, **not our data** | – | `stripe-mock -port 12111`; app flips base URL only if Wi-Fi dies |

Skipped per build plan: Lago, Odoo, ERPNext, Formance, Blnk, TigerBeetle, Prism, other benchmarks.

## Scenario → where its data is planted

| Scenario | Planted in | What the desk must find |
|---|---|---|
| Refund deposit arrives | Increase: 1 inbound wire (principal + interest); Mockoon `/cbp/refunds`: notice listing 6 entries | Match deposit ↔ notice ↔ 6 shipments; split principal vs interest |
| Steel tariff in forecast (planted mistake) | Mockoon entries carry Section 232 steel lines; `hts_programs.csv` marks 232 non-refundable; Beancount history has a forecast entry that includes them | Forecast counts only refundable programs |
| Duty paid vs Customs mismatch | bank duty payments ≠ `/cbp/entries` duty on an entry | Flag it, trust CBP, open a work item |
| Bean Street clear share | Stripe invoices with surcharge lines tagged by `shipment_ids`; contract text explicit | Share = surcharge collected on refunded entries → credit note via gateway |
| Harbor ambiguous share | Same Stripe shape; contract wording ambiguous | Question to lawyer; no proposal |
| Pushback | `inbox/`: CFO email (authority), signed waiver (evidence) | Only the uploaded document changes a decision |
| Learning loop | 2 distributor contracts with Harbor wording; lawyer's answer in `personas/scripts/lawyer.yaml` | Rule proposed → approved → next similar contract auto-handled |
| Audit query / decisive facts | Desk DB `events`, `facts` | Full history; the 2 decisive facts |
| New tariff announced, CFO asks impact on a country pair | New row in `hts_programs.csv`; ERP shipments + vendors by origin country; open POs | Exposure by origin → US pair; proposed forecast entry; nothing posted without approval |

## Repo layout

| Path | Holds |
|---|---|
| `data/truth/` | Layer 1 true story: one CSV per entity (spreadsheet-exportable) plus `world.json`. The desk cannot read it |
| `data/answer_key/` | Planted mistakes and expected results. Only the scorer reads it |
| `data/reference/` | `hts_programs.csv`, `refund_policy.md`, cached EDGAR / BenchRec |
| `data/documents/` | `contracts/`, `inbox/` |
| `data/views/{customs,bank,billing}/` | Each system's view, as the loader pushes it |
| `systems/books/` | Beancount files |
| `systems/mockoon/` | Mockoon environment, generated from `data/views/customs/` |
| `systems/erp/` | `schema.sql`, `seed.sql`, regenerated `brewline.db` (gitignored) |
| `systems/desk/` | `desk.db` (gitignored); schema is written at the event |
| `personas/` | Role cards + scripted answers |
| `docs/` | Plans and this file |
| `.env` | Increase, Stripe, LLM keys (gitignored) |
