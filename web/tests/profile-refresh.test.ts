import assert from "node:assert/strict";
import test from "node:test";
import { api } from "../src/lib/api.ts";

test("a forced profile refresh reads the new inventory after an in-flight normal load", async () => {
  const originalFetch = globalThis.fetch;
  let releaseFirst: ((response: Response) => void) | undefined;
  const firstResponse = new Promise<Response>((resolve) => { releaseFirst = resolve; });
  const requests: string[] = [];

  globalThis.fetch = async (input) => {
    const path = String(input);
    requests.push(path);
    if (requests.length === 1) return firstResponse;
    return Response.json({ version: "fresh" });
  };

  try {
    const oldInventory = api.profiles();
    const refreshedInventory = api.profiles(true);
    releaseFirst?.(Response.json({ version: "old" }));

    assert.equal((await oldInventory as unknown as { version: string }).version, "old");
    assert.equal((await refreshedInventory as unknown as { version: string }).version, "fresh");
    assert.deepEqual(requests, ["/api/profiles", "/api/profiles?refresh=true"]);
  } finally {
    globalThis.fetch = originalFetch;
    api.invalidateProfiles();
  }
});

test("invalidating profiles prevents an older in-flight response from repopulating the cache", async () => {
  const originalFetch = globalThis.fetch;
  let releaseFirst: ((response: Response) => void) | undefined;
  const firstResponse = new Promise<Response>((resolve) => { releaseFirst = resolve; });
  const requests: string[] = [];

  globalThis.fetch = async (input) => {
    requests.push(String(input));
    return requests.length === 1 ? firstResponse : Response.json({ version: "after-mutation" });
  };

  try {
    const oldInventory = api.profiles(true);
    api.invalidateProfiles();
    releaseFirst?.(Response.json({ version: "before-mutation" }));
    await oldInventory;

    const currentInventory = await api.profiles();
    assert.equal((currentInventory as unknown as { version: string }).version, "after-mutation");
    assert.deepEqual(requests, ["/api/profiles?refresh=true", "/api/profiles"]);
  } finally {
    globalThis.fetch = originalFetch;
    api.invalidateProfiles();
  }
});
