/* Every tie-out as two numbers from two places. Equal is green. */
import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { CheckDot, cx } from "./ui";

type Check = { group: string; name: string; left_label: string; left: string; right_label: string; right: string; ok: boolean; expected?: boolean; note?: string };

export default function TieOutBoard({ checks, title }: { checks: Check[]; title?: string }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const groups = Array.from(new Set(checks.map((c) => c.group)));
  const okCount = checks.filter((c) => c.ok).length;
  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between">
        <div className="text-[13px] text-ink-2">{title}</div>
        <div className={cx("num text-[13px] font-medium", okCount === checks.length ? "text-good" : "text-bad")}>{okCount}/{checks.length} tie</div>
      </div>
      <div className="divide-y divide-line rounded-md border border-line">
        {groups.map((g) => {
          const rows = checks.filter((c) => c.group === g);
          const bad = rows.filter((c) => !c.ok).length;
          const isOpen = open[g] ?? bad > 0;
          return (
            <div key={g}>
              <button onClick={() => setOpen((o) => ({ ...o, [g]: !isOpen }))}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-2 cursor-pointer">
                <CheckDot ok={bad === 0} />
                <span className="text-[13px] font-medium">{g}</span>
                <span className="num text-[12px] text-ink-3">{rows.length - bad}/{rows.length}</span>
                <ChevronDown className={cx("ml-auto h-4 w-4 text-ink-3 transition-transform", isOpen && "rotate-180")} />
              </button>
              {isOpen && (
                <table className="w-full border-t border-line text-[12.5px]">
                  <tbody>
                    {rows.map((c) => (
                      <tr key={c.name} className="border-b border-line/70 last:border-0">
                        <td className="w-6 px-4 py-2 align-top"><CheckDot ok={c.ok} /></td>
                        <td className="py-2 pr-3 align-top font-medium">{c.name.replace(/^\[(before|after)\] /, "")}{c.expected && <div className="mt-1 text-[11.5px] text-ink-3">Expected starting discrepancy</div>}</td>
                        <td className="py-2 pr-3 align-top text-ink-2">
                          <div className="text-[11.5px] text-ink-3">{c.left_label}</div>
                          <div className="num">{c.left}</div>
                        </td>
                        <td className="w-5 py-2 text-center align-top text-ink-3">{c.ok ? "=" : "≠"}</td>
                        <td className="py-2 pr-4 align-top text-ink-2">
                          <div className="text-[11.5px] text-ink-3">{c.right_label}</div>
                          <div className="num">{c.right}</div>
                          {c.note && <div className="mt-0.5 text-[11.5px] text-ink-3">{c.note}</div>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
