# Brewline Tariff Desk — Agent Integration Guide (agents only)

**Scope:** the three Brewline agents as built in CREAO. This does **not** design or modify the
desk's existing workflow — it only describes how to drive the three agents from outside and
what each turn looks like. Source of truth: `CREAO_AGENTS_001d1b7f.md` §3–5 (agent specs),
§9 (what the desk asked Creao for), and the appendix tool schemas; plus the three registered
app manifests under `/home/user/agent/apps/`.

The three agents are registered. The dependency "check" summaries that reported Agent 2 and
Agent 3 missing were stale — all three are present now.

| Seat | Agent id (app) | App name | Tickets owned |
|---|---|---|---|
| seat1 | `876e3e03-3bf3-42e3-9c1f-6d49288f3c6e` | Tariffs & Refunds (Brewline) | 6 tariff/refund kinds |
| seat2 | `7cdf1832-18f9-413f-9f3f-3e6788fe9e8d` | Sourcing analyst — Brewline Tariff Desk | 2 sourcing kinds |
| seat3 | `d96d0e79-1492-43ec-8ce6-0e1a1aa73a6c` | stock-customers | 6 stock/customer kinds |

---

## 1. Agent id / endpoint / API key

The platform does **not** expose a per-agent REST endpoint or a per-agent API key in the
in-app model. An agent is addressed by its **app id (the UUID above) / app name** and run
by the signed-in account, not by a URL+token.

- **In-app (how these are actually invoked):** `start_app_run({ name })` with the form
  values, a scheduled run (`manage_app_schedule`), or a workflow dispatch
  (`submit_workflow_run`). No key; the platform authenticates the signed-in user.
- **External / programmatic:** the closest platform-provided path is the **Developer API**
  ("programmatic access to run agents") or the **Rooms API** (post into a team room that
  triggers the room's agent), both authenticated with an **Account API key stored under
  Secrets**. The endpoint contract for those is **not mounted in this sandbox**, so I will
  not invent endpoints, request bodies, or key names here. See
  https://agent.docs.creao.ai/developer-api/overview and `/developer-api/rooms`. Until the
  desk reads that contract, treat in-app `start_app_run` as the integration surface and the
  Developer/Rooms API as the external path to confirm.

**Bottom line for the desk:** call the agent by app id/name through `start_app_run`
(in-app) or the Developer API (external, Account key under Secrets). There is no
agent-scoped URL or token to hand the desk's code.

---

## 2. Input shape for a turn (per agent)

A "turn" = one `start_app_run` with the agent's **form fields** filled. The form fields are
the only caller-supplied inputs; they carry the ticket. The "conversation so far" and the
"tool definitions" from §9 are **not** caller-supplied — see §4 and §5 below.

### Agent 1 — Tariffs & Refunds (`876e3e03…`, seat1)
Form fields (the turn input):
- `ticket_kind` *(select, required)* — one of `WI-REFUND-DEPOSIT`,
  `WI-FORECAST-CORRECTION`, `WI-TARIFF-INCREASE`, `WI-DUTY-RECONCILIATION`,
  `WI-ONBOARD-POLICY`, `WI-ONBOARD-REVIEW`.
- `ticket_id` *(string, required)* — the work-item id, e.g. `WI-REFUND-DEPOSIT`.
- `context` *(textarea, optional)* — the ticket body / the event that opened it.

### Agent 2 — Sourcing (`7cdf1832…`, seat2)
Form fields:
- `ticket_kind` *(select, required)* — `WI-SOURCING-REVIEW` or `WI-SOURCING-WATCH`.
- `ticket` *(textarea, required)* — the part at risk, the tariff change/exposure, and
  current volumes.

### Agent 3 — Stock & Customers (`d96d0e79…`, seat3)
Form fields:
- `ticket` *(textarea, required)* — the work item: its kind and the refund/shipment/
  customer/invoice refs it names, plus any attached inbox document path.
- `ticket_kind` *(select, required)* — one of `WI-REFUND-SPLIT`, `WI-CUST-001-CREDIT`,
  `WI-CUST-002-QUESTION`, `WI-CUST-002-CLOSE`, `WI-CUST-PASSTHROUGH`, `WI-ONBOARD-POLICY`.

**"Conversation so far":** the platform keeps the run's message history; it is not passed by
the caller. Cross-run state (e.g. Agent 3 refining a proposal after a document arrives) is
carried by **`persistFields`**, not by re-sending history. If the desk needs to feed a prior
tool result or a new document into the next turn, it goes through a form field (`context` on
Agent 1, `ticket` on Agents 2/3) or a new run with the persisted fields.

---

## 3. Output shape (per agent)

Each run returns the agent's final message **plus the `persistFields`** (the durable result
the platform records for the session). Tool calls the agent made come back as
`{ "name": <tool>, "arguments": {…} }`; tool results are returned to the agent on the next
turn (see §5).

