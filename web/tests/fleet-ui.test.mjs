import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Bundle the actual TSX modules for Node SSR; no browser mock, API or host data source.
const directory = mkdtempSync(join(process.cwd(), "node_modules/.fleet-ui-test-"));
after(() => rmSync(directory, { recursive: true, force: true }));
const entryPoints = ["src/views/FleetView.tsx", "src/views/OverviewView.tsx", "src/components/HostDetail.tsx"];
await build({
  entryPoints,
  outdir: directory,
  outbase: "src",
  bundle: true,
  platform: "node",
  format: "esm",
  external: ["react", "react-dom"],
  logLevel: "silent",
});
const { FleetView } = await import(pathToFileURL(join(directory, "views/FleetView.js")).href);
const { OverviewView } = await import(pathToFileURL(join(directory, "views/OverviewView.js")).href);
const { HostDetail, displayedHistory } = await import(pathToFileURL(join(directory, "components/HostDetail.js")).href);

function host(id, overrides = {}) {
  const sample = {
    host_id: id, hostname: id, os: "darwin", os_version: "fixture", arch: "arm64",
    sampled_at: "2026-10-22T08:00:00Z", sample_age_s: 3600,
    uptime_s: 300, agent_version: "2.1.4", schema: 2,
    cpu: { percent: 73, source: "collector" },
    memory: { percent: 80, pressure_used_percent: 66, total_bytes: 16000000000 },
    disk: [{ mount: "/", total_bytes: 100000000000, free_bytes: 30000000000,
      available_bytes: 40000000000, available_stale: true, available_age_s: 420 }],
    gpu: { name: "GPU fixture", utilization_percent: 20, source: "collector", reason: null },
    thermal: { cpu_c: 62, pressure_level: null, reason: null },
    battery: { present: false, percent: null },
    network: { rx_bps: 1000, tx_bps: 500 }, processes: [],
  };
  return {
    id, name: id, subtitle: `${id} runtime`, os: "darwin", role: "host", image: null,
    is_control_plane: id === "minipc", lifecycle: "managed", reachable: true,
    stale: false, error: null, sample, health: {
      severity: "ok", headline: "Regolare", reason: null,
      signals: [
        { key: "cpu", label: "CPU", value: 73, severity: "ok" },
        { key: "memory", label: "Memoria", value: 66, severity: "ok" },
        { key: "disk", label: "Disco", value: 40, severity: "ok" },
        { key: "gpu", label: "GPU", value: 20, severity: "ok" },
        { key: "thermal", label: "Termica", value: 62, severity: "ok" },
      ],
    },
    ...overrides,
  };
}

function fleetMarkup(hosts, connectionOffline = false) {
  const managed = hosts.filter((item) => item.lifecycle === "managed");
  const snapshot = {
    generated_at: 1792690000,
    hosts,
    fleet: { total: hosts.length, managed_total: managed.length,
      reachable: managed.filter((item) => item.reachable).length,
      pending: hosts.length - managed.length, worst_severity: "ok" },
  };
  return renderToStaticMarkup(createElement(FleetView, {
    snapshot, connectionOffline, histories: {}, summary: null, receipts: [],
    onSelectHost: () => {}, onNavigate: () => {},
  }));
}

function overviewMarkup(hosts, connectionOffline = false) {
  const managed = hosts.filter((item) => item.lifecycle === "managed");
  return renderToStaticMarkup(createElement(OverviewView, {
    fleet: { generated_at: 1792690000, hosts, fleet: {
      total: hosts.length, managed_total: managed.length,
      pending: hosts.length - managed.length,
      reachable: managed.filter((item) => item.reachable).length,
      worst_severity: "ok",
    } },
    connectionOffline, summary: null, profiles: null, receipts: [],
    loadErrors: { summary: null, profiles: null, receipts: null },
    onNavigate: () => {}, onHost: () => {}, onRefresh: () => {},
  }));
}

test("Fleet 4/4 managed con Windows pending non dichiara stato Regolare", () => {
  const hosts = [
    host("minipc"), host("mac-studio"), host("macbook-air"), host("matebooklinux"),
    host("matebook", { lifecycle: "pending", reachable: false, sample: null }),
  ];
  const html = fleetMarkup(hosts);
  assert.match(html, /4\/4/);
  assert.match(html, /Sconosciuto/);
  assert.doesNotMatch(html, />Regolare</);
  assert.match(html, /pending on boot/);
  assert.match(html, /matebooklinux/);
  assert.match(html, /matebook/);
  const overview = overviewMarkup(hosts);
  assert.match(overview, /4\/4/);
  assert.match(overview, /pending on boot/);
  assert.match(overview, /FleetProbe 2\.1\.4/);
  assert.match(overview, /campione 22\/10\/2026/);
  assert.match(overview, /bg-signal-unknown/);
});

