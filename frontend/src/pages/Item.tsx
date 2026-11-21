/* One decision. Plain words first; everything technical one click away. */
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, FileText, Quote, X } from "lucide-react";
import { api, ROLES, STATE_TONE, STATE_WORD, agentName } from "../lib/api";
import { useDesk, useLive } from "../lib/desk";
import Feed from "../components/Feed";
import { Raw, Term } from "../components/Term";
import { Amount, Badge, Button, Card, Mono, Skeleton, cx } from "../components/ui";

export default function Item() {
  const { id = "" } = useParams();
  const { role, actor, act, events } = useDesk();
  const { data: b, error } = useLive(() => api.brief(id), [id], 2000);
  const { data: docs } = useLive(() => api.documents(), [], 0);
  const [doc, setDoc] = useState<{ path: string; text: string } | null>(null);
  const [reason, setReason] = useState("");
  const [counter, setCounter] = useState("");
  const [counterDoc, setCounterDoc] = useState("");
  const [outcome, setOutcome] = useState<any>(null);
  const [answering, setAnswering] = useState(false);

  if (error) return <Card><div className="text-bad">{error}</div></Card>;
  if (!b) return <div className="space-y-3"><Skeleton className="h-8 w-1/2" /><Skeleton className="h-40" /></div>;

  const w = b.work_item;
  const p = b.plain || {};
  const prop = b.proposal || {};
  const canDecide = role === "cfo" && w.state === "PROPOSED";
  const inbox = (docs?.documents || []).filter((d: any) => d.path.includes("/inbox/"));
  const openDoc = async (path: string) => {
    try { setDoc({ path, text: await api.document(path) }); } catch (e) { setDoc({ path, text: String(e) }); }
  };
  const pushBack = async () => {
    setAnswering(true);
    try {
      const r = await act(() => api.challenge(id, actor, counter, counterDoc || null));
      if (r) { setOutcome(r); setCounter(""); }
    } finally { setAnswering(false); }
  };
  const replier = (by: string | null | undefined) => by && by.startsWith("ai_agent.") ? agentName(by.slice(9)) : "The desk";

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <Link to="/items" className="inline-flex items-center gap-1 text-[12.5px] text-ink-3 hover:text-ink"><ArrowLeft className="h-3.5 w-3.5" /> all decisions</Link>
          <h2 className="mt-1 text-[22px] font-semibold leading-tight">{p.title}</h2>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[12.5px] text-ink-3">
            <Badge tone={STATE_TONE[w.state] as any}>{STATE_WORD[w.state] || w.state}</Badge>
            {w.seat && <span>proposed by <span className="font-medium text-ink">{agentName(w.seat)}</span></span>}
            {w.approved_by && <span>· approved by the controller</span>}
          </div>
        </div>
        {p.amount && <div className="shrink-0 text-right"><Amount value={p.amount} className="hero-num text-[32px]" /><div className="text-[11.5px] text-ink-3">USD</div></div>}
      </div>

      <div className={cx("rounded-lg p-4 text-[13.5px] ring-1 ring-inset", w.state === "PROPOSED" ? "bg-warn-soft ring-warn/20" : "bg-surface-2 ring-line")}>
        <span className="font-semibold">{p.decision}. </span>
        {w.state === "PROPOSED" && p.if_approved && <span className="text-ink-2">If you approve: {p.if_approved}</span>}
        {w.state === "POSTED" && <span className="text-ink-2">To change it, push back with a document; a verified document opens a correcting entry and this one stays on the books.</span>}
      </div>

      <div className="grid gap-5 xl:grid-cols-[1fr_360px]">
        <div className="space-y-5">
          <Card title="In short">
            <p className="text-[14px] leading-relaxed">{p.summary}</p>
            {p.why?.length > 0 && (
              <ul className="mt-3 space-y-1.5 text-[13.5px]">
                {p.why.map((line: string, i: number) => <li key={i} className="flex gap-2"><span className="mt-[8px] h-1.5 w-1.5 shrink-0 rounded-full bg-ink" />{line}</li>)}
              </ul>
            )}
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-ink-3">
              {p.computed && <span>every figure computed by code, not estimated</span>}
              {b.invariant_gate && <span>{b.invariant_gate.allowed ? "all five " : "a "}<Term k="invariant">checks</Term>{b.invariant_gate.allowed ? " passed before approval" : " stopped this"}</span>}
            </div>
          </Card>

          {p.money_lines?.length > 0 && (
            <Card title="Money movement">
              <table className="w-full text-[13.5px]">
                <tbody>
                  {p.money_lines.map((m: any, i: number) => (
                    <tr key={i} className="border-b border-line last:border-0">
                      <td className="py-2"><Raw label={m.label} raw={m.account} /></td>
                      <td className="py-2 text-right text-ink-3">{m.side === "debit" ? "in" : "out"}</td>
                      <td className="py-2 pl-4 text-right"><Amount value={m.amount} className={m.side === "debit" ? "" : "text-ink-2"} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-2 text-[12px] text-ink-3">“{prop.journal_entry?.narration}” · dated {prop.journal_entry?.date}</div>
            </Card>
          )}

          <Card title="Checked against" aside={<span className="text-[12px] text-ink-3">{p.read?.length || 0} documents read by the agent</span>}>
            <div className="flex flex-wrap gap-2">
              {(p.evidence || []).map((e: any) => (
                <button key={e.path} onClick={() => openDoc(e.path)} className="inline-flex cursor-pointer items-center gap-1.5 rounded-md bg-info-soft px-3 py-1.5 text-[13px] text-info hover:bg-[#d9e3fb]">
                  <FileText className="h-3.5 w-3.5" />{e.title}
                </button>
              ))}
              {(p.evidence || []).length === 0 && <span className="text-[12.5px] text-ink-3">No documents cited yet.</span>}
            </div>
          </Card>

          {(b.questions.length > 0 || b.challenges.length > 0 || b.decisive_facts || b.precedents.length > 0) && (
            <Card title="People">
              {b.questions.map((q: any) => (
                <div key={q.id} className="mb-3 rounded-md border border-line p-3 text-[13px]">
                  <div className="text-ink-3">The agent asked the <span className="font-medium text-ink">{q.to_role.replace("human_", "")}</span></div>
                  <p className="mt-1">“{q.text}”</p>
                  <p className="mt-1.5 text-ink-2">{q.answer ? <><span className="font-medium text-ink">Answer:</span> {q.answer}</> : <Badge tone="warn">no answer yet</Badge>}</p>
                </div>
              ))}
              {b.precedents.map((pr: any) => (
                <div key={pr.precedent_id} className="mb-3 rounded-md bg-surface-2 p-3 text-[12.5px]">
                  <div className="flex items-center gap-2"><Badge tone="info"><Term k="precedent">precedent</Term></Badge><span className="text-ink-3">from the {pr.decided_by.replace("human_", "")}{pr.clause ? ` on ${pr.clause}` : ""}</span></div>
                  <p className="mt-1.5 text-ink-2">{pr.decision}</p>
                </div>
              ))}
              {b.challenges.map((c: any) => (
                <div key={c.challenge_id} className="mb-3 rounded-md border border-line p-3 text-[12.5px]">
                  <div className="flex items-center gap-2">
                    <Badge tone={c.classification === "evidence" ? "good" : c.classification === "authority" ? "warn" : "neutral"}>{c.classification}</Badge>
                    <span className="text-ink-3">from the {c.raised_by.replace("human_", "")}</span>
                    <Badge tone={c.changed ? "good" : "neutral"} className="ml-auto">{c.changed ? "changed the decision" : "changed nothing"}</Badge>
                  </div>
                  <p className="mt-1.5">“{c.text}”</p>
                  {c.reply && <div className="mt-2 rounded-md bg-surface-2 p-2.5"><div className="text-[11.5px] font-semibold text-ink-3">{replier(c.replied_by)} answered</div><p className="mt-1 whitespace-pre-wrap text-ink">{c.reply}</p></div>}
                  <p className="mt-1 text-ink-3">{c.outcome}</p>
                </div>
              ))}
              {b.decisive_facts && (
                <div className="rounded-md bg-good-soft p-3 text-[12.5px]">
                  <div className="font-semibold text-good">Changed to {b.decisive_facts.revised_amount} on a verified document. Every quote was checked word for word.</div>
                  <ul className="mt-2 space-y-1.5">
                    {b.decisive_facts.facts.map((f: any) => <li key={f.fact} className="flex gap-2"><Quote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-good" /><span><span className="font-medium">{f.fact}</span>: {String(f.value)} · “{f.quote}”</span></li>)}
                  </ul>
                </div>
              )}
            </Card>
          )}

          <details className="rounded-lg bg-surface shadow-card">
            <summary className="cursor-pointer px-5 py-3 text-[13px] font-semibold uppercase tracking-[0.06em] text-ink-2">Details for the controller and the auditor</summary>
            <div className="space-y-4 border-t border-line p-5 text-[12.5px]">
              <div className="grid gap-2 sm:grid-cols-2">
                <div><span className="text-ink-3">Work item </span><Mono>{w.id}</Mono> <span className="text-ink-3">· kind </span><Mono>{w.kind}</Mono></div>
                <div><span className="text-ink-3">Rule applied </span>{prop.policy ? <Raw label={prop.policy.name || prop.policy.ref} raw={prop.policy.ref} /> : "—"}</div>
              </div>
              {prop.rationale && <p><span className="font-medium">The agent's own words. </span>{prop.rationale}</p>}
              {b.computations.length > 0 && (
                <div>
                  <div className="font-medium">Figures from code</div>
                  <ul className="mt-1 space-y-1.5">
                    {b.computations.map((c: any, i: number) => (
                      <li key={i} className="rounded-md bg-surface-2 p-2.5">
                        <Mono className="font-medium text-ink">{c.tool}</Mono> <span className="text-ink-3">{Object.entries(c.arguments || {}).map(([k, v]) => `${k}=${v}`).join(" ")}</span>
                        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">{Object.entries(c.result || {}).slice(0, 8).map(([k, v]) => <span key={k}><span className="text-ink-3">{k} </span><span className="num">{String(v)}</span></span>)}</div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {b.invariant_gate && <p><span className="font-medium">Check before approval: </span>{b.invariant_gate.reason}</p>}
              <div>
                <div className="font-medium">Everything cited</div>
                <div className="mt-1 flex flex-wrap gap-1.5">{b.evidence.map((e: any) => <Mono key={e.ref} className="rounded bg-surface-2 px-1.5 py-0.5">{e.ref}</Mono>)}</div>
              </div>
              <div>
                <div className="mb-1 font-medium">Full history</div>
                <Feed events={events} subject={id} limit={80} technical height="max-h-[360px]" />
              </div>
            </div>
          </details>
        </div>

        <div className="space-y-4">
          <Card title={`You are the ${ROLES[role].title}`}>
            {role === "cfo" && (
              <div className="space-y-3">
                <Button variant="approve" size="lg" className="w-full" disabled={!canDecide}
                  onClick={() => act(() => api.approve(id, actor, "approved on screen"), (r: any) =>
                    r.entry ? "Approved and posted to the books." : "Approved and settled. Nothing posted.")}>Approve</Button>
                <textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why? Needed to decline or send back. The desk keeps it."
                  className="w-full rounded-md border border-line bg-surface p-2.5 text-[13px]" rows={2} />
                <div className="flex gap-2">
                  <Button variant="danger" className="flex-1" disabled={!canDecide || !reason.trim()} onClick={() => act(() => api.reject(id, actor, reason), () => "Declined.")}>Decline</Button>
                  <Button className="flex-1" disabled={!canDecide || !reason.trim()} onClick={() => act(() => api.sendBack(id, actor, reason), () => "Sent back. The agent will try again with your reason.")}>Send back</Button>
                </div>
                {!canDecide && <p className="text-[13px] text-ink-3">{w.state === "PROPOSED" ? "" : `Nothing to decide: this is ${STATE_WORD[w.state] || w.state}.`}</p>}
                {canDecide && p.why_you && <p className="text-[13.5px] text-ink"><span className="font-semibold">Why you:</span> {p.why_you}.</p>}
              </div>
            )}
            {role === "lawyer" && <p className="text-[14px] text-ink-2">Contract questions come to you on the Lawyer page. You do not approve entries; you can push back below or ask why.</p>}
          </Card>

          {(
            <Card title={<span><Term k="pushback">Push back</Term></span> as any}>
              <textarea value={counter} onChange={(e) => setCounter(e.target.value)} rows={3} disabled={answering}
                placeholder={role === "cfo" ? "e.g. I'm signing off: book it all to P&L." : "Ask a question or make your case. The agent answers with the facts."}
                className="w-full rounded-md border border-line bg-surface p-2.5 text-[13px]" />
              <select value={counterDoc} onChange={(e) => setCounterDoc(e.target.value)} className="mt-2 w-full rounded-md border border-line bg-surface px-2 py-2 text-[12.5px]">
                <option value="">No document</option>
                {inbox.map((d: any) => <option key={d.path} value={d.path}>{d.path.split("/").pop()?.replace(/^\d{4}-\d{2}-\d{2}-/, "").replace(/\.md$/, "").replace(/-/g, " ")}</option>)}
              </select>
              <Button className="mt-2 w-full" variant="primary" disabled={!counter.trim() || answering} loading={answering} onClick={pushBack}>{answering ? "The agent is answering" : "Submit"}</Button>
              <p className="mt-2 text-[11.5px] text-ink-3">Every pushback gets an answer. An argument or a position changes nothing; a document with matching parties and dates can.</p>
              {outcome && (
                <div className="mt-3 rounded-md bg-surface-2 p-3 text-[12.5px]">
                  <div className="flex items-center gap-2"><Badge tone={outcome.classification === "evidence" ? "good" : outcome.classification === "authority" ? "warn" : "neutral"}>{outcome.classification}</Badge>
                    <Badge tone={outcome.changed ? "good" : "neutral"}>{outcome.changed ? "decision changed" : "nothing changed"}</Badge></div>
                  {outcome.reply?.text && <div className="mt-2 rounded-md bg-surface p-2.5 ring-1 ring-inset ring-line"><div className="text-[11.5px] font-semibold text-ink-3">{replier(outcome.reply.by)} answers</div><p className="mt-1 whitespace-pre-wrap text-[13px] text-ink">{outcome.reply.text}</p></div>}
                  <p className="mt-1.5 text-ink-2">{outcome.outcome}</p>
                  {outcome.routed_to && <p className="mt-1 text-ink-3">Sent to the {outcome.routed_to.replace("human_", "")}.</p>}
                  {outcome.seat_verification?.note && <p className="mt-1 text-ink-3">{outcome.seat_verification.note}</p>}
                </div>
              )}
            </Card>
          )}
        </div>
      </div>

      {doc && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-ink/40 p-6" onClick={() => setDoc(null)}>
          <div className="max-h-[85vh] w-full max-w-3xl overflow-hidden rounded-lg bg-surface shadow-pop" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-line px-5 py-3">
              <Mono className="text-ink">{doc.path}</Mono>
              <button onClick={() => setDoc(null)} className="cursor-pointer text-ink-3 hover:text-ink" aria-label="Close"><X className="h-4 w-4" /></button>
            </div>
            <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap p-5 font-sans text-[13px] leading-relaxed">{doc.text}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
