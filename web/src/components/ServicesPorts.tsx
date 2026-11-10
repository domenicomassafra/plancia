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

function isPrivateLanBind(bind: string): boolean {
  const octets = bind.split(".").map(Number);
  if (octets.length === 4 && octets.every((part) => Number.isInteger(part) && part >= 0 && part <= 255)) {
    return octets[0] === 10 ||
      (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
      (octets[0] === 192 && octets[1] === 168);
  }
  return /^f[cd][0-9a-f:]+$/i.test(bind);
}

function verifiedLanUrl(row: FleetPort): string | null {
  if (!row.web || row.status !== "online" || !row.bind || !row.port || !isPrivateLanBind(row.bind)) return null;
  for (const link of row.links) {
    if (link.scope !== "lan") continue;
    try {
      const url = new URL(link.url);
      const hostname = url.hostname.replace(/^\[|\]$/g, "");
      if ((url.protocol === "http:" || url.protocol === "https:") &&
          hostname === row.bind && Number(url.port) === row.port &&
          !url.username && !url.password && !url.search && !url.hash &&
          url.pathname === "/") return url.href;
    } catch {
      // An unparseable endpoint is never offered to the browser.
    }
  }
  return null;
}

function copyLink(url: string) {
  void navigator.clipboard.writeText(url).catch(() => window.alert("Impossibile copiare il link."));
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

export function PortsDirectoryRows({ rows, hosts = [], hostFilter, scopeFilter, stateFilter }: {
  rows: FleetPort[];
  hosts?: HostView[];
  hostFilter: string;
  scopeFilter: Filter;
  stateFilter: StateFilter;
}) {
  const visible = rows.filter((row) =>
    (hostFilter === "all" || row.host_id === hostFilter) &&
    (stateFilter === "all" || row.status === stateFilter) &&
    (scopeFilter === "all" ||
      (scopeFilter === "localhost" && isLocal(row.bind)) ||
      (scopeFilter === "lan" && verifiedLanUrl(row) !== null) ||
      (scopeFilter === "tailnet" && isVerifiedUrl(row)))
  );

  const groups = new Map<string, { name: string; rows: FleetPort[] }>();
  if (scopeFilter === "all" && stateFilter === "all") {
    for (const host of hosts) {
      if (hostFilter === "all" || hostFilter === host.id) {
        groups.set(host.id, { name: host.name, rows: [] });
      }
    }
  }
  for (const row of visible) {
    if (!groups.has(row.host_id)) groups.set(row.host_id, { name: row.host_name, rows: [] });
    groups.get(row.host_id)!.rows.push(row);
  }

  if (groups.size === 0) {
    return <p className="rounded-lg border border-ink-700 p-4 text-sm text-ink-300">Nessuna porta verificabile per questi filtri.</p>;
  }
  return (
    <div className="space-y-5">
      {Array.from(groups, ([hostId, group]) => {
        const sockets = group.rows.filter((row) => row.port !== null);
        const openable = sockets.filter((row) => isVerifiedUrl(row) || verifiedLanUrl(row)).length;
        const status = group.rows[0]?.status ?? "unknown";
        return <section key={hostId} aria-label={group.name} className="space-y-3 rounded-xl border border-ink-700 bg-ink-900/30 p-3 sm:p-4">
          <header className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="font-semibold text-ink-100">{group.name}</h3>
              <p className="mt-1 text-xs text-ink-400">{sockets.length} {sockets.length === 1 ? "porta osservata" : "porte osservate"} · {openable} servizi con link di rete</p>
            </div>
            <span className="text-xs text-ink-300">{STATE_LABEL[status]}</span>
          </header>
          {group.rows.length === 0 ? <p className="text-sm text-ink-300">Inventario non disponibile per questo host.</p> :
          <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {group.rows.map((row, i) => {
        const url = isVerifiedUrl(row) ? row.url : null;
        const lanUrl = verifiedLanUrl(row);
        return (
          <li key={`${row.host_id}:${row.bind}:${row.port}:${i}`} className="min-w-0 rounded-xl border border-ink-700 bg-ink-900/55 px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong className="text-sm text-ink-100">{row.service || row.process || (url || lanUrl ? "Servizio web verificato" : "Porta TCP")}</strong>
              <span className="text-xs text-ink-300">{row.port ? "In ascolto" : STATE_LABEL[row.status]}</span>
            </div>
            {row.service && row.process && row.service !== row.process && <p className="mt-1 text-xs text-ink-400">{row.process}</p>}
            <p className="mt-2 break-words font-mono text-sm text-ink-200">
              {row.port ? `${row.protocol.toUpperCase()} · ${row.bind}:${row.port}` : "Porte non disponibili"}
            </p>
            <p className="mt-1 break-words text-xs text-ink-400">
              {row.sampled_at ? `Campione ${row.sampled_at}` : "Campione non verificabile"} · Tailscale {row.tailnet}
            </p>
            {row.truncated && <p className="mt-1 text-xs text-ink-300">Inventario parziale: limite di porte raggiunto.</p>}
            {row.links.filter((link) => link.scope === "server-local").map((link) => (
              <p key={`${link.scope}:${link.url}`} className="mt-1 break-all text-xs text-ink-400">
                Solo sul server: {link.url}
              </p>
            ))}
            {url ? (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 break-all text-xs text-ink-300">{url}</span>
                <a className="rounded-lg border border-ink-600 px-3 py-2 text-sm text-ink-100 underline-offset-2 hover:underline" href={url} target="_blank" rel="noopener noreferrer">Apri</a>
                <button type="button" className="rounded-lg border border-ink-600 px-3 py-2 text-sm text-ink-100" onClick={() => copyLink(url)}>Copia link</button>
              </div>
            ) : null}
            {lanUrl && <div className="mt-3 space-y-2">
              <p className="text-xs text-ink-400">LAN: servizio verificato sull'host; accessibilità da iPad non verificata (serve la stessa rete).</p>
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 break-all text-xs text-ink-300">{lanUrl}</span>
                <a className="rounded-lg border border-ink-600 px-3 py-2 text-sm text-ink-100 underline-offset-2 hover:underline" href={lanUrl} target="_blank" rel="noopener noreferrer">Apri LAN</a>
                <button type="button" className="rounded-lg border border-ink-600 px-3 py-2 text-sm text-ink-100" onClick={() => copyLink(lanUrl)}>Copia LAN</button>
              </div>
            </div>}
            {!url && !lanUrl && <p className="mt-2 text-xs text-ink-400">Nessun URL di rete verificato e apribile da questo dispositivo</p>}
          </li>
        );
      })}
          </ul>}
        </section>;
      })}
    </div>
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
          <p className="text-xs text-ink-400">Porte TCP per host. Link HTTPS Tailscale verificati e URL LAN con verifica limitata al server; localhost resta solo sul computer.</p>
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
            <PortsDirectoryRows rows={currentPortRows(rows, hosts)} hosts={hosts} hostFilter={hostFilter} scopeFilter={scopeFilter} stateFilter={stateFilter} />}
    </section>
  );
}