| Agent | `persistFields` (durable result) | Terminal tool calls |
|---|---|---|
| Agent 1 | `ticket_kind`, `ticket_id`, `action`, `to_role` | `propose` (journal entry) or `ask` (`human_cfo`/`human_lawyer`); may also `propose_policy`/`apply_policy` |
| Agent 2 | `ticket_kind`, `part_id`, `action`, `recommended_candidate`, `saving_per_year` | `propose_sourcing` (recommendation, **no journal entry**) or `ask` |
| Agent 3 | `ticket`, `ticket_kind`, `action`, `amount` | `propose` (entry; credit **must name invoices** in `detail.credit_notes`; refund split **must carry per-shipment breakdown**) or `ask` (lawyer); may `apply_policy`, `revise_proposal` (document-only) |

The `propose`/`ask`/`propose_sourcing` calls are the agent's "finish" — they are work-kind
tools that submit the ticket's decision. `propose_sourcing` is deliberately **not** a journal
entry; it is advice for the CFO.

---

## 4. Room membership — polled or pushed?

**Pushed, not polled.** None of the three agents polls a ticket queue. The desk routes each
ticket to its owning agent by starting a run with the ticket kind + body. A team **room** can
be used as the queue (post the ticket, @mention or room-shortcut the agent), but even there
the agent reacts to a **dispatch/mention**, not to polling. If the desk wants "the room is a
work queue" (§1), the workable pattern is: a router (desk or human) reads an inbound ticket,
decides the kind, and `start_app_run`s the owning agent — the agent never listens on its own.

**One call or session?** **One run per ticket.** A run may be multi-step internally (the
agent follows its SKILL.md procedure), but the unit of dispatch is one run. Refinement — e.g.
Agent 3's `revise_proposal` after a signed letter arrives — is a **new run** that reuses the
prior session's `persistFields` plus the new evidence, not a continuation of the old call.

---

## 5. Tools — declared per agent or per request? And the call/result loop

**Per agent, not per request.** Each agent's tool set is fixed in its **SKILL.md** (the
system prompt, which contains the §3–5 tool tables in prose). The registered app manifest
(`apps/<id>.json`) carries only `formFields` + `persistFields` — **there is no per-request
tool list and no `tools`/`connectors` field in these three manifests**. So the caller never
passes tool definitions; the agent already knows its tools.

**Important — these tools are desk-hosted, not CREAO built-ins.** `run_sql`, `propose`,
`scrape_suppliers`, `tariff_rates_for_origin`, `compare_sourcing`, `split_refund`,
`revise_proposal`, etc. are the desk's Python implementations (appendix schemas), not CREAO
platform tools. The agent manifest declares **no MCP connectors** for them. Two workable
patterns to actually execute a tool call the agent emits:

1. **Desk-driven request/response loop (closest to as-built).** The agent's turn returns a
   tool call `{name, arguments}`; the desk runs its implementation and submits the
   `tool_result` back as the next turn's input (through the `context`/`ticket` field or a
   new run with the persisted fields). The agent never touches the books, DB, or files
   directly — the desk does, exactly as §9 requires.
