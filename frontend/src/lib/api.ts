/* Client for the desk API (desk/api.py). Every call returns the parsed body or
   throws ApiError carrying the server's own reason, so the screen can show a
   refusal in the server's words. */

export class ApiError extends Error {
  status: number;
  body: any;
  constructor(status: number, body: any) {
    super((body && (body.detail || body.reason || body.error)) || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function call<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json", ...(init?.headers || {}) }, ...init });
  const text = await res.text();
  let body: any = text;
  try { body = text ? JSON.parse(text) : null; } catch { /* plain-text endpoints */ }
  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}
const get = <T = any>(path: string) => call<T>(path);
const post = <T = any>(path: string, body?: any) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => get("/health"),
  status: () => get("/status"),
  events: (after = 0, limit = 300) => get(`/events?after=${after}&limit=${limit}`),
  attention: () => get("/attention"),
  issues: () => get("/issues"),
  workItems: (params = "") => get(`/work-items${params}`),
  workItem: (id: string) => get(`/work-items/${id}`),
  brief: (id: string) => get(`/work-items/${id}/brief`),
  approve: (id: string, actor: string, reason = "") => post(`/work-items/${id}/approve`, { actor, reason, post: true }),
  reject: (id: string, actor: string, reason: string) => post(`/work-items/${id}/reject`, { actor, reason }),
  sendBack: (id: string, actor: string, reason: string) => post(`/work-items/${id}/send-back`, { actor, reason }),
  challenge: (id: string, actor: string, text: string, document?: string | null) =>
    post(`/work-items/${id}/challenge`, { actor, text, document: document || null, verify_with_seat: true }),
  resume: (id: string) => post(`/work-items/${id}/resume`),
  questions: (state?: string) => get(`/questions${state ? `?state=${state}` : ""}`),
  answer: (qid: string, actor: string, text: string, document?: string | null, rule_proposal?: any) =>
    post(`/questions/${qid}/answer`, { actor, text, document: document || null, rule_proposal: rule_proposal || null }),
  policies: () => get("/policies"),
  approvePolicy: (ref: string, actor: string, reason = "") => post(`/policies/${encodeURIComponent(ref)}/approve`, { actor, reason }),
  precedents: (q = "") => get(`/precedents?q=${encodeURIComponent(q)}`),
  balances: () => get("/books/balances"),
  posted: () => call<string>("/books/posted"),
  invariants: () => get("/invariants"),
  documents: () => get("/documents"),
  document: (path: string) => call<string>(`/documents/${path}`),
  upload: (body: any) => post("/inbox", body),
  seats: () => get("/seats"),
  scenarios: () => get("/scenarios"),
  activeScenario: () => get("/scenarios/active"),
  activate: (name: string) => post(`/scenarios/${name}/activate`),
  runScenario: (name: string, llm?: string, controller?: string) =>
    post(`/scenarios/${name}/run`, { llm: llm || null, controller: controller || null }),
  deactivate: () => post("/scenarios/deactivate"),
  simulateRefund: (llm?: string, controller = "human") => post("/simulate/refund-arrives", { llm: llm || null, controller }),
  runs: () => get("/runs"),
  run: (id: string) => get(`/runs/${id}`),
};

/* ---- formatting ------------------------------------------------------------ */

export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "–";
  const n = typeof value === "number" ? value : parseFloat(String(value).replace(/,/g, ""));
  if (Number.isNaN(n)) return String(value);
  const abs = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return n < 0 ? `−${abs}` : abs;
}

