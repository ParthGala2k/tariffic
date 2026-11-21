/* The books: balances in plain names, the tie-outs, what the gateway posted, the log. */
import { api, accountName } from "../lib/api";
import { useDesk, useLive } from "../lib/desk";
import Feed from "../components/Feed";
import TieOutBoard from "../components/TieOutBoard";
import { Raw, Term } from "../components/Term";
import { Amount, Badge, Card, Mono } from "../components/ui";

export default function Ledger() {
  const { scenario, events } = useDesk();
  const { data: balances } = useLive(() => api.balances(), [], 3000);
  const { data: posted } = useLive(() => api.posted(), [], 3000);
  const report = scenario?.report;
  const checks = report?.after || report?.before;
  const rows = Object.entries(balances?.balances || {}) as [string, string][];
  const groups = ["Assets", "Liabilities", "Equity", "Income", "Expenses"];

  return (
    <div className="space-y-5">
      {report && (
        <Card title="Reconciliation checks" aside={
          <Badge tone={checks.failed ? "bad" : checks.expected_findings ? "warn" : "good"} dot>
            {checks.failed ? "unresolved mismatch" : checks.expected_findings ? `${checks.expected_findings} starting discrepancies` : "reconciled"}
          </Badge>}>
          <div className="grid gap-6 lg:grid-cols-2">
            <TieOutBoard checks={report.before.checks} title="Before the desk worked the event" />
            {report.after ? <TieOutBoard checks={report.after.checks} title="After" /> : <div className="text-[12.5px] text-ink-3">The same checks run again once the event is worked.</div>}
          </div>
        </Card>
      )}

      <div className="grid gap-5 xl:grid-cols-[400px_1fr]">
        <Card title="Balances" aside={balances
          ? <Badge tone={balances.bean_check ? "good" : "bad"} dot><Term k="bean">balance check</Term> {balances.bean_check ? "passed" : "failed"}</Badge>
          : <Badge tone="neutral">checking</Badge>} pad={false}>
          <table className="w-full text-[12.5px]">
            <tbody>
              {groups.map((g) => {
                const r = rows.filter(([a]) => a.startsWith(g + ":"));
                if (!r.length) return null;
                return (
                  <>
                    <tr key={g} className="bg-surface-2"><td colSpan={2} className="px-5 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">{g}</td></tr>
                    {r.map(([account, amount]) => (
                      <tr key={account} className="border-t border-line">
                        <td className="px-5 py-1.5"><Raw label={accountName(account)} raw={account} /></td>
                        <td className="px-5 py-1.5 text-right"><Amount value={amount} className={parseFloat(amount) < 0 ? "text-ink-2" : ""} /></td>
                      </tr>
                    ))}
                  </>
                );
              })}
            </tbody>
          </table>
        </Card>
        <Card title={<span>Posted by the <Term k="gateway" /></span> as any} aside={<Mono>systems/books/posted/desk.beancount</Mono>} pad={false}>
          <pre className="max-h-[520px] overflow-auto p-5 font-mono text-[12px] leading-relaxed text-ink-2">{posted || "…"}</pre>
        </Card>
      </div>

      <Card title="Audit log" aside={<span className="text-[12px] text-ink-3">append-only; refusals included</span>} pad={false}>
        <div className="p-3"><Feed events={events} limit={200} technical /></div>
      </Card>
    </div>
  );
}
