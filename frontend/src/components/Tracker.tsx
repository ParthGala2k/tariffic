/* The workflow tracker: one node per step of the run, drawn off the audit log.
   A node lights up when its run:step row says "started", gets its check when it
   says "finished", and the line to the next node fills in. Nothing here is
   guessed from timers; every state is a logged row. */
import { motion } from "framer-motion";
import {
  BookOpen, Check, CheckCheck, Landmark, Loader2, Receipt, RefreshCw, Scale, ShieldCheck, Split, UserCheck, X,
} from "lucide-react";
import { STEP_LABELS } from "../lib/api";
import type { DeskEvent } from "../lib/desk";
import { Badge, Mono, cx } from "./ui";

type NodeState = "pending" | "active" | "waiting" | "done" | "failed";
type Node = { key: string; title: string; sub: string; state: NodeState; workItem?: string | null; itemState?: string | null; seat?: string; detail?: string };

const ICONS: Record<string, any> = {
  sync: RefreshCw, onboarding: BookOpen, invariants: ShieldCheck, deposit: Landmark, split: Split,
  credit: Receipt, question: Scale, tie_outs: CheckCheck,
};

export function deriveNodes(steps: string[], events: DeskEvent[], status: any, scenario: any): Node[] {
  const answered: Record<string, string> = {};
  const byStep: Record<string, { status: string; workItem: string | null; note: string }> = {};
  let tieOuts: NodeState = "pending";
  for (const e of events) {
    if (e.action === "run:step" && e.evidence?.step) {
      byStep[e.evidence.step] = { status: e.evidence.status, workItem: e.evidence.work_item || e.subject, note: e.reason || "" };
    }
    if (e.action === "run:tie_outs") tieOuts = e.allowed ? "done" : "failed";
    if (e.action === "answer" && e.allowed && e.subject) answered[e.subject] = e.who;
  }
  const seatByItem: Record<string, string> = {};
  for (const s of Object.values(status?.seats || {}) as any[]) if (s.work_item) seatByItem[s.work_item] = s.seat;

  const synced = Boolean(scenario?.report?.before) || events.some((e) => e.action === "sync");
  const anyStarted = Object.keys(byStep).length > 0;
  const nodes: Node[] = [{
    key: "sync", ...STEP_LABELS.sync,
    state: synced ? "done" : anyStarted ? "done" : "pending",
    detail: scenario?.report?.before ? `${scenario.report.before.ok}/${scenario.report.before.total} tie-outs before the run` : undefined,
  }];
  for (const step of steps) {
    const meta = STEP_LABELS[step] || { title: step, sub: "" };
    const seen = byStep[step];
    const state: NodeState = !seen ? "pending" : seen.status === "started" ? "active"
      : seen.status === "waiting" ? "waiting" : seen.status === "failed" ? "failed" : "done";
    const wi = seen?.workItem || null;
    let itemState: string | null = null;
    if (wi && status?.seats) {
      const seat = (Object.values(status.seats) as any[]).find((s) => s.work_item === wi);
      itemState = seat?.work_item_state || null;
    }
    nodes.push({ key: step, title: meta.title, sub: meta.sub, state, workItem: wi, itemState,
      seat: meta.seat || (wi ? seatByItem[wi] : undefined),
      detail: seen?.status === "failed" ? seen.note
        : seen?.status === "waiting" ? "needs the controller's decision"
        : wi && answered[wi] ? `answered by the ${answered[wi].toLowerCase()}; nothing to post` : undefined });
  }
  const allDone = steps.length > 0 && steps.every((s) => byStep[s]?.status === "finished");
  nodes.push({
    key: "tie_outs", ...STEP_LABELS.tie_outs,
    state: tieOuts !== "pending" ? tieOuts : allDone ? "active" : "pending",
    detail: scenario?.report?.after ? `${scenario.report.after.ok}/${scenario.report.after.total} tie-outs after the run` : undefined,
  });
  return nodes;
}

export default function Tracker({ nodes }: { nodes: Node[] }) {
  return (
    <ol className="flex flex-col gap-0 lg:flex-row lg:items-start">
      {nodes.map((n, i) => (
        <li key={n.key} className="relative flex flex-1 min-w-0 flex-row items-start gap-3 lg:flex-col lg:items-center lg:gap-0">
          <div className="flex items-center lg:w-full">
            {i > 0 && <Connector filled={nodes[i - 1].state === "done"} side="left" />}
            <NodeCircle node={n} />
            {i < nodes.length - 1 && <Connector filled={n.state === "done"} side="right" />}
          </div>
          <div className="pb-6 lg:mt-3 lg:px-2 lg:text-center">
            <div className={cx("text-[13px] font-semibold", n.state === "pending" ? "text-ink-3" : "text-ink")}>{n.title}</div>
            <div className="mt-0.5 text-[12px] leading-snug text-ink-3">{n.sub}</div>
            {(n.workItem || n.seat) && (
              <div className="mt-2 flex flex-wrap items-center justify-start gap-1.5 lg:justify-center">
                {n.seat && <Badge tone={n.seat as any}>{n.seat.replace("seat", "Seat ")}</Badge>}
                {n.workItem && <Mono className="rounded bg-surface-2 px-1.5 py-0.5">{n.workItem}{n.itemState ? ` · ${n.itemState}` : ""}</Mono>}
              </div>
            )}
            {n.detail && <div className={cx("mt-1.5 text-[12px] font-medium", n.state === "failed" ? "text-bad" : n.state === "waiting" ? "text-warn" : "text-good")}>{n.detail}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Connector({ filled, side }: { filled: boolean; side: "left" | "right" }) {
  return (
    <div className={cx("relative hidden h-[2px] flex-1 overflow-hidden bg-line-2 lg:block", side === "left" ? "mr-2" : "ml-2")}>
      <motion.div className="absolute inset-y-0 left-0 bg-good" initial={{ width: 0 }}
        animate={{ width: filled ? "100%" : 0 }} transition={{ duration: 0.6, ease: "easeOut" }} />
    </div>
  );
}

function NodeCircle({ node }: { node: Node }) {
  const Icon = ICONS[node.key.split(":")[0]] || Check;
  return (
    <div className="relative shrink-0">
      <motion.div
        key={node.state}
        initial={{ scale: node.state === "done" ? 0.7 : 1 }}
        animate={{ scale: 1 }}
        transition={{ type: "spring", stiffness: 420, damping: 22 }}
        className={cx(
          "flex h-11 w-11 items-center justify-center rounded-full ring-1 ring-inset",
          node.state === "pending" && "bg-surface text-ink-3 ring-line-2",
          node.state === "active" && "bg-ink text-accent ring-ink pulse",
          node.state === "waiting" && "bg-warn-soft text-warn ring-warn pulse",
          node.state === "done" && "bg-good text-white ring-good",
          node.state === "failed" && "bg-bad text-white ring-bad")}
        aria-label={`${node.title}: ${node.state}`}>
        {node.state === "active" ? <Loader2 className="h-5 w-5 animate-spin" /> :
          node.state === "waiting" ? <UserCheck className="h-5 w-5" /> :
          node.state === "done" ? <Check className="h-5 w-5" strokeWidth={3} /> :
            node.state === "failed" ? <X className="h-5 w-5" strokeWidth={3} /> :
              <Icon className="h-[18px] w-[18px]" />}
      </motion.div>
    </div>
  );
}
