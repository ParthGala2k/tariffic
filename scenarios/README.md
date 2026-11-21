# Scenarios

A scenario is one world for the desk to work, described in YAML, run in a sandbox.

```
python -m desk scenario scenarios/rosy-baseline.yaml              # scripted seats, no model needed
python -m desk scenario scenarios/rosy-baseline.yaml --llm live   # the real model
python -m desk scenario scenarios/rosy-pending-refund.yaml        # tie-outs only (run: null)
```

The runner copies `data/`, `systems/` and `personas/` into `.sandbox/<name>/`, applies the
scenario's patches there, rebuilds the ERP, syncs the golden tables, and ties the books out
against every other system of record. If the scenario has a `run:` block it then works the
world with the desk and ties out again. The repo's own files are never written. The report is
printed and saved as `report.txt` and `report.json` in the sandbox.

## Files

| Scenario | World | Desk run |
|---|---|---|
| `rosy-baseline.yaml` | Both planted mistakes removed; one refund of 14,317.00 arrives | onboarding, invariants (none fire), deposit, split, Bean Street credit, Harbor question |
| `rosy-pending-refund.yaml` | Same clean world a month earlier: entries liquidated, no notice, no deposit | none; documents one known INV-2 false alarm |

## Writing one

```yaml
name: my-scenario                 # also the sandbox folder name
description: one paragraph
patches:                          # applied in order to the sandbox copy
  - file: data/views/customs/cbp_entries.json
    json_set: [{where: {invoice_no: BRK-INV-25004}, set: {"lines.0.amount": 3000.0}}]
    json_delete: [{where: {invoice_no: BRK-INV-25010}}]
  - file: data/views/bank/transactions.csv
    csv_set: [{where: {txn_id: BNK-0007}, set: {amount: "6900.00"}}]
    csv_delete: [{where: {txn_id: BNK-0026}}]
    recompute_running_balance: {balance: balance_after, amount: amount, direction: direction, credit_value: credit}
  - file: systems/books/history/2025.beancount
    replace: [{old: "exact text", new: "replacement", count: 1}]
  - file: data/views/customs/cbp_refunds.json
    json_write: []
  - file: data/documents/inbox/2025-10-14-cbp-refund-notice.md
    remove_file: true
expect:                           # checked before the desk runs
  invariant_violations: 0
  unrecorded_deposits: [BNK-0026]
  ledger_before_run: {Assets:Bank:Increase:Operating: 331058.00}
run:                              # omit, or `run: null`, for tie-outs only
  llm: scripted                   # scripted | live (overridden by --llm)
  controller: scripted            # scripted | human (the API's run body can override)
  steps: [onboarding, invariants, deposit, split, credit:CUST-001, question:CUST-002]
  expect:
    invariant_violations: 0
    work_items_opened_by_invariants: []
    posted_entries: 3
    questions_to: [human_lawyer]
    ledger_after_run: {Assets:Receivable:TariffRefund: 0.00}
    scorer: {phase: 3}            # reported, never a verdict: the scorer is not scenario-aware
known_issues: [one paragraph each; printed with the report]
```

With `controller: human` every money entry waits for a person: the seat proposes, the run
blocks, and the API's approve / send-back / reject endpoints move it on (the UI's Needs-you
cards and Decision page are that person's screen). Onboarding stays scripted. The demo's
`POST /simulate/refund-arrives` builds this world and runs it with a human controller.

Steps: `onboarding` (the seats propose their rules, the controller approves), `invariants`
(scan; any work item opened is worked by Seat 1), `deposit` (Seat 1 records the Treasury
deposit), `split` (Seat 2 splits it), `credit:<customer>` (Seat 3 proposes the credit),
`question:<customer>` (Seat 3 must ask, not decide). Scripted mode replays the fixed transcripts
in `desk/scripted.py`, so it only fits worlds whose amounts match the base world; use
`--llm live` for anything else.

## What the tie-outs compare

| Group | Left | Right |
|---|---|---|
| books / bank | ledger operating account | bank feed closing balance less deposits the desk has not booked yet |
| | bank wires out | ERP shipments received at cost |
| | bank credits from customers | billing invoices marked paid |
| books / customs | ledger duty postings (by programme) | CBP 7501 duty lines |
| | ledger refund receivable | refundable duty on liquidated entries less refunds already booked |
| | Treasury deposit | refund notice total; notice principal vs CBP refundable duty on its entries |
| books / ERP | ledger inventory | ERP landed value on hand less refunded duty on parts on hand |
| | ledger PO receipts | ERP qty received x unit cost |
| | ledger cost of parts sold | ERP consumption x unit cost |
| | ledger POL-001 capitalisation | ERP qty on hand x duty per unit |
| books / billing | ledger sales + surcharge | billing invoice totals; trade receivable vs unpaid invoices |
| refund split | to COGS + to inventory | principal |
| integrity | trial balance = 0, bean-check, invariant count | the scenario's expectations |
