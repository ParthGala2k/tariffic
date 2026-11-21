/* Developer tools: replay a recorded world step by step. Not part of the demo. */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Play, RotateCcw } from "lucide-react";
import { api } from "../lib/api";
import { useDesk, useLive } from "../lib/desk";
import Tracker, { deriveNodes } from "../components/Tracker";
import Feed from "../components/Feed";
import { Badge, Button, Card, Mono, cx } from "../components/ui";

export default function Simulate() {
  const { scenario, status, events, act, health } = useDesk();
  const { data: list } = useLive(() => api.scenarios(), [], 5000);
  const [busy, setBusy] = useState<string | null>(null);
  const [llm, setLlm] = useState<"scripted" | "live">("scripted");
  const [controller, setController] = useState<"human" | "scripted">("scripted");

  const active = scenario?.active as string | undefined;
  const steps: string[] = scenario?.steps || [];
  const running = Boolean(scenario?.run && ["queued", "running"].includes(scenario.run.state));
  const nodes = useMemo(() => deriveNodes(steps, events, status, scenario), [steps, events, status, scenario]);
  const finished = Boolean(scenario?.report?.after) || ["finished", "failed"].includes(scenario?.run?.state || "");

  const activate = async (name: string) => { setBusy(name); await act(() => api.activate(name), (r: any) => `${name} built; ${r.report.before.ok}/${r.report.before.total} tie-outs hold.`); setBusy(null); };
  const run = async () => { if (!active) return; setBusy("run"); await act(() => api.runScenario(active, llm, controller), () => `Running ${active}.`); setBusy(null); };
  const leave = async () => { setBusy("leave"); await act(() => api.deactivate(), () => "Back on the base data."); setBusy(null); };

  return (
    <div className="space-y-6">
      <div className="rounded-md bg-surface-2 p-3 text-[12.5px] text-ink-2">
        For development: replay a recorded world with scripted agents and a scripted controller. The demo uses the event trigger on <Link to="/activity" className="font-medium underline-offset-2 hover:underline">Activity</Link> instead.
      </div>
      <Card title="Worlds" aside={active && <div className="flex items-center gap-2"><Badge tone="accent" dot>{active}</Badge><Button size="sm" variant="ghost" onClick={leave} disabled={running || busy === "leave"}>Leave sandbox</Button></div>}>
        <div className="grid gap-3 md:grid-cols-2">
          {list?.scenarios?.map((s: any) => (
            <div key={s.name} className={cx("rounded-md border p-4", active === s.name ? "border-ink" : "border-line")}>
              <div className="flex items-center justify-between gap-2"><div className="text-[14px] font-semibold">{s.name}</div><Mono>{s.steps?.length ? `${s.steps.length} steps` : "tie-outs only"}</Mono></div>
              <p className="mt-1 text-[12.5px] leading-snug text-ink-2">{s.description}</p>
              {s.known_issues?.length > 0 && <p className="mt-2 text-[12px] text-warn">Documents {s.known_issues.length} known issue</p>}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {active !== s.name ? (
                  <Button size="sm" onClick={() => activate(s.name)} loading={busy === s.name} disabled={running || Boolean(busy)}>Build sandbox</Button>
                ) : s.steps?.length ? (
                  <>
                    <Button size="sm" variant="accent" onClick={run} loading={busy === "run"} disabled={running || finished}><Play className="h-3.5 w-3.5" /> {finished ? "Done" : running ? "Running" : "Run"}</Button>
                    {finished && <Button size="sm" variant="ghost" className="whitespace-nowrap" onClick={() => activate(s.name)} loading={busy === s.name}><RotateCcw className="h-3.5 w-3.5" /> Rebuild</Button>}
                    <select value={controller} onChange={(e) => setController(e.target.value as any)} disabled={running} className="h-8 rounded-md border border-line bg-surface px-2 text-[12.5px]">
                      <option value="scripted">scripted controller</option><option value="human">human controller</option>
                    </select>
                    <select value={llm} onChange={(e) => setLlm(e.target.value as any)} disabled={running} className="h-8 rounded-md border border-line bg-surface px-2 text-[12.5px]">
                      <option value="scripted">scripted agents</option>
                      <option value="live" disabled={!health?.llm?.configured}>live model{health?.llm?.configured ? ` (${health.llm.model})` : " (no key)"}</option>
                    </select>
                  </>
                ) : <Badge tone="neutral">nothing to run</Badge>}
              </div>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Steps" aside={running ? <Badge tone="accent" dot>working</Badge> : finished ? <Badge tone={scenario?.report?.passed ? "good" : "bad"} dot>{scenario?.report?.passed ? "every check ties" : "see the report"}</Badge> : <Badge tone="neutral">waiting</Badge>}>
        {!active ? <div className="text-[13px] text-ink-3">Build a sandbox above.</div> : <Tracker nodes={nodes} />}
      </Card>
      <Card title="Log (technical)" pad={false}><div className="p-3"><Feed events={events} technical /></div></Card>
    </div>
  );
}