export function compact(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "–";
  const n = typeof value === "number" ? value : parseFloat(String(value).replace(/,/g, ""));
  if (Number.isNaN(n)) return String(value);
  const abs = Math.abs(n);
  const sign = n < 0 ? "−" : "";
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 10_000) return `${sign}${(abs / 1_000).toFixed(1)}K`;
  return `${sign}${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

/* ---- vocabulary -------------------------------------------------------------- */

export const ROLES = {
  cfo: { actor: "human_cfo", title: "CFO", blurb: "Approves or declines what the desk cannot settle alone. Can push back. Never posts." },
  lawyer: { actor: "human_lawyer", title: "Lawyer", blurb: "Answers contract questions and reads every decision." },
} as const;
export type RoleKey = keyof typeof ROLES;

export const AGENTS: Record<string, { name: string; job: string }> = {
  seat1: { name: "Trade compliance", job: "matches refunds, reconciles duty paid with Customs" },
  seat2: { name: "Cost accounting", job: "splits refunds between parts sold and parts in stock" },
  seat3: { name: "Revenue accounting", job: "decides what each customer is owed" },
};
export const agentName = (seat?: string | null) => (seat && AGENTS[seat]?.name) || seat || "";

export const ACCOUNT_NAMES: Record<string, string> = {
  "Assets:Bank:Increase:Operating": "Operating cash",
  "Assets:Receivable:TariffRefund": "Tariff refund receivable",
  "Assets:Receivable:Trade": "Customer receivables",
  "Assets:Inventory:Parts": "Parts inventory",
  "Liabilities:Payable:Vendors": "Owed to vendors",
  "Liabilities:CustomerCredits": "Credits owed to customers",
  "Equity:Opening-Balances": "Opening balances",
  "Income:Sales:Machines": "Machine sales",
  "Income:Sales:TariffSurcharge": "Tariff surcharge billed",
  "Income:Interest:TariffRefund": "Interest on the refund",
  "Expenses:COGS:Parts": "Cost of parts sold",
  "Expenses:COGS:Tariff": "Tariff cost",
};
export const accountName = (a: string) => ACCOUNT_NAMES[a] || a.split(":").pop() || a;

/* Every term a judge might stumble on, explained in one sentence for a hover tip. */
export const GLOSSARY: Record<string, { term: string; tip: string }> = {
  cbp: { term: "U.S. Customs", tip: "U.S. Customs and Border Protection: collects import duties and pays refunds." },
  ieepa: { term: "IEEPA duties", tip: "Tariffs imposed under emergency powers in 2025; a court struck them down, so they are being refunded with interest." },
  s232: { term: "Section 232", tip: "Steel and aluminium tariffs. Still in force, never refunded." },
  s301: { term: "Section 301", tip: "China trade tariffs from 2018. Still in force, never refunded." },
  liquidated: { term: "liquidated", tip: "Customs has finalised the duty on an entry; only then can it be refunded." },
  entry: { term: "customs entry", tip: "One import declaration (form 7501) with its duty lines." },
  receivable: { term: "refund receivable", tip: "Refund money the books expect from Customs but have not received yet." },
  surcharge: { term: "tariff surcharge", tip: "A separate line Brewline added to customer invoices to pass on tariff cost." },
  split: { term: "refund split", tip: "Part of a refund belongs to parts already sold (cost of goods), the rest to parts still in stock (inventory)." },
  invariant: { term: "checks", tip: "Five rules the books must always satisfy, run by code before any approval." },
  gateway: { term: "gateway", tip: "The only piece of code that writes to the books, and only after a person approves." },
  controller: { term: "CFO", tip: "The person who approves or declines what the desk proposes. The CFO cannot." },
  precedent: { term: "precedent", tip: "A recorded answer from the lawyer that the desk may cite in a matching case." },
  rule: { term: "rule", tip: "A reusable policy the desk learned; versioned, adopted by the controller, applied by code." },
  pushback: { term: "pushback", tip: "An argument against a decision. Opinion and authority change nothing; a verified document can." },
  evidence: { term: "evidence", tip: "A document on file with matching parties and dates. The only thing that changes a decision." },
  bean: { term: "balance check", tip: "The ledger is re-validated after every posting; a bad entry is removed before it lands." },
};

export const STATE_TONE: Record<string, string> = {
  NEW: "neutral", PROPOSED: "warn", NEEDS_INFO: "info", APPROVED: "good", POSTED: "good", REJECTED: "bad", CLOSED: "neutral",
};
export const STATE_WORD: Record<string, string> = {
  NEW: "queued", PROPOSED: "awaiting approval", NEEDS_INFO: "waiting on an answer", APPROVED: "approved",
  POSTED: "posted", REJECTED: "declined", CLOSED: "settled",
};

export const MODE_LABEL: Record<string, string> = {
  idle: "Idle", picking_up: "Picking up", thinking: "Thinking", investigating: "Reading",
  consulting_rules: "Checking the rules", computing: "Computing", proposing: "Proposing",
  asking: "Asking a person", revising: "Revising", refused: "Stopped by a check",
  waiting_approval: "Awaiting approval", waiting_answer: "Awaiting an answer", answered: "Answer received",
};

/* Developer-only: labels for the scenario runner's steps. */
export const STEP_LABELS: Record<string, { title: string; sub: string; seat?: string }> = {
  sync: { title: "Sync the records", sub: "bank, customs, ERP, billing, books" },
  onboarding: { title: "Learn the rules", sub: "agents read the policy and history", seat: "seat1" },
  invariants: { title: "Scan the books", sub: "five checks; anything wrong opens work", seat: "seat1" },
  deposit: { title: "Record the deposit", sub: "match the credit to the refund notice", seat: "seat1" },
  split: { title: "Split the refund", sub: "parts sold vs parts in stock", seat: "seat2" },
  "credit:CUST-001": { title: "Bean Street's share", sub: "explicit clause: the desk decides", seat: "seat3" },
  "question:CUST-002": { title: "Harbor's share", sub: "ambiguous clause: to the lawyer", seat: "seat3" },
  "credit:CUST-003": { title: "Northwind's share", sub: "under the learned rule", seat: "seat3" },
  tie_outs: { title: "Tie out the books", sub: "ledger vs bank, ERP, customs, billing" },
};
