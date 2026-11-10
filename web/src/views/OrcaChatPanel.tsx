import { useEffect, useState, type FormEvent } from "react";
import { orcaScreen, orcaSearch, orcaSend, orcaTerminals, type OrcaAttempt, type OrcaSearchHit, type OrcaSearchHost, type OrcaTerminal } from "../lib/orca-chat";
import { request } from "../lib/api";

type Result<T> = { state: "loading" } | { state: "ready"; value: T } | { state: "error"; reason: string };
const errorText = (error: unknown) => error instanceof Error ? error.message : "Servizio non disponibile";

/** The bridge operates existing Orca PTYs and its native search index. No new agent is launched. */
export function OrcaChatPanel({ active }: { active: boolean }) {
  const [terminals, setTerminals] = useState<Result<OrcaTerminal[]>>({ state: "loading" });
  const [selectedHandle, setSelectedHandle] = useState<string | null>(null);
  const [screen, setScreen] = useState<Result<string>>({ state: "loading" });
  const [query, setQuery] = useState("");
  const [searchHost, setSearchHost] = useState<OrcaSearchHost>("minipc");
  const [hits, setHits] = useState<Result<OrcaSearchHit[]>>({ state: "ready", value: [] });
  const [selectedHit, setSelectedHit] = useState<OrcaSearchHit | null>(null);
  const [draft, setDraft] = useState("");
  const [attempts, setAttempts] = useState<Record<string, OrcaAttempt>>({});
  const [sending, setSending] = useState(false);
  const [sendState, setSendState] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    orcaTerminals(request, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setTerminals({ state: "ready", value });
          setSelectedHandle((handle) => value.some((entry) => entry.handle === handle) ? handle : null);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setTerminals({ state: "error", reason: errorText(error) });
      });
    return () => controller.abort();
  }, [active, refresh]);

  const selectedTerminal = terminals.state === "ready"
    ? terminals.value.find((entry) => entry.handle === selectedHandle) : undefined;
  const pending = selectedHandle ? attempts[selectedHandle] : undefined;
  const changedProcess = Boolean(pending && pending.incarnationId !== selectedTerminal?.incarnationId);

  useEffect(() => {
    if (!active || !selectedHandle) return;
    let disposed = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    setScreen({ state: "loading" });
    const update = async () => {
      controller = new AbortController();
      try {
        const value = await orcaScreen({ handle: selectedHandle,
          incarnationId: selectedTerminal?.incarnationId ?? null }, request, controller.signal);
        if (!disposed) setScreen({ state: "ready", value });
      } catch (error) {
        if (!disposed) setScreen({ state: "error", reason: errorText(error) });
      } finally {
        if (!disposed) timeout = setTimeout(update, 2500);
      }
    };
    void update();
    return () => {
      disposed = true;
      controller?.abort();
      if (timeout) clearTimeout(timeout);
    };
  }, [active, selectedHandle, selectedTerminal?.incarnationId]);
  const doSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (query.trim().length < 2) return;
    setHits({ state: "loading" });
    setSelectedHit(null);
    try {
      setHits({ state: "ready", value: await orcaSearch(query, request, searchHost) });
    } catch (error) {
      setHits({ state: "error", reason: errorText(error) });
    }
  };
  const doSend = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedTerminal?.writable || !selectedTerminal.incarnationId || sending || changedProcess
      || !(pending?.text ?? draft).trim()) return;
    const attempt = pending ?? { handle: selectedTerminal.handle, incarnationId: selectedTerminal.incarnationId,
      retryRequest: crypto.randomUUID(), text: draft };
    setAttempts((current) => ({ ...current, [attempt.handle]: attempt }));
    setSending(true);
    setSendState("");
    try {
      const status = await orcaSend(attempt, request);
      setAttempts((current) => { const next = { ...current }; delete next[attempt.handle]; return next; });
      setDraft("");
      setSendState(status === "observed" ? "Invio osservato dal terminale"
        : "Invio accettato; conferma dal terminale ancora assente");
    } catch (error) {
      setSendState(`Esito sconosciuto: ${errorText(error)}. Rileggi lo schermo; «Riprova stesso invio» conserva il request ID.`);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="grid min-h-[560px] min-w-0 gap-0 overflow-hidden rounded-2xl border border-ink-700 bg-ink-900 lg:grid-cols-[minmax(220px,300px)_minmax(0,1fr)]">
      <aside className="min-w-0 border-b border-ink-700 lg:border-r lg:border-b-0" aria-label="Sessioni Orca">
        <div className="flex items-center justify-between border-b border-ink-700 p-4">
          <div><h2 className="font-semibold text-ink-100">Pi e altri agenti</h2><p className="text-xs text-ink-400">Origine: Orca</p></div>
          <button type="button" onClick={() => setRefresh((v) => v + 1)} className="button-secondary min-h-11 text-xs">Aggiorna</button>
        </div>
        <div className="p-3">
          <h3 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-ink-400">Terminali live</h3>
          {terminals.state === "loading" && <p role="status" className="px-1 text-xs text-ink-400">Verifica terminali…</p>}
          {terminals.state === "error" && <p role="status" className="px-1 text-xs text-ink-300">{terminals.reason}</p>}
          {terminals.state === "ready" && terminals.value.length === 0 &&
            <p className="px-1 text-xs leading-5 text-ink-400">Nessun terminale Orca attivo. Cerca una sessione Pi nella cronologia qui sotto.</p>}
          {terminals.state === "ready" && terminals.value.map((terminal) => (
            <button key={terminal.handle} type="button" aria-pressed={selectedHandle === terminal.handle}
              disabled={sending}
              onClick={() => { setSelectedHit(null); setSelectedHandle(terminal.handle); setSendState(""); }}
              className={`mb-1 block min-h-11 w-full rounded-lg px-3 py-2 text-left hover:bg-ink-800 ${selectedHandle === terminal.handle ? "bg-ink-800 ring-1 ring-ink-600" : ""}`}>
              <span className="block truncate text-sm text-ink-100">{terminal.title}</span>
              <span className="text-xs text-ink-400">{terminal.agent} · {terminal.writable ? "Scrivibile" : "Sola lettura"}</span>
            </button>
          ))}
        </div>
        <form onSubmit={(event) => { void doSearch(event); }} className="border-t border-ink-700 p-3" aria-label="Cerca nella cronologia Orca">
          <label htmlFor="orca-chat-search" className="mb-2 block text-xs font-semibold uppercase tracking-wide text-ink-400">Cronologia indicizzata</label>
          <select aria-label="Host cronologia Orca" value={searchHost} onChange={(event) => setSearchHost(event.target.value as OrcaSearchHost)}
            className="mb-2 min-h-11 w-full rounded-lg border border-ink-700 bg-ink-950 px-3 text-sm text-ink-100">
            <option value="minipc">MiniPC</option>
            <option value="studio">Mac Studio</option>
            <option value="macbookair">MacBook Air</option>
            <option value="matebooklinux">MateBook Linux</option>
          </select>
          <div className="flex gap-2">
            <input id="orca-chat-search" value={query} onChange={(event) => setQuery(event.target.value)} maxLength={120} placeholder="Cerca messaggi…" className="min-h-11 min-w-0 flex-1 rounded-lg border border-ink-700 bg-ink-950 px-3 text-sm text-ink-100" />
            <button className="button-secondary min-h-11" type="submit" disabled={query.trim().length < 2}>Cerca</button>
          </div>
          <p className="mt-2 text-xs text-ink-400">Ricerca sulle sessioni originali, senza copie in Plancia. Mostra estratti, non il transcript completo.</p>
        </form>
        <div className="max-h-80 space-y-1 overflow-auto p-3" aria-live="polite">
          {hits.state === "loading" && <p className="text-xs text-ink-400">Ricerca in corso…</p>}
          {hits.state === "error" && <p role="status" className="text-xs text-ink-300">{hits.reason}</p>}
          {hits.state === "ready" && hits.value.map((hit, index) => (
            <button type="button" key={`${hit.sessionId}-${index}`} disabled={sending}
              onClick={() => { setSelectedHandle(null); setSelectedHit(hit); }}
              className={`block min-h-11 w-full rounded-lg p-3 text-left hover:bg-ink-800 ${selectedHit === hit ? "bg-ink-800 ring-1 ring-ink-600" : ""}`}>
              <span className="block truncate text-sm text-ink-100">{hit.title}</span>
              <span className="block text-xs text-ink-400">{hit.agent} · {hit.updatedAt || "Data non nota"}</span>
            </button>
          ))}
          {hits.state === "ready" && query.trim().length > 1 && hits.value.length === 0 &&
            <p className="text-xs text-ink-400">Nessuna corrispondenza fra i risultati disponibili.</p>}
        </div>
      </aside>

      <section className="flex min-h-[520px] min-w-0 flex-col" aria-label="Conversazione Orca">
        <header className="flex items-center justify-between border-b border-ink-700 px-5 py-4">
          <div>
            <h3 className="font-semibold text-ink-100">{selectedTerminal?.title || selectedHit?.title || "Conversazioni Orca"}</h3>
            <p className="mt-1 text-xs text-ink-400">{selectedTerminal ? "Terminale nativo · aggiornamento ogni 2,5 s" : selectedHit ? `Sessione ${selectedHit.sessionId} · estratto indicizzato` : "Seleziona un terminale o cerca una sessione Pi"}</p>
          </div>
          {selectedTerminal && <span className="rounded-full border border-ink-600 px-2 py-1 text-xs text-ink-300">{selectedTerminal.connected ? "Live" : "Disconnesso"}</span>}
        </header>
        <div className="min-h-72 flex-1 overflow-auto p-5" aria-live="polite" aria-atomic="false">
          {selectedTerminal && screen.state === "loading" && <p className="text-sm text-ink-400">Collegamento al terminale…</p>}
          {selectedTerminal && screen.state === "error" && <p role="status" className="text-sm text-ink-300">{screen.reason}</p>}
          {selectedTerminal && screen.state === "ready" &&
            <pre className="whitespace-pre-wrap break-words rounded-lg border border-ink-700 bg-ink-950 p-4 font-mono text-xs leading-6 text-ink-200">{screen.value || "Terminale vuoto"}</pre>}
          {selectedHit && <article className="max-w-3xl space-y-4">
            <p className="text-xs text-ink-400">{selectedHit.agent} · {selectedHit.messageCount ?? "?"} messaggi indicizzati nella sessione</p>
            <div className="rounded-xl border border-ink-700 bg-ink-850 p-4">
              <p className="mb-2 text-xs font-semibold text-ink-300">Estratto della ricerca</p>
              <p className="whitespace-pre-wrap break-words text-sm leading-7 text-ink-100">{selectedHit.snippet || "Estratto non disponibile"}</p>
            </div>
            <p className="text-xs text-ink-400">Orca non espone il transcript completo di questa sessione attraverso l'API owner corrente.</p>
          </article>}
          {!selectedTerminal && !selectedHit && <div className="grid min-h-64 place-content-center text-center">
            <p className="text-base font-medium text-ink-100">Sessioni nello stesso spazio</p>
            <p className="mt-2 max-w-md text-sm text-ink-400">Cronologia Pi, Codex e CoS tramite l'indice Orca; schermo aggiornato per i terminali connessi.</p>
          </div>}
        </div>
        <form onSubmit={(event) => { void doSend(event); }} className="border-t border-ink-700 p-4">
          <label htmlFor="orca-chat-draft" className="sr-only">Messaggio al terminale</label>
          <textarea id="orca-chat-draft" rows={2} maxLength={2000} value={pending?.text ?? draft} onChange={(event) => setDraft(event.target.value)}
            readOnly={Boolean(pending)} disabled={!selectedTerminal?.writable || sending || changedProcess}
            placeholder={selectedTerminal?.writable ? "Messaggio alla sessione attiva…" : "Invio disponibile solo su un terminale Orca con permesso di scrittura"}
            className="w-full resize-y rounded-xl border border-ink-700 bg-ink-950 p-3 text-sm text-ink-100 disabled:cursor-not-allowed disabled:opacity-60" />
          <div className="mt-2 flex items-center justify-between gap-3">
            <p role="status" className="text-xs text-ink-400">{changedProcess
              ? "Processo Orca cambiato: retry bloccato. Verifica la sessione prima di inviare nuovi messaggi."
              : sendState || "Allegati e tool strutturati richiedono una API nativa abilitata."}</p>
            <button type="submit" className="button-secondary min-h-11 shrink-0"
              disabled={!selectedTerminal?.writable || !(pending?.text ?? draft).trim() || sending || changedProcess}>
              {pending ? "Riprova stesso invio" : "Invia"}</button>
          </div>
        </form>
      </section>
    </div>
  );
}
