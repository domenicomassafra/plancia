import { DeviceImage } from "../components/DeviceImage";
import { Sparkline } from "../components/Sparkline";
import { ServicesPorts } from "../components/ServicesPorts";
import { overviewFleetStatus, overviewHostState, showCurrentMetrics } from "../lib/overview-state";
import {
  SEVERITY_BG,
  SEVERITY_LABEL,
  SEVERITY_TEXT,
  dueLabel,
  hostConnectivityMessage,
  metric,
  osLabel,
  receiptActionLabel,
  receiptOutcomeSeverity,
  relativeTime,
  sampleAgeLabel,
  sourceReason,
  thermalLabel,
} from "../lib/format";
import type {
  ControlCenterSummary,
  DiskEntry,
  FleetSnapshot,
  HistoryPoint,
  HostView,
  Receipt,
  Severity,
} from "../lib/types";

interface FleetViewProps {
  snapshot: FleetSnapshot;
  connectionOffline: boolean;
  histories: Record<string, HistoryPoint[]>;
  summary: ControlCenterSummary | null;
  receipts: Receipt[];
  onSelectHost: (hostId: string) => void;
  onNavigate: (view: "workspace" | "audit") => void;
}

function signalFor(host: HostView, key: string) {
  return host.health.signals.find((signal) => signal.key === key);
}

