/* Shared live state: status polling, the SSE feed, the active scenario, the role
   the person is acting as, and a notice line for results and refusals. */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, api, ROLES, type RoleKey } from "./api";

export type DeskEvent = {
  event_id: number; at: string; actor: string; who: string; seat: string | null; action: string;
  subject: string | null; allowed: boolean; mode: string; label: string; plain?: string;
  technical_only?: boolean; reason: string | null; evidence: any; diff: any; noise: boolean;
};

type Notice = { id: number; tone: "good" | "bad" | "info" | "warn"; text: string };

type Desk = {
  status: any; scenario: any; health: any;
  events: DeskEvent[]; lastEventId: number; connected: boolean;
  role: RoleKey; actor: string; setRole: (r: RoleKey) => void;
  demoMode: "scripted" | "live"; setDemoMode: (mode: "scripted" | "live") => void;
  notices: Notice[]; notify: (tone: Notice["tone"], text: string) => void; dismiss: (id: number) => void;
  refresh: () => Promise<void>;
  act: <T,>(fn: () => Promise<T>, okText?: (r: T) => string) => Promise<T | undefined>;
};

const Ctx = createContext<Desk | null>(null);

function loadRole(): RoleKey {
  try {
    const saved = localStorage.getItem("desk.role");
    if (saved && saved in ROLES) return saved as RoleKey;
  } catch { /* private mode etc. */ }
  return "cfo";
}

export function DeskProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<any>(null);
  const [scenario, setScenario] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [events, setEvents] = useState<DeskEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [role, setRoleState] = useState<RoleKey>(loadRole);
  const [demoMode, setDemoMode] = useState<"scripted" | "live">("scripted");
  const [notices, setNotices] = useState<Notice[]>([]);
  const lastId = useRef(0);
  const streamKey = useRef("");

  const setRole = useCallback((r: RoleKey) => {
    setRoleState(r);
    try { localStorage.setItem("desk.role", r); } catch { /* ignore */ }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [s, sc] = await Promise.all([api.status(), api.activeScenario()]);
      setStatus(s);
      setScenario(sc);
    } catch { /* the server may be restarting; keep the last state */ }
  }, []);

  useEffect(() => {
    refresh();
    api.health().then(setHealth).catch(() => undefined);
    const t = setInterval(refresh, 1500);
    const h = setInterval(() => api.health().then(setHealth).catch(() => undefined), 10000);
    return () => { clearInterval(t); clearInterval(h); };
  }, [refresh]);

  // One EventSource per world: re-open when a scenario is activated or dropped,
  // because the sandbox has its own log and event ids start over.
  const world = scenario?.active || "base";
  useEffect(() => {
    if (streamKey.current === world) return;
    streamKey.current = world;
    setEvents([]);
    lastId.current = 0;
    const source = new EventSource(`/stream?after=0`);
    source.addEventListener("desk", (e: MessageEvent) => {
      const row = JSON.parse(e.data) as DeskEvent;
      lastId.current = Math.max(lastId.current, row.event_id);
      setEvents((prev) => {
        const next = prev.length > 600 ? prev.slice(prev.length - 400) : prev.slice();
        next.push(row);
        return next;
      });
    });
    source.addEventListener("seats", () => refresh());
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    return () => source.close();
  }, [world, refresh]);

  const notify = useCallback((tone: Notice["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setNotices((n) => [...n, { id, tone, text }]);
    setTimeout(() => setNotices((n) => n.filter((x) => x.id !== id)), tone === "bad" ? 9000 : 5000);
  }, []);
  const dismiss = useCallback((id: number) => setNotices((n) => n.filter((x) => x.id !== id)), []);

  const act = useCallback(async <T,>(fn: () => Promise<T>, okText?: (r: T) => string) => {
    try {
      const result = await fn();
      if (okText) notify("good", okText(result));
      await refresh();
      return result;
    } catch (error) {
      if (error instanceof ApiError) {
        const reason = error.body?.detail || error.message;
        notify(error.status === 403 ? "warn" : "bad", error.status === 403 ? `Refused by code: ${reason}` : reason);
      } else {
        notify("bad", String(error));
      }
      await refresh();
      return undefined;
    }
  }, [notify, refresh]);

  const value = useMemo<Desk>(() => ({
    status, scenario, health, events, lastEventId: lastId.current, connected, demoMode, setDemoMode,
    role, actor: ROLES[role].actor, setRole, notices, notify, dismiss, refresh, act,
  }), [status, scenario, health, events, connected, role, setRole, notices, notify, dismiss, refresh, act, demoMode]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useDesk(): Desk {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useDesk outside DeskProvider");
  return ctx;
}

/* Poll any GET on an interval, re-fetching when the world changes or events arrive. */
export function useLive<T>(fetcher: () => Promise<T>, deps: any[] = [], everyMs = 2500) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { scenario, lastEventId } = useDesk();
  const world = scenario?.active || "base";
  // A fetch in flight is never thrown away: while a run streams events, the deps change
  // many times a second, and cancelling each fetch left the page on its skeleton. Refetch
  // at most every 600ms, keep the result whenever it lands, stop only on unmount.
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;
  const mounted = useRef(true);
  const timer = useRef<number | undefined>(undefined);
  const last = useRef(0);
  const go = useCallback(() => {
    timer.current = undefined;
    last.current = Date.now();
    fetchRef.current().then((d) => { if (mounted.current) { setData(d); setError(null); } })
      .catch((e) => { if (mounted.current) setError(e instanceof ApiError ? e.message : String(e)); });
  }, []);
  useEffect(() => () => { mounted.current = false; if (timer.current) window.clearTimeout(timer.current); }, []);
  useEffect(() => {
    if (timer.current === undefined) timer.current = window.setTimeout(go, Math.max(0, 600 - (Date.now() - last.current)));
    const t = everyMs ? window.setInterval(go, everyMs) : undefined;
    return () => { if (t) window.clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [world, lastEventId, ...deps]);
  return { data, error };
}
