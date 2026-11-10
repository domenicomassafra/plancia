import assert from "node:assert/strict";
import test from "node:test";
import {
  orcaScreen, orcaSearch, orcaSend, orcaTerminals,
  type OrcaRequester,
} from "../src/lib/orca-chat.ts";

const reply = (body: unknown): OrcaRequester => async <T>() => body as T;

test("Orca terminal inventory keeps the native handle and a server-controlled write grant", async () => {
  const called: string[] = [];
  const client: OrcaRequester = async <T>(path: string) => {
    called.push(path);
    return { donor: "orca", terminals: [
      { handle: "term_native-1", title: "Pi", agent: "pi", connected: false, writable: true },
    ] } as T;
  };
  const terminals = await orcaTerminals(client);
  assert.equal(called[0], "/api/v1/agents/terminals");
  assert.equal(terminals[0].writable, false);
  const legacy = await orcaTerminals(reply({ donor: "orca", terminals: [
    { handle: "term_old", title: "Legacy", agent: "pi", connected: true, writable: true },
  ] }));
  assert.equal(legacy[0].writable, false, "no process identity means no safe native retry");
  await assert.rejects(orcaTerminals(reply({ donor: "fake", terminals: [] })), /incompatibile/);
  await assert.rejects(orcaTerminals(reply({ donor: "orca", terminals: [
    { handle: "../escape", agent: "pi", title: "bad" },
  ] })), /Handle/);
});

test("Live readback refuses another terminal's screen and unbounded payloads", async () => {
  const terminal = { handle: "term_a", incarnationId: "process-123", title: "Pi", agent: "pi",
    connected: true, writable: true };
  const valid = { handle: "term_a", incarnationId: "process-123", source: "screen", text: "output incrementale" };
  assert.equal(await orcaScreen(terminal, reply(valid)), valid.text);
  await assert.rejects(orcaScreen(terminal, reply({ ...valid, handle: "term_b" })), /cambiato/);
  await assert.rejects(orcaScreen(terminal, reply({ ...valid, incarnationId: "other-process" })), /cambiato/);
  await assert.rejects(orcaScreen(terminal, reply({ ...valid, text: "x".repeat(16_001) })), /non valido/);
  await assert.rejects(orcaScreen({ ...terminal, handle: "../oops" }, reply(valid)), /non valido/);
});

test("Pi's native indexed session remains a search excerpt, not a fabricated transcript", async () => {
  const calls: { path: string; init?: RequestInit }[] = [];
  const client: OrcaRequester = async <T>(path, init) => {
    calls.push({ path, init });
    return {
      host: "minipc",
      hits: [{ agent: "pi", sessionId: "original-1", title: "Test",
        updatedAt: null, messageCount: 9, snippet: "Estratto autentico" }],
    } as T;
  };
  const hits = await orcaSearch("  git   ", client);
  assert.equal(hits[0].agent, "pi");
  assert.equal(hits[0].sessionId, "original-1");
  assert.equal(calls[0].path, "/api/v1/agents/search");
  assert.equal(calls[0].init?.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { query: "git", host: "minipc" });
  await assert.rejects(orcaSearch("x", client), /2–120/);
  await assert.rejects(orcaSearch("git", reply({ host: "other", hits: [] })), /incompatibili/);
  const studioCalls: string[] = [];
  await orcaSearch("repo", async <T>(_path, init) => {
    studioCalls.push(String(init?.body));
    return { host: "studio", hits: [] } as T;
  }, "studio");
  assert.deepEqual(JSON.parse(studioCalls[0]), { query: "repo", host: "studio" });
});

test("Native send retains request and process identity across caller retries", async () => {
  const calls: { path: string; body: unknown }[] = [];
  const attempt = { handle: "term_real", incarnationId: "process-123",
    text: "ciao", retryRequest: "req_same_1234" };
  const client: OrcaRequester = async <T>(path, init) => {
    calls.push({ path, body: JSON.parse(String(init?.body)) });
    assert.equal(init?.method, "POST");
    return { handle: "term_real", incarnationId: "process-123", accepted: true,
      status: "accepted", submissionObserved: false } as T;
  };
  assert.equal(await orcaSend(attempt, client), "accepted");
  assert.equal(await orcaSend(attempt, client), "accepted");
  const expected = { path: "/api/v1/agents/terminals/term_real/send", body: {
    text: "ciao", incarnation_id: "process-123", retry_request: "req_same_1234",
  } };
  assert.deepEqual(calls, [expected, expected]);
  await assert.rejects(orcaSend({ ...attempt, text: "bad\u0000" }, client), /non valido/);
  await assert.rejects(orcaSend({ ...attempt, retryRequest: "!" }, client), /non valido/);
  assert.equal(calls.length, 2);
  await assert.rejects(orcaSend(attempt, reply({ handle: "another", accepted: true })), /confermato/);
});

test("Accepted-but-lost native input remains unknown until same-request replay is read back", async () => {
  const attempt = { handle: "term_real", incarnationId: "process-123",
    text: "fixture only", retryRequest: "req_stable_1234" };
  const calls: unknown[] = [];
  const admitted = new Set<string>();
  const client: OrcaRequester = async <T>(_path, init) => {
    const body = JSON.parse(String(init?.body));
    calls.push(body);
    admitted.add(body.retry_request);
    if (calls.length === 1) throw new Error("503 after native acceptance");
    return { handle: "term_real", incarnationId: "process-123", accepted: true,
      status: "observed", submissionObserved: true, replayed: true } as T;
  };
  await assert.rejects(orcaSend(attempt, client), /503 after native acceptance/);
  assert.equal(await orcaSend(attempt, client), "observed");
  assert.deepEqual(calls, [{ text: "fixture only", incarnation_id: "process-123",
    retry_request: "req_stable_1234" }, { text: "fixture only", incarnation_id: "process-123",
    retry_request: "req_stable_1234" }]);
  assert.equal(admitted.size, 1);
});
