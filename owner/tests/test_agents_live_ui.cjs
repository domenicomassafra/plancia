// Regression of the live send form without browser packages or real agent input.
const assert = require("node:assert/strict");
const {test} = require("node:test");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(
  process.argv[2] || path.join(__dirname, "../backend/agents/client.js"), "utf8");

function element(tag = "div") {
  return {
    tag, value: "", textContent: "", disabled: false, options: [], handlers: {},
    scrollTop: 0, clientHeight: 100, scrollHeight: 100,
    addEventListener(event, cb) { this.handlers[event] = cb; },
    append(child) { this.options.push(child); },
    replaceChildren(...children) { this.options = [...children]; this.value = ""; },
    get firstChild() { return this.options[0]; },
  };
}

async function setup() {
  const ids = {};
  for (const name of ["search-form", "status", "results", "search-button",
    "index-status", "terminal-select", "terminal-screen", "terminal-state",
    "terminal-form", "terminal-input", "terminal-send"]) ids[name] = element();
  const page = {
    cookie: "__Host-controlcenter_csrf=test",
    visibilityState: "visible",
    getElementById(name) { return ids[name] ||= element(); },
    createElement(tag) { return element(tag); },
  };
  let inventory = [];
  const calls = [];
  let releaseSend;
  let activeSend = false;
  async function fetch(url, options = {}) {
    if (url === "/api/v1/agents/status") return {
      ok: true, json: async () => ({indexReady: true, agents: {}}),
    };
    if (url === "/api/v1/agents/terminals") return {
      ok: true, json: async () => ({terminals: inventory}),
    };
    if (url.endsWith("/screen")) return {
      ok: true, json: async () => ({text: "mock screen", status: "running"}),
    };
    if (url.endsWith("/send")) {
      calls.push({url, options});
      activeSend = true;
      await new Promise(resolve => { releaseSend = resolve; });
      activeSend = false;
      return {ok: true, json: async () => ({accepted: true, submissionObserved: false})};
    }
    throw Error("Unexpected endpoint: " + url);
  }
  const ctx = vm.createContext({document: page, window: {setInterval(){}}, fetch,
    encodeURIComponent, console});
  vm.runInContext(source, ctx, {filename: "agents/client.js"});
  async function tick() {
    for (let i = 0; i < 7; i++) await Promise.resolve();
  }
  async function refresh() { await vm.runInContext("loadLiveTerminals()", ctx); await tick(); }
  async function select(handle) {
    ids["terminal-select"].value = handle;
    ids["terminal-select"].handlers.change();
    await tick();
  }
  await tick();
  return {
    ids, calls, tick, refresh, select,
    setInventory(entries) { inventory = entries; },
    release() { assert.ok(activeSend, "expected pending request"); releaseSend(); },
    submit() { return ids["terminal-form"].handlers.submit({preventDefault(){}}); },
  };
}

const a = {handle: "term_A", title: "A", agent: "opencode",
  connected: true, writable: true};
const b = {handle: "term_B", title: "B", agent: "opencode",
  connected: true, writable: true};

test("read-only terminal stays selectable but cannot be sent to", async () => {
  const x = await setup();
  x.setInventory([{...a, writable: false}]);
  await x.refresh();
  await x.select(a.handle);
  assert.equal(x.ids["terminal-send"].disabled, true);
});

test("polling and selection cannot duplicate a pending send or erase another draft", async () => {
  const x = await setup();
  x.setInventory([a, b]);
  await x.refresh();
  await x.select(a.handle);
  x.ids["terminal-input"].value = "original message";
  const first = x.submit();
  await x.tick();
  assert.equal(x.calls.length, 1);
  assert.equal(x.ids["terminal-send"].disabled, true);
  await x.refresh(); // simulate four-second inventory poll while POST is pending
  assert.equal(x.ids["terminal-send"].disabled, true);
  x.ids["terminal-input"].value = "accidental duplicate";
  const duplicate = x.submit();
  await x.tick();
  assert.equal(x.calls.length, 1, "only one request while first is pending");
  await x.select(b.handle);
  x.ids["terminal-input"].value = "draft for B";
  assert.equal(x.ids["terminal-send"].disabled, true);
  x.release();
  await first;
  await duplicate;
  await x.tick();
  assert.equal(x.ids["terminal-input"].value, "draft for B");
  assert.equal(x.calls.length, 1);
  assert.equal(x.ids["terminal-send"].disabled, false);
  assert.notEqual(x.ids["terminal-state"].textContent,
    "Input accettato; verifica nello schermo che il turno sia iniziato.");
});
