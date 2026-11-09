import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { FleetPort, HostView } from "../lib/types";

type Filter = "all" | "localhost" | "lan" | "tailnet";
type StateFilter = "all" | FleetPort["status"];

const STATE_LABEL: Record<FleetPort["status"], string> = {
  online: "Online", offline: "Offline", pending: "In attesa", unknown: "Sconosciuto",
};

function isLocal(bind: string | null) {
  return bind === "127.0.0.1" || bind === "::1";
}

function isVerifiedUrl(row: FleetPort): boolean {
  if (!row.web || !row.url || row.status !== "online" || row.tailnet !== "on") return false;
  try {
    const url = new URL(row.url);
    return url.protocol === "https:" && url.hostname.endsWith(".ts.net") &&
      !url.username && !url.password && !url.search && !url.hash && (url.pathname === "/" || !url.pathname);
  } catch {
    return false;
  }
}

export function currentPortRows(rows: FleetPort[], hosts: HostView[]): FleetPort[] {
  const live = new Map(hosts.map((host) => [host.id, host]));
  return rows.map((row) => {
    const host = live.get(row.host_id);
    const sampledAt = row.sampled_at ? Date.parse(row.sampled_at) : NaN;
    const recent = Number.isFinite(sampledAt) && Math.abs(Date.now() - sampledAt) <= 90_000;
    if (host?.lifecycle === "managed" && host.reachable && !host.stale && recent) return row;
    return {
      ...row, status: host?.lifecycle === "pending" ? "pending" : row.status === "offline" ? "offline" : "unknown",
      web: false, url: null, links: [],
    };
  });
}

