/* Home. One question: what needs this person right now? Everything routine posts on the
   CFO's standing authorization and is listed, not asked. Each card is a link to the page
   where the person acts. */
import { useState, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, CheckCircle2, Gavel, ShieldAlert, ShieldCheck, Zap } from "lucide-react";
import { api, compact, money } from "../lib/api";
import { useDesk, useLive } from "../lib/desk";
import { Term } from "../components/Term";
import { Amount, Badge, Button, Card, Skeleton, Stat, cx } from "../components/ui";

const CHECKS: Array<[string, string]> = [
  ["Duty paid equals Customs", "What the books paid in duty equals what Customs assessed, entry by entry."],
  ["Only refundable duty", "The refund receivable holds only duty under programmes that are actually refunded."],
  ["Refund equals notice", "Money received never exceeds refundable duty, and matches the notice."],
  ["Split adds up", "Parts sold plus parts in stock equals the refund, to the cent."],
  ["Inventory ties out", "Ledger inventory equals the stock records' landed value."],
];

export default function NeedsYou() {
  const { role, actor, act, scenario, demoMode } = useDesk();
  const nav = useNavigate();
  const { data: balances } = useLive(() => api.balances(), [], 3000);
  const { data: att } = useLive(() => api.attention(), [], 2500);
  const { data: issuesBody } = useLive(() => api.issues(), [], 2500);
  const [declining, setDeclining] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const bal = balances?.balances || {};
  const owed = bal["Liabilities:CustomerCredits"] ? -parseFloat(bal["Liabilities:CustomerCredits"]) : 0;
  const open = att?.invariants?.open || [];
  const report = scenario?.report;
  const issues = issuesBody?.issues || [];
  const busy = issues.filter((i: any) => i.state !== "done");
  const forCfo = att?.for_controller || [];
  const openQuestions = (att?.routed_to_lawyer || []).filter((q: any) => q.state === "OPEN");
  const routine = (att?.handled_on_its_own || []).filter((w: any) => w.under_authorization).slice().reverse();
  const routineTotal = routine.reduce((s: number, w: any) => s + (parseFloat(w.amount || "0") || 0), 0);
  const loaded = att !== null && issuesBody !== null;

  const mine = role === "cfo" ? forCfo.length : openQuestions.length;

  const trigger = async () => {
    const result = await act(() => api.simulateRefund(demoMode), (r: any) =>
      r.llm === "scripted" ? "Recorded replay started. No model calls." : "Live model run started.");
    if (result) nav("/activity");
  };

  const decide = async (e: MouseEvent, id: string, what: "approve" | "reject") => {
    e.preventDefault(); e.stopPropagation();
    if (what === "approve") {
      await act(() => api.approve(id, actor, "approved on screen"), (r: any) =>
        r.entry ? "Approved and posted to the books." : "Approved and settled. Nothing posted.");
    } else {
      await act(() => api.reject(id, actor, reason), () => "Declined. The desk has your reason.");
      setDeclining(null); setReason("");
    }
  };

  return (
    <div className="space-y-8">
      <div className="grid gap-4 md:grid-cols-4">
        <Stat label="Operating cash" value={balances ? compact(bal["Assets:Bank:Increase:Operating"]) : <Skeleton className="h-8 w-32" />}
          hint={balances ? <Amount value={bal["Assets:Bank:Increase:Operating"]} /> : undefined} />
        <Stat label={<Term k="receivable">Refund still due from Customs</Term> as any}
          value={balances ? compact(bal["Assets:Receivable:TariffRefund"] || 0) : <Skeleton className="h-8 w-32" />}
          hint={!balances ? undefined : parseFloat(bal["Assets:Receivable:TariffRefund"] || "0") === 0 ? "nothing outstanding" : "expected from Customs"}
          tone={parseFloat(bal["Assets:Receivable:TariffRefund"] || "0") === 0 ? "good" : "neutral"} />
        <Stat label="Parts inventory" value={balances ? compact(bal["Assets:Inventory:Parts"]) : <Skeleton className="h-8 w-32" />}
          hint={balances ? "at cost, including duty" : undefined} />
        <Stat label="Owed to customers" value={balances ? compact(owed) : <Skeleton className="h-8 w-32" />}
          hint={!balances ? undefined : owed ? "surcharge credits booked" : "no credits yet"} tone={owed ? "warn" : "neutral"} />
      </div>

      {/* the person's queue */}
      <section>
        <div className="mb-4 flex items-end justify-between">
          <h2 className="text-[22px] font-semibold tracking-tight">
            {!loaded ? "Checking your queue" : mine === 0 ? "Nothing needs you" : `${mine} ${role === "cfo" ? (mine === 1 ? "decision needs" : "decisions need") : (mine === 1 ? "question needs" : "questions need")} you`}
          </h2>
          {busy.length > 0 && <Link to="/activity" className="inline-flex items-center gap-1.5 text-[14px] font-medium text-ink-2 hover:text-ink">
            {busy.length} event{busy.length === 1 ? "" : "s"} being worked <ArrowRight className="h-4 w-4" /></Link>}
        </div>

        {!loaded && <div className="rounded-xl bg-surface p-6 shadow-card"><Skeleton className="h-6 w-80" /><Skeleton className="mt-4 h-4 w-full" /><Skeleton className="mt-2 h-4 w-2/3" /></div>}

        {loaded && role === "cfo" && forCfo.map((w: any) => {
          const p = w.plain || {};
          return (
            <Link key={w.id} to={`/items/${w.id}`} className="mb-4 block rounded-xl bg-surface p-6 shadow-card ring-2 ring-inset ring-warn/40 transition-shadow hover:shadow-pop">
              <div className="flex items-start justify-between gap-8">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.06em] text-warn"><ShieldAlert className="h-4 w-4" /> Your call</div>
                  <div className="mt-2 text-[20px] font-semibold leading-snug">{p.title}</div>
                  <p className="mt-2 text-[15px] leading-relaxed text-ink-2">{p.summary}</p>
                  {(w.why_you || p.why_you) && <p className="mt-3 text-[14px] text-ink"><span className="font-semibold">Why you:</span> {w.why_you || p.why_you}.</p>}
                  <div className="mt-2 text-[13px] text-ink-3">Proposed by {p.proposer}{p.computed ? " · every figure computed, not estimated" : ""}{p.checked ? " · all checks pass" : ""}</div>
                </div>
                {p.amount && <div className="shrink-0 text-right"><Amount value={p.amount} className="hero-num text-[34px]" /><div className="text-[12px] text-ink-3">USD</div></div>}
              </div>
              {p.if_approved && <div className="mt-4 rounded-lg bg-surface-2 px-4 py-3 text-[14px] text-ink-2"><span className="font-semibold text-ink">If you approve: </span>{p.if_approved}</div>}
              <div className="mt-5 flex flex-wrap items-center gap-3" onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}>
                <Button variant="approve" size="lg" onClick={(e) => decide(e, w.id, "approve")}>Approve</Button>
                {declining === w.id ? (
                  <>
                    <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why? The desk learns from it."
                      className="h-12 w-80 rounded-lg border border-line-2 bg-surface px-3 text-[14px]" autoFocus />
                    <Button variant="danger" size="lg" disabled={!reason.trim()} onClick={(e) => decide(e, w.id, "reject")}>Confirm decline</Button>
                    <Button variant="ghost" size="lg" onClick={(e) => { e.preventDefault(); setDeclining(null); }}>Cancel</Button>
                  </>
                ) : <Button variant="danger" size="lg" onClick={(e) => { e.preventDefault(); e.stopPropagation(); setDeclining(w.id); }}>Decline</Button>}
                <span className="ml-auto inline-flex items-center gap-1 text-[14px] font-medium text-ink-2">Open the decision <ArrowRight className="h-4 w-4" /></span>
              </div>
            </Link>
          );
        })}

        {loaded && (role === "lawyer" || role === "cfo") && openQuestions.map((q: any) => (
          <Link key={q.question_id} to={`/lawyer#${q.question_id}`} className={cx("mb-4 block rounded-xl bg-surface p-6 shadow-card transition-shadow hover:shadow-pop",
            role === "lawyer" ? "ring-2 ring-inset ring-info/40" : "")}>
            <div className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.06em] text-info"><Gavel className="h-4 w-4" /> {role === "lawyer" ? "A contract question for you" : "With the lawyer"}</div>
            <p className="mt-3 text-[17px] leading-snug">“{q.text.length > 260 ? q.text.slice(0, 258) + "…" : q.text}”</p>
            <div className="mt-4 flex items-center gap-3 text-[14px] font-medium text-ink-2">
              {role === "lawyer" ? "Answer it" : "Waiting on the lawyer"} <ArrowRight className="h-4 w-4" />
            </div>
          </Link>
        ))}

        {loaded && mine === 0 && (
          <div className="rounded-xl border-2 border-dashed border-line-2 bg-surface px-8 py-12 text-center">
            <CheckCircle2 className="mx-auto h-8 w-8 text-good" />
            <div className="mt-3 text-[18px] font-semibold">{busy.length ? "The agents are on it" : "Nothing needs you right now"}</div>
            <p className="mx-auto mt-2 max-w-lg text-[14px] text-ink-2">
              {busy.length
                ? "Routine entries post on your standing authorization. You are asked only when the desk is genuinely stuck."
                : "When money moves, a tariff changes or a document arrives, the desk works it in the background and asks you only when it is stuck: an ambiguous clause, a document that changes a decision, pushback, or an amount above your cap."}
            </p>
            {busy.length === 0 && (
              <Button variant="accent" size="lg" className="mt-6" onClick={trigger}>
                <Zap className="h-4 w-4" /> Simulate an event: a tariff refund arrives
              </Button>
            )}
            {busy.length === 0 && <div className="mt-3 text-[12.5px] text-ink-3">{demoMode === "live" ? "live agents; uses API credit" : "recorded agents; no model calls"}</div>}
          </div>
        )}
      </section>

      {/* what posted without asking */}
      {loaded && role === "cfo" && routine.length > 0 && (
        <Card title={<span className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-good" /> Posted on your standing authorization</span> as any}
          aside={<span className="num text-[14px] text-ink-2">{routine.length} entr{routine.length === 1 ? "y" : "ies"} · {money(routineTotal)}</span>} pad={false}>
          <ul>
            {routine.map((w: any) => (
              <li key={w.id}>
                <Link to={`/items/${w.id}`} className="flex items-center gap-4 border-b border-line px-6 py-4 last:border-0 hover:bg-surface-2">
                  <CheckCircle2 className="h-5 w-5 shrink-0 text-good" />
                  <div className="min-w-0 flex-1">
                    <div className="text-[15px] font-semibold">{w.plain?.title}</div>
                    <div className="mt-0.5 text-[13px] text-ink-3">{w.plain?.summary}</div>
                  </div>
                  {w.amount && <Amount value={w.amount} className="text-[16px] font-medium" />}
                  <Badge tone="good">posted</Badge>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* trust strip */}
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <Card title={<span>Five <Term k="invariant">checks</Term> the books must always pass</span> as any}
          aside={<Badge tone={open.length ? "bad" : "good"} dot>{open.length ? `${open.length} failing` : "all holding"}</Badge>}>
          <div className="grid gap-3 sm:grid-cols-5">
            {CHECKS.map(([t, tip], i) => {
              const v = open.find((o: any) => o.invariant === `INV-${i + 1}`);
              return (
                <div key={t} title={tip} className={cx("rounded-lg p-4 ring-1 ring-inset", v ? "bg-bad-soft ring-bad/30" : "bg-good-soft ring-good/20")}>
                  {v ? <ShieldAlert className="h-5 w-5 text-bad" /> : <ShieldCheck className="h-5 w-5 text-good" />}
                  <div className="mt-2 text-[14px] font-semibold leading-snug">{t}</div>
                  <div className={cx("num mt-1.5 text-[13px] font-medium", v ? "text-bad" : "text-good")}>{v ? `off by ${money(v.magnitude)}` : "holding"}</div>
                </div>
              );
            })}
          </div>
        </Card>
        <Card title="Books tie out">
          {report ? (
            <div>
              <div className={cx("hero-num text-[40px]", (report.after || report.before).failed ? "text-bad" : (report.after || report.before).expected_findings ? "text-warn" : "text-good")}>
                {(report.after || report.before).ok}<span className="text-ink-3">/{(report.after || report.before).total}</span>
              </div>
              <div className="mt-1 text-[14px] text-ink-2">checks agree{(report.after || report.before).expected_findings ? `; ${(report.after || report.before).expected_findings} known starting discrepanc${(report.after || report.before).expected_findings === 1 ? "y" : "ies"}` : ""}</div>
              <Link to="/books" className="mt-4 inline-flex items-center gap-1 text-[14px] font-medium underline-offset-4 hover:underline">See every pair <ArrowRight className="h-4 w-4" /></Link>
            </div>
          ) : <div className="text-[14px] text-ink-2">The ledger is checked against the bank, Customs, the stock records and billing after every event.</div>}
        </Card>
      </div>
    </div>
  );
}
