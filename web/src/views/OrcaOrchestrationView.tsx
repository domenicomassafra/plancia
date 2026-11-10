import { useEffect, useState, type FormEvent } from "react";
import { request } from "../lib/api";
import type { OrcaSearchHost } from "../lib/orca-chat";
import {
  orcaRunPage, orcaRunDetail, orcaWorktrees, orcaWorkerOutput, orcaStartWorker,
  orcaWorkerLifecycle, orcaMoreWorkers, type OrcaLifecycle, type OrcaRun, type OrcaRunDetail,
  type OrcaStart, type OrcaWorktree,
} from "../lib/orca-orchestration";

const HOSTS: { id: OrcaSearchHost; name: string }[] = [
  { id: "minipc", name: "MiniPC" }, { id: "studio", name: "Mac Studio" },
  { id: "macbookair", name: "MacBook Air" }, { id: "matebooklinux", name: "MateBook Linux" },
];
const message = (error: unknown) => error instanceof Error ? error.message : "Orca non disponibile";
const fieldClass = "min-h-11 w-full rounded-lg border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-ink-100";
type LifecycleAttempt = { action: "stop" | "release"; dispatchId: string; payload: OrcaLifecycle };

/** A native Orca reader and command surface; task/dispatch authority remains with Orca. */
export function OrcaOrchestrationView({ active }: { active: boolean }) {
  const [runs, setRuns] = useState<OrcaRun[]>([]);
  const [runsCursor, setRunsCursor] = useState<string | null>(null);
  const [runsBusy, setRunsBusy] = useState(false);
  const [runId, setRunId] = useState("");
  const [detail, setDetail] = useState<OrcaRunDetail | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Seleziona una Run per leggere incarichi e worker.");
  const [host, setHost] = useState<OrcaSearchHost>("minipc");
  const [worktrees, setWorktrees] = useState<OrcaWorktree[]>([]);
  const [worktreeId, setWorktreeId] = useState("");
  const [agent, setAgent] = useState<OrcaStart["agent"]>("pi");
  const [taskTitle, setTaskTitle] = useState("");
  const [spec, setSpec] = useState("");
  const [startAttempt, setStartAttempt] = useState<OrcaStart | null>(null);
  const [lifecycleAttempt, setLifecycleAttempt] = useState<LifecycleAttempt | null>(null);
  const [selectedDispatch, setSelectedDispatch] = useState("");
  const [workerText, setWorkerText] = useState("");
  const [workerCursor, setWorkerCursor] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [moreBusy, setMoreBusy] = useState(false);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    orcaRunPage(request).then((page) => {
      if (cancelled) return;
      const rows = page.runs;
      setRuns(rows);
      setRunsCursor(page.nextCursor);
      setRunId((previous) => rows.some((row) => row.id === previous) ? previous : rows[0]?.id ?? "");
    }).catch((error: unknown) => { if (!cancelled) setStatus(message(error)); });
    return () => { cancelled = true; };
  }, [active, refresh]);

  const moreRuns = async () => {
    if (!runsCursor || runsBusy) return;
    setRunsBusy(true);
    try {
      const page = await orcaRunPage(request, runsCursor);
      setRuns((current) => {
        const known = new Set(current.map((run) => run.id));
        return [...current, ...page.runs.filter((run) => !known.has(run.id))];
      });
      setRunsCursor(page.nextCursor);
    } catch (error) {
      setStatus(message(error));
    } finally {
      setRunsBusy(false);
    }
  };

  useEffect(() => {
    if (!active || !runId) return;
    const controller = new AbortController();
    setDetail(null);
    orcaRunDetail(runId, request, controller.signal).then((data) => {
      if (!controller.signal.aborted) setDetail(data);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setStatus(message(error));
    });
    return () => controller.abort();
  }, [active, runId, refresh]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setWorktrees([]);
    setWorktreeId("");
    orcaWorktrees(host, request).then((rows) => {
      if (!cancelled) setWorktrees(rows);
    }).catch((error: unknown) => { if (!cancelled) setStatus(message(error)); });
    return () => { cancelled = true; };
  }, [active, host]);

  const readWorker = async (dispatchId: string, more = false) => {
    if (reading) return;
    setReading(true);
    if (!more) {
      setSelectedDispatch(dispatchId);
      setWorkerText("");
      setWorkerCursor(null);
    }
    try {
      const output = await orcaWorkerOutput(dispatchId, request, more ? workerCursor ?? undefined : undefined);
      setWorkerText((previous) => more ? `${previous}\n${output.text}` : output.text);
      setWorkerCursor(output.cursor);
      setStatus(`Output letto da Orca · ${output.source}`);
    } catch (error) {
      setStatus(message(error));
    } finally {
      setReading(false);
    }
  };

  const moreWorkers = async () => {
    if (!detail?.workersNextCursor || moreBusy || !runId) return;
    setMoreBusy(true);
    try {
      const page = await orcaMoreWorkers(runId, detail.workersNextCursor, request);
      setDetail((current) => {
        if (!current || current.run.id !== runId) return current;
        const known = new Set(current.workers.map((worker) => worker.dispatchId));
        return { ...current, workers: [...current.workers,
          ...page.workers.filter((worker) => !known.has(worker.dispatchId))],
          workersNextCursor: page.nextCursor };
      });
    } catch (error) {
      setStatus(message(error));
    } finally {
      setMoreBusy(false);
    }
  };

  const start = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy || !runId || !detail?.run.manageable) return;
    let attempt = startAttempt;
    if (!attempt) {
      if (!worktreeId || !taskTitle.trim() || !spec.trim()) return;
      if (!window.confirm(`Avviare ${agent} su ${HOSTS.find((item) => item.id === host)?.name} nella Run ${runId}?\n\n${taskTitle}`)) return;
      attempt = { run_id: runId, host, agent, worktree_id: worktreeId,
        task_title: taskTitle.trim(), spec: spec.trim(), confirmed: true,
        retry_request: crypto.randomUUID() };
      setStartAttempt(attempt);
    }
    setBusy(true);
    try {
      const dispatch = await orcaStartWorker(attempt, request);
      setStartAttempt(null);
      setTaskTitle("");
      setSpec("");
      setStatus(`Avvio Orca accettato: ${dispatch} · richiesta ${attempt.retry_request}`);
      setRefresh((value) => value + 1);
    } catch (error) {
      setStatus(`Avvio non confermato: ${message(error)}. Riprova con la stessa richiesta dopo la verifica della Run.`);
    } finally {
      setBusy(false);
    }
  };

  const lifecycle = async (action: "stop" | "release", dispatchId: string) => {
    if (busy || !runId || !detail?.run.manageable) return;
    let attempt = lifecycleAttempt;
    if (attempt && (attempt.dispatchId !== dispatchId || attempt.action !== action)) return;
    if (!attempt) {
      const label = action === "stop" ? "Fermare" : "Rilasciare";
      if (!window.confirm(`${label} il worker ${dispatchId} nella Run ${runId}?\nIl comando opera sul worker Orca indicato.`)) return;
      attempt = { action, dispatchId, payload: {
        run_id: runId, confirmed: true, retry_request: crypto.randomUUID(),
      } };
      setLifecycleAttempt(attempt);
    }
    setBusy(true);
    try {
      const receipt = await orcaWorkerLifecycle(attempt.action, attempt.dispatchId, attempt.payload, request);
      setLifecycleAttempt(null);
      setStatus(`${action === "stop" ? "Stop" : "Rilascio"} Orca: ${receipt} · ${dispatchId} · richiesta ${attempt.payload.retry_request}`);
      setRefresh((value) => value + 1);
    } catch (error) {
      setStatus(`Esito non confermato: ${message(error)}. Riprova lo stesso comando con lo stesso ID.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5 rounded-2xl border border-ink-700 bg-ink-900 p-4 sm:p-5" aria-label="Orchestrazione Orca">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink-100">Run, incarichi e worker</h2>
          <p className="mt-1 text-sm text-ink-400">Stato e comandi dal runtime Orca originale.</p>
        </div>
        <button type="button" className="button-secondary min-h-11" onClick={() => setRefresh((value) => value + 1)}
          disabled={busy}>Aggiorna Run</button>
      </header>
      <label className="block space-y-2 text-sm text-ink-300">
        <span>Run Orca</span>
        <select aria-label="Seleziona Run" className={fieldClass} value={runId}
          disabled={busy || !!startAttempt || !!lifecycleAttempt}
          onChange={(event) => { setRunId(event.target.value); setSelectedDispatch(""); setWorkerText(""); setWorkerCursor(null); }}>
          {runs.length === 0 && <option value="">Nessuna Run disponibile</option>}
          {runs.map((run) => <option key={run.id} value={run.id}>{run.id} · {run.objective.slice(0, 90)}</option>)}
        </select>
      </label>
      {runsCursor && <button type="button" className="button-secondary min-h-11 text-xs"
        disabled={runsBusy || busy} onClick={() => { void moreRuns(); }}>Carica altre Run</button>}
      {detail && <>
        <p className="text-sm leading-6 text-ink-200">{detail.run.objective}</p>
        <p className="text-xs text-ink-300">{detail.run.manageable
          ? "Run gestita dal coordinatore Orca di Plancia: comandi disponibili."
          : "Run di un altro coordinatore Orca: consultazione disponibile, comandi disabilitati."}</p>
        <p className="text-xs text-ink-400">Aggiornata: {detail.run.updatedAt || "data non disponibile"} · Task: {detail.tasks.length} · Worker nella pagina: {detail.workers.length}</p>
        <div className="grid gap-4 lg:grid-cols-2">
          <section aria-label="Task Orca" className="min-w-0 rounded-xl border border-ink-700 p-3">
            <h3 className="mb-3 font-semibold text-ink-100">Task</h3>
            <div className="max-h-80 space-y-2 overflow-auto">
              {detail.tasks.length === 0 && <p className="text-sm text-ink-400">Nessun Task registrato.</p>}
              {detail.tasks.map((task) => <div key={task.id} className="rounded-lg bg-ink-850 p-3">
                <p className="break-words text-sm text-ink-100">{task.title || task.id}</p>
                <p className="mt-1 break-all text-xs text-ink-400">{task.id} · {task.status}</p>
              </div>)}
            </div>
          </section>
          <section aria-label="Dispatch Orca" className="min-w-0 rounded-xl border border-ink-700 p-3">
            <h3 className="mb-3 font-semibold text-ink-100">Dispatch e worker</h3>
            <div className="max-h-80 space-y-2 overflow-auto">
              {detail.workers.length === 0 && <p className="text-sm text-ink-400">Nessun worker in questa pagina.</p>}
              {detail.workers.map((worker) => {
                const stopped = ["stopped", "succeeded", "failed"].includes(worker.workerState);
                const activeWorker = worker.terminalState === "active";
                const retrying = lifecycleAttempt?.dispatchId === worker.dispatchId;
                return <div key={worker.dispatchId} className="rounded-lg bg-ink-850 p-3">
                  <p className="break-all text-sm font-medium text-ink-100">{worker.dispatchId}</p>
                  <p className="mt-1 break-all text-xs text-ink-400">{worker.taskId} · {worker.host || "host sconosciuto"}</p>
                  <p className="mt-1 text-xs text-ink-300">{worker.workerState} · {worker.dispatchStatus} · risorsa: {worker.terminalState || "assente"}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button type="button" className="button-secondary min-h-11 text-xs" disabled={reading}
                      onClick={() => { void readWorker(worker.dispatchId); }}>Leggi output</button>
                    <button type="button" className="button-secondary min-h-11 text-xs"
                      disabled={busy || !detail.run.manageable || (!retrying && !activeWorker) || (lifecycleAttempt !== null && (!retrying || lifecycleAttempt.action !== "stop"))}
                      onClick={() => { void lifecycle("stop", worker.dispatchId); }}>
                      {retrying && lifecycleAttempt?.action === "stop" ? "Riprova stop" : "Stop"}
                    </button>
                    <button type="button" className="button-secondary min-h-11 text-xs"
                      disabled={busy || !detail.run.manageable || (!retrying && !stopped) || (lifecycleAttempt !== null && (!retrying || lifecycleAttempt.action !== "release"))}
                      onClick={() => { void lifecycle("release", worker.dispatchId); }}>
                      {retrying && lifecycleAttempt?.action === "release" ? "Riprova rilascio" : "Rilascia"}
                    </button>
                  </div>
                </div>;
              })}
            </div>
            {detail.workersNextCursor && <button type="button" className="button-secondary mt-3 min-h-11 text-xs"
              disabled={moreBusy} onClick={() => { void moreWorkers(); }}>Carica altri worker</button>}
          </section>
        </div>
      </>}
      {selectedDispatch && <section aria-label="Output worker Orca" className="rounded-xl border border-ink-700 p-3">
        <h3 className="mb-2 break-all text-sm font-semibold text-ink-100">Output · {selectedDispatch}</h3>
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words bg-ink-950 p-3 font-mono text-xs text-ink-200">{workerText || "Nessun testo restituito da Orca."}</pre>
        {workerCursor && <button type="button" className="button-secondary mt-2 min-h-11 text-xs" disabled={reading}
          onClick={() => { void readWorker(selectedDispatch, true); }}>Continua lettura</button>}
      </section>}
      <form onSubmit={(event) => { void start(event); }} className="space-y-3 rounded-xl border border-ink-700 p-3" aria-label="Avvia worker Orca">
        <h3 className="font-semibold text-ink-100">Avvia worker supervisionato</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm text-ink-300">Host
            <select value={host} className={fieldClass} disabled={busy || !!startAttempt}
              onChange={(event) => setHost(event.target.value as OrcaSearchHost)}>
              {HOSTS.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label className="space-y-1 text-sm text-ink-300">Agente
            <select className={fieldClass} value={agent} disabled={busy || !!startAttempt}
              onChange={(event) => setAgent(event.target.value as OrcaStart["agent"])}>
              {(["pi", "codex", "claude", "opencode", "opencode2"] as const).map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </label>
        </div>
        <label className="block space-y-1 text-sm text-ink-300">Worktree Orca già registrato
          <select className={fieldClass} value={worktreeId} disabled={busy || !!startAttempt}
            onChange={(event) => setWorktreeId(event.target.value)}>
            <option value="">Scegli il worktree di destinazione</option>
            {worktrees.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.id.slice(0, 8)}</option>)}
          </select>
        </label>
        <label className="block space-y-1 text-sm text-ink-300">Titolo incarico
          <input className={fieldClass} value={taskTitle} maxLength={120} disabled={busy || !!startAttempt}
            onChange={(event) => setTaskTitle(event.target.value)} placeholder="Verifica in sola lettura" />
        </label>
        <label className="block space-y-1 text-sm text-ink-300">Istruzioni worker
          <textarea className={fieldClass} rows={3} value={spec} maxLength={3000} disabled={busy || !!startAttempt}
            onChange={(event) => setSpec(event.target.value)} placeholder="Leggi lo stato del servizio e restituisci la ricevuta; non modificare file." />
        </label>
        <button type="submit" className="button-secondary min-h-11"
          disabled={busy || !detail?.run.manageable || (!startAttempt && (!worktreeId || taskTitle.trim().length < 3 || spec.trim().length < 8))}>
          {startAttempt ? "Riprova stesso avvio" : "Conferma e avvia"}
        </button>
        {startAttempt && <p className="text-xs text-ink-400">Tentativo ${startAttempt.retry_request}: l'esito può essere incerto. Aggiorna la Run prima di riprovare.</p>}
      </form>
      <p role="status" className="break-words text-sm text-ink-300" aria-live="polite">{status}</p>
    </div>
  );
}
