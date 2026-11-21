/* Primitives. One look for every button, badge, card and number. */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Check, Loader2 } from "lucide-react";
import { money } from "../lib/api";

export function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "accent" | "seat1" | "seat2" | "seat3";

const TONE_BG: Record<Tone, string> = {
  neutral: "bg-surface-2 text-ink-2 ring-line",
  good: "bg-good-soft text-good ring-good/15",
  warn: "bg-warn-soft text-warn ring-warn/15",
  bad: "bg-bad-soft text-bad ring-bad/15",
  info: "bg-info-soft text-info ring-info/15",
  accent: "bg-accent text-accent-ink ring-accent-2",
  seat1: "bg-info-soft text-seat1 ring-seat1/15",
  seat2: "bg-good-soft text-seat2 ring-seat2/15",
  seat3: "bg-[#ece9fb] text-seat3 ring-seat3/15",
};

export function Badge({ tone = "neutral", children, className, dot }: { tone?: Tone; children: ReactNode; className?: string; dot?: boolean }) {
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12.5px] font-semibold ring-1 ring-inset", TONE_BG[tone], className)}>
      {dot && <span className={cx("h-1.5 w-1.5 rounded-full", tone === "neutral" ? "bg-ink-3" : "bg-current")} />}
      {children}
    </span>
  );
}

export function Card({ children, className, title, aside, pad = true }: { children: ReactNode; className?: string; title?: ReactNode; aside?: ReactNode; pad?: boolean }) {
  return (
    <section className={cx("rounded-xl bg-surface shadow-card", className)}>
      {(title || aside) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-6 py-4">
          <h3 className="text-[14px] font-semibold uppercase tracking-[0.06em] text-ink">{title}</h3>
          {aside}
        </header>
      )}
      <div className={pad ? "p-6" : ""}>{children}</div>
    </section>
  );
}

type Variant = "primary" | "approve" | "danger" | "ghost" | "outline" | "accent";
const VARIANT: Record<Variant, string> = {
  primary: "bg-ink text-white hover:bg-[#2a2d33] disabled:bg-ink/40",
  approve: "bg-good text-white hover:bg-[#126d33] disabled:bg-good/40",
  danger: "bg-surface text-bad ring-1 ring-inset ring-bad/30 hover:bg-bad-soft disabled:opacity-50",
  ghost: "bg-transparent text-ink-2 hover:bg-surface-2 disabled:opacity-50",
  outline: "bg-surface text-ink ring-1 ring-inset ring-line-2 hover:bg-surface-2 disabled:opacity-50",
  accent: "bg-accent text-accent-ink hover:bg-accent-2 disabled:opacity-50",
};

export function Button({ variant = "outline", size = "md", loading, children, className, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" | "lg"; loading?: boolean }) {
  const sizes = { sm: "h-9 px-3 text-[13.5px]", md: "h-10 px-4 text-[14px]", lg: "h-12 px-6 text-[15px]" };
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cx("inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors cursor-pointer disabled:cursor-not-allowed", sizes[size], VARIANT[variant], className)}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx("num text-[12.5px] text-ink-2", className)}>{children}</span>;
}

export function Amount({ value, className }: { value: string | number | null | undefined; className?: string }) {
  return <span className={cx("num", className)}>{money(value)}</span>;
}

export function Stat({ label, value, hint, tone = "neutral", mono = false }: { label: ReactNode; value: ReactNode; hint?: ReactNode; tone?: Tone; mono?: boolean }) {
  return (
    <div className="rounded-xl bg-surface p-6 shadow-card">
      <div className="text-[13.5px] font-semibold text-ink-2">{label}</div>
      <div className={cx("mt-2.5 text-[34px] leading-none", mono ? "num font-medium" : "hero-num")}>{value}</div>
      {hint && <div className={cx("mt-2.5 text-[13.5px]", tone === "neutral" ? "text-ink-3" : `text-${tone}`)}>{hint}</div>}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-md border border-dashed border-line-2 px-5 py-8 text-center">
      <div className="text-[13.5px] font-medium text-ink-2">{title}</div>
      {hint && <div className="mt-1 text-[12.5px] text-ink-3">{hint}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton h-4 w-full", className)} />;
}

export function CheckDot({ ok }: { ok: boolean }) {
  return ok ? (
    <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-good text-white"><Check className="h-3 w-3" strokeWidth={3} /></span>
  ) : (
    <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-bad text-white text-[11px] font-bold">!</span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line-2 bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-ink-2">{children}</kbd>;
}

export function Divider() {
  return <hr className="border-0 border-t border-line" />;
}
