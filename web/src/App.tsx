import { useCallback, useEffect, useMemo, useState } from "react";
import { HostDetail } from "./components/HostDetail";
import { OmniRoutePanel } from "./components/OmniRoutePanel";
import { api, subscribeFleet, type StreamStatus } from "./lib/api";
import { SEVERITY_BG } from "./lib/format";
import type {
  ControlCenterSummary,
  FleetSnapshot,
  HistoryPoint,
  ProfileFleet,
  Receipt,
} from "./lib/types";
import { ActivityView } from "./views/ActivityView";
import { AgentsSection } from "./views/AgentsSection";
import { FleetView } from "./views/FleetView";
import { GithubView } from "./views/GithubView";
import { OverviewView } from "./views/OverviewView";
import { ProfilesView } from "./views/ProfilesView";
import { WorkspaceView } from "./views/WorkspaceView";

type View = "overview" | "fleet" | "agents" | "profiles" | "workspace" | "github" | "audit";

const VIEWS = new Set<View>(["overview", "fleet", "agents", "profiles", "workspace", "github", "audit"]);

function viewFromLocation(): View {
  const candidate = window.location.hash.replace(/^#\/?/, "") as View;
  return VIEWS.has(candidate) ? candidate : "overview";
}

const NAV_PATHS: { id: View; label: string; path: string }[] = [
  { id: "overview", label: "Panoramica", path: "M4 13h6V4H4zM14 20h6v-7h-6zM14 9h6V4h-6zM4 20h6v-3H4z" },
  { id: "fleet", label: "Flotta", path: "M4 6h7v5H4zM13 6h7v5h-7zM4 13h7v5H4zM13 13h7v5h-7z" },
  { id: "agents", label: "Agenti", path: "M9 5a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM17 7a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM4 18v-2a5 5 0 0 1 10 0v2H4ZM15 14a4 4 0 0 1 5 4h-4" },
  { id: "profiles", label: "Account", path: "M3 5h18v14H3zM3 9h18M6 7h.01M9 7h.01" },
  { id: "workspace", label: "Workspace", path: "M6 3h10l3 3v15H6zM16 3v4h4M9 11h7M9 15h7" },
  { id: "github", label: "GitHub", path: "M9 19c-4 1-4-2-6-3m12 3c4 1 4-2 6-3M12 2a10 10 0 0 0-3 19v-5c-3 1-4-1-4-3m7-11a10 10 0 0 1 3 19v-5c3 1 4-1 4-3" },
  { id: "audit", label: "Audit", path: "M4 19h16M5 15l4-4 3 3 7-8M17 6h2v2" },
];

const NAV = NAV_PATHS.map((entry) => ({
  id: entry.id,
  label: entry.label,
  icon: (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-[18px] w-[18px]"
    >
      <path d={entry.path} />
    </svg>
  ),
}));

const STATUS_COPY: Record<StreamStatus, { text: string; tone: string }> = {
  connecting: { text: "Connessione…", tone: "bg-signal-warn" },
  live: { text: "In diretta", tone: "bg-signal-ok" },
  polling: { text: "Aggiornamento periodico", tone: "bg-signal-warn" },
  offline: { text: "Nessuna connessione", tone: "bg-signal-crit" },
};

export default function App() {
  const initialView = useMemo(viewFromLocation, []);
  const [view, setView] = useState<View>(initialView);
  const [snapshot, setSnapshot] = useState<FleetSnapshot | null>(null);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [fleetSubscriptionEpoch, setFleetSubscriptionEpoch] = useState(0);
  const [histories, setHistories] = useState<Record<string, HistoryPoint[]>>({});
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [summary, setSummary] = useState<ControlCenterSummary | null>(null);
  const [profiles, setProfiles] = useState<ProfileFleet | null>(null);
  const [loadErrors, setLoadErrors] = useState<{
    summary: string | null;
    profiles: string | null;
    receipts: string | null;
  }>({ summary: null, profiles: null, receipts: null });
  const [selectedHost, setSelectedHost] = useState<string | null>(null);
  const [visited, setVisited] = useState<Set<View>>(() => new Set([initialView]));

  const navigate = useCallback((nextView: View) => {
    const nextHash = `#${nextView}`;
    if (window.location.hash !== nextHash) {
      window.history.pushState(null, "", nextHash);
    }
    setVisited((current) => new Set(current).add(nextView));
    setView(nextView);
  }, []);

  useEffect(() => {
    const syncLocation = () => {
      const nextView = viewFromLocation();
      setVisited((current) => new Set(current).add(nextView));
      setView(nextView);
    };
    window.addEventListener("popstate", syncLocation);
    window.addEventListener("hashchange", syncLocation);
    return () => {
      window.removeEventListener("popstate", syncLocation);
      window.removeEventListener("hashchange", syncLocation);
    };
  }, []);

  useEffect(() => {
    const label = NAV_PATHS.find((entry) => entry.id === view)?.label ?? "Plancia";
    document.title = `${label} · Plancia`;
  }, [view]);

  useEffect(
    () => subscribeFleet(setSnapshot, setStatus),
    [fleetSubscriptionEpoch],
  );

  const loadReceipts = useCallback(() => {
    setLoadErrors((current) => ({ ...current, receipts: null }));
    void api
      .receipts()
      .then((data) => setReceipts(data.receipts))
      .catch((reason: Error) => setLoadErrors((current) => ({ ...current, receipts: reason.message })));
  }, []);

  const loadSummary = useCallback((refresh = false) => {
    setLoadErrors((current) => ({ ...current, summary: null }));
    void api
      .summary(refresh)
      .then(setSummary)
      .catch((reason: Error) => setLoadErrors((current) => ({ ...current, summary: reason.message })));
  }, []);

  const loadProfiles = useCallback((refresh = false) => {
    setLoadErrors((current) => ({ ...current, profiles: null }));
    void api
      .profiles(refresh)
      .then(setProfiles)
      .catch((reason: Error) => setLoadErrors((current) => ({ ...current, profiles: reason.message })));
  }, []);

  const retryFleet = useCallback(() => {
    setStatus("connecting");
    setFleetSubscriptionEpoch((value) => value + 1);
  }, []);

  const refreshOverview = useCallback(() => {
    loadSummary(true);
    loadReceipts();
    loadProfiles(true);
    void api.fleet().then(setSnapshot).catch(() => undefined);
  }, [loadProfiles, loadReceipts, loadSummary]);

  useEffect(() => {
    if (view === "overview") {
      loadReceipts();
      loadSummary();
      loadProfiles();
      return;
    }
    if (view === "fleet") {
      loadReceipts();
      loadSummary();
      return;
    }
    if (view === "profiles") {
      loadProfiles();
      return;
    }
    if (view === "audit") loadReceipts();
  }, [loadProfiles, loadReceipts, loadSummary, view]);

  useEffect(() => {
    const visible = () => typeof document === "undefined" || document.visibilityState === "visible";
    const timers: number[] = [];
    if (view === "overview" || view === "fleet") {
      timers.push(window.setInterval(() => visible() && loadSummary(true), 90_000));
    }
    if (view === "overview" || view === "fleet" || view === "audit") {
      timers.push(window.setInterval(() => visible() && loadReceipts(), 30_000));
    }
    if (view === "overview") {
      // Browser inventory fans out over SSH, so refresh it less aggressively
      // than local telemetry and source summaries.
      timers.push(window.setInterval(() => visible() && loadProfiles(true), 300_000));
    }
    return () => timers.forEach((timer) => window.clearInterval(timer));
  }, [loadProfiles, loadReceipts, loadSummary, view]);

  // The comparison rows need a little history for their sparklines; refresh it
  // slowly so the live socket stays the fast path.
  const hostIds = useMemo(
    () => (snapshot ? snapshot.hosts.map((host) => host.id).join(",") : ""),
    [snapshot],
  );

  useEffect(() => {
    if (!hostIds || (view !== "fleet" && view !== "overview")) return;
    let alive = true;
    const load = () => {
      hostIds.split(",").forEach((hostId) => {
        void api
          .history(hostId, 60)
          .then((data) => {
            if (alive) setHistories((prev) => ({ ...prev, [hostId]: data.points }));
          })
          .catch(() => undefined);
      });
    };
    load();
    const timer = window.setInterval(load, 60000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [hostIds, view]);

  const selected = snapshot?.hosts.find((host) => host.id === selectedHost) ?? null;
  const statusInfo = STATUS_COPY[status];

  return (
    <div data-app-surface="plancia" className="flex min-h-full min-w-0 flex-col bg-ink-950 lg:flex-row">
      <a className="skip-link" href="#main-content" onClick={(event) => {
        event.preventDefault();
        document.getElementById("main-content")?.focus();
      }}>Vai al contenuto</a>
      <header className="mobile-header sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-ink-800 bg-ink-950 px-4 lg:hidden">
        <Brand />
        <ConnectionStatus status={statusInfo} />
      </header>
      <aside className="hidden h-screen w-60 shrink-0 flex-col border-r border-ink-800 bg-ink-900 px-4 py-6 lg:sticky lg:top-0 lg:flex">
        <div className="mb-10 px-2"><Brand /></div>
        <nav aria-label="Navigazione principale" className="flex flex-col gap-1">
          {NAV.map((entry) => (
            <button
              key={entry.id}
              type="button"
              onClick={() => navigate(entry.id)}
              aria-current={view === entry.id ? "page" : undefined}
              className={`flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                view === entry.id ? "bg-ink-800 text-ink-100" : "text-ink-300 hover:bg-ink-850 hover:text-ink-100"
              }`}
            >
              {entry.icon}<span>{entry.label}</span>
            </button>
          ))}
        </nav>
        <div className="mt-auto border-t border-ink-800 px-2 pt-5">
          <ConnectionStatus status={statusInfo} />
        </div>
      </aside>
      <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 px-4 pt-5 pb-28 outline-none sm:px-6 lg:px-8 lg:py-7">
        <div className="mx-auto max-w-[1540px]">
          <section hidden={view !== "overview"}>
            {visited.has("overview") && (
              <OverviewView
                fleet={snapshot}
                connectionOffline={status === "offline"}
                summary={summary}
                profiles={profiles}
                receipts={receipts}
                loadErrors={loadErrors}
                onNavigate={navigate}
                onHost={setSelectedHost}
                onRefresh={refreshOverview}
              />
            )}
          </section>
          <section hidden={view !== "fleet"}>
          {visited.has("fleet") && <OmniRoutePanel active={view === "fleet"} />}
          {visited.has("fleet") &&
            (snapshot ? (
              <FleetView
                snapshot={snapshot}
                connectionOffline={status === "offline"}
                histories={histories}
                summary={summary}
                receipts={receipts}
                onSelectHost={setSelectedHost}
                onNavigate={navigate}
              />
            ) : status === "offline" ? (
              <FleetUnavailable onRetry={retryFleet} />
            ) : (
              <SkeletonFleet />
            ))}
          </section>
          <section hidden={view !== "agents"} aria-label="Agenti">
            {visited.has("agents") && <AgentsSection active={view === "agents"} />}
          </section>
          <section hidden={view !== "profiles"}>
            {visited.has("profiles") && <ProfilesView active={view === "profiles"} initialFleet={profiles} onInventory={setProfiles} />}
          </section>
          <section hidden={view !== "workspace"}>
            {visited.has("workspace") && <WorkspaceView active={view === "workspace"} onRefresh={loadReceipts} />}
          </section>
          <section hidden={view !== "github"}>
            {visited.has("github") && <GithubView active={view === "github"} />}
          </section>
          <section hidden={view !== "audit"}>
          {visited.has("audit") && (
            <ActivityView receipts={receipts} onRefresh={loadReceipts} />
          )}
          </section>
        </div>
      </main>
      <nav aria-label="Navigazione principale" className="mobile-dock fixed inset-x-0 bottom-0 z-40 border-t border-ink-700 bg-ink-900 lg:hidden">
        <div className="mobile-dock-grid mx-auto grid max-w-xl grid-cols-7 px-1">
          {NAV.map((entry) => (
            <button
              key={entry.id}
              type="button"
              onClick={() => navigate(entry.id)}
              aria-current={view === entry.id ? "page" : undefined}
              className={`mobile-dock-item flex min-h-14 min-w-0 flex-col items-center justify-center gap-1 rounded-lg px-0.5 text-center font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent ${
                view === entry.id ? "bg-ink-800 text-ink-100" : "text-ink-300"
              }`}
            >
              {entry.icon}<span className="mobile-dock-label">{entry.label}</span>
            </button>
          ))}
        </div>
      </nav>

      {selected && (
        <HostDetail
          host={selected}
          connectionOffline={status === "offline" || status === "connecting"}
          receipts={receipts}
          onClose={() => setSelectedHost(null)}
          onReceiptsChanged={loadReceipts}
        />
      )}
    </div>
  );
}

function Brand() {
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-ink-600 bg-ink-800 text-ink-100">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
          <path d="M6 20V4h8a4 4 0 0 1 0 8H6" />
        </svg>
      </span>
      <span className="text-base font-semibold tracking-tight text-ink-100">Plancia</span>
    </div>
  );
}

function ConnectionStatus({ status }: { status: { text: string; tone: string } }) {
  return (
    <div role="status" aria-live="polite" className="flex min-w-0 items-center gap-2 text-right text-[11px] leading-4 text-ink-300">
      <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${status.tone}`} />
      <span>{status.text}</span>
    </div>
  );
}

function FleetUnavailable({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="rounded-xl border border-signal-crit/25 bg-signal-crit/5 p-6">
      <p className="eyebrow">Flotta</p>
      <h1 className="mt-2 text-xl font-semibold text-ink-100">Telemetria non raggiungibile</h1>
      <p role="status" className="mt-2 max-w-2xl text-sm leading-6 text-ink-400">
        Plancia non ha ancora ricevuto un campione né una risposta HTTP valida. Lo stato di alimentazione dei computer resta sconosciuto finché il control plane non risponde.
      </p>
      <button type="button" onClick={onRetry} className="button-secondary mt-4">
        Riprova connessione
      </button>
    </div>
  );
}

function SkeletonFleet() {
  return (
    <div className="space-y-5">
      <div className="grid overflow-hidden rounded-xl border border-ink-700 bg-ink-900/65 sm:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <div
            key={index}
            className="h-20 animate-pulse border-b border-ink-700 bg-ink-900/60 last:border-b-0 sm:border-r sm:border-b-0 sm:last:border-r-0"
          />
        ))}
      </div>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="overflow-hidden rounded-xl border border-ink-700 bg-ink-900/55">
          {[0, 1, 2, 3].map((index) => (
            <div
              key={index}
              className="h-24 animate-pulse border-b border-ink-700 bg-ink-900/50 last:border-b-0"
            />
          ))}
        </div>
        <div className="h-72 animate-pulse rounded-xl border border-ink-700 bg-ink-900/55" />
      </div>
      <p className="flex items-center gap-2 text-xs text-ink-400">
        <span className={`h-2 w-2 animate-pulse rounded-full ${SEVERITY_BG.unknown}`} />
        In attesa del primo campione dagli agenti…
      </p>
    </div>
  );
}
