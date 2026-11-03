import { SEVERITY_BG, SEVERITY_TEXT, exactTimestamp, receiptOutcomeSeverity, relativeTime, sampleAgeLabel, sourceReason } from "../lib/format";
import { overviewFleetStatus, overviewHostState, showCurrentMetrics } from "../lib/overview-state";
import type {
  ControlCenterSummary,
  FleetSnapshot,
  HostView,
  ProfileFleet,
  Receipt,
} from "../lib/types";

type Destination = "fleet" | "profiles" | "workspace" | "audit";

interface OverviewProps {
  fleet: FleetSnapshot | null;
  connectionOffline: boolean;
  summary: ControlCenterSummary | null;
  profiles: ProfileFleet | null;
  receipts: Receipt[];
  loadErrors: { summary: string | null; profiles: string | null; receipts: string | null };
  onNavigate: (view: Destination) => void;
  onHost: (hostId: string) => void;
  onRefresh: () => void;
}

function primaryDisk(host: HostView) {
  return host.sample?.disk?.[0] ?? null;
}

function memoryPressure(host: HostView) {
  return host.sample?.memory?.pressure_used_percent ?? host.sample?.memory?.percent ?? null;
}

function cpuValue(host: HostView) {
  return host.sample?.cpu?.percent ?? null;
}

function formatPercent(value: number | null | undefined) {
  return value == null ? "—" : `${Math.round(value)}%`;
}

function formatFree(bytes: number | null | undefined) {
  if (bytes == null) return "—";
  const gb = bytes / 1_000_000_000;
  return `${gb >= 10 ? gb.toFixed(0) : gb.toFixed(1)} GB`;
}

