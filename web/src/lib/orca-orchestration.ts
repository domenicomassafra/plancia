/** Orca's orchestration contracts, projected through Plancia's owner-only API. */
import type { OrcaRequester, OrcaSearchHost } from "./orca-chat";

export type OrcaRun = { id: string; objective: string; updatedAt: string; manageable?: boolean };
export type OrcaTask = { id: string; title: string; status: string };
export type OrcaWorker = {
  dispatchId: string; taskId: string; workerState: string; dispatchStatus: string;
  terminalState: string; host: string; outcome: string;
};
export type OrcaRunDetail = { run: OrcaRun; tasks: OrcaTask[]; workers: OrcaWorker[];
  counts: Record<string, number>; workersNextCursor: string | null };
export type OrcaWorkerPage = { workers: OrcaWorker[]; nextCursor: string | null };
export type OrcaWorktree = { id: string; name: string };
export type OrcaStart = {
  run_id: string; task_title: string; spec: string; host: OrcaSearchHost;
  agent: "pi" | "codex" | "claude" | "opencode" | "opencode2";
  worktree_id: string; confirmed: true; retry_request: string;
};
export type OrcaLifecycle = { run_id: string; confirmed: true; retry_request: string };
const RUN = /^run_[a-zA-Z0-9]{8,40}$/;
const DISPATCH = /^ctx_[a-zA-Z0-9]{8,40}$/;

function checkRun(id: string) {
  if (!RUN.test(id)) throw new Error("Run Orca non valida");
}
function checkDispatch(id: string) {
  if (!DISPATCH.test(id)) throw new Error("Dispatch Orca non valido");
}

export async function orcaRuns(request: OrcaRequester): Promise<OrcaRun[]> {
  const body = await request<{ donor: string; runs: OrcaRun[] }>("/api/v1/agents/orchestration/runs",
    { cache: "no-store" });
  if (body.donor !== "orca" || !Array.isArray(body.runs) || body.runs.some((run) => !RUN.test(run.id))) {
    throw new Error("Inventario Run Orca incompatibile");
  }
  return body.runs;
}

export async function orcaRunDetail(id: string, request: OrcaRequester, signal?: AbortSignal): Promise<OrcaRunDetail> {
  checkRun(id);
  const body = await request<OrcaRunDetail>(`/api/v1/agents/orchestration/runs/${id}`,
    { signal, cache: "no-store" });
  if (body.run?.id !== id || !Array.isArray(body.tasks) || !Array.isArray(body.workers)
    || body.workers.some((worker) => !DISPATCH.test(worker.dispatchId))) {
    throw new Error("Dettaglio Run Orca incompatibile");
  }
  return body;
}

export async function orcaMoreWorkers(id: string, cursor: string, request: OrcaRequester): Promise<OrcaWorkerPage> {
  checkRun(id);
  if (!cursor || cursor.length > 2048) throw new Error("Cursore Orca non valido");
  const page = await request<OrcaWorkerPage>(
    `/api/v1/agents/orchestration/runs/${id}/workers?cursor=${encodeURIComponent(cursor)}`,
    { cache: "no-store" });
  if (!Array.isArray(page.workers) || page.workers.some((worker) => !DISPATCH.test(worker.dispatchId))) {
    throw new Error("Pagina worker Orca incompatibile");
  }
  return page;
}

export async function orcaWorktrees(host: OrcaSearchHost, request: OrcaRequester): Promise<OrcaWorktree[]> {
  const body = await request<{ host: string; worktrees: OrcaWorktree[] }>(
    `/api/v1/agents/orchestration/hosts/${host}/worktrees`, { cache: "no-store" });
  if (body.host !== host || !Array.isArray(body.worktrees)
    || body.worktrees.some((worktree) => typeof worktree.id !== "string" || !worktree.id.includes("::"))) {
    throw new Error("Worktree Orca incompatibili");
  }
  return body.worktrees;
}

export async function orcaWorkerOutput(dispatchId: string, request: OrcaRequester, cursor?: string) {
  checkDispatch(dispatchId);
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  const body = await request<{ dispatchId: string; text: string; source: string; cursor: string | null }>(
    `/api/v1/agents/orchestration/dispatches/${dispatchId}/output${query}`, { cache: "no-store" });
  if (body.dispatchId !== dispatchId || typeof body.text !== "string" || body.text.length > 16_000) {
    throw new Error("Output Orca incompatibile");
  }
  return body;
}

export async function orcaStartWorker(attempt: OrcaStart, request: OrcaRequester) {
  checkRun(attempt.run_id);
  const body = await request<{ action: string; dispatchId: string; retryRequest: string }>(
    "/api/v1/agents/orchestration/workers/start",
    { method: "POST", body: JSON.stringify(attempt) });
  if (body.action !== "start" || !DISPATCH.test(body.dispatchId)
    || body.retryRequest !== attempt.retry_request) throw new Error("Ricevuta Orca non confermata");
  return body.dispatchId;
}

export async function orcaWorkerLifecycle(action: "stop" | "release", dispatchId: string,
  attempt: OrcaLifecycle, request: OrcaRequester) {
  checkRun(attempt.run_id);
  checkDispatch(dispatchId);
  const body = await request<{ action: string; dispatchId: string; retryRequest: string; receipt: string }>(
    `/api/v1/agents/orchestration/dispatches/${dispatchId}/${action}`,
    { method: "POST", body: JSON.stringify(attempt) });
  if (body.action !== action || body.dispatchId !== dispatchId
    || body.retryRequest !== attempt.retry_request) throw new Error("Ricevuta Orca non confermata");
  return body.receipt;
}
