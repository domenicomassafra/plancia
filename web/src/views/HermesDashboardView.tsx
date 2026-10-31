import { hermesDashboardUrl } from "../lib/hermes-dashboard";

/** The native Hermes dashboard owns profile selection, websocket and session input. */
export function HermesDashboardView() {
  const url = typeof window === "undefined" ? null : hermesDashboardUrl(window.location);
  return (
    <section className="space-y-4" aria-label="App Hermes ufficiale">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Gateway originale · MiniPC</p>
          <h2 className="mt-1 text-xl font-semibold text-ink-100">Chat Hermes</h2>
          <p className="mt-2 text-sm text-ink-400">
            L'app Hermes ufficiale gestisce le conversazioni dei Signori, con sessioni e permessi propri.
          </p>
        </div>
        {url && <a href={url} target="_blank" rel="noopener noreferrer"
          className="button-secondary min-h-11">Apri Hermes a schermo intero</a>}
      </header>
      {url ? (
        <div className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-900">
          <iframe title="Hermes ufficiale · chat dei Signori" src={url}
            loading="lazy" referrerPolicy="no-referrer"
            className="h-[min(76vh,960px)] min-h-[560px] w-full border-0" />
        </div>
      ) : (
        <p role="status" className="rounded-xl border border-ink-700 bg-ink-900 p-5 text-sm text-ink-300">
          L'app Hermes integrata è disponibile aprendo Plancia dall'URL HTTPS tailnet del MiniPC.
        </p>
      )}
    </section>
  );
}
