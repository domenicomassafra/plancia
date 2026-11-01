import assert from "node:assert/strict";
import test from "node:test";
import { hermesDashboardUrl, loadHermesDashboardConfig } from "../src/lib/hermes-dashboard.ts";

const PLANCIA = { protocol: "https:", hostname: "minipc.example.ts.net", port: "8443" };
const HERMES = "https://hermes-service.example.ts.net";

test("dashboard config preserves unavailable versus unconfigured states", async () => {
  assert.deepEqual(await loadHermesDashboardConfig(async () => ({ origin: null })),
    { kind: "unconfigured", origin: null });
  assert.deepEqual(await loadHermesDashboardConfig(async () => ({ origin: HERMES })),
    { kind: "ready", origin: HERMES });
  assert.deepEqual(await loadHermesDashboardConfig(async () => { throw new Error("401 session required"); }),
    { kind: "error", origin: null });
  assert.deepEqual(await loadHermesDashboardConfig(async () => ({ origin: undefined as unknown as null })),
    { kind: "error", origin: null });
});

test("Hermes app requires an explicit HTTPS service with a different hostname", () => {
  assert.equal(hermesDashboardUrl(PLANCIA, HERMES), HERMES + "/chat");
  assert.equal(hermesDashboardUrl(PLANCIA, null), null);
  assert.equal(hermesDashboardUrl(PLANCIA, "https://minipc.example.ts.net:19443"), null);
  assert.equal(hermesDashboardUrl(PLANCIA, "https://minipc.example.ts.net"), null);
});

test("no embed or link for unsafe sources, URL tricks, or non-tailnet Plancia", () => {
  for (const origin of [
    "http://hermes-service.example.ts.net",
    "https://hermes-service.example.ts.net:19443",
    "https://hermes-service.example.ts.net/chat",
    "https://hermes-service.example.ts.net@attacker.example.com",
    "https://hermes-service.example.ts.net.evil.example",
    "https://hermes-service.example.ts.net?secret=yes",
    "https://hermes-service.example.ts.net/#chat",
  ]) assert.equal(hermesDashboardUrl(PLANCIA, origin), null, origin);
  for (const location of [
    { protocol: "http:", hostname: PLANCIA.hostname, port: "8443" },
    { protocol: "https:", hostname: "other.example.com", port: "8443" },
    { protocol: "https:", hostname: PLANCIA.hostname, port: "9443" },
  ]) assert.equal(hermesDashboardUrl(location, HERMES), null);
});
