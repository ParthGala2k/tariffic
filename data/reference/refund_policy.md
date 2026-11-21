---
doc_id: POL-001
type: accounting_policy
title: Tariff duties and refunds
effective: 2025-10-01
owner: Controller
---

# Accounting policy: tariff duties and refunds

Written from published Big-4 guidance on tariff refund accounting, simplified for Brewline. Rule-based seats apply this policy; the controller owns changes to it.

## 1. Duties paid

1.1 Import duties are a cost of the parts imported. Until 2025-09-30 Brewline expensed duties to `Expenses:COGS:Tariff` when paid to CBP. From 2025-10-01 duties on parts still on hand belong in `Assets:Inventory:Parts` (see 3). On 2025-10-01 the controller capitalized the duties on parts then on hand (57,972.00) into `Assets:Inventory:Parts`, crediting `Expenses:COGS:Tariff`.

1.2 The customs entry summary (CBP Form 7501) is the source of truth for duty amounts. Brewline pays CBP directly, so the amount paid must equal the 7501 line for line. Any difference between the books and the 7501 opens a work item; it is not plugged.

## 2. Refund recognition

2.1 Recognise a refund receivable only for duties under programs flagged `refundable = yes` in `data/reference/hts_programs.csv`. Section 232, Section 301 and Section 122 duties are never included in a refund forecast unless that table changes.

2.2 Recognise the receivable when the entry has liquidated and a refund notice, court order or CBP guidance makes the refund virtually certain. Unliquidated entries are disclosed, not booked.

2.3 On receipt of the deposit, clear the receivable. Any difference between the notice and the deposit opens a work item; it is not plugged.

2.4 Interest received on a refund is `Income:Interest:TariffRefund`. It is never passed to customers (see 4).

## 3. Split of the refund

3.1 Split each entry's refunded principal between parts already sold and parts still on hand, using the ERP's FIFO layers at the refund date:

    to_cogs       = principal x (qty consumed / qty received)
    to_inventory  = principal - to_cogs

3.2 `to_cogs` credits `Expenses:COGS:Tariff`. `to_inventory` credits `Assets:Inventory:Parts`.

3.3 Invariant: `to_cogs + to_inventory = principal` for every entry, and the sum over entries equals the deposit principal.

## 4. Customer pass-through

4.1 Whether a customer is owed part of a refund is a contract question, decided per contract by rule or by the lawyer. The desk may propose; it may not decide an ambiguous clause.

4.2 Explicit pass-through (CON-001 wording): credit the full tariff surcharge billed on machines whose parts came from refunded entries. Book `Income:Sales:TariffSurcharge` / `Liabilities:CustomerCredits`, then issue the credit note through the gateway.

4.3 Ambiguous wording (distributor template): hold, ask the lawyer, and apply the resulting rule to every contract with the same wording once the controller approves the rule.

4.4 A signed waiver or amendment overrides 4.2 and 4.3 for the invoices it covers. Only an uploaded document with matching parties and dates counts.

## 5. Controls

5.1 Only the gateway writes to the books or issues credit notes, and only after controller approval.

5.2 Every proposal cites a source document for every number. Opinion and authority, including the CFO's, do not change a proposal; evidence does.

5.3 Every proposal, approval, rejection and refused attempt is logged and cannot be edited.
