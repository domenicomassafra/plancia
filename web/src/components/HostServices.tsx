import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { exactTimestamp } from "../lib/format";
import type { FleetService, FleetServiceLogs, FleetServicesSnapshot, ServiceOperation } from "../lib/types";
import { filterServices, pollServiceReceipt, SERVICE_PAGE_SIZE, serviceOperationAllowed, servicePreviewMatches, verifiedServicePreview, type ScopedServicePreview } from "./service-controls";

const OPERATIONS: { key: ServiceOperation; label: string }[] = [
  { key: "start", label: "Avvia" },
  { key: "stop", label: "Ferma" },
  { key: "restart", label: "Riavvia" },
];

function errorText(error: unknown): string {
  const message = error instanceof Error ? error.message : "Richiesta non riuscita.";
  if (/session|401/i.test(message)) return "Sessione non valida o scaduta. Accedi nuovamente e riprova.";
  if (/403|forbidden|permission denied/i.test(message)) return `Permesso negato. ${message}`;
  if (/409|conflict/i.test(message)) return `Conflitto: aggiorna i servizi e prepara una nuova anteprima. ${message}`;
  return message;
}

function ServiceLogs({ hostId, serviceId }: { hostId: string; serviceId: string }) {
  const [entries, setEntries] = useState<FleetServiceLogs["entries"]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const requestVersion = useRef(0);
  const pageLock = useRef(false);

  useEffect(() => {
    const version = ++requestVersion.current;
    setLoading(true);
    setEntries([]);
    setCursor(null);
    setHasMore(false);
    setError(null);
    void api.serviceLogs(hostId, serviceId)
      .then((result) => {
        if (requestVersion.current !== version) return;
        if (result.host_id !== hostId || result.service_id !== serviceId) throw new Error("Risposta log di un altro servizio.");
        setEntries(result.entries);
        setCursor(result.next_cursor);
        setHasMore(result.has_more);
      })
      .catch((cause) => {
        if (requestVersion.current === version) setError(errorText(cause));
      })
      .finally(() => {
        if (requestVersion.current === version) setLoading(false);
      });
    return () => { requestVersion.current += 1; };
  }, [hostId, serviceId, revision]);

  const loadMore = async () => {
    if (pageLock.current || loading || !hasMore || !cursor) return;
    pageLock.current = true;
    setLoadingMore(true);
    setError(null);
    const version = requestVersion.current;
    try {
      const result = await api.serviceLogs(hostId, serviceId, cursor);
      if (requestVersion.current !== version) return;
      if (result.host_id !== hostId || result.service_id !== serviceId) throw new Error("Risposta log di un altro servizio.");
      setEntries((current) => [...current, ...result.entries]);
      setCursor(result.next_cursor);
      setHasMore(result.has_more);
    } catch (cause) {
      if (requestVersion.current === version) setError(errorText(cause));
    } finally {
      pageLock.current = false;
      if (requestVersion.current === version) setLoadingMore(false);
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-ink-700 bg-ink-900 p-3" aria-label={`Log di ${serviceId}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h5 className="text-xs font-semibold text-ink-200">Log recenti · {serviceId}</h5>
        <button type="button" disabled={loading || loadingMore}
          onClick={() => setRevision((n) => n + 1)}
          className="min-h-11 rounded-lg border border-ink-700 px-3 text-xs text-ink-300 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50">
          Aggiorna log
        </button>
      </div>
      {loading ? <p role="status" className="mt-2 text-xs text-ink-400">Caricamento log…</p> :
        entries.length === 0 ? <p className="mt-2 text-xs text-ink-400">Nessun log disponibile.</p> :
          <ol className="mt-2 divide-y divide-ink-800">
            {entries.map((entry, index) => (
              <li key={`${entry.cursor}-${index}`} className="py-2 text-xs">
                {entry.timestamp
                  ? <time className="text-[11px] text-ink-400" dateTime={entry.timestamp}>{exactTimestamp(entry.timestamp)}</time>
                  : <span className="text-[11px] text-ink-400">Data non disponibile</span>}
                <p className="mt-1 whitespace-pre-wrap break-words text-ink-200">{entry.message}</p>
              </li>
            ))}
          </ol>}
      {error && <p role="alert" className="mt-2 text-xs text-signal-warn">{error}</p>}
      {hasMore && cursor && !loading && (
        <button type="button" disabled={loadingMore} onClick={() => void loadMore()}
          className="mt-3 min-h-11 rounded-lg border border-ink-700 px-3 text-xs text-ink-200 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50">
          {loadingMore ? "Caricamento…" : "Carica altri log"}
        </button>
      )}
    </div>
  );
}

export function HostServices({ hostId, onReceiptsChanged }: { hostId: string; onReceiptsChanged: () => void }) {
  const [inventory, setInventory] = useState<FleetServicesSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [selectedLogs, setSelectedLogs] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [visibleCount, setVisibleCount] = useState(SERVICE_PAGE_SIZE);
  const [preview, setPreview] = useState<ScopedServicePreview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const actionLock = useRef(false);
  const idempotencyKey = useRef<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(null);
    setPreview(null);
    setConfirmation("");
    idempotencyKey.current = null;
    void api.services(hostId)
      .then((value) => {
        if (!active) return;
        if (value.host_id !== hostId) throw new Error("Inventario di un altro host.");
        setInventory(value);
      })
      .catch((cause) => {
        if (!active) return;
        setInventory(null);
        setLoadError(errorText(cause));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [hostId, revision]);

  const allowed = (service: FleetService, operation: ServiceOperation) =>
    !loading && inventory?.available === true && serviceOperationAllowed(service, operation);
  const matchingServices = filterServices(inventory?.services ?? [], search);
  const visibleServices = matchingServices.slice(0, visibleCount);

  const prepare = async (service: FleetService, operation: ServiceOperation) => {
    if (actionLock.current || !allowed(service, operation)) return;
    actionLock.current = true;
    setBusy(true);
    setFeedback(null);
    setPreview(null);
    setConfirmation("");
    idempotencyKey.current = null;
    try {
      const response = await api.previewServiceOperation(hostId, service.id, operation);
      if (!alive.current) return;
      if (!verifiedServicePreview(response, hostId, service.id, operation)) {
        throw new Error("Anteprima non valida per il servizio e l'operazione richiesti.");
      }
      setPreview({ hostId, serviceId: service.id, operation, receiptId: response.receipt_id });
      onReceiptsChanged();
    } catch (cause) {
      if (alive.current) setFeedback(errorText(cause));
    } finally {
      actionLock.current = false;
      if (alive.current) setBusy(false);
    }
  };

  const apply = async (service: FleetService, operation: ServiceOperation) => {
    if (actionLock.current || !allowed(service, operation) ||
      !servicePreviewMatches(preview, hostId, service.id, operation) || confirmation !== "CONFERMA") return;
    actionLock.current = true;
    setBusy(true);
    setFeedback(null);
    const key = idempotencyKey.current ?? crypto.randomUUID();
    idempotencyKey.current = key;
    try {
      const dispatched = await api.dispatchServiceOperation(hostId, service.id, operation, key, preview.receiptId);
      if (!alive.current) return;
      if (!dispatched.receipt_id) throw new Error("Il server non ha restituito la ricevuta dell'operazione.");
      setFeedback(`Operazione ${operation} ${dispatched.replayed ? "già acquisita" : "acquisita"} · ricevuta ${dispatched.receipt_id} in esecuzione…`);
      setPreview(null);
      setConfirmation("");
      idempotencyKey.current = null;
      onReceiptsChanged();
      const terminal = await pollServiceReceipt(
        dispatched.receipt_id,
        api.receipt,
        () => new Promise((resolve) => window.setTimeout(resolve, 300)),
        () => alive.current,
      );
      if (!alive.current) return;
      if (terminal) {
        setFeedback(`Operazione ${operation} · ricevuta ${terminal.id} · esito ${terminal.outcome}. Inventario in aggiornamento.`);
        setRevision((n) => n + 1);
      } else {
        setFeedback(`Operazione ${operation} ancora in esecuzione o stato della ricevuta non verificabile · ricevuta ${dispatched.receipt_id}. Consulta Audit e aggiorna manualmente.`);
      }
      onReceiptsChanged();
    } catch (cause) {
      if (alive.current) setFeedback(errorText(cause));
    } finally {
      actionLock.current = false;
      if (alive.current) setBusy(false);
    }
  };

  return (
    <section className="mt-6" aria-label="Servizi host">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-xs font-semibold tracking-wider text-ink-400 uppercase">Servizi host</h3>
          <p className="mt-1 text-xs text-ink-400">Stato dei servizi e operazioni autorizzate dal server.</p>
        </div>
        <button type="button" disabled={loading || busy}
          onClick={() => { setSelectedLogs(null); setRevision((n) => n + 1); }}
          className="min-h-11 rounded-lg border border-ink-700 px-3 text-xs text-ink-200 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50">
          Aggiorna servizi
        </button>
      </div>
      {loading && <p role="status" className="mt-3 text-xs text-ink-400">Caricamento inventario servizi…</p>}
      {loadError && <p role="alert" className="mt-3 rounded-lg border border-ink-700 p-3 text-xs text-signal-warn">{loadError}</p>}
      {inventory && !loadError && (
        <>
          <p className="mt-3 text-[11px] text-ink-400">
            {inventory.os} · osservato {exactTimestamp(inventory.observed_at)}
          </p>
          {!inventory.available ? (
            <p role="status" className="mt-2 rounded-lg border border-ink-700 p-3 text-xs text-ink-300">
              Inventario e controlli non disponibili: {inventory.reason || "host o adattatore non disponibile."}
            </p>
          ) : inventory.services.length === 0 ? (
            <p className="mt-3 text-xs text-ink-400">Nessun servizio restituito dal server.</p>
          ) : (
            <>
              <label className="mt-3 block text-xs text-ink-300" htmlFor="host-service-search">Cerca servizi per nome o ID</label>
              <input id="host-service-search" type="search" value={search}
                onChange={(event) => { setSearch(event.target.value); setVisibleCount(SERVICE_PAGE_SIZE); }}
                placeholder="Nome o ID servizio"
                className="mt-2 min-h-11 w-full rounded-lg border border-ink-700 bg-ink-900 px-3 text-sm text-ink-200 focus-visible:outline-2 focus-visible:outline-accent" />
              <p role="status" className="mt-2 text-xs text-ink-400">
                Visualizzati {visibleServices.length} di {matchingServices.length} servizi
                {matchingServices.length !== inventory.services.length ? ` (su ${inventory.services.length} totali)` : ""}.
              </p>
              {matchingServices.length === 0 ? (
                <p className="mt-3 text-xs text-ink-400">Nessun servizio corrisponde alla ricerca.</p>
              ) : (
                <ul className="mt-3 space-y-3">
              {visibleServices.map((service, index) => (
                <li key={service.id} className="min-w-0 rounded-xl bg-ink-850 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h4 className="break-words text-sm font-medium text-ink-200">{service.name}</h4>
                      <p className="mt-1 break-words text-xs text-ink-400">{service.description}</p>
                      <p className="mt-1 break-all text-[11px] text-ink-400">{service.id}</p>
                    </div>
                    <span className="rounded-lg border border-ink-700 px-2 py-1 text-xs text-ink-300">
                      {service.state} · {service.substate}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {OPERATIONS.map(({ key, label }) => (
                      <button key={key} type="button" disabled={busy || !allowed(service, key)}
                        onClick={() => void prepare(service, key)}
                        aria-label={`${label} servizio ${service.name}`}
                        className="min-h-11 rounded-lg border border-ink-700 px-3 text-xs font-medium text-ink-200 focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-40">
                        {label}
                      </button>
                    ))}
                    <button type="button"
                      aria-expanded={selectedLogs === service.id}
                      aria-controls={`service-logs-${index}`}
                      onClick={() => setSelectedLogs((current) => current === service.id ? null : service.id)}
                      className="min-h-11 rounded-lg border border-ink-600 px-3 text-xs font-medium text-ink-200 focus-visible:outline-2 focus-visible:outline-accent">
                      {selectedLogs === service.id ? "Chiudi log" : "Mostra log"}
                    </button>
                  </div>
                  {preview && preview.hostId === hostId && preview.serviceId === service.id && (
                    <div className="mt-3 rounded-lg border border-ink-600 bg-ink-900 p-3">
                      <p role="status" className="text-xs text-ink-200">
                        Anteprima verificata per {service.name} · {preview.operation} · ricevuta {preview.receiptId}. Nessuna mutazione eseguita.
                      </p>
                      <label className="mt-3 block text-xs text-ink-300">
                        Scrivi CONFERMA per autorizzare questa operazione
                        <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)}
                          autoComplete="off" spellCheck={false} placeholder="CONFERMA"
                          className="mt-2 min-h-11 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 text-sm text-ink-200 focus-visible:outline-2 focus-visible:outline-accent" />
                      </label>
                      <button type="button"
                        disabled={busy || !allowed(service, preview.operation) || confirmation !== "CONFERMA" ||
                          !servicePreviewMatches(preview, hostId, service.id, preview.operation)}
                        onClick={() => void apply(service, preview.operation)}
                        className="mt-3 min-h-11 rounded-lg bg-ink-700 px-3 text-xs font-semibold text-ink-100 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40">
                        {busy ? "Invio…" : `Conferma ${preview.operation}`}
                      </button>
                      <button type="button" disabled={busy} onClick={() => { setPreview(null); setConfirmation(""); idempotencyKey.current = null; }}
                        className="ml-2 min-h-11 rounded-lg px-3 text-xs text-ink-300 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40">
                        Annulla
                      </button>
                    </div>
                  )}
                  {selectedLogs === service.id && (
                    <div id={`service-logs-${index}`}>
                      <ServiceLogs key={`${hostId}:${service.id}`} hostId={hostId} serviceId={service.id} />
                    </div>
                  )}
                </li>
              ))}
                </ul>
              )}
              {visibleCount < matchingServices.length && (
                <button type="button"
                  onClick={() => setVisibleCount((count) => count + SERVICE_PAGE_SIZE)}
                  className="mt-3 min-h-11 w-full rounded-lg border border-ink-700 px-3 text-sm font-medium text-ink-200 focus-visible:outline-2 focus-visible:outline-accent">
                  Mostra altri servizi
                </button>
              )}
            </>
          )}
        </>
      )}
      {feedback && <p role="status" className="mt-3 break-words rounded-lg border border-ink-700 p-3 text-xs text-ink-200">{feedback}</p>}
    </section>
  );
}
