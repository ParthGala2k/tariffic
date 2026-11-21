/* The narration: what happened, in the words the log already carries. Technical
   codes (action names, ids) only appear when `technical` is on. */
import { useState } from "react";
import { AlertTriangle, Bot, Gavel, Landmark, ShieldCheck, UserRound, Workflow } from "lucide-react";
import { Link } from "react-router-dom";
import { when } from "../lib/api";
import type { DeskEvent } from "../lib/desk";
import { Badge, Mono, cx } from "./ui";

function iconFor(e: DeskEvent) {
  if (!e.allowed) return AlertTriangle;
  if (e.actor === "gateway") return Landmark;
  if (e.action.startsWith("invariant")) return ShieldCheck;
  if (e.actor.startsWith("ai_agent")) return Bot;
  if (e.actor.startsWith("human_lawyer")) return Gavel;
  if (e.actor.startsWith("human")) return UserRound;
  return Workflow;
}

// Plumbing rows nobody but a developer wants to read (the server marks them too).
const PLUMBING = new Set(["run:step", "run:tie_outs", "run:run", "run:reset", "sync", "match_question"]);

function plainLabel(e: DeskEvent): string {
  // The server writes the plain reading; older rows fall back to the label minus path noise.
  if (e.plain) return e.plain;
  return e.label
    .replace(/data\/documents\/contracts\//g, "contract ")
    .replace(/data\/documents\/inbox\//g, "")
    .replace(/data\/reference\/refund_policy\.md/g, "the refund policy")
    .replace(/data\/reference\/hts_programs\.csv/g, "the tariff programme table")
    .replace(/\.md\b/g, "");
}

export default function Feed({ events, limit = 80, subject, technical = false, height = "max-h-[520px]" }:
  { events: DeskEvent[]; limit?: number; subject?: string; technical?: boolean; height?: string }) {
  const [refusedOnly, setRefusedOnly] = useState(false);
  const rows = events
    .filter((e) => !e.noise && (!subject || e.subject === subject) && (!refusedOnly || !e.allowed)
      && (technical || (!PLUMBING.has(e.action) && !e.technical_only)))
    .slice(-limit)
    .reverse();
  const refused = events.filter((e) => !e.allowed && !e.noise).length;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[12px] text-ink-3">{refused ? `${refused} attempt${refused === 1 ? "" : "s"} stopped by a check` : "nothing stopped by a check"}</span>
        <label className="flex cursor-pointer items-center gap-2 text-[12px] text-ink-2">
          <input type="checkbox" checked={refusedOnly} onChange={(e) => setRefusedOnly(e.target.checked)} className="accent-ink" />
          stopped only
        </label>
      </div>
      <ol className={cx("overflow-y-auto rounded-md border border-line", height)}>
        {rows.length === 0 && <li className="px-4 py-6 text-center text-[12.5px] text-ink-3">Quiet. When an event arrives, the desk's work shows here as it happens.</li>}
        {rows.map((e) => {
          const Icon = iconFor(e);
          return (
            <li key={e.event_id} className={cx("flex items-start gap-3 border-b border-line px-3 py-2 last:border-0", !e.allowed && "bg-bad-soft/60")}>
              <Mono className="w-[62px] shrink-0 pt-0.5 text-ink-3">{when(e.at)}</Mono>
              <Icon className={cx("mt-0.5 h-4 w-4 shrink-0", !e.allowed ? "text-bad" : "text-ink-3")} />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] leading-snug">{technical ? e.label : plainLabel(e)}</div>
                {technical && (
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11.5px] text-ink-3">
                    <Badge tone="neutral" className="!px-1.5 !py-0 !text-[11px]">{e.who}</Badge>
                    <span className="num">{e.action}</span>
                    {e.subject && <Link to={`/items/${e.subject}`} className="num underline-offset-2 hover:underline">{e.subject}</Link>}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
