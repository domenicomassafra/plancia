import assert from "node:assert/strict";
import test from "node:test";
import { isTerminalStreamError, subscribeFleet } from "../src/lib/api.ts";

test("non-retryable stream errors stop reconnect attempts", () => {
  assert.equal(isTerminalStreamError({ type: "error", retryable: false }), true);
  assert.equal(isTerminalStreamError({ type: "error", retryable: true }), false);
  assert.equal(isTerminalStreamError({ type: "snapshot" }), false);
});

test("a delivering HTTP polling fallback keeps a non-offline status after a failure", async () => {
  const scope = globalThis as Record<string, unknown>;
  const original = {
    window: scope.window,
    fetch: scope.fetch,
    WebSocket: scope.WebSocket,
  };

  const intervals: (() => void)[] = [];
  const sockets: { onclose?: () => void }[] = [];
  let fail = false;

  scope.window = {
    location: { protocol: "https:", host: "fleet.example.test" },
    setInterval: (handler: () => void) => {
      intervals.push(handler);
      return intervals.length;
    },
    clearInterval: () => {},
    setTimeout: () => 1,
    clearTimeout: () => {},
  };
  scope.fetch = async () => {
    if (fail) throw new Error("temporary network failure");
    return {
      ok: true,
      json: async () => ({ generated_at: 0, poll_interval_s: 5, hosts: [], fleet: {} }),
    };
  };
  scope.WebSocket = class {
    onclose?: () => void;
    constructor() {
      sockets.push(this);
    }
    close() {}
  };

  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
  try {
    const statuses: string[] = [];
    const unsubscribe = subscribeFleet(
      () => {},
      (status) => statuses.push(status),
    );

    sockets[0].onclose?.(); // WebSocket drops, HTTP fallback takes over.
    await settle();
    assert.equal(statuses.at(-1), "polling");

    fail = true;
    intervals[0](); // One poll tick fails.
    await settle();
    assert.equal(statuses.at(-1), "offline");

    fail = false;
    intervals[0](); // The next poll tick recovers with live data.
    await settle();
    assert.equal(statuses.at(-1), "polling");

    unsubscribe();
  } finally {
    scope.window = original.window;
    scope.fetch = original.fetch;
    scope.WebSocket = original.WebSocket;
  }
});

