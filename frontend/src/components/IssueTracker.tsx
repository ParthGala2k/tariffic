/* One event, as a line of stages. Every state is a work item's state in the desk DB. */
import { motion } from "framer-motion";
import { Check, Gavel, Loader2, UserCheck, X } from "lucide-react";
import { Link } from "react-router-dom";
import { Amount, cx } from "./ui";

export type Stage = {
  id: string; kind: string; state: string; title: string; summary: string; amount: string | null;
  owner: string | null; awaiting: string | null; updated_at: string;
};
export type Issue = { id: string; title: string; kind: string; state: string; opened_at: string; stages: Stage[]; awaiting: string[] };

type Visual = "pending" | "active" | "needs_you" | "with_lawyer" | "done" | "failed";

const SHORT: Record<string, string> = {
  "WI-REFUND-DEPOSIT": "Record the refund", "WI-REFUND-SPLIT": "Split the refund",
  "WI-FORECAST-CORRECTION": "Correct the estimate", "WI-DUTY-RECONCILIATION": "Reconcile duty paid",
  "WI-CUST-002-QUESTION": "Harbor: ask the lawyer", "WI-CUST-002-CLOSE": "Harbor: settle",
  "WI-CORRECTION": "Correcting entry", "WI-ONBOARD-POLICY": "Adopt a rule", "WI-ONBOARD-REVIEW": "Review past practice",
};

export function shortTitle(s: Stage): string {
  if (SHORT[s.kind]) return SHORT[s.kind];
  if (s.kind.startsWith("WI-CUST-") && s.kind.endsWith("-CREDIT")) return s.title.replace(/ for the tariff surcharge$/, "");
  return s.title.length > 34 ? s.title.slice(0, 32) + "…" : s.title;
}

export function visualOf(s: Stage, working: Set<string>): Visual {
  if (s.state === "POSTED" || s.state === "CLOSED") return "done";
  if (s.state === "REJECTED") return "failed";
  if (s.state === "PROPOSED") return "needs_you";
  if (s.state === "NEEDS_INFO") return s.awaiting && s.awaiting !== "desk" ? "with_lawyer" : "active";
  return working.has(s.id) ? "active" : "pending";
}

export default function IssueTracker({ issue, working, compact = false }: { issue: Issue; working: Set<string>; compact?: boolean }) {
  const stages = issue.stages;
  return (
    <ol className={cx("flex flex-col lg:flex-row lg:items-start", compact ? "gap-0" : "gap-0")}>
      {stages.map((s, i) => {
        const v = visualOf(s, working);
        const prevDone = i > 0 && visualOf(stages[i - 1], working) === "done";
        return (
          <li key={s.id} className="relative flex flex-1 min-w-0 flex-row items-start gap-3 lg:flex-col lg:items-center">
            <div className="flex items-center lg:w-full">
              <Line filled={prevDone} side="left" hidden={i === 0} />
              <Node v={v} title={s.title} />
              <Line filled={v === "done"} side="right" hidden={i === stages.length - 1} />
            </div>
            <div className={cx("pb-5 lg:mt-2.5 lg:px-1.5 lg:text-center", compact && "pb-2")}>
              <Link to={`/items/${s.id}`} className={cx("text-[14px] font-semibold underline-offset-4 hover:underline", v === "pending" ? "text-ink-3" : "text-ink")}>
                {shortTitle(s)}
              </Link>
              {!compact && <div className="mt-0.5 text-[12px] text-ink-3">{s.owner || ""}</div>}
              {s.amount && <div className="mt-0.5"><Amount value={s.amount} className="text-[12.5px] text-ink-2" /></div>}
              <div className={cx("mt-1 text-[12px] font-medium",
                v === "needs_you" ? "text-warn" : v === "with_lawyer" ? "text-info" : v === "failed" ? "text-bad" : v === "done" ? "text-good" : "text-ink-3")}>
                {v === "needs_you" ? "needs the CFO" : v === "with_lawyer" ? `with the ${s.awaiting}` : v === "done" ? (s.state === "POSTED" ? "posted" : "settled")
                  : v === "failed" ? "declined" : v === "active" ? "being worked" : "queued"}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Line({ filled, side, hidden }: { filled: boolean; side: "left" | "right"; hidden?: boolean }) {
  return (
    <div className={cx("relative hidden h-[2px] flex-1 overflow-hidden lg:block", hidden ? "bg-transparent" : "bg-line-2", side === "left" ? "mr-2" : "ml-2")}>
      {!hidden && <motion.div className="absolute inset-y-0 left-0 bg-good" initial={{ width: 0 }} animate={{ width: filled ? "100%" : 0 }} transition={{ duration: 0.6, ease: "easeOut" }} />}
    </div>
  );
}

function Node({ v, title }: { v: Visual; title: string }) {
  return (
    <motion.div key={v} initial={{ scale: v === "done" ? 0.7 : 1 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 420, damping: 22 }}
      aria-label={`${title}: ${v}`}
      className={cx("flex h-10 w-10 shrink-0 items-center justify-center rounded-full ring-1 ring-inset",
        v === "pending" && "bg-surface text-ink-3 ring-line-2",
        v === "active" && "bg-ink text-accent ring-ink pulse",
        v === "needs_you" && "bg-warn-soft text-warn ring-warn pulse",
        v === "with_lawyer" && "bg-info-soft text-info ring-info",
        v === "done" && "bg-good text-white ring-good",
        v === "failed" && "bg-bad text-white ring-bad")}>
      {v === "active" ? <Loader2 className="h-4 w-4 animate-spin" /> : v === "needs_you" ? <UserCheck className="h-4 w-4" />
        : v === "with_lawyer" ? <Gavel className="h-4 w-4" /> : v === "done" ? <Check className="h-4 w-4" strokeWidth={3} />
          : v === "failed" ? <X className="h-4 w-4" strokeWidth={3} /> : <span className="h-2 w-2 rounded-full bg-line-2" />}
    </motion.div>
  );
}
