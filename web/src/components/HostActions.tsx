import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { receiptActionLabel, relativeTime } from "../lib/format";
import type { Capabilities, Receipt } from "../lib/types";
import { hostActionFeedback, type HostActionFeedback } from "./host-action-feedback";

interface HostActionsProps {
  hostId: string;
  onReceiptsChanged: () => void;
}

export function HostActions({ hostId, onReceiptsChanged }: HostActionsProps) {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [capabilitiesError, setCapabilitiesError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [preview, setPreview] = useState<{ key: string; receiptId: string } | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<HostActionFeedback | null>(null);
  const actionLock = useRef(false);
  const idempotencyKeys = useRef<Record<string, string>>({});

  useEffect(() => {
    let alive = true;
    setCapabilities(null);
    setCapabilitiesError(null);
    void api.capabilities(hostId, refresh > 0)
      .then((value) => { if (alive) setCapabilities(value); })
      .catch(() => {
        if (!alive) return;
        setCapabilitiesError("Preflight azioni non disponibile. Nessuna azione viene abilitata senza una verifica riuscita.");
      });
    return () => { alive = false; };
  }, [hostId, refresh]);

  const prepare = async (key: string) => {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    setPreview(null);
    setConfirmation("");
    setFeedback(null);
    try {
      const response = await api.previewAction(hostId, key);
      if (response.preview.mutation_count !== 0 || response.preview.status !== "dry-run") {
        throw new Error("Il server non ha confermato una preview senza mutazioni.");
      }
      delete idempotencyKeys.current[key];
      setPreview({ key, receiptId: response.receipt_id });
      onReceiptsChanged();
    } catch (error) {
      setFeedback({ kind: "error", text: (error as Error).message });
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  };

  const apply = async (key: string) => {
    if (actionLock.current || preview?.key !== key) return;
    const action = capabilities?.actions[key];
    if (!action?.available || (action.destructive && confirmation.trim().toUpperCase() !== "CONFERMA")) return;
    actionLock.current = true;
    setBusy(true);
    setFeedback(null);
    const idempotencyKey = idempotencyKeys.current[key] ?? crypto.randomUUID();
    idempotencyKeys.current[key] = idempotencyKey;
    try {
      const dispatched = await api.dispatch(hostId, key, confirmation, idempotencyKey, preview.receiptId);
      setPreview(null);
      setConfirmation("");
      setFeedback({ kind: "warn", text: `Azione ${receiptActionLabel(key)} acquisita; verifico la ricevuta…` });
      onReceiptsChanged();
      let terminal: Receipt | null = null;
      for (let attempt = 0; attempt < 30; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 300));
        try {
          const value = await api.receipt(dispatched.receipt_id);
          if (value.receipt.outcome !== "running") {
            terminal = value.receipt;
            break;
          }
        } catch {
          // Receipt can briefly be unavailable during dispatch.
        }
      }
      if (!terminal) {
        setFeedback({ kind: "warn", text: `${receiptActionLabel(key)} ancora in esecuzione; consulta Audit.` });
      } else {
        setFeedback(hostActionFeedback(terminal, receiptActionLabel(key)));
      }
      onReceiptsChanged();
    } catch (error) {
      setFeedback({ kind: "error", text: (error as Error).message });
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  };

  return (
    <section className="mt-6">
      <h3 className="text-xs font-semibold tracking-wider text-ink-400 uppercase">Azioni</h3>
      {capabilities === null ? (
        capabilitiesError ? (
          <div className="mt-3 rounded-xl border border-signal-warn/25 bg-signal-warn/5 p-3">
            <p role="status" className="text-xs leading-5 text-signal-warn">{capabilitiesError}</p>
            <button type="button" onClick={() => setRefresh((value) => value + 1)}
              className="mt-2 min-h-11 rounded-lg border border-ink-700 px-3 text-xs font-medium text-ink-200 hover:bg-ink-800 focus-visible:outline-2 focus-visible:outline-accent">
              Riprova preflight
            </button>
          </div>
        ) : <p className="mt-3 text-xs text-ink-400">Verifica dei privilegi in corso…</p>
      ) : (
        <div className="mt-3 space-y-2">
          {Object.values(capabilities.actions).map((action) => (
            <div key={action.key} className="rounded-xl bg-ink-850 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink-200">{action.label}</p>
                  <p className="mt-0.5 text-xs text-ink-400">{action.description}</p>
                </div>
                <button type="button" disabled={!action.available || busy}
                  onClick={() => void prepare(action.key)}
                  className={`min-h-11 shrink-0 rounded-lg px-3 py-2 text-xs font-medium transition focus-visible:outline-2 focus-visible:outline-accent ${
                    action.available ? "bg-ink-700 text-ink-200 hover:bg-ink-600" : "cursor-not-allowed bg-ink-800 text-ink-400"
                  }`}>
                  {action.available ? "Prepara preview" : "Non disponibile"}
                </button>
              </div>
              {!action.available && action.reason && (
                <p className="mt-2 border-l-2 border-ink-600 pl-3 text-[11px] leading-relaxed text-ink-400">
                  {action.reason}
                  {action.fix && <><br /><span className="text-ink-300">Per abilitarla: </span>
                    <code className="text-[10px] break-all">{action.fix}</code></>}
                </p>
              )}
              {preview?.key === action.key && (
                <div className="mt-3 rounded-lg border border-ink-600 bg-ink-900 p-3">
                  <p role="status" className="text-xs text-ink-200">
                    Preview verificata: nessuna mutazione · ricevuta {preview.receiptId} in Audit.
                  </p>
                  {action.destructive && (
                    <label className="mt-3 block text-[11px] text-ink-300">
                      Scrivi <code className="font-semibold text-signal-crit">CONFERMA</code> per procedere
                      <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)}
                        autoComplete="off" spellCheck={false} placeholder="CONFERMA"
                        className="mt-2 min-h-11 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-xs text-ink-200 outline-none focus:border-accent" />
                    </label>
                  )}
                  <button type="button" disabled={busy || (action.destructive && confirmation.trim().toUpperCase() !== "CONFERMA")}
                    onClick={() => void apply(action.key)}
                    className="mt-3 min-h-11 rounded-lg bg-signal-crit px-3 py-2 text-xs font-semibold text-ink-950 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40">
                    {busy ? "Invio…" : `Applica ${action.label}`}
                  </button>
                </div>
              )}
            </div>
          ))}
          <p className="text-[11px] leading-5 text-ink-400">
            Preflight {relativeTime(capabilities.probed_at)} · SSH {capabilities.transport_ok ? "verificato" : "non verificato"} · FleetProbe {capabilities.restart_agent_proven ? "verificato" : "non verificato"} · power privilege {capabilities.privileged ? "verificato" : "non disponibile"}
          </p>
        </div>
      )}
      {feedback && (
        <p role="status" className={`mt-3 rounded-lg px-3 py-2 text-xs ${
          feedback.kind === "ok" ? "bg-signal-ok/10 text-signal-ok"
            : feedback.kind === "warn" ? "bg-signal-warn/10 text-signal-warn"
              : "bg-signal-crit/10 text-signal-crit"
        }`}>{feedback.text}</p>
      )}
    </section>
  );
}
