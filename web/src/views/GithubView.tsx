import { useEffect, useRef, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  github, type GithubDetailResponse, type GithubItem, type GithubKind, type GithubState,
} from "../lib/github";

function readableDate(value: string | null) {
  if (!value) return "Data non disponibile";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" });
}

function CheckStatus({ status, conclusion }: { status: string; conclusion: string | null }) {
  const failed = conclusion === "failure" || conclusion === "timed_out";
  const passed = conclusion === "success";
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs ${failed ? "border-red-700 text-red-300" : passed ? "border-ink-500 text-ink-100" : "border-ink-700 text-ink-400"}`}>
      {conclusion ?? status}
    </span>
  );
}

export function GithubView({ active = true }: { active?: boolean }) {
  const detailRef = useRef<HTMLElement>(null);
  const [repos, setRepos] = useState<{ name: string; private: boolean }[]>([]);
  const [kind, setKind] = useState<GithubKind>("issues");
  const [state, setState] = useState<GithubState>("open");
  const [repo, setRepo] = useState("");
  const [searchText, setSearchText] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [items, setItems] = useState<GithubItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [total, setTotal] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [selected, setSelected] = useState<GithubItem | null>(null);
  const [detail, setDetail] = useState<GithubDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [draft, setDraft] = useState("");
  const [labelChoice, setLabelChoice] = useState("");
  const [autoMerge, setAutoMerge] = useState(false);
  const [acting, setActing] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    void github.repos().then((data) => {
      if (!cancelled) setRepos(data.repos);
    }).catch((reason: Error) => {
      if (!cancelled) setListError(reason.message);
    });
    return () => { cancelled = true; };
  }, [active]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setListLoading(true);
    setListError("");
    void github.items({ kind, state, repo, query, page }).then((data) => {
      if (cancelled) return;
      setItems(data.items);
      setHasMore(data.has_more);
      setTotal(data.total_count);
    }).catch((reason: Error) => {
      if (!cancelled) setListError(reason.message);
    }).finally(() => { if (!cancelled) setListLoading(false); });
    return () => { cancelled = true; };
  }, [active, kind, state, repo, query, page, reload]);

  useEffect(() => {
    if (!selected || !active) return;
    let cancelled = false;
    setDetail(null);
    setDetailLoading(true);
    setDetailError("");
    setLabelChoice("");
    void github.detail(selected).then((data) => {
      if (!cancelled) setDetail(data);
    }).catch((reason: Error) => {
      if (!cancelled) setDetailError(reason.message);
    }).finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [active, selected, reload]);

  function filtersChanged(update: () => void) {
    update();
    setPage(1);
    setSelected(null);
    setDetail(null);
    setActionError("");
    setActionMessage("");
  }

  async function execute(name: string, action: () => Promise<unknown>, success: string) {
    setActing(name);
    setActionError("");
    setActionMessage("");
    try {
      await action();
      setActionMessage(success);
      setReload((value) => value + 1);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Operazione non riuscita");
    } finally {
      setActing("");
    }
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    filtersChanged(() => setQuery(searchText.trim()));
  }

  const current = detail?.item && selected
    && detail.item.repo === selected.repo && detail.item.kind === selected.kind
    && detail.item.number === selected.number ? detail.item : null;
  return (
    <section aria-label="GitHub" className="min-w-0 space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink-100">GitHub</h1>
          <p className="mt-1 text-sm text-ink-400">Issue e pull request dei tuoi repository. Operazioni protette dalla sessione Plancia.</p>
        </div>
        <button className="button-secondary min-h-11" type="button" onClick={() => setReload((value) => value + 1)}>
          Aggiorna
        </button>
      </header>

      <div className="grid gap-3 rounded-xl border border-ink-800 bg-ink-900 p-4 sm:grid-cols-3">
        <label className="grid gap-1 text-xs text-ink-400" htmlFor="github-kind">
          Tipo
          <select id="github-kind" className="min-h-11 rounded-lg border border-ink-700 bg-ink-950 px-3 text-sm text-ink-100"
            value={kind} onChange={(event) => filtersChanged(() => setKind(event.target.value as GithubKind))}>
            <option value="issues">Issue</option>
            <option value="prs">Pull request</option>
          </select>
        </label>
        <label className="grid gap-1 text-xs text-ink-400" htmlFor="github-state">
          Stato
          <select id="github-state" className="min-h-11 rounded-lg border border-ink-700 bg-ink-950 px-3 text-sm text-ink-100"
            value={state} onChange={(event) => filtersChanged(() => setState(event.target.value as GithubState))}>
            <option value="open">Aperte</option><option value="closed">Chiuse</option><option value="all">Tutte</option>
          </select>
        </label>
        <label className="grid gap-1 text-xs text-ink-400" htmlFor="github-repo">
          Repository
          <select id="github-repo" className="min-h-11 rounded-lg border border-ink-700 bg-ink-950 px-3 text-sm text-ink-100"
            value={repo} onChange={(event) => filtersChanged(() => setRepo(event.target.value))}>
            <option value="">Tutti i repository</option>
            {repos.map((row) => <option key={row.name} value={row.name}>{row.name}{row.private ? " · privato" : ""}</option>)}
          </select>
        </label>
        <form onSubmit={submitSearch} className="flex gap-2 sm:col-span-3">
          <label htmlFor="github-search" className="sr-only">Cerca issue e pull request</label>
          <input id="github-search" type="search" maxLength={100} value={searchText} onChange={(event) => setSearchText(event.target.value)}
            placeholder="Cerca titolo o testo" className="min-h-11 min-w-0 flex-1 rounded-lg border border-ink-700 bg-ink-950 px-3 text-sm text-ink-100" />
          <button type="submit" className="button-secondary min-h-11">Cerca</button>
        </form>
      </div>

      <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,400px)_minmax(0,1fr)]">
        <div className="min-w-0 overflow-hidden rounded-xl border border-ink-800 bg-ink-900">
          <div className="flex items-center justify-between border-b border-ink-800 px-4 py-3 text-sm text-ink-300">
            <span>{listLoading ? "Caricamento…" : `${total} risultati`}</span>
            <span>Pagina {page}</span>
          </div>
          {listError && <p role="alert" className="px-4 py-3 text-sm text-red-300">{listError}</p>}
          {!listLoading && !listError && items.length === 0 && <p className="px-4 py-6 text-sm text-ink-400">Nessun risultato con questi filtri.</p>}
          <div className="divide-y divide-ink-800">
            {items.map((item) => {
              const picked = selected?.repo === item.repo && selected.number === item.number && selected.kind === item.kind;
              return (
                <button type="button" key={`${item.repo}-${item.kind}-${item.number}`}
                  onClick={() => {
                    setSelected(item); setDetail(null); setActionError(""); setActionMessage(""); setDraft("");
                    if (window.matchMedia("(max-width: 1279px)").matches) {
                      detailRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
                    }
                  }}
                  aria-pressed={picked} className={`w-full min-h-20 px-4 py-3 text-left hover:bg-ink-800 focus-visible:outline-2 focus-visible:outline-accent ${picked ? "bg-ink-800" : ""}`}>
                  <span className="block text-xs text-ink-400">{item.repo} #{item.number} · {item.state}</span>
                  <span className="mt-1 block break-words text-sm font-medium text-ink-100">{item.title}</span>
                  <span className="mt-1 block text-xs text-ink-500">Aggiornata {readableDate(item.updated_at)}</span>
                </button>
              );
            })}
          </div>
          <div className="flex items-center justify-between border-t border-ink-800 px-4 py-3">
            <button className="button-secondary min-h-10 text-sm" disabled={page === 1} type="button"
              onClick={() => setPage((n) => Math.max(1, n - 1))}>Precedente</button>
            <button className="button-secondary min-h-10 text-sm" disabled={!hasMore} type="button"
              onClick={() => setPage((n) => n + 1)}>Successiva</button>
          </div>
        </div>

        <article ref={detailRef} aria-label="Dettaglio GitHub" className="min-w-0 overflow-hidden rounded-xl border border-ink-800 bg-ink-900 p-4 sm:p-6">
          {!selected && <p className="text-sm text-ink-400">Seleziona un elemento per leggere dettagli, commenti e verifiche.</p>}
          {selected && detailLoading && <p role="status" className="text-sm text-ink-400">Caricamento dettaglio…</p>}
          {detailError && <p role="alert" className="text-sm text-red-300">{detailError}</p>}
          {current && selected && (
            <div className="min-w-0 space-y-6">
              <header className="space-y-2">
                <div className="flex flex-wrap items-center gap-2 text-xs text-ink-400">
                  <span>{current.repo} #{current.number}</span>
                  <span className="rounded-full border border-ink-600 px-2 py-0.5">{current.merged ? "merged" : current.state}</span>
                  {current.draft && <span>Bozza</span>}
                  <a href={current.url} target="_blank" rel="noopener noreferrer" className="ml-auto underline underline-offset-4">Apri su GitHub</a>
                </div>
                <h2 className="break-words text-xl font-semibold text-ink-100">{current.title}</h2>
                <p className="text-xs text-ink-400">Di {current.author ?? "utente sconosciuto"} · {readableDate(current.updated_at)}</p>
                <div className="flex flex-wrap gap-1">
                  {current.labels.map((label) => <span key={label} className="rounded-md border border-ink-700 px-2 py-0.5 text-xs text-ink-300">{label}</span>)}
                </div>
              </header>
              <div className="prose prose-invert max-w-none overflow-x-auto break-words text-sm text-ink-200">
                {current.body ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{current.body}</ReactMarkdown> : <p className="text-ink-500">Nessuna descrizione.</p>}
              </div>

              {current.kind === "prs" && (
                <section aria-label="Verifiche CI" className="space-y-3 border-t border-ink-800 pt-5">
                  <h3 className="font-semibold text-ink-100">Check e stato merge</h3>
                  <p className="break-all text-xs text-ink-400">Head {current.head_sha ?? "non disponibile"} · Merge: {current.mergeable_state ?? "non ancora calcolato"}</p>
                  {(detail?.checks ?? []).length === 0 && <p className="text-sm text-ink-400">Nessun check riportato da GitHub.</p>}
                  {(detail?.checks ?? []).map((check, index) => (
                    <div key={`${check.name}-${index}`} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-ink-800 p-3 text-sm">
                      <a href={check.url} target="_blank" rel="noopener noreferrer" className="break-words text-ink-100 underline underline-offset-4">{check.name}</a>
                      <CheckStatus status={check.status} conclusion={check.conclusion} />
                    </div>
                  ))}
                  {(detail?.runs ?? []).filter((run) => run.conclusion === "failure" || run.conclusion === "timed_out").map((run) => (
                    <div key={run.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-ink-800 p-3 text-sm">
                      <span className="text-ink-300">Workflow: {run.name}</span>
                      <button className="button-secondary min-h-10" type="button" disabled={!!acting || !current.head_sha}
                        onClick={() => current.head_sha && void execute("rerun", () => github.rerun(current, current.head_sha!, run.id), "Nuova esecuzione richiesta a GitHub.")}>
                        Riprova check falliti
                      </button>
                    </div>
                  ))}
                  {current.state === "open" && current.head_sha && (
                    <div className="space-y-3 rounded-lg border border-ink-700 p-3">
                      <label className="flex items-center gap-2 text-sm text-ink-300">
                        <input type="checkbox" checked={autoMerge} onChange={(event) => setAutoMerge(event.target.checked)} />
                        Consento il merge automatico anche se un autore con accesso write pubblica commit successivi prima dell'esecuzione
                      </label>
                      <button className="button-secondary min-h-11" type="button" disabled={!!acting}
                        onClick={() => {
                          if (!window.confirm(autoMerge
                            ? `Attenzione: GitHub può mantenere attivo il merge automatico su nuovi commit di autori con accesso write. Confermi il merge automatico della PR #${current.number} con head attuale ${current.head_sha?.slice(0, 12)}?`
                            : `Confermi squash merge immediato della PR #${current.number} all'head ${current.head_sha?.slice(0, 12)}?`)) return;
                          void execute("merge", () => github.merge(current, current.head_sha!, autoMerge),
                            autoMerge ? "Merge automatico richiesto: controlla i requisiti GitHub." : "Merge richiesto: verifica lo stato aggiornato.");
                        }}>
                        Squash merge · head verificato
                      </button>
                    </div>
                  )}
                </section>
              )}

              {current.kind === "issues" && (
                <section aria-label="Azioni issue" className="space-y-3 border-t border-ink-800 pt-5">
                  <h3 className="font-semibold text-ink-100">Gestione issue</h3>
                  <button className="button-secondary min-h-11" type="button" disabled={!!acting}
                    onClick={() => {
                      const next = current.state === "open" ? "closed" : "open";
                      if (!window.confirm(`Confermi ${next === "closed" ? "la chiusura" : "la riapertura"} della issue #${current.number}?`)) return;
                      void execute("state", () => github.state(current, next), next === "closed" ? "Issue chiusa su GitHub." : "Issue riaperta su GitHub.");
                    }}>
                    {current.state === "open" ? "Chiudi issue" : "Riapri issue"}
                  </button>
                  <div className="flex flex-wrap gap-2">
                    <label htmlFor="github-label" className="sr-only">Etichetta disponibile</label>
                    <select id="github-label" className="min-h-11 min-w-0 flex-1 rounded-lg border border-ink-700 bg-ink-950 px-3 text-sm"
                      value={labelChoice} onChange={(event) => setLabelChoice(event.target.value)}>
                      <option value="">Scegli un'etichetta</option>
                      {current.available_labels.filter((label) => !current.labels.includes(label)).map((label) =>
                        <option key={label} value={label}>{label}</option>)}
                    </select>
                    <button className="button-secondary min-h-11" type="button" disabled={!!acting || !labelChoice}
                      onClick={() => void execute("label", () => github.label(current, labelChoice), "Etichetta aggiunta.")}>
                      Aggiungi
                    </button>
                  </div>
                </section>
              )}

              <section aria-label="Commenti GitHub" className="space-y-3 border-t border-ink-800 pt-5">
                <h3 className="font-semibold text-ink-100">Commenti</h3>
                {current.comments.length === 0 && <p className="text-sm text-ink-400">Nessun commento.</p>}
                {current.comments.map((comment) => (
                  <div key={comment.id} className="min-w-0 rounded-lg border border-ink-800 p-3">
                    <div className="mb-2 flex flex-wrap justify-between gap-2 text-xs text-ink-400">
                      <span>{comment.author ?? "utente"} · {readableDate(comment.created_at)}</span>
                      <a href={comment.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">GitHub</a>
                    </div>
                    {comment.context && <p className="mb-2 break-all text-xs text-ink-500">{comment.context}</p>}
                    <div className="prose prose-invert max-w-none overflow-x-auto break-words text-sm text-ink-200">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{comment.body}</ReactMarkdown>
                    </div>
                  </div>
                ))}
                {current.comments.length >= 100 && <p className="text-xs text-ink-400">Visualizzati i primi 100 commenti; apri GitHub per i precedenti.</p>}
                <label htmlFor="github-comment" className="block text-sm text-ink-300">Nuovo commento</label>
                <textarea id="github-comment" rows={4} maxLength={10000} value={draft} onChange={(event) => setDraft(event.target.value)}
                  placeholder="Scrivi un commento" className="w-full rounded-lg border border-ink-700 bg-ink-950 p-3 text-sm text-ink-100" />
                <button className="button-secondary min-h-11" type="button" disabled={!!acting || !draft.trim()}
                  onClick={() => void execute("comment", async () => {
                    await github.comment(current, draft.trim());
                    setDraft("");
                  }, "Commento pubblicato su GitHub.")}>
                  Pubblica commento
                </button>
              </section>
              {acting && <p role="status" className="text-sm text-ink-400">Operazione in corso…</p>}
              {actionError && <p role="alert" className="text-sm text-red-300">{actionError}</p>}
              {actionMessage && <p role="status" className="text-sm text-ink-200">{actionMessage}</p>}
            </div>
          )}
        </article>
      </div>
    </section>
  );
}