export function FleetView({
  snapshot,
  connectionOffline,
  histories,
  summary,
  receipts,
  onSelectHost,
  onNavigate,
}: FleetViewProps) {
  const fleetStatus = overviewFleetStatus(snapshot, connectionOffline);
  const attention = snapshot.hosts.filter(
    (host) => showCurrentMetrics(host, connectionOffline) && (host.health.severity === "warn" || host.health.severity === "crit"),
  );
  const unreachable = snapshot.hosts.filter(
    (host) => host.lifecycle === "managed" && (!host.reachable || host.stale),
  );
  const pending = snapshot.hosts.filter((host) => host.lifecycle === "pending");
  const todayItems = summary?.notion.items ?? [];
  const ordered = [...snapshot.hosts].sort((a, b) => {
    const weight = { crit: 0, warn: 1, unknown: 2, ok: 3 } as const;
    const bySeverity = weight[a.health.severity] - weight[b.health.severity];
    if (bySeverity !== 0) return bySeverity;
    if (a.is_control_plane !== b.is_control_plane) return a.is_control_plane ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold tracking-[0.16em] text-accent uppercase">
            Mission Control
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-ink-100">
            Flotta
          </h1>
          <p className="mt-1 text-sm text-ink-300">
            Confronto live dei runtime gestiti sui quattro computer. Windows del MateBook resta una runtime identity separata finché non viene avviata e provata.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-ink-300">
          <span
            className={`h-2.5 w-2.5 rounded-full ${SEVERITY_BG[fleetStatus]}`}
          />
          <span className={SEVERITY_TEXT[fleetStatus]}>
            {unreachable.length
              ? `${unreachable.length} ${unreachable.length === 1 ? "host non verificabile" : "host non verificabili"}`
              : attention.length
                ? `${attention.length} ${attention.length === 1 ? "host richiede" : "host richiedono"} attenzione`
                : pending.length
                  ? `${pending.length} runtime pending on boot`
                  : fleetStatus === "unknown"
                    ? "Stato non verificabile"
                    : "Nessun segnale aperto"}
          </span>
        </div>
      </header>

      {connectionOffline && (
        <p role="status" className="rounded-xl border border-signal-warn/25 bg-signal-warn/5 px-4 py-3 text-sm leading-6 text-signal-warn">
          Control plane non raggiungibile. I valori mostrati sono l'ultimo snapshot ricevuto e non costituiscono prova di stato live.
        </p>
      )}

      <dl className="grid overflow-hidden rounded-xl border border-ink-700 bg-ink-900/65 sm:grid-cols-4">
        <FleetStat
          label="Raggiungibili"
          value={connectionOffline ? "—" : `${snapshot.fleet.reachable}/${snapshot.fleet.managed_total}`}
          detail={connectionOffline ? "control plane non raggiungibile" : snapshot.fleet.pending ? `${snapshot.fleet.pending} runtime pending separata` : "agenti live"}
        />
        <FleetStat
          label="Stato peggiore"
          value={SEVERITY_LABEL[fleetStatus]}
          detail={
            attention.map((host) => host.name).join(", ") ||
            (fleetStatus === "unknown" ? pending.length ? `${pending.length} pending · stato parziale` : "stato parziale" : "nessuna soglia superata")
          }
          tone={SEVERITY_TEXT[fleetStatus]}
        />
        <FleetStat
          label="Da fare"
          value={
            summary?.notion.available
              ? `${summary.notion.counts.due_or_overdue ?? 0}${summary.notion.has_more ? "+" : ""}`
              : "—"
          }
          detail={
            summary?.notion.available
              ? `${summary.notion.counts.overdue ?? 0}${summary.notion.has_more ? "+" : ""} in ritardo`
              : summary
                ? sourceReason(summary.notion.reason)
                : "Notion in caricamento"
          }
        />
        <FleetStat
          label="Non raggiungibili"
          value={connectionOffline ? "—" : String(unreachable.length)}
          detail={connectionOffline ? "stream offline" : unreachable.length ? "host da verificare" : "nessun host"}
          tone={!connectionOffline && unreachable.length ? "text-signal-warn" : undefined}
        />
      </dl>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="overflow-hidden rounded-xl border border-ink-700 bg-ink-900/55">
          <div className="flex items-center justify-between border-b border-ink-700 px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-ink-100">Computer</h2>
              <p className="mt-0.5 text-xs text-ink-400">
                Metriche confrontabili, stessa scala e stessa gerarchia.
              </p>
            </div>
            <span className="tnum text-xs text-ink-400">
              {connectionOffline ? "ultimo snapshot" : `${snapshot.fleet.reachable} online`}
            </span>
          </div>

          <div className="hidden grid-cols-[minmax(240px,1.4fr)_130px_150px_150px_120px_24px] gap-4 border-b border-ink-700 bg-ink-900 px-4 py-2.5 text-[10px] font-semibold tracking-[0.12em] text-ink-400 uppercase lg:grid">
            <span>Host</span>
            <span>CPU</span>
            <span>Memoria</span>
            <span>Disco</span>
            <span>Termica / GPU</span>
            <span />
          </div>

          <div className="divide-y divide-ink-700/80">
            {ordered.map((host) => (
              <HostRow
                key={host.id}
                host={host}
                history={histories[host.id] ?? []}
                connectionOffline={connectionOffline}
                onSelect={onSelectHost}
              />
            ))}
          </div>
        </section>

        <aside className="overflow-hidden rounded-xl border border-ink-700 bg-ink-900/55">
          <OperationalPanel
            title="Azioni dovute"
            count={
              summary?.notion.available
                ? summary.notion.counts.due_or_overdue ?? 0
                : null
            }
            action="Apri workspace"
            onAction={() => onNavigate("workspace")}
          >
            {todayItems.length === 0 ? (
              <EmptyPanel
                text={
                  summary?.notion.available
                    ? "Nessuna azione in scadenza."
                    : summary
                      ? sourceReason(summary.notion.reason)
                      : "Notion in caricamento."
                }
              />
            ) : (
              <ul className="divide-y divide-ink-700/70">
                {todayItems.slice(0, 5).map((item) => (
                  <li key={item.id} className="flex items-start gap-3 px-4 py-3">
                    <span
                      className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                        item.overdue ? "bg-signal-crit" : "bg-accent"
                      }`}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-ink-200">{item.title}</p>
                      <p
                        className={`mt-0.5 text-xs ${
                          item.overdue ? "text-signal-crit" : "text-ink-400"
                        }`}
                      >
                        {dueLabel(item.due)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </OperationalPanel>

          <div className="border-t border-ink-700">
            <OperationalPanel
              title="Attività recente"
              count={receipts.length}
              action="Tutte le ricevute"
              onAction={() => onNavigate("audit")}
            >
              {receipts.length === 0 ? (
                <EmptyPanel text="Nessuna azione registrata." />
              ) : (
                <ul className="divide-y divide-ink-700/70">
                  {receipts.slice(0, 4).map((receipt) => (
                    <li key={receipt.id} className="flex items-start gap-3 px-4 py-3">
                      <span
                        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                          SEVERITY_BG[receiptOutcomeSeverity(receipt.outcome)]
                        }`}
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm text-ink-200">
                          {receiptActionLabel(receipt.action)}
                        </p>
                        <p className="mt-0.5 text-xs text-ink-400">
                          {receipt.host_id} · {relativeTime(receipt.started_at)}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </OperationalPanel>
          </div>
        </aside>
      </div>
      <ServicesPorts hosts={snapshot.hosts} connectionOffline={connectionOffline} />
    </div>
  );
}

function FleetStat({
  label,
  value,
  detail,
  tone = "text-ink-100",
}: {
  label: string;
  value: string;
  detail: string;
  tone?: string;
}) {
  return (
    <div className="border-b border-ink-700 px-4 py-3 last:border-b-0 sm:border-r sm:border-b-0 sm:last:border-r-0">
      <dt className="text-[10px] font-semibold tracking-[0.12em] text-ink-400 uppercase">
        {label}
      </dt>
      <dd className={`tnum mt-1 text-xl font-semibold ${tone}`}>{value}</dd>
      <dd className="mt-0.5 truncate text-[11px] text-ink-400" title={detail}>
        {detail}
      </dd>
    </div>
  );
}

function HostRow({
  host,
  history,
  connectionOffline,
  onSelect,
}: {
  host: HostView;
  history: HistoryPoint[];
  connectionOffline: boolean;
  onSelect: (hostId: string) => void;
}) {
  const pending = host.lifecycle === "pending";
  const offline = !showCurrentMetrics(host, connectionOffline);
  const hostState = overviewHostState(host, connectionOffline);
  const cpu = offline ? undefined : signalFor(host, "cpu");
  const memory = offline ? undefined : signalFor(host, "memory");
  const disk = offline ? undefined : signalFor(host, "disk");
  const gpu = offline ? undefined : signalFor(host, "gpu");
  const thermal = offline ? undefined : signalFor(host, "thermal") ?? signalFor(host, "thermal-pressure");
  const primaryDisk = offline ? null : host.sample?.disk?.[0] ?? null;

  return (
    <button
      type="button"
      onClick={() => onSelect(host.id)}
      className="group grid w-full grid-cols-2 gap-x-4 gap-y-3 px-4 py-4 text-left transition-colors hover:bg-ink-850/75 focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent lg:grid-cols-[minmax(240px,1.4fr)_130px_150px_150px_120px_24px] lg:items-center lg:gap-4"
    >
      <div className="col-span-2 flex min-w-0 items-center gap-3 lg:col-span-1">
        <DeviceImage
          image={host.image}
          className="h-12 w-12 shrink-0 rounded-lg object-cover ring-1 ring-ink-600"
          fallback={<span className="h-12 w-12 shrink-0 rounded-lg bg-ink-800" />}
          loading="lazy"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 shrink-0 rounded-full ${SEVERITY_BG[offline ? "unknown" : host.health.severity]}`}
            />
            <h3 className="truncate text-sm font-semibold text-ink-100">{host.name}</h3>
            {host.is_control_plane && (
              <span className="shrink-0 rounded border border-accent/30 bg-accent/10 px-1.5 py-0.5 text-[9px] font-semibold tracking-wide text-accent uppercase">
                Control
              </span>
            )}
            {pending && (
              <span className="shrink-0 rounded border border-ink-600 bg-ink-800 px-1.5 py-0.5 text-[9px] font-semibold tracking-wide text-ink-300 uppercase">
                Pending
              </span>
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-ink-400">
            {host.sample
              ? `${osLabel(host.os)} · ${host.sample.os_version}`
              : host.subtitle}
          </p>
          {host.sample && !offline && (
            <p className="mt-0.5 text-[10px] text-ink-400">
              Campione aggiornato {sampleAgeLabel(host.sample.sample_age_s)}
            </p>
          )}
          {pending ? (
            <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-ink-400">
              Runtime Windows separata, pending on boot; nessuna evidenza Omarchy viene riutilizzata.
            </p>
          ) : connectionOffline ? (
            <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-signal-warn">
              Control plane non raggiungibile: questo è l'ultimo campione ricevuto.
            </p>
          ) : offline && (
            <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-signal-warn">
              {hostState === "stale" ? "Stale · ultimo campione conservato, non live." : hostConnectivityMessage(host.error)}
            </p>
          )}
        </div>
      </div>

      <div className="min-w-0">
        <MetricLabel label="CPU" />
        <div className="mt-1 flex items-end gap-2">
          <span className={`tnum text-xl font-semibold ${SEVERITY_TEXT[cpu?.severity ?? "unknown"]}`}>
            {metric(cpu?.value, "%")}
          </span>
          {!offline && history.length > 1 && (
            <Sparkline
              points={history.slice(-30).map((point) => point.cpu)}
              className={SEVERITY_TEXT[cpu?.severity ?? "unknown"]}
              height={24}
              label={`CPU recente su ${host.name}`}
            />
          )}
        </div>
      </div>

      <MetricCell
        label={memory?.label ?? "Memoria"}
        value={memory?.value ?? null}
        severity={memory?.severity ?? "unknown"}
      />
      <StorageCell disk={primaryDisk} severity={disk?.severity ?? "unknown"} />
      <div>
        <MetricLabel label="Termica / GPU" />
        <p className={`tnum mt-1 truncate text-sm font-semibold ${SEVERITY_TEXT[thermal?.severity ?? "unknown"]}`}>
          {!offline && host.sample
            ? thermalLabel(
                host.sample.thermal.cpu_c,
                host.sample.thermal.pressure_level,
                host.sample.thermal.reason,
              )
            : "—"}
        </p>
        <p className={`mt-0.5 truncate text-[10px] ${gpu ? SEVERITY_TEXT[gpu.severity] : "text-ink-500"}`} title={offline ? undefined : host.sample?.gpu?.name ?? undefined}>
          {offline ? "—" : gpu?.value !== undefined && gpu?.value !== null
            ? `GPU ${metric(gpu.value, "%")}`
            : host.sample?.gpu?.name ?? "GPU non esposta"}
        </p>
      </div>

      <svg
        viewBox="0 0 24 24"
        className="hidden h-5 w-5 text-ink-400 transition-transform group-hover:translate-x-0.5 group-hover:text-ink-200 lg:block"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
      >
        <path d="m9 18 6-6-6-6" />
      </svg>
    </button>
  );
}

function StorageCell({ disk, severity }: { disk: DiskEntry | null; severity: Severity }) {
  if (!disk) {
    return <MetricCell label="Spazio libero" value={null} severity="unknown" />;
  }
  const headroom = disk.available_bytes ?? disk.free_bytes;
  const freeGb = headroom / 1_000_000_000;
  const freePercent = disk.available_percent ?? disk.free_percent ?? (disk.total_bytes ? (headroom / disk.total_bytes) * 100 : null);
  return (
    <div className="min-w-0">
      <div className="flex items-center justify-between gap-2">
        <MetricLabel label={disk.available_bytes != null ? (disk.available_stale ? "Disponibile (cache)" : "Disponibile") : "Spazio libero"} />
        <span className={`tnum text-xs font-semibold ${SEVERITY_TEXT[severity]}`}>
          {freeGb >= 10 ? freeGb.toFixed(0) : freeGb.toFixed(1)} GB
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-700">
        <span
          className={`block h-full rounded-full ${SEVERITY_BG[severity]}`}
          style={{ width: `${Math.max(2, Math.min(100, freePercent ?? 0))}%` }}
        />
      </div>
      {disk.available_bytes != null && (
        <p className="tnum mt-1 truncate text-[9px] text-ink-500" title="Blocchi fisicamente liberi prima del reclaim APFS">
          {disk.free_bytes / 1_000_000_000 >= 10
            ? (disk.free_bytes / 1_000_000_000).toFixed(0)
            : (disk.free_bytes / 1_000_000_000).toFixed(1)} GB fisici
          {disk.available_stale && disk.available_age_s != null ? ` · APFS ${Math.round(disk.available_age_s)}s fa` : ""}
        </p>
      )}
    </div>
  );
}

function MetricLabel({ label }: { label: string }) {
  return (
    <p className="truncate text-[10px] font-semibold tracking-[0.1em] text-ink-400 uppercase">
      {label}
    </p>
  );
}

function MetricCell({
  label,
  value,
  severity,
}: {
  label: string;
  value: number | null;
  severity: Severity;
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-center justify-between gap-2">
        <MetricLabel label={label} />
        <span className={`tnum text-xs font-semibold ${SEVERITY_TEXT[severity]}`}>
          {metric(value, "%")}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-700">
        {value === null ? (
          <span className="block h-full w-full bg-[repeating-linear-gradient(135deg,transparent,transparent_3px,var(--color-ink-600)_3px,var(--color-ink-600)_5px)]" />
        ) : (
          <span
            className={`block h-full rounded-full ${SEVERITY_BG[severity]}`}
            style={{ width: `${Math.max(2, Math.min(100, value))}%` }}
          />
        )}
      </div>
    </div>
  );
}

function OperationalPanel({
  title,
  count,
  action,
  onAction,
  children,
}: {
  title: string;
  count: number | null;
  action: string;
  onAction: () => void;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-ink-100">{title}</h2>
          {count !== null && (
            <span className="tnum rounded bg-ink-700 px-1.5 py-0.5 text-[10px] text-ink-300">
              {count}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onAction}
          className="min-h-11 rounded-lg px-2 text-xs font-medium text-accent hover:bg-accent/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {action}
        </button>
      </div>
      {children}
    </section>
  );
}

function EmptyPanel({ text }: { text: string }) {
  return <p className="border-t border-ink-700 px-4 py-5 text-sm text-ink-400">{text}</p>;
}