export function PortsDirectoryRows({ rows, hostFilter, scopeFilter, stateFilter }: {
  rows: FleetPort[];
  hostFilter: string;
  scopeFilter: Filter;
  stateFilter: StateFilter;
}) {
  const visible = rows.filter((row) =>
    (hostFilter === "all" || row.host_id === hostFilter) &&
    (stateFilter === "all" || row.status === stateFilter) &&
    (scopeFilter === "all" ||
      (scopeFilter === "localhost" && isLocal(row.bind)) ||
      (scopeFilter === "lan" && row.links.some((link) => link.scope === "lan")) ||
      (scopeFilter === "tailnet" && isVerifiedUrl(row)))
  );

  if (visible.length === 0) {
    return <p className="rounded-lg border border-ink-700 p-4 text-sm text-ink-300">Nessuna porta verificabile per questi filtri.</p>;
  }
  return (
    <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {visible.map((row, i) => {
        const url = isVerifiedUrl(row) ? row.url : null;
        return (
          <li key={`${row.host_id}:${row.bind}:${row.port}:${i}`} className="min-w-0 rounded-xl border border-ink-700 bg-ink-900/55 px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong className="text-sm text-ink-100">{row.host_name}</strong>
              <span className="text-xs text-ink-300">{STATE_LABEL[row.status]}</span>
            </div>
            <p className="mt-2 break-words font-mono text-sm text-ink-200">
              {row.port ? `${row.protocol.toUpperCase()} · ${row.bind}:${row.port}` : "Porte non disponibili"}
            </p>
            <p className="mt-1 break-words text-xs text-ink-400">
              {row.sampled_at ? `Campione ${row.sampled_at}` : "Campione non verificabile"} · Tailscale {row.tailnet}
            </p>
            {row.truncated && <p className="mt-1 text-xs text-ink-300">Inventario parziale: limite di porte raggiunto.</p>}
            {row.links.filter((link) => link.scope !== "tailnet").map((link) => (
              <p key={`${link.scope}:${link.url}`} className="mt-1 break-all text-xs text-ink-400">
                {link.scope === "server-local" ? "Solo sul server" : "LAN, accessibilità client non verificata"}: {link.url}
              </p>
            ))}
            {url ? (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 break-all text-xs text-ink-300">{url}</span>
                <a className="rounded-lg border border-ink-600 px-3 py-2 text-sm text-ink-100 underline-offset-2 hover:underline" href={url} target="_blank" rel="noopener noreferrer">Apri</a>
                <button type="button" className="rounded-lg border border-ink-600 px-3 py-2 text-sm text-ink-100" onClick={() => {
                  void navigator.clipboard.writeText(url).catch(() => window.alert("Impossibile copiare il link."));
                }}>Copia link</button>
              </div>
            ) : (
              <p className="mt-2 text-xs text-ink-400">Nessun URL tailnet verificato e apribile da iPad</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function ServicesPorts({ hosts, connectionOffline }: { hosts: HostView[]; connectionOffline: boolean }) {
  const [rows, setRows] = useState<FleetPort[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [hostFilter, setHostFilter] = useState("all");
  const [scopeFilter, setScopeFilter] = useState<Filter>("all");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");

  useEffect(() => {
    if (connectionOffline) return;
    const timer = window.setInterval(() => setRefresh((n) => n + 1), 45_000);
    return () => window.clearInterval(timer);
  }, [connectionOffline]);

  useEffect(() => {
    if (connectionOffline) return;
    let alive = true;
    // The agent caches expensive native discovery; refresh is user initiated.
    void api.ports().then((data) => {
      if (!alive) return;
      setRows(data);
      setError(null);
      setLoading(false);
    }).catch(() => {
      if (!alive) return;
      setError("Inventario porte non disponibile.");
      setLoading(false);
    });
    return () => { alive = false; };
  }, [connectionOffline, refresh]);

  return (
    <section className="space-y-3" aria-label="Servizi e porte">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-ink-100">Servizi e porte</h2>
          <p className="text-xs text-ink-400">Socket TCP osservati. Solo i link HTTPS Tailscale verificati sono apribili da iPad.</p>
        </div>
        <button type="button" disabled={loading || connectionOffline} className="rounded-lg border border-ink-600 px-3 py-2 text-sm text-ink-200 disabled:opacity-50" onClick={() => { setLoading(true); setRefresh((n) => n + 1); }}>Aggiorna</button>
      </div>
      <div className="flex flex-wrap gap-2">
        <label className="text-xs text-ink-300">Host
          <select className="ml-2 rounded-lg border border-ink-600 bg-ink-900 p-2" value={hostFilter} onChange={(event) => setHostFilter(event.target.value)}>
            <option value="all">Tutti</option>
            {hosts.map((host) => <option key={host.id} value={host.id}>{host.name}</option>)}
          </select>
        </label>
        <label className="text-xs text-ink-300">Rete
          <select className="ml-2 rounded-lg border border-ink-600 bg-ink-900 p-2" value={scopeFilter} onChange={(event) => setScopeFilter(event.target.value as Filter)}>
            <option value="all">Tutte</option>
            <option value="localhost">Localhost</option>
            <option value="lan">LAN/bind esterno</option>
            <option value="tailnet">Tailscale HTTPS</option>
          </select>
        </label>
        <label className="text-xs text-ink-300">Stato
          <select className="ml-2 rounded-lg border border-ink-600 bg-ink-900 p-2" value={stateFilter} onChange={(event) => setStateFilter(event.target.value as StateFilter)}>
            <option value="all">Tutti</option>
            {Object.entries(STATE_LABEL).map(([state, label]) => <option key={state} value={state}>{label}</option>)}
          </select>
        </label>
      </div>
      {connectionOffline ? <p role="status" className="text-sm text-ink-300">Control plane offline: nessun link verificabile.</p> :
        error ? <p role="alert" className="text-sm text-ink-300">{error}</p> :
          loading && !rows.length ? <p role="status" className="text-sm text-ink-300">Caricamento porte…</p> :
            <PortsDirectoryRows rows={currentPortRows(rows, hosts)} hostFilter={hostFilter} scopeFilter={scopeFilter} stateFilter={stateFilter} />}
    </section>
  );
}