2. **Expose the tools as MCP connectors wired to the agent.** The platform supports a
   `connectors:` field at registration (used for workflow agents; not set on these three).
   The desk would stand up an MCP server exposing the tool set and wire it per agent so
   CREAO executes calls in-loop. This wiring is **not present in this build** — if the desk
   wants in-loop execution it must add it; I am not inventing the connector URLs.

Either way the agent emits `{name, arguments}` and receives `tool_result` on the next turn;
the difference is only who runs the implementation. Pick one and be consistent across all
three agents.

---

## 6. Agent 2 — who runs the Apify actor?

**The user (desk) hosts `scrape_suppliers`; Creao does not call Apify on the agent's
behalf.** There is no Apify integration in this account's connected integrations, and the
agent manifest declares no Apify connector. So:

- `scrape_suppliers` is a **desk-hosted tool** that wraps the Apify actor run. The Apify
  API token lives in the **desk's** own secret store — not in CREAO Secrets and never in the
  agent. The agent calls `scrape_suppliers(part_description, hts_code?, countries[],
  max_results?)` like any other desk tool; the desk executes the actor and returns the
  supplier rows (name, country, city, unit price, MOQ, lead time, certifications, **source
  URL**).
- **Caching is the desk's job:** §4 promises "results are cached so a repeated run is free
  and identical." That cache lives with the desk's `scrape_suppliers` implementation, keyed
  by the actor inputs.
- **Demo vs production is transparent to the agent:** for the demo the directory is served
  locally (`python systems/suppliers/serve.py` → `http://127.0.0.1:3002`) and the same actor
  scrapes it; in production the desk points the actor at real directories. Nothing about the
  agent changes. The page-level "names and prices are invented for the demo" disclaimer must
  be preserved by the desk and must **not** be used by the agent as licence to invent
  suppliers.

If the desk would rather have Creao run Apify directly, that requires connecting an Apify
integration that is **not currently available** on this account — say so and use the
desk-hosted pattern above until/unless one exists.

---

## 7. Tools the desk must host/build (checklist)

