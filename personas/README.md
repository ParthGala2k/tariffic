# Personas

One role card per person the desk talks to, plus a scripted answer file for repeatable runs.

| File | Role | Can do |
|---|---|---|
| `controller.md` | Approver | Approve or reject work items and rules. Never posts directly |
| `lawyer.md` | Contract counsel | Answer contract questions; propose rules |
| `cfo.md` | Authority pushback | Instruct, insist, sign off. Never evidence |
| `auditor.md` | Asks "why" | Query the audit log and decisive facts |

Modes:
- **Scripted** (`scripts/<role>.yaml`): fixed answers keyed by `question_id`. Used for scoring. No LLM key needed.
- **Live**: the role card becomes the system prompt for an LLM. Used only in the demo.

Scripted answers are data. They are the ground truth for the learning loop and the pushback tests, so they must stay consistent with `data/answer_key/answer_key.json`.
