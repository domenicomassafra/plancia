import assert from "node:assert/strict";
import test from "node:test";
import {
  orcaRuns, orcaRunPage, orcaRunDetail, orcaWorktrees, orcaWorkerOutput,
  orcaStartWorker, orcaWorkerLifecycle,
} from "../src/lib/orca-orchestration.ts";
import type { OrcaRequester } from "../src/lib/orca-chat.ts";

const RUN = "run_abcdef123456";
const DISPATCH = "ctx_abcdef123456";
const reply = (body: unknown): OrcaRequester => async <T>() => body as T;

test("Run inventory and detail use native identifiers and reject another Run", async () => {
  assert.equal((await orcaRuns(reply({ donor: "orca", runs: [{ id: RUN }] })))[0].id, RUN);
  const seen: string[] = [];
  const page = await orcaRunPage(async <T>(path) => {
    seen.push(path);
    return { donor: "orca", runs: [{ id: RUN }], nextCursor: "cursor-two" } as T;
  }, "cursor-one");
  assert.equal(seen[0], "/api/v1/agents/orchestration/runs?cursor=cursor-one");
  assert.equal(page.nextCursor, "cursor-two");
  await assert.rejects(orcaRuns(reply({ donor: "local", runs: [] })), /incompatibile/);
  const detail = { run: { id: RUN }, tasks: [], workers: [{ dispatchId: DISPATCH }] };
  assert.equal((await orcaRunDetail(RUN, reply(detail))).workers[0].dispatchId, DISPATCH);
  await assert.rejects(orcaRunDetail(RUN, reply({ ...detail, run: { id: "run_wrong123456" } })), /incompatibile/);
  await assert.rejects(orcaRunDetail("../escape", reply(detail)), /non valida/);
});

test("worktrees and native output are bounded by host and dispatch identity", async () => {
  const worktrees = await orcaWorktrees("studio", reply({ host: "studio", worktrees: [
    { id: "repo-1::/some/path", name: "Registered" },
  ] }));
  assert.equal(worktrees[0].name, "Registered");
  await assert.rejects(orcaWorktrees("studio", reply({ host: "minipc", worktrees: [] })), /incompatibili/);
  const output = await orcaWorkerOutput(DISPATCH, reply({
    dispatchId: DISPATCH, source: "transcript", text: "Verified", cursor: null,
  }));
  assert.equal(output.text, "Verified");
  await assert.rejects(orcaWorkerOutput(DISPATCH, reply({
    dispatchId: "ctx_other123456", text: "Wrong",
  })), /incompatibile/);
});

test("start and stop forward one native retry request and require matching receipts", async () => {
  const calls: { path: string; body: unknown }[] = [];
  const start = { run_id: RUN, task_title: "Inspect", spec: "Read status only",
    host: "studio" as const, agent: "pi" as const,
    worktree_id: "repo-1::/some/path", confirmed: true as const, retry_request: "req_abcdefgh" };
  const client: OrcaRequester = async <T>(path, init) => {
    calls.push({ path, body: JSON.parse(String(init?.body)) });
    assert.equal(init?.method, "POST");
    return (path.endsWith("/start")
      ? { action: "start", dispatchId: DISPATCH, retryRequest: start.retry_request }
      : { action: "stop", dispatchId: DISPATCH, retryRequest: start.retry_request, receipt: "stopped" }) as T;
  };
  assert.equal(await orcaStartWorker(start, client), DISPATCH);
  assert.equal(await orcaWorkerLifecycle("stop", DISPATCH,
    { run_id: RUN, confirmed: true, retry_request: start.retry_request }, client), "stopped");
  assert.equal((calls[0].body as { retry_request: string }).retry_request, start.retry_request);
  await assert.rejects(orcaStartWorker(start, reply({ action: "start", dispatchId: "other" })), /non confermata/);
  await assert.rejects(orcaWorkerLifecycle("release", "../nope",
    { run_id: RUN, confirmed: true, retry_request: start.retry_request }, client), /non valido/);
});