**All tools are desk-hosted** (§9: "I will wire the tools to the desk's existing
implementations. The agents never touch the books, the database or the files directly.").
The seven marked **to build** in the spec are not implemented yet — the rest already exist
in `desk/tools/__init__.py` (per the appendix) and only need wiring.

To-build (must be implemented **and** hosted by the desk):

- [ ] **`tariff_change_impact`** — Agent 1, *compute* — args `program_id`, `new_rate_pct`,
      `effective_from` → affected entries/shipments, additional duty per entry, total.
- [ ] **`scrape_suppliers`** — Agent 2, *read* — args `part_description`, `hts_code?`,
      `countries[]`, `max_results?` → per-supplier rows incl. **source URL**; wraps the Apify
      actor (desk-hosted, desk-held Apify token, desk-side cache).
- [ ] **`tariff_rates_for_origin`** — Agent 2, *compute* — args `hts_code`, `origin_country`
      → every programme applying to that commodity from that country + combined rate, from
      `data/reference/hts_programs.csv`.
- [ ] **`compare_sourcing`** — Agent 2, *compute* — args `part_id`, `current_vendor_id`,
      `candidates[]` → unit price, duty rate, duty/unit, landed cost/unit, saving per
      unit/shipment/year, all `Decimal`. (The agent never does this arithmetic itself.)
- [ ] **`propose_sourcing`** — Agent 2, *work* — args `part_id`, `recommendation`,
      `candidates`, `saving`, `rationale`, `evidence` → submits the recommendation. **Not a
      journal entry** — advice for the CFO.
- [ ] **`stock_position`** — Agent 3, *compute* — args `part_id?`, `shipment_id?` → on-hand,
      consumed, in-transit per part/shipment with landed value of each.
- [ ] **`passthrough_on_increase`** — Agent 3, *compute* — args `customer_id`, `program_id`,
      `new_rate_pct` → surcharge passable to this customer per invoice/machine under the
      contract terms.

Already exist (wire only): `run_sql`, `read_doc`, `get_policies`, `sum_duties`,
`match_deposit`, `reconcile_duty_paid`, `breakdown_receivable`, `propose`, `ask`,
`propose_policy`, `apply_policy` (Agent 1); `ask` (shared); `search_precedents`,
`split_refund`, `surcharge_on_invoices`, `revise_proposal` (Agent 3).

---

## 8. Ticket-kind → agent routing table

| Ticket kind | Owning agent | App id | Seat |
|---|---|---|---|
| `WI-REFUND-DEPOSIT` | Agent 1 — Tariffs & Refunds | `876e3e03…` | seat1 |
| `WI-FORECAST-CORRECTION` | Agent 1 | `876e3e03…` | seat1 |
| `WI-TARIFF-INCREASE` | Agent 1 | `876e3e03…` | seat1 |
| `WI-DUTY-RECONCILIATION` | Agent 1 | `876e3e03…` | seat1 |
| `WI-ONBOARD-REVIEW` | Agent 1 | `876e3e03…` | seat1 |
| `WI-SOURCING-REVIEW` | Agent 2 — Sourcing | `7cdf1832…` | seat2 |
| `WI-SOURCING-WATCH` | Agent 2 | `7cdf1832…` | seat2 |
| `WI-REFUND-SPLIT` | Agent 3 — Stock & Customers | `d96d0e79…` | seat3 |
| `WI-CUST-001-CREDIT` | Agent 3 | `d96d0e79…` | seat3 |
| `WI-CUST-002-QUESTION` | Agent 3 | `d96d0e79…` | seat3 |
| `WI-CUST-002-CLOSE` | Agent 3 | `d96d0e79…` | seat3 |
| `WI-CUST-PASSTHROUGH` | Agent 3 | `d96d0e79…` | seat3 |

**Known collision to resolve — `WI-ONBOARD-POLICY`:** this kind appears in **both** Agent 1's
and Agent 3's `ticket_kind` select, but they answer different questions (Agent 1: *which
programmes are refundable?*; Agent 3: *what are the stock-split and explicit-clause rules?*).
The router cannot disambiguate by kind alone. Pick one before wiring: either (a) disambiguate
by the ticket's question/domain at dispatch time and route to the matching seat, or (b) rename
to seat-scoped kinds (e.g. `WI-ONBOARD-POLICY-REFUND` / `WI-ONBOARD-POLICY-STOCK`) and update
both agents' selects. The spec itself flags the seat mapping as intended-not-current
(appendix: the code's `seat1/seat2/seat3` do not yet match these three agents), so this is
consistent with a known re-wire.

---

## 9. Open / unknown (said plainly, not invented)

- **No per-agent endpoint or API key** exists in the in-app model; external run path is the
  Developer/Rooms API with an Account key under Secrets — endpoint contract not mounted here,
  not enumerated.
- **No tool-execution wiring in the manifests.** As built, the brewline tools are not CREAO
  built-ins and not MCP connectors; the desk must drive the call/result loop (§5 pattern 1)
  or stand up an MCP connector (§5 pattern 2). Neither is present yet.
- **No Apify connector** on this account; `scrape_suppliers` is desk-hosted (§6).
- **Seat mapping mismatch** (appendix): the code's current `seat1/2/3` do not match these
  three agents; the `WI-ONBOARD-POLICY` collision above is part of that.
- The four sourcing to-build tools (`scrape_suppliers`, `tariff_rates_for_origin`,
  `compare_sourcing`, `propose_sourcing`) have **prose argument lists in §4 but no appendix
  `input_schema` JSON** — the desk must author their JSON Schema from the prose before
  registering, matching the argument names exactly.