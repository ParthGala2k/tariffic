/* A word with a hover tip. Jargon stays out of the sentence; the explanation is one hover away. */
import type { ReactNode } from "react";
import { GLOSSARY } from "../lib/api";
import { cx } from "./ui";

export function Term({ k, children, className }: { k: keyof typeof GLOSSARY | string; children?: ReactNode; className?: string }) {
  const entry = GLOSSARY[k];
  const label = children ?? entry?.term ?? k;
  if (!entry) return <span className={className}>{label}</span>;
  return (
    <span className={cx("tip relative cursor-help border-b border-dotted border-ink-3", className)} tabIndex={0}>
      {label}
      <span role="tooltip" className="tip-box">{entry.tip}</span>
    </span>
  );
}

/* A raw technical name (account string, id, ref) shown on hover under a plain label. */
export function Raw({ label, raw, className }: { label: ReactNode; raw: string; className?: string }) {
  return (
    <span className={cx("tip relative cursor-help", className)} tabIndex={0}>
      {label}
      <span role="tooltip" className="tip-box num">{raw}</span>
    </span>
  );
}
