import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const directory = mkdtempSync(join(process.cwd(), "node_modules/.services-ports-test-"));
after(() => rmSync(directory, { recursive: true, force: true }));
await build({
  entryPoints: ["src/components/ServicesPorts.tsx"], outdir: directory, bundle: true,
  platform: "node", format: "esm", external: ["react", "react-dom"], logLevel: "silent",
});
const { PortsDirectoryRows, currentPortRows } = await import(pathToFileURL(join(directory, "ServicesPorts.js")).href);

function row(overrides = {}) {
  return {
    host_id: "minipc", host_name: "MiniPC", status: "online", sampled_at: "2026-11-09T13:00:00Z",
    tailnet: "on", protocol: "tcp", port: 8000, bind: "127.0.0.1", service: null,
    process: null, web: false, url: null, links: [], truncated: false, ...overrides,
  };
}

const render = (rows, hostFilter = "all", scopeFilter = "all", stateFilter = "all") =>
  renderToStaticMarkup(createElement(PortsDirectoryRows, { rows, hostFilter, scopeFilter, stateFilter }));

test("generic listener has no web action, local-only port is not offered to remote iPad", () => {
  const html = render([row({ port: 5555, bind: "127.0.0.1" })]);
  assert.match(html, /5555/);
  assert.match(html, /Nessun URL di rete verificato/);
  assert.doesNotMatch(html, /Apri|Copia link|href=/);
});

test("only the verified native Tailscale URL can be opened or copied", () => {
  const html = render([row({ web: true, url: "https://fixture.ts.net:8443",
    links: [{ scope: "tailnet", url: "https://fixture.ts.net:8443" }] })]);
  assert.match(html, /https:\/\/fixture\.ts\.net:8443/);
  assert.match(html, /Apri/);
  assert.match(html, /Copia link/);
  assert.match(html, /noopener noreferrer/);
});

test("host and tailnet filters do not promote offline or unknown entries", () => {
  const rows = [row({ port: 5555 }), row({ host_id: "air", host_name: "Air", status: "offline", port: null })];
  assert.doesNotMatch(render(rows, "air"), /5555/);
  assert.match(render(rows, "air"), /Offline/);
  assert.match(render(rows, "all", "tailnet"), /Nessuna porta/);
  assert.doesNotMatch(render(rows, "all", "tailnet"), /Copia link/);
});

test("host becoming stale or offline immediately disables a formerly verified URL", () => {
  const url = "https://fixture.ts.net:8443";
  const fresh = row({ web: true, url, sampled_at: new Date().toISOString(), links: [{ scope: "tailnet", url }] });
  const host = { id: "minipc", lifecycle: "managed", reachable: true, stale: false };
  assert.match(render(currentPortRows([fresh], [host])), /Copia link/);
  for (const changed of [{ ...host, reachable: false }, { ...host, stale: true }]) {
    const html = render(currentPortRows([fresh], [changed]));
    assert.doesNotMatch(html, /Copia link|href=/);
  }
  const old = { ...fresh, sampled_at: "2020-01-01T00:00:00Z" };
  assert.doesNotMatch(render(currentPortRows([old], [host])), /Copia link|href=/);
});

test("HTTP-local and LAN addresses show their scope without an iPad navigation action", () => {
  const html = render([row({ web: true, url: null, links: [{ scope: "server-local", url: "http://127.0.0.1:8000" }] })]);
  assert.match(html, /Solo sul server/);
  assert.doesNotMatch(html, /Copia link|href=/);
  assert.match(render([row({ truncated: true })]), /Inventario parziale/);
});

test("verified LAN listener exposes a contextual link but does not promise iPad reachability", () => {
  const html = render([row({ bind: "192.168.1.8", port: 6768, web: true,
    links: [{ scope: "lan", url: "http://192.168.1.8:6768" }] })]);
  assert.match(html, /Apri LAN/);
  assert.match(html, /Copia LAN/);
  assert.match(html, /accessibilità da iPad non verificata/);
  assert.match(html, /href="http:\/\/192\.168\.1\.8:6768\/"/);
});

test("LAN link must match the native bind and port; forged URLs are not navigable", () => {
  const html = render([row({ bind: "192.168.1.8", port: 6768, web: true, links: [
    { scope: "lan", url: "https://untrusted.example:6768" },
    { scope: "lan", url: "http://192.168.1.8:1234" },
    { scope: "lan", url: "http://192.168.1.8:6768/?token=private" },
  ] })]);
  assert.doesNotMatch(html, /href=|Copia LAN|Apri LAN/);
  assert.doesNotMatch(render([row({ bind: "203.0.113.8", port: 6768, web: true,
    links: [{ scope: "lan", url: "http://203.0.113.8:6768" }] })]), /href=|Copia LAN|Apri LAN/);
});

test("all hosts remain visible, including the missing inventory on a fourth host", () => {
  const hosts = [
    { id: "studio", name: "Studio" }, { id: "minipc", name: "MiniPC" },
    { id: "air", name: "Air" }, { id: "matebook", name: "MateBook" },
  ];
  const html = renderToStaticMarkup(createElement(PortsDirectoryRows, {
    rows: [row({ port: 8000, service: "Plancia" })], hosts,
    hostFilter: "all", scopeFilter: "all", stateFilter: "all",
  }));
  assert.match(html, /Plancia/);
  for (const host of hosts) assert.match(html, new RegExp(`aria-label="${host.name}"`));
  assert.match(html, /Inventario non disponibile per questo host/);
});
