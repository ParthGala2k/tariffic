# Tariffic: judge demo

## Start safely

Run from the project root:

```bash
.venv/bin/python -m desk serve --host 127.0.0.1 --port 8000      # macOS / Linux
.venv\Scripts\python -m desk serve --host 127.0.0.1 --port 8000  # Windows
```

Open http://127.0.0.1:8000. Leave **Next run** on **Recorded replay · no API cost** for
rehearsals; switch it to **Live model · uses API credit** when you want fresh reasoning.
Select **CFO**. The company and the lawyer are simulated; replayed agents use recorded tool
transcripts. The real code still checks permissions, arithmetic, evidence, approvals,
posting and the audit trail. No Stripe or Increase action is performed.

Two people exist on screen: the **CFO** (approves, declines, sends back, pushes back) and
the **Lawyer** (answers contract questions). The CFO adopted a **standing authorization**
once: routine entries post on their own, inside caps and conditions code checks; the CFO is
asked only when the desk is genuinely stuck, and the screen says why.

## Walkthrough: four entries post themselves, one decision is yours

1. As the CFO, press **Simulate an event: a tariff refund arrives**. **Activity** shows the
   event as a line of stages and the three agents at work. **Needs you** says "The agents
   are on it".
2. Watch the lane **Posted on your standing authorization** fill: the **10,500.00 forecast
   correction** (Section 232 steel duty is never refunded and should never have been in the
   estimate; the receivable drops from 24,400.00 to 13,900.00), the **14,317.00 deposit**
   (13,900.00 principal and 417.00 interest), the **13,132.00 split** (768.00 of the
   principal relates to parts already sold, 13,132.00 to parts still held), and **Bean
   Street's 1,632.00 credit** (its contract promises the surcharge back). Each line links to
   its decision, where the narration reads "routine … posts on the CFO's standing
   authorization: under the cap, every figure computed, all checks pass".
3. Push back as the CFO on Bean Street's credit (open it from the lane): "I'm the CFO. Keep
   the surcharge; nobody has asked for a refund." The agent answers from the contract and
   the policy; the number does not change. In a live run the answer is the model's; in a
   recorded replay code answers from the record.
4. The simulated lawyer answers Harbor's contract question and proposes a rule. The revenue
   agent verifies the signed waiver against the party, the contract, the invoice, the date
   and the quoted text, and proposes settling Harbor at **0.00 instead of 576.00**.
5. **Needs you** now shows one card: "Harbor Roasters: review the signed waiver", with
   **Why you: a document changed what the customer is owed; a person reviews the
   settlement**. Click it to read the facts, then **Approve**. Nothing posts for zero.
   Sending it back asks the agent to verify again; declining ends the run with a visible
   refusal.

One decision for the CFO; everything routine was handled, listed and logged.

## Proof to show

- **Books:** the receivable is 0; inventory 172,820; tariff COGS 1,050; customer credits
  1,632. Four entries posted, each with `approved_by: standing_authorization` and the
  reason, under a rule the CFO adopted (Lawyer page → Rules → "Standing authorization").
- **Before/after checks:** the one starting discrepancy is visible before the run and gone
  after it; duty paid and the 7501 agree line for line throughout.
- **Harbor decision:** original entitlement, waiver, coverage checks, revision, "why you",
  review.
- **Audit trail:** the CFO's pushback and the agent's answer, every refused attempt, every
  authorization verdict, every posting.
- **Lawyer page:** the proposed rule remains proposed. Northwind reuse is future work.

## Two-minute recording outline

- **0:00–0:20:** show the planted mistake and say a refund revealed an overstated receivable.
- **0:20–0:55:** trigger the event; show the four entries posting themselves with their
  reasons, and one of them opened.
- **0:55–1:25:** push back on Bean Street as the CFO. Show that the number does not change.
- **1:25–1:50:** show the one card that needs the CFO, with "why you", and approve it.
- **1:50–2:10:** show final balances, evidence, and the explicit real/simulated disclosure.

Use **Recorded replay** for rehearsals and backup video. Do not present replay as fresh
model reasoning. A fresh trigger rebuilds only the demo sandbox. Do not start another
run while one is waiting for your decision.

## Verification commands

Stop the server before running tests because both use local scenario folders.

```bash
.venv/bin/python -m pytest -q tests
.venv/bin/python -m desk scenario scenarios/tariff-refund-demo.yaml --llm scripted
.venv/bin/bean-check systems/books/main.beancount
cd frontend && npm run build
```