export function OverviewView({
  fleet,
  connectionOffline,
  summary,
  profiles,
  receipts,
  loadErrors,
  onNavigate,
  onHost,
  onRefresh,
}: OverviewProps) {
  const hostAlerts = fleet?.hosts.filter(
    (host) => host.lifecycle === "managed" && (!host.reachable || host.stale || host.health.severity !== "ok"),
  ) ?? [];
  const profileOffline = profiles?.inventories.filter((item) => !item.online) ?? [];
  const actionCount = summary?.notion.available ? summary.notion.counts.due_or_overdue ?? 0 : null;
  const actionCountLabel = actionCount == null ? "—" : `${actionCount}${summary?.notion.has_more ? "+" : ""}`;
  const profileInstallations = profiles?.inventories.reduce((sum, inventory) => sum + inventory.profiles.length, 0) ?? null;
  const sourceProblems = [
    connectionOffline ? "fleet-stream" : null,
    summary && !summary.notion.available ? "Notion" : null,
    summary && !summary.obsidian.available ? "Obsidian" : null,
    loadErrors.summary ? "summary" : null,
    loadErrors.profiles ? "profiles" : null,
    loadErrors.receipts ? "receipts" : null,
  ].filter(Boolean);
  const totalAttention = hostAlerts.length + profileOffline.length + sourceProblems.length;
  const attentionReady = Boolean(fleet || summary || profiles || sourceProblems.length);
  const fleetStatus = overviewFleetStatus(fleet, connectionOffline);
  const pendingCount = fleet?.fleet.pending ?? 0;
  const notionStatus = !summary
    ? "unknown"
    : !summary.notion.available
      ? "unknown"
      : (summary.notion.counts.overdue ?? 0) > 0
        ? "warn"
        : "ok";

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Control Center</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-[-0.035em] text-ink-100 sm:text-4xl">
            Stato reale, senza rumore.
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-ink-400">
            Telemetria dei quattro computer con runtime separati, account browser e workspace personale. I valori mancanti restano mancanti: nessun dato viene stimato per riempire la UI.
          </p>
          {fleet && <p className="mt-2 text-[11px] text-ink-400">Snapshot Fleet2 ricevuto {relativeTime(fleet.generated_at)} · {exactTimestamp(new Date(fleet.generated_at * 1000).toISOString())}</p>}
          {summary && <p className="mt-1 text-[11px] text-ink-400">Riepilogo workspace generato {relativeTime(summary.generated_at)} · {exactTimestamp(new Date(summary.generated_at * 1000).toISOString())}</p>}
        </div>
        <button type="button" onClick={onRefresh} className="button-secondary">
          Aggiorna tutto
        </button>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Riepilogo operativo">
        <SummaryCard
          label="Flotta"
          value={connectionOffline ? "—" : fleet ? `${fleet.fleet.reachable}/${fleet.fleet.managed_total}` : "—"}
          detail={connectionOffline ? "control plane non raggiungibile · ultimo snapshot conservato" : fleet ? (pendingCount ? `${pendingCount} runtime pending on boot · stato complessivo parziale` : "runtime gestiti raggiungibili") : "telemetria in caricamento"}
          status={fleetStatus}
          onClick={() => onNavigate("fleet")}
        />
        <SummaryCard
          label="Azioni"
          value={actionCountLabel}
          detail={!summary ? loadErrors.summary ? "Riepilogo fonti non disponibile" : "Notion in caricamento" : summary.notion.available ? `${summary.notion.counts.overdue ?? 0}${summary.notion.has_more ? "+" : ""} in ritardo` : sourceReason(summary.notion.reason)}
          status={notionStatus}
          onClick={() => onNavigate("workspace")}
        />
        <SummaryCard
          label="Account browser"
          value={profiles ? String(profiles.accounts.length) : "—"}
          detail={profiles ? `${profileInstallations ?? 0} installazioni su ${profiles.inventories.length} host` : loadErrors.profiles ? "inventario non disponibile" : "inventario in caricamento"}
          status={loadErrors.profiles ? "unknown" : profileOffline.length ? "unknown" : profiles?.freshness === "stale" ? "unknown" : profiles ? "ok" : "unknown"}
          onClick={() => onNavigate("profiles")}
        />
        <SummaryCard
          label="Attenzione"
          value={attentionReady ? String(totalAttention) : "—"}
          detail={!attentionReady ? "fonti in caricamento" : totalAttention ? "segnali da verificare" : pendingCount ? `${pendingCount} runtime pending · stato non misurato` : "nessun blocco rilevato"}
          status={!attentionReady ? "unknown" : totalAttention ? "warn" : fleetStatus === "unknown" ? "unknown" : "ok"}
          onClick={() => onNavigate("audit")}
        />
      </section>

      {connectionOffline && fleet && (
        <p role="status" className="rounded-xl border border-signal-warn/25 bg-signal-warn/5 px-4 py-3 text-sm leading-6 text-signal-warn">
          Connessione al control plane interrotta. Le metriche sotto sono l'ultimo snapshot ricevuto e non vengono trattate come stato live.
        </p>
      )}

      <div className="grid gap-5 2xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,0.55fr)]">
        <section className="panel overflow-hidden">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Fleet live</p>
              <h2 className="mt-1 text-lg font-semibold text-ink-100">Quattro computer, runtime separati e verificabili</h2>
            </div>
            <button type="button" onClick={() => onNavigate("fleet")} className="text-link">Dettagli fleet →</button>
          </div>
          <div className="grid gap-px bg-ink-800 md:grid-cols-2">
            {(fleet?.hosts ?? []).map((host) => {
              const hostState = overviewHostState(host, connectionOffline);
              const currentMetrics = showCurrentMetrics(host, connectionOffline);
              const hostSeverity = hostState === "pending" || hostState === "stale" ? "unknown" : hostState;
              return (
                <button
                  type="button"
                  key={host.id}
                  onClick={() => onHost(host.id)}
                  className="group bg-ink-900 px-4 py-4 text-left transition hover:bg-ink-850 focus-visible:outline-2 focus-visible:outline-accent"
                >
                  <div className="flex items-center gap-3">
                    <span className={`relative h-2.5 w-2.5 shrink-0 rounded-full ${SEVERITY_BG[hostSeverity]}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="truncate text-sm font-semibold text-ink-100">{host.name}</p>
                        {host.is_control_plane && <span className="tag">control</span>}
                      </div>
                      <p className="mt-0.5 truncate text-[11px] text-ink-500">
                        {hostState === "pending"
                          ? "pending on boot · nessuna sostituzione con Omarchy"
                          : hostState === "stale"
                            ? "stale · ultimo campione, non live"
                            : hostState === "unknown"
                              ? "unknown · nessuna misura corrente affidabile"
                              : host.sample
                                ? `${host.sample.os_version || host.os} · ${sampleAgeLabel(host.sample.sample_age_s)}`
                                : "nessun campione"}
                      </p>
                      {host.sample && hostState !== "pending" && (
                        <p className="mt-1 text-[11px] leading-4 text-ink-400">
                          FleetProbe {host.sample.agent_version} · campione {exactTimestamp(host.sample.sampled_at)} · CPU: {host.sample.cpu?.source || "fonte non dichiarata"}
                        </p>
                      )}
                    </div>
                    <span className="text-ink-600 transition group-hover:translate-x-0.5 group-hover:text-ink-300">→</span>
                  </div>
                  <dl className="mt-4 grid grid-cols-3 gap-3">
                    <MiniMetric label="CPU" value={currentMetrics ? formatPercent(cpuValue(host)) : "—"} />
                    <MiniMetric label="Memoria" value={currentMetrics ? formatPercent(memoryPressure(host)) : "—"} />
                    <MiniMetric
                      label={currentMetrics && primaryDisk(host)?.available_bytes != null
                        ? (primaryDisk(host)?.available_stale ? "Disponibile (cache)" : "Disponibile")
                        : "Libero"}
                      value={currentMetrics ? formatFree(primaryDisk(host)?.available_bytes ?? primaryDisk(host)?.free_bytes) : "—"}
                    />
                  </dl>
                </button>
              );
            })}
            {!fleet && <div className="col-span-full bg-ink-900 p-5 text-sm text-ink-500">Attendo i primi campioni dagli agenti…</div>}
          </div>
        </section>

        <section className="panel overflow-hidden">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Fonti</p>
              <h2 className="mt-1 text-lg font-semibold text-ink-100">Workspace personale</h2>
            </div>
            <button type="button" onClick={() => onNavigate("workspace")} className="text-link">Apri →</button>
          </div>
          <div className="divide-y divide-ink-800">
            <SourceRow
              label="Notion · Actions"
              detail={summary?.notion.available ? `${actionCountLabel} azioni dovute o scadute` : summary ? sourceReason(summary.notion.reason) : loadErrors.summary ? "Riepilogo fonti non disponibile" : "in caricamento"}
              available={summary?.notion.available ?? null}
            />
            <SourceRow
              label="Obsidian · vault canonico"
              detail={summary?.obsidian.available ? `${summary.obsidian.recent.length} note recenti indicizzate` : summary ? sourceReason(summary.obsidian.reason) : loadErrors.summary ? "Riepilogo fonti non disponibile" : "in caricamento"}
              available={summary?.obsidian.available ?? null}
            />
            <SourceRow
              label="Profile Atlas"
              detail={profiles ? `${profiles.inventories.filter((item) => item.online).length}/${profiles.inventories.length} inventari raggiungibili` : loadErrors.profiles ? "Inventario non disponibile" : "in caricamento"}
              available={profiles ? profileOffline.length === 0 && profiles.freshness !== "stale" : null}
            />
          </div>
        </section>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="panel overflow-hidden">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Segnali</p>
              <h2 className="mt-1 text-lg font-semibold text-ink-100">Da verificare</h2>
            </div>
          </div>
          {totalAttention === 0 ? (
            <div className="px-4 py-6 text-sm text-ink-400">Nessun segnale operativo aperto.</div>
          ) : (
            <div className="divide-y divide-ink-800">
              {hostAlerts.map((host) => {
                const state = overviewHostState(host, connectionOffline);
                const status = state === "stale" || state === "pending" ? "unknown" : state;
                return (
                  <button type="button" key={host.id} onClick={() => onHost(host.id)} className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-ink-850">
                    <span className={`mt-1.5 h-2 w-2 rounded-full ${SEVERITY_BG[status]}`} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm text-ink-200">{host.name}</span>
                      <span className={`mt-0.5 block text-xs ${SEVERITY_TEXT[status]}`}>{state === "stale" ? "Stale · ultimo campione, non live" : state === "unknown" ? host.error ?? "Nessuna misura corrente affidabile" : host.health.headline}</span>
                    </span>
                  </button>
                );
              })}
              {profileOffline.map((inventory) => (
                <button type="button" key={`profile:${inventory.host.id}`} onClick={() => onNavigate("profiles")} className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-ink-850">
                  <span className="mt-1.5 h-2 w-2 rounded-full bg-signal-warn" />
                  <span><span className="block text-sm text-ink-200">{inventory.host.label} · profili</span><span className="mt-0.5 block text-xs text-ink-500">{inventory.error_code ?? "inventario non disponibile"}</span></span>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="panel overflow-hidden">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Audit</p>
              <h2 className="mt-1 text-lg font-semibold text-ink-100">Ultime operazioni</h2>
            </div>
            <button type="button" onClick={() => onNavigate("audit")} className="text-link">Tutte →</button>
          </div>
          <div className="divide-y divide-ink-800">
            {receipts.slice(0, 5).map((receipt) => (
              <div key={receipt.id} className="flex items-start gap-3 px-4 py-3">
                <span className={`mt-1.5 h-2 w-2 rounded-full ${SEVERITY_BG[receiptOutcomeSeverity(receipt.outcome)]}`} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-ink-200">{receipt.action}</p>
                  <p className="mt-0.5 truncate text-[11px] text-ink-500">{receipt.host_id} · {relativeTime(receipt.started_at)}</p>
                </div>
              </div>
            ))}
            {!receipts.length && <p className="px-4 py-5 text-sm text-ink-500">Nessuna operazione recente.</p>}
          </div>
        </section>
      </div>
    </div>
  );
}

function SummaryCard({ label, value, detail, status, onClick }: { label: string; value: string; detail: string; status: "ok" | "warn" | "crit" | "unknown"; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="metric-card group">
      <div className="flex items-center justify-between gap-3">
        <span className="eyebrow">{label}</span>
        <span className={`h-2 w-2 rounded-full ${SEVERITY_BG[status]}`} />
      </div>
      <p className="tnum mt-5 text-3xl font-semibold tracking-[-0.04em] text-ink-100">{value}</p>
      <p className="mt-1 truncate text-xs text-ink-500">{detail}</p>
    </button>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-[9px] font-semibold tracking-[0.12em] text-ink-600 uppercase">{label}</dt><dd className="tnum mt-1 text-sm font-medium text-ink-300">{value}</dd></div>;
}

function SourceRow({ label, detail, available }: { label: string; detail: string; available: boolean | null }) {
  return (
    <div className="flex items-start gap-3 px-4 py-4">
      <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${available ? "bg-signal-ok" : "bg-signal-unknown"}`} />
      <div className="min-w-0"><p className="text-sm font-medium text-ink-200">{label}</p><p className="mt-1 text-xs leading-5 text-ink-500">{detail}</p></div>
    </div>
  );
}
