import assert from "node:assert/strict";
import test from "node:test";
import { hermesDashboardUrl } from "../src/lib/hermes-dashboard.ts";

test("embed uses the official Hermes app on the same MiniPC tailnet host", () => {
  assert.equal(hermesDashboardUrl({ protocol: "https:", hostname: "minipc.example.ts.net", port: "8443" }),
    "https://minipc.example.ts.net:19443/chat");
});

test("embed does not follow unexpected origins, ports or local development hosts", () => {
  for (const location of [
    { protocol: "https:", hostname: "other.example.com", port: "8443" },
    { protocol: "http:", hostname: "minipc.example.ts.net", port: "8443" },
    { protocol: "https:", hostname: "minipc.example.ts.net", port: "8888" },
    { protocol: "https:", hostname: "attacker.ts.net.evil.example", port: "8443" },
  ]) assert.equal(hermesDashboardUrl(location), null);
});
