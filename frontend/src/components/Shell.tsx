import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Activity, BookOpenCheck, FlaskConical, Gavel, Inbox, ListChecks, Wrench, X } from "lucide-react";
import { ROLES, type RoleKey } from "../lib/api";
import { useDesk } from "../lib/desk";
import { cx } from "./ui";

const NAV = [
  { to: "/", label: "Needs you", icon: Inbox, end: true },
  { to: "/activity", label: "Activity", icon: Activity },
  { to: "/items", label: "Decisions", icon: ListChecks },
  { to: "/lawyer", label: "Lawyer", icon: Gavel },
  { to: "/books", label: "Books", icon: BookOpenCheck },
];

const TITLES: Record<string, string> = {
  "/": "Needs you", "/activity": "What the desk is doing", "/items": "Decisions", "/lawyer": "Lawyer",
  "/books": "Books", "/dev": "Developer tools",
};

export default function Shell() {
  const { role, setRole, status, scenario, health, connected, notices, dismiss, demoMode, setDemoMode } = useDesk();
  const brain = health?.llm?.provider === "creao" ? (health?.llm?.dry_run ? "Creao agents (dry run)" : "Creao agents") : "Live model";
  const { pathname } = useLocation();
  const title = TITLES[pathname] || (pathname.startsWith("/items/") ? "Decision" : "Tariff Desk");
  const running = ["queued", "running"].includes(status?.run?.state || scenario?.run?.state || "");

  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 flex h-screen w-[224px] shrink-0 flex-col border-r border-line bg-surface">
        <div className="flex items-center gap-3 px-5 pt-5 pb-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-ink text-accent"><FlaskConical className="h-[18px] w-[18px]" /></div>
          <div>
            <div className="text-[14px] font-semibold leading-tight">Tariff Desk</div>
            <div className="text-[11.5px] text-ink-3">Brewline Equipment Co.</div>
          </div>
        </div>
        <nav className="flex flex-col gap-0.5 px-3">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => cx(
              "flex items-center gap-2.5 rounded-md px-3 py-2.5 text-[15px] font-medium transition-colors",
              isActive ? "bg-surface-2 text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink")}>
              <Icon className="h-4 w-4" />{label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-3 px-3 pb-5">
          <NavLink to="/dev" className={({ isActive }) => cx(
            "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[12.5px] transition-colors",
            isActive ? "bg-surface-2 text-ink" : "text-ink-3 hover:bg-surface-2 hover:text-ink")}>
            <Wrench className="h-3.5 w-3.5" />Developer tools
          </NavLink>
          <div className="flex items-center gap-2 px-2.5 text-[12px] text-ink-3">
            <span className={cx("h-2 w-2 shrink-0 rounded-full", connected ? (running ? "bg-accent-2 pulse" : "bg-good") : "bg-bad")} />
            {connected ? (running ? "the desk is working" : "connected") : "reconnecting"}
          </div>
          <div className="px-2.5 text-[12px] text-ink-2">
            {scenario?.run?.llm === "live" ? brain : scenario?.run?.llm === "scripted" ? "Recorded replay · no model calls" : "No agent run yet"}
            <div className="mt-1 text-ink-3">Fictional company · simulated lawyer</div>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-line bg-paper/90 px-8 py-4 backdrop-blur">
          <h1 className="text-[24px] font-semibold tracking-tight">{title}</h1>
          <div className="flex items-center gap-4">
            <label className="text-[12px] text-ink-3">Next run
              <select aria-label="Demo mode" value={demoMode} disabled={running}
                onChange={(e) => setDemoMode(e.target.value as "scripted" | "live")}
                className="ml-2 rounded-md border border-line bg-surface px-2 py-1.5 text-ink">
                <option value="scripted">Recorded replay · no API cost</option>
                <option value="live">{health?.llm?.provider === "creao" ? "Live Creao agents · uses credit" : "Live model · uses API credit"}</option>
              </select>
            </label>
            <RoleSwitch role={role} setRole={setRole} />
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1320px] flex-1 px-10 py-8"><Outlet /></main>
      </div>

      <div className="fixed bottom-5 right-5 z-50 flex w-[380px] flex-col gap-2">
        {notices.map((n) => (
          <div key={n.id} className={cx("flex items-start gap-3 rounded-md px-4 py-3 text-[13px] shadow-pop",
            n.tone === "good" && "bg-good text-white", n.tone === "bad" && "bg-bad text-white",
            n.tone === "warn" && "bg-ink text-white", n.tone === "info" && "bg-surface text-ink")}>
            <span className="flex-1">{n.text}</span>
            <button onClick={() => dismiss(n.id)} className="cursor-pointer opacity-70 hover:opacity-100" aria-label="Dismiss"><X className="h-4 w-4" /></button>
          </div>
        ))}
      </div>
    </div>
  );
}

function RoleSwitch({ role, setRole }: { role: RoleKey; setRole: (r: RoleKey) => void }) {
  return (
    <div className="flex items-center gap-3">
      <span className="hidden text-[12.5px] text-ink-3 lg:inline">{ROLES[role].blurb}</span>
      <div className="flex rounded-md bg-surface-2 p-0.5 ring-1 ring-inset ring-line" role="tablist" aria-label="Act as">
        {(Object.keys(ROLES) as RoleKey[]).map((k) => (
          <button key={k} role="tab" aria-selected={role === k} onClick={() => setRole(k)}
            className={cx("cursor-pointer rounded-[7px] px-4 py-2 text-[14px] font-semibold transition-colors",
              role === k ? "bg-ink text-white shadow-sm" : "text-ink-2 hover:text-ink")}>
            {ROLES[k].title}
          </button>
        ))}
      </div>
    </div>
  );
}
