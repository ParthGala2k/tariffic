/* What the desk is doing: each event as a line of stages, the agents, the narration. */
import { useMemo, useState } from "react";
import { Zap } from "lucide-react";
import { api, when } from "../lib/api";
import { useDesk, useLive } from "../lib/desk";
import IssueTracker, { shortTitle, type Issue } from "../components/IssueTracker";
import SeatCards from "../components/SeatCards";
import Feed from "../components/Feed";
import { Badge, Button, Card, Empty } from "../components/ui";

export default function ActivityPage() {
  const { status, events, act, scenario, demoMode } = useDesk();
  const { data } = useLive(() => api.issues(), [], 2000);
  const [technical, setTechnical] = useState(false);
  const issues: Issue[] = (data?.issues || []).slice().reverse();
  const running = ["queued", "running"].includes(scenario?.run?.state || "");

  const working = useMemo(() => {
    const s = new Set<string>();
    for (const seat of Object.values(status?.seats || {}) as any[]) {
      if (seat.work_item && !["idle", "waiting_approval", "waiting_answer", "answered"].includes(seat.mode)) s.add(seat.work_item);
    }
    return s;
  }, [status]);
  const titles = useMemo(() => {
    const t: Record<string, string> = {};
    for (const i of issues) for (const s of i.stages) t[s.id] = shortTitle(s);
    return t;
  }, [issues]);

  const trigger = () => act(() => api.simulateRefund(demoMode), (r: any) =>
    r.llm === "scripted" ? "Recorded replay started. No model calls." : "Live model run started.");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <p className="text-[13px] text-ink-2">Events arrive; the agents work them under strict checks; people only decide. Every state here is a row in the audit log.</p>
        <Button variant="accent" onClick={trigger} disabled={running}>
          <Zap className="h-4 w-4" /> {running ? "An event is being worked" : "Simulate an event: a tariff refund arrives"}
        </Button>
      </div>

      {issues.length === 0 && <Empty title="No events yet" hint="Trigger one above, or wait for the bank, Customs or a customer to send something." />}
      {issues.map((issue) => (
        <Card key={issue.id} title={<span className="normal-case tracking-normal text-[15px] text-ink">{issue.title}</span>} aside={
          <div className="flex items-center gap-2">
            <span className="text-[12px] text-ink-3">opened {when(issue.opened_at)}</span>
            {issue.state === "needs_you" && <Badge tone="warn" dot>needs the {issue.awaiting.join(" and the ")}</Badge>}
            {issue.state === "working" && <Badge tone="accent" dot>being worked</Badge>}
            {issue.state === "done" && <Badge tone="good" dot>done</Badge>}
          </div>}>
          <IssueTracker issue={issue} working={working} compact={issue.kind === "onboarding"} />
        </Card>
      ))}

      <div className="grid gap-6 xl:grid-cols-[1fr_440px]">
        <div className="space-y-3">
          <h2 className="text-[13px] font-semibold uppercase tracking-[0.06em] text-ink-2">The three agents</h2>
          <SeatCards seats={status?.seats} titles={titles} />
          <p className="text-[12.5px] text-ink-3">
            Agents propose and ask. They cannot approve or post; an attempt is stopped by code and logged.
            {scenario?.run?.llm === "live" ? " Live model run." : scenario?.run?.llm === "scripted" ? " Recorded replay; no model calls." : ""}
          </p>
        </div>
        <Card title="As it happens" pad={false} aside={
          <label className="flex cursor-pointer items-center gap-2 text-[12px] text-ink-2">
            <input type="checkbox" checked={technical} onChange={(e) => setTechnical(e.target.checked)} className="accent-ink" /> technical
          </label>}>
          <div className="p-3"><Feed events={events} technical={technical} /></div>
        </Card>
      </div>
    </div>
  );
}