test("Panoramica e segnali non promuovono la salute vecchia di un host stale", () => {
  const html = overviewMarkup([host("mac-studio", { stale: true })]);
  assert.match(html, /Stale · ultimo campione, non live/);
  assert.doesNotMatch(html, />Regolare</);
  assert.doesNotMatch(html, /Disponibile \(cache\)/);
  assert.doesNotMatch(html, />73%<|>66%<|>40(?:\.0)? GB</);
  assert.match(html, /FleetProbe 2\.1\.4/);
});

test("Fleet non presenta CPU, RAM, APFS, termica, GPU storiche come correnti", () => {
  for (const overrides of [{ stale: true }, { reachable: false }, { lifecycle: "pending", reachable: false }]) {
    const html = fleetMarkup([host("mac-studio", overrides)]);
    assert.doesNotMatch(html, /73%|66%|40(?:\.0)? GB|62\.0°C|GPU 20%/);
    assert.match(html, /non live|nessun campione recente|pending on boot/i);
  }
  const live = fleetMarkup([host("mac-studio")]);
  assert.match(live, /Disponibile \(cache\)/);
  assert.match(live, /APFS 420s fa/);
  assert.match(live, /66%/); // macOS memory pressure, not total used percent 80
  const disconnected = fleetMarkup([host("mac-studio")], true);
  assert.doesNotMatch(disconnected, /73%|66%|40(?:\.0)? GB|62\.0°C|GPU 20%/);
  assert.match(disconnected, /ultimo snapshot/i);
});

test("un host critico misurato mantiene la precedenza sul runtime pending", () => {
  const html = fleetMarkup([
    host("minipc", { health: {
      severity: "crit", headline: "Soglia superata", reason: null, signals: [],
    } }),
    host("matebook", { lifecycle: "pending", sample: null, reachable: false }),
  ]);
  assert.match(html, /Critico/);
  assert.doesNotMatch(html, />Regolare</);
});

test("HostDetail segnala l'intero campione precedente come storico", () => {
  const html = renderToStaticMarkup(createElement(HostDetail, {
    host: host("mac-studio", { stale: true }), connectionOffline: false, receipts: [],
    onClose: () => {}, onReceiptsChanged: () => {},
  }));
  assert.match(html, /ultimo campione/i);
  assert.match(html, /non live/i);
  assert.doesNotMatch(html, />Regolare</);
  assert.match(html, /FleetProbe/);
  assert.match(html, /APFS in cache/);
  const ranges = [...html.matchAll(/>(1 ora|6 ore|24 ore|14 giorni)<\/button>/g)].map((match) => match[1]);
  assert.deepEqual(ranges, ["1 ora", "6 ore", "24 ore", "14 giorni"]);
});

test("HostDetail senza segnale stream dal chiamante resta prudente; il segnale verificato abilita live", () => {
  const props = { host: host("mac-studio"), receipts: [], onClose: () => {}, onReceiptsChanged: () => {} };
  const unwired = renderToStaticMarkup(createElement(HostDetail, props));
  assert.match(unwired, /Connessione stream non verificata/);
  assert.doesNotMatch(unwired, />Regolare</);
  const live = renderToStaticMarkup(createElement(HostDetail, { ...props, connectionOffline: false }));
  assert.match(live, />Regolare</);
  assert.doesNotMatch(live, /Ultimo campione disponibile/);
});

test("storico 1h → 14 giorni: richiesta in corso nasconde serie vecchia e ignorati esiti fuori ordine", () => {
  const previous = { key: "minipc:60:0", points: [{ t: 1, cpu: 71, memory: null }], error: null };
  assert.deepEqual(displayedHistory(previous, "minipc:20160:0"), {
    loading: true, points: [], error: null,
  });
  assert.deepEqual(displayedHistory({ key: "minipc:20160:0", points: [{ t: 2, cpu: null, memory: null }], error: null }, "minipc:20160:0"), {
    loading: false, points: [{ t: 2, cpu: null, memory: null }], error: null,
  });
  assert.deepEqual(displayedHistory(previous, "mac-studio:60:0"), {
    loading: true, points: [], error: null,
  });
});
