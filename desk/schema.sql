-- Desk DB: the system of work. Rebuilt by `python -m desk reset`.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS work_items (
  id             TEXT PRIMARY KEY,
  kind           TEXT NOT NULL,
  state          TEXT NOT NULL,
  seat           TEXT,
  parent_id      TEXT REFERENCES work_items(id),
  payload        TEXT NOT NULL DEFAULT '{}',   -- JSON: the proposal
  evidence       TEXT NOT NULL DEFAULT '[]',   -- JSON: source row / document ids
  proposed_by    TEXT,
  approved_by    TEXT,
  policy_id      TEXT,
  policy_version TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

-- Append-only audit log. Written in the same transaction as the change it records.
CREATE TABLE IF NOT EXISTS events (
  event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  at             TEXT NOT NULL,
  actor          TEXT NOT NULL,
  action         TEXT NOT NULL,
  subject        TEXT,
  allowed        INTEGER NOT NULL,             -- 1 permitted, 0 refused
  reason         TEXT,
  diff           TEXT,                         -- JSON {"before": ..., "after": ...}
  evidence       TEXT,                         -- JSON
  policy_version TEXT
);

CREATE INDEX IF NOT EXISTS events_subject_idx ON events(subject, event_id);

-- Append-only enforced by the database, not by convention.
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
  SELECT RAISE(ABORT, 'events is append-only: UPDATE blocked');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
  SELECT RAISE(ABORT, 'events is append-only: DELETE blocked');
END;

CREATE TABLE IF NOT EXISTS questions (
  id           TEXT PRIMARY KEY,
  work_item_id TEXT NOT NULL REFERENCES work_items(id),
  asked_by     TEXT NOT NULL,
  to_role      TEXT NOT NULL,
  text         TEXT NOT NULL,
  state        TEXT NOT NULL,                  -- OPEN | ANSWERED
  answer       TEXT,
  answered_by  TEXT,
  created_at   TEXT NOT NULL,
  answered_at  TEXT
);

CREATE TABLE IF NOT EXISTS policies (
  policy_id      TEXT NOT NULL,
  version        TEXT NOT NULL,
  name           TEXT NOT NULL,
  -- PROPOSED | APPROVED | REJECTED | FLAGGED_BAD | RETIRED
  status         TEXT NOT NULL,
  rule           TEXT NOT NULL DEFAULT '{}',   -- JSON: the executable rule
  scope          TEXT,
  evidence       TEXT NOT NULL DEFAULT '[]',   -- JSON: source ids behind the rule
  evidence_count INTEGER NOT NULL DEFAULT 0,
  reason         TEXT,                          -- why adopted, or why flagged bad
  work_item_id   TEXT REFERENCES work_items(id),
  proposed_by    TEXT,
  approved_by    TEXT,
  created_at     TEXT NOT NULL,
  PRIMARY KEY (policy_id, version)
);

-- The controller's own written policy, POL-001 (data/reference/refund_policy.md).
-- Seeded so proposals can cite a policy from day one; the learned POL-L* rules are
-- proposed by the seats at onboarding and approved by the controller.
INSERT OR IGNORE INTO policies
  (policy_id, version, name, status, rule, scope, evidence, evidence_count, reason,
   proposed_by, approved_by, created_at)
VALUES
  ('POL-001', '1', 'Tariff duties and refunds (written policy)', 'APPROVED',
   '{"kind": "document", "doc": "data/reference/refund_policy.md", "doc_id": "POL-001"}',
   'all tariff duty and refund accounting', '["data/reference/refund_policy.md"]', 1,
   'The controller''s written policy, in force from 2025-10-01.',
   'human_controller', 'human_controller', '2025-10-01T00:00:00+00:00');

-- The CFO's standing authorization, POL-A1 (desk/authorization.py). Routine entries
-- inside these caps and conditions post without a review; anything else waits for the
-- CFO. Adopted once by the CFO, versioned like every other rule.
INSERT OR IGNORE INTO policies
  (policy_id, version, name, status, rule, scope, evidence, evidence_count, reason,
   proposed_by, approved_by, created_at)
VALUES
  ('POL-A1', '1', 'Standing authorization: routine refund entries', 'APPROVED',
   '{"kind": "standing_authorization", "covers": {"WI-FORECAST-CORRECTION": {"cap": "15000.00"}, "WI-REFUND-DEPOSIT": {"cap": "25000.00"}, "WI-REFUND-SPLIT": {"cap": "20000.00"}, "WI-CUST-*-CREDIT": {"cap": "2000.00", "clause": "explicit"}}, "conditions": ["every figure computed by code", "all five checks pass", "no question open and no pushback on the item", "never sent back and never revised on a document", "a customer credit only under an explicit contract clause"]}',
   'A refund deposit that matches its notice, a forecast correction under an adopted rule, a refund split under an adopted rule, and a customer credit under an explicit contract clause, each under its cap. Ambiguous clauses, documents that change a decision, pushback, send-backs and anything above a cap wait for the CFO.',
   '[]', 0,
   'Adopted by the CFO so the desk asks only when it is genuinely stuck.',
   'human_cfo', 'human_cfo', '2025-10-01T00:00:00+00:00');

-- ===================== golden tables (sync output) =====================
-- Every golden row carries raw_ref back to the row it came from.
-- run_sql may read these and nothing else.

CREATE TABLE IF NOT EXISTS golden_bank_txns (
  txn_id       TEXT PRIMARY KEY,
  date         TEXT NOT NULL,
  direction    TEXT NOT NULL,
  amount       REAL NOT NULL,
  type         TEXT,
  counterparty TEXT,
  description  TEXT,
  reference    TEXT,
  raw_ref      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS golden_cbp_entries (
  entry_no           TEXT PRIMARY KEY,
  entry_date         TEXT NOT NULL,
  importer_no        TEXT,
  importer_ref       TEXT,
  country_of_origin  TEXT,
  exporter           TEXT,
  commercial_hts     TEXT,
  description        TEXT,
  entered_value      REAL,
  total_duty         REAL,
  liquidation_status TEXT,
  liquidation_date   TEXT,
  refund_status      TEXT,
  refund_id          TEXT,
  raw_ref            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS golden_cbp_duty_lines (
  entry_no  TEXT NOT NULL REFERENCES golden_cbp_entries(entry_no),
  program   TEXT NOT NULL,
  hts_ch99  TEXT,
  rate_pct  REAL,
  duty      REAL NOT NULL,
  raw_ref   TEXT NOT NULL,
  PRIMARY KEY (entry_no, program)
);

CREATE TABLE IF NOT EXISTS golden_cbp_refunds (
  refund_id       TEXT PRIMARY KEY,
  notice_date     TEXT NOT NULL,
  payment_date    TEXT NOT NULL,
  payment_method  TEXT,
  payee           TEXT,
  importer_no     TEXT,
  reason          TEXT,
  total_principal REAL NOT NULL,
  total_interest  REAL NOT NULL,
  total           REAL NOT NULL,
  raw_ref         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS golden_cbp_refund_entries (
  refund_id TEXT NOT NULL REFERENCES golden_cbp_refunds(refund_id),
  entry_no  TEXT NOT NULL,
  program   TEXT,
  hts_ch99  TEXT,
  principal REAL NOT NULL,
  interest  REAL NOT NULL,
  raw_ref   TEXT NOT NULL,
  PRIMARY KEY (refund_id, entry_no)
);

CREATE TABLE IF NOT EXISTS golden_ledger_postings (
  posting_id TEXT PRIMARY KEY,
  date       TEXT NOT NULL,
  narration  TEXT,
  account    TEXT NOT NULL,
  amount     REAL NOT NULL,
  currency   TEXT NOT NULL,
  raw_ref    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS golden_erp_inventory_layers (
  layer_id      INTEGER PRIMARY KEY,
  shipment_id   TEXT NOT NULL,
  entry_no      TEXT,                          -- crosswalk key to customs
  part_id       TEXT NOT NULL,
  qty_received  INTEGER NOT NULL,
  qty_on_hand   INTEGER NOT NULL,
  qty_consumed  INTEGER NOT NULL,
  unit_cost     REAL NOT NULL,
  duty_per_unit REAL NOT NULL,
  raw_ref       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS golden_erp_consumption (
  so_id    TEXT NOT NULL,
  layer_id INTEGER NOT NULL,
  part_id  TEXT NOT NULL,
  qty      INTEGER NOT NULL,
  raw_ref  TEXT NOT NULL,
  PRIMARY KEY (so_id, layer_id, part_id)
);

CREATE TABLE IF NOT EXISTS golden_billing_customers (
  customer_id        TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  email              TEXT,
  contract_id        TEXT,
  passthrough_clause TEXT,                    -- explicit | ambiguous
  raw_ref            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS golden_billing_invoices (
  invoice_id      TEXT PRIMARY KEY,
  customer_id     TEXT NOT NULL,
  contract_id     TEXT,
  invoice_date    TEXT NOT NULL,
  paid_date       TEXT,
  machine_total   REAL NOT NULL,
  surcharge_total REAL NOT NULL,
  total           REAL NOT NULL,
  raw_ref         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS golden_billing_invoice_lines (
  invoice_id      TEXT NOT NULL REFERENCES golden_billing_invoices(invoice_id),
  line_no         INTEGER NOT NULL,
  kind            TEXT NOT NULL,              -- machine | tariff_surcharge
  product         TEXT,
  description     TEXT,
  quantity        INTEGER NOT NULL,
  unit_amount     REAL NOT NULL,              -- dollars, converted from cents on sync
  amount          REAL NOT NULL,
  machine_id      TEXT,
  shipment_ids    TEXT,                       -- surcharge lines: which shipments it covers
  tariff_program  TEXT,
  surcharge_basis TEXT,
  raw_ref         TEXT NOT NULL,
  PRIMARY KEY (invoice_id, line_no)
);

-- Past human decisions the desk can reuse. A precedent is a record, not a rule:
-- it says what a named human decided and why, so a later case can cite it.
CREATE TABLE IF NOT EXISTS precedents (
  precedent_id   TEXT PRIMARY KEY,
  question_id    TEXT,
  work_item_id   TEXT REFERENCES work_items(id),
  subject        TEXT NOT NULL,               -- what it is about, e.g. CON-002 clause 7.2
  decided_by     TEXT NOT NULL,
  decision       TEXT NOT NULL,
  clause         TEXT,
  scope          TEXT,
  evidence       TEXT NOT NULL DEFAULT '[]',
  decided_at     TEXT NOT NULL
);

-- Pushback and what code did about it (CLAUDE.md 6.8). One row per challenge.
CREATE TABLE IF NOT EXISTS challenges (
  challenge_id   TEXT PRIMARY KEY,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  raised_by      TEXT NOT NULL,
  text           TEXT NOT NULL,
  document       TEXT,                        -- inbox path, if one was attached
  classification TEXT NOT NULL,               -- evidence | opinion | authority
  classified_by  TEXT NOT NULL,               -- which model, or 'code'
  reasoning      TEXT,
  changed        INTEGER NOT NULL DEFAULT 0,  -- did the proposal actually change?
  -- NULL = no document to test; 1 = the facts checked out; 0 = they did not
  verified       INTEGER,
  outcome        TEXT NOT NULL,
  routed_to      TEXT,                        -- authority: who it was escalated to
  facts          TEXT,                        -- JSON: extracted facts with their quotes
  reply          TEXT,                        -- the seat's answer to the person, in words
  replied_by     TEXT,                        -- the seat (ai_agent.seatN) or 'code'
  created_at     TEXT NOT NULL
);
