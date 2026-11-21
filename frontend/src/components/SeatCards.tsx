/* The three agents, live: what each is doing now, from /status. */
import { Bot } from "lucide-react";
import { AGENTS, MODE_LABEL } from "../lib/api";
import { Badge, cx } from "./ui";

const BUSY = new Set(["picking_up", "thinking", "investigating", "consulting_rules", "computing", "proposing", "asking", "revising"]);

function plainLabel(s: any): string {
  if (s.plain) return s.plain;
  return String(s.label || "").replace(/data\/documents\/contracts\//g, "contract ").replace(/data\/documents\/inbox\//g, "")
    .replace(/data\/reference\/refund_policy\.md/g, "the refund policy").replace(/data\/reference\/hts_programs\.csv/g, "the tariff programme table")
    .replace(/\.md\b/g, "").replace(/^[^:]+: /, "");
}

export default function SeatCards({ seats }: { seats: Record<string, any> | undefined; titles?: Record<string, string> }) {
  const list = seats ? Object.values(seats) : [];
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {list.map((s: any) => {
        const busy = BUSY.has(s.mode);
        const tone = s.mode === "refused" ? "bad" : s.mode.startsWith("waiting") ? "warn" : s.mode === "answered" ? "info" : busy ? "accent" : "neutral";
        const agent = AGENTS[s.seat];
        return (
          <div key={s.seat} className={cx("rounded-lg bg-surface p-4 shadow-card transition-shadow", busy && "ring-2 ring-accent-2")}>
            <div className="flex items-start gap-3">
              <div className={cx("flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-white", busy && "pulse",
                s.seat === "seat1" && "bg-seat1", s.seat === "seat2" && "bg-seat2", s.seat === "seat3" && "bg-seat3")}>
                <Bot className="h-[18px] w-[18px]" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[14px] font-semibold leading-tight">{agent?.name || s.name}</div>
                <div className="mt-0.5 text-[11.5px] leading-snug text-ink-3">{agent?.job}</div>
              </div>
            </div>
            <div className="mt-3"><Badge tone={tone as any} dot>{MODE_LABEL[s.mode] || s.mode}</Badge></div>
            <p className="mt-2 min-h-[36px] text-[12.5px] leading-snug text-ink-2">{plainLabel(s)}</p>
          </div>
        );
      })}
    </div>
  );
}