test("a delayed polling reply cannot overwrite a newer live WebSocket snapshot", async () => {
  const scope = globalThis as Record<string, unknown>;
  const original = { window: scope.window, fetch: scope.fetch, WebSocket: scope.WebSocket };
  const reconnects: (() => void)[] = [];
  const sockets: { onopen?: () => void; onclose?: () => void }[] = [];
  let finishPoll: ((result: Response) => void) | undefined;

  scope.window = {
    location: { protocol: "https:", host: "fleet.example.test" },
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout: (fn: () => void) => { reconnects.push(fn); return reconnects.length; },
    clearTimeout: () => {},
  };
  scope.fetch = () => new Promise<Response>((resolve) => { finishPoll = resolve; });
  scope.WebSocket = class {
    onopen?: () => void;
    onclose?: () => void;
    constructor() { sockets.push(this); }
    close() {}
  };

  let unsubscribe: (() => void) | undefined;
  try {
    const snapshots: unknown[] = [];
    unsubscribe = subscribeFleet((value) => snapshots.push(value), () => {});

    sockets[0].onclose?.(); // Starts HTTP polling with a pending response.
    assert.ok(finishPoll, "polling request started");
    reconnects[0]();
    sockets[1].onopen?.(); // The live connection supersedes polling.

    finishPoll?.(Response.json({ generated_at: 1, hosts: ["stale"] }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.deepEqual(snapshots, [], "outdated HTTP data must not replace the live UI state");
  } finally {
    unsubscribe?.();
    Object.assign(scope, original);
  }
});

test("events from a replaced WebSocket cannot close or downgrade the live connection", async () => {
  const scope = globalThis as Record<string, unknown>;
  const original = { window: scope.window, fetch: scope.fetch, WebSocket: scope.WebSocket };
  const sockets: {
    onopen?: () => void;
    onclose?: () => void;
    onerror?: () => void;
    onmessage?: (event: { data: string }) => void;
    closeCount: number;
    close: () => void;
  }[] = [];

  scope.window = {
    location: { protocol: "https:", host: "fleet.example.test" },
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout: () => 1,
    clearTimeout: () => {},
  };
  scope.fetch = async () => Response.json({ session: "refreshed" });
  scope.WebSocket = class {
    onopen?: () => void;
    onclose?: () => void;
    onerror?: () => void;
    onmessage?: (event: { data: string }) => void;
    closeCount = 0;
    constructor() { sockets.push(this); }
    close() { this.closeCount += 1; }
  };

  let unsubscribe: (() => void) | undefined;
  try {
    const statuses: string[] = [];
    unsubscribe = subscribeFleet(() => {}, (status) => statuses.push(status));
    const oldSocket = sockets[0];
    // A session-expired frame starts bootstrap immediately; the native close
    // event for this socket can arrive after bootstrap has opened a new one.
    oldSocket.onmessage?.({ data: JSON.stringify({ type: "error", code: "session_expired" }) });
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(sockets.length, 2, "session bootstrap should reconnect");
    const liveSocket = sockets[1];
    liveSocket.onopen?.();
    assert.equal(statuses.at(-1), "live");

    oldSocket.onerror?.();
    assert.equal(liveSocket.closeCount, 0, "old socket error must not close the new socket");
    oldSocket.onclose?.();
    assert.equal(statuses.at(-1), "live", "old close must not restart polling");
  } finally {
    unsubscribe?.();
    Object.assign(scope, original);
  }
});

test("a stream awaiting session refresh ignores further frames from its old socket", async () => {
  const scope = globalThis as Record<string, unknown>;
  const original = { window: scope.window, fetch: scope.fetch, WebSocket: scope.WebSocket };
  const sockets: {
    onmessage?: (event: { data: string }) => void;
    close: () => void;
  }[] = [];
  let finishBootstrap: ((response: Response) => void) | undefined;
  let bootstrapCount = 0;
  scope.window = {
    location: { protocol: "https:", host: "fleet.example.test" },
    clearInterval: () => {},
    clearTimeout: () => {},
  };
  scope.fetch = () => {
    bootstrapCount += 1;
    return new Promise<Response>((resolve) => { finishBootstrap = resolve; });
  };
  scope.WebSocket = class {
    onmessage?: (event: { data: string }) => void;
    constructor() { sockets.push(this); }
    close() {}
  };

  let unsubscribe: (() => void) | undefined;
  try {
    const snapshots: unknown[] = [];
    unsubscribe = subscribeFleet((value) => snapshots.push(value), () => {});
    const staleSocket = sockets[0];
    staleSocket.onmessage?.({ data: JSON.stringify({ type: "error", code: "session_expired" }) });
    staleSocket.onmessage?.({ data: JSON.stringify({ type: "snapshot", data: { hosts: ["stale"] } }) });
    staleSocket.onmessage?.({ data: JSON.stringify({ type: "error", code: "session_expired" }) });

    assert.deepEqual(snapshots, [], "pending auth recovery must ignore stale snapshots");
    assert.equal(bootstrapCount, 1, "pending auth recovery must not start twice");
    finishBootstrap?.(Response.json({ session: "refreshed" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(sockets.length, 2, "only one replacement socket should be opened");
  } finally {
    unsubscribe?.();
    Object.assign(scope, original);
  }
});

test("unsubscribed fleet stream ignores late WebSocket updates", () => {
  const scope = globalThis as Record<string, unknown>;
  const original = { window: scope.window, WebSocket: scope.WebSocket };
  const sockets: {
    onopen?: () => void;
    onmessage?: (event: { data: string }) => void;
    close: () => void;
  }[] = [];
  scope.window = {
    location: { protocol: "https:", host: "fleet.example.test" },
    clearInterval: () => {},
    clearTimeout: () => {},
  };
  scope.WebSocket = class {
    onopen?: () => void;
    onmessage?: (event: { data: string }) => void;
    constructor() { sockets.push(this); }
    close() {}
  };
  try {
    const statuses: string[] = [];
    const snapshots: unknown[] = [];
    const unsubscribe = subscribeFleet((value) => snapshots.push(value), (status) => statuses.push(status));
    unsubscribe();
    sockets[0].onopen?.();
    sockets[0].onmessage?.({ data: JSON.stringify({ type: "snapshot", data: { hosts: ["late"] } }) });
    assert.deepEqual(statuses, ["connecting"], "teardown must not publish a live connection");
    assert.deepEqual(snapshots, [], "teardown must not publish a late fleet snapshot");
  } finally {
    Object.assign(scope, original);
  }
});
