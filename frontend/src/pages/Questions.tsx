/* The lawyer's desk: questions the agents could not decide, the answers, and the rules. */
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../lib/api";
import { useDesk, useLive } from "../lib/desk";
import { Term } from "../components/Term";
import { Badge, Button, Card, Empty } from "../components/ui";

export default function Questions() {
  const { role, actor, act } = useDesk();
  const { data: qs } = useLive(() => api.questions(), [], 3000);
  const { data: pol } = useLive(() => api.policies(), [], 3000);
  const { data: pre } = useLive(() => api.precedents(), [], 4000);
  const [answer, setAnswer] = useState<Record<string, string>>({});
  const [rule, setRule] = useState<Record<string, boolean>>({});
  const [ruleText, setRuleText] = useState<Record<string, { id: string; rule: string; scope: string; applies_to: string }>>({});
  const { hash } = useLocation();
  useEffect(() => {
    if (!hash || !qs) return;
    const el = document.getElementById(hash.slice(1));
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("ring-2", "ring-info"); }
  }, [hash, qs]);

  const openQs = (qs?.questions || []).filter((q: any) => q.state === "OPEN");
  const answered = (qs?.questions || []).filter((q: any) => q.state !== "OPEN");

  const submit = async (q: any) => {
    const rp = rule[q.id] ? {
      id: ruleText[q.id]?.id || "RULE-001", rule: ruleText[q.id]?.rule || "", scope: ruleText[q.id]?.scope || "",
      applies_to: (ruleText[q.id]?.applies_to || "").split(",").map((s) => s.trim()).filter(Boolean),
    } : null;
    await act(() => api.answer(q.id, actor, answer[q.id] || "", null, rp),
      (r: any) => `Answer recorded${r.precedent_id ? " as a precedent" : ""}${r.rule_proposed ? "; the rule goes to the controller" : ""}.`);
  };

  return (
    <div className="grid gap-5 xl:grid-cols-[1fr_380px]">
      <div className="space-y-5">
        <Card title="Open questions" aside={<Badge tone={openQs.length ? "warn" : "neutral"}>{openQs.length}</Badge>}>
          {openQs.length === 0 && <Empty title="Nothing waiting on the lawyer" hint="When a contract clause is open to more than one reading, the agent asks here instead of deciding." />}
          {openQs.map((q: any) => (
            <div key={q.id} id={q.id} className="mb-3 rounded-lg border border-line p-5">
              <div className="text-[13px] text-ink-3">Asked by {q.asked_by.startsWith("ai_agent") ? "the revenue agent" : q.asked_by} · <Link to={`/items/${q.work_item_id}`} className="underline-offset-2 hover:underline">see the case</Link></div>
              <p className="mt-2 text-[15px] leading-relaxed">“{q.text}”</p>
              {actor === q.to_role ? (
                <div className="mt-3 space-y-2">
                  <textarea rows={3} value={answer[q.id] || ""} onChange={(e) => setAnswer({ ...answer, [q.id]: e.target.value })}
                    placeholder="Your answer. Quote the clause you rely on." className="w-full rounded-md border border-line bg-surface p-2.5 text-[13px]" />
                  {role === "lawyer" && (
                    <label className="flex items-center gap-2 text-[12.5px]"><input type="checkbox" checked={!!rule[q.id]} onChange={(e) => setRule({ ...rule, [q.id]: e.target.checked })} className="accent-ink" />
                      Also propose a <Term k="rule" /> for every contract with this wording</label>
                  )}
                  {rule[q.id] && (
                    <div className="grid gap-2 sm:grid-cols-2">
                      {([["id", "rule id, e.g. RULE-001"], ["scope", "which contracts it covers"], ["rule", "the rule in one sentence"], ["applies_to", "contract ids, comma separated"]] as const).map(([f, ph]) => (
                        <input key={f} value={ruleText[q.id]?.[f] || ""} placeholder={ph}
                          onChange={(e) => setRuleText({ ...ruleText, [q.id]: { ...(ruleText[q.id] || { id: "", rule: "", scope: "", applies_to: "" }), [f]: e.target.value } })}
                          className="rounded-md border border-line bg-surface px-2.5 py-2 text-[12.5px]" />
                      ))}
                    </div>
                  )}
                  <Button variant="primary" disabled={!(answer[q.id] || "").trim()} onClick={() => submit(q)}>Answer</Button>
                </div>
              ) : (
                <p className="mt-3 text-[12.5px] text-ink-3">Only the {q.to_role.replace("human_", "")} can answer this.</p>
              )}
            </div>
          ))}
        </Card>

        <Card title="Answered" aside={<Badge tone="neutral">{answered.length}</Badge>}>
          {answered.length === 0 && <div className="text-[12.5px] text-ink-3">No answers yet.</div>}
          {answered.map((q: any) => (
            <div key={q.id} className="mb-3 rounded-md bg-surface-2 p-3 text-[13px]">
              <div className="text-[12.5px] text-ink-3">answered by the {q.answered_by?.replace("human_", "")} · <Link to={`/items/${q.work_item_id}`} className="underline-offset-2 hover:underline">the case</Link></div>
              <p className="mt-1.5">“{q.text}”</p>
              <p className="mt-1.5 text-ink-2">→ {q.answer}</p>
            </div>
          ))}
        </Card>
      </div>

      <div className="space-y-5">
        <Card title={<span><Term k="rule">Rules</Term> the desk works by</span> as any} aside={<span className="text-[12px] text-ink-3">adopted by the controller</span>}>
          <ul className="space-y-2">
            {pol?.policies?.map((p: any) => (
              <li key={p.ref} className="rounded-md border border-line p-3 text-[12.5px]">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{p.name}</span>
                  <Badge tone={p.status === "APPROVED" ? "good" : p.status === "PROPOSED" ? "warn" : p.status === "FLAGGED_BAD" ? "bad" : "neutral"} className="ml-auto shrink-0">
                    {p.status === "APPROVED" ? "in force" : p.status === "PROPOSED" ? "proposed" : p.status === "FLAGGED_BAD" ? "never apply" : p.status.toLowerCase()}
                  </Badge>
                </div>
                {p.scope && <div className="mt-0.5 text-ink-3">covers: {p.scope}</div>}
                <div className="mt-1 text-ink-3">{p.proposed_by.startsWith("ai_agent") ? "learned by the desk" : `from the ${p.proposed_by.replace("human_", "")}`}{p.approved_by ? ", adopted by the controller" : ""}</div>
                {p.status === "PROPOSED" && p.rule?.kind === "distributor_passthrough" && (
                  <div className="mt-2 text-[12px] text-ink-3">Proposed only. Automatic reuse on other customers is planned.</div>
                )}
                {p.status === "PROPOSED" && p.rule?.kind !== "distributor_passthrough" && (
                  <Button size="sm" variant="approve" className="mt-2" disabled={role !== "cfo"}
                    title={role !== "cfo" ? "Only the CFO adopts a rule" : undefined}
                    onClick={() => act(() => api.approvePolicy(p.ref, actor, "adopted on screen"), () => "Adopted. The desk applies it from now on.")}>
                    Adopt
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
        <Card title={<span><Term k="precedent">Precedents</Term></span> as any}>
          {pre && pre.count === 0 && <div className="text-[12.5px] text-ink-3">A lawyer's answer becomes a precedent the desk can cite in a matching case.</div>}
          {pre?.precedents?.map((p: any) => (
            <div key={p.precedent_id} className="mb-2 rounded-md bg-surface-2 p-3 text-[12.5px]">
              <div className="text-ink-3">from the {p.decided_by.replace("human_", "")}{p.clause ? ` on ${p.clause}` : ""}</div>
              <p className="mt-1 text-ink-2">{p.decision}</p>
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}
