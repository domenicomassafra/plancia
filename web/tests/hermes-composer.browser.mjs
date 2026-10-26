import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "vite";

// A same-origin contract fixture exercised in the dedicated Mac Studio automation browser.
// The synthetic session cannot submit anything to an installed Hermes gateway.
test("Hermes browser grants, single submit, error and stable explicit retry", async () => {
  const cdp = process.env.PLANCIA_CDP;
  assert.ok(cdp, "PLANCIA_CDP must point to the dedicated automation browser");
  let grant = false;
  let rejectNextSend = false;
  let nextStatus = null;
  const writes = [];
  const reads = [];
  const lease = { source: "hermes-native", profile_id: "signor-coding",
    session_id: "existing_1", instance_id: "native-instance-001", authority_epoch: 12 };
  const respond = (res, data, status = 200) => {
    res.statusCode = status;
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(data));
  };
  const vite = await createServer({
    server: { host: "127.0.0.1", port: 0 },
    plugins: [{
      name: "synthetic-hermes-browser-fixture",
      resolveId(id) { if (id === "virtual:hermes-test") return "\0virtual:hermes-test"; },
      load(id) {
        if (id !== "\0virtual:hermes-test") return null;
        return `import React from 'react';
          import { createRoot } from 'react-dom/client';
          import { AgentsSignoriView } from '/src/views/AgentsSignoriView.tsx';
          createRoot(document.getElementById('root')).render(React.createElement(AgentsSignoriView));`;
      },
      configureServer(server) {
        server.middlewares.use(async (req, res, next) => {
          if (req.url === "/__hermes-test") {
            res.setHeader("content-type", "text/html");
            return res.end(await server.transformIndexHtml(req.url,
              '<html><body><div id="root"></div><script type="module" src="/@id/__x00__virtual:hermes-test"></script></body></html>'));
          }
          if (!req.url?.startsWith("/api/v1/hermes/")) return next();
          const url = new URL(req.url, "http://fixture");
          if (req.method === "GET") {
            reads.push(url.pathname);
            if (url.pathname.endsWith("/messages")) return respond(res, { source: "hermes-native",
              profile_id: "signor-coding", session_id: "existing_1", redacted: true,
              messages: [{ role: "assistant", text: "Synthetic readback", timestamp: null }] });
            if (url.pathname.endsWith("/sessions")) return respond(res, { source: "hermes-native",
              profile_id: "signor-coding", sessions: [{ id: "existing_1", title: "Fixture", updated_at: null }] });
            return respond(res, { source: "hermes-native", profiles: [{ id: "signor-coding",
              name: "Coding", host: "synthetic", gateway: "running", version: "fixture",
              capabilities: { readable: true, attachable: grant, sendable: grant } }] });
          }
          let raw = "";
          for await (const chunk of req) raw += chunk;
          writes.push({ path: url.pathname, body: raw ? JSON.parse(raw) : null });
          if (!grant) return respond(res, { detail: "Permesso negato" }, 403);
          if (url.pathname.endsWith("/attach")) return respond(res, lease);
          if (rejectNextSend) {
            rejectNextSend = false;
            return respond(res, { detail: "Gateway non disponibile per questa sessione" }, 409);
          }
          const status = nextStatus ?? "queued";
          nextStatus = null;
          return respond(res, { ...lease, admission_id: "admit_fixture_1", status });
        });
      },
    }],
  });
  let target, ws;
  try {
    await vite.listen();
    const url = `http://127.0.0.1:${vite.httpServer.address().port}/__hermes-test`;
    target = await (await fetch(`${cdp}/json/new?${encodeURIComponent(url)}`, { method: "PUT" })).json();
    assert.ok(target.webSocketDebuggerUrl, "Chrome CDP target required");
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    let sequence = 0;
    const pending = new Map();
    ws.onmessage = ({ data }) => {
      const msg = JSON.parse(data);
      if (pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    };
    const call = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      pending.set(id, (msg) => msg.error ? reject(msg.error) : resolve(msg.result));
      ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expression) => {
      const result = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    const wait = async (expression) => {
      for (let i = 0; i < 100; i++) {
        if (await js(expression)) return;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      assert.fail(`Browser condition not met: ${expression}`);
    };
    const click = (label) => js(`[...document.querySelectorAll('button')].find(e=>e.textContent.trim()===${JSON.stringify(label)}).click()`);
    const fill = (value) => js(`(()=>{const e=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
    await call("Runtime.enable");
    await call("Page.enable");
    await call("Page.navigate", { url });
    await wait("document.body.textContent.includes('Conversazioni dei Signori')");
    await wait("[...document.querySelectorAll('button')].some(e=>e.textContent.includes('Coding'))");
    await js("[...document.querySelectorAll('button')].find(e=>e.textContent.includes('Coding')).click()");
    await wait("[...document.querySelectorAll('button')].some(e=>e.textContent.includes('Fixture'))");
    await js("[...document.querySelectorAll('button')].find(e=>e.textContent.includes('Fixture')).click()");
    await wait("document.querySelector('textarea')?.disabled");
    assert.equal(writes.length, 0, "capability false must cause no POST");
    assert.equal(await js("document.querySelector('textarea').placeholder.includes('non ha ancora autorizzato')"), true);

    grant = true;
    await click("Aggiorna");
    await wait("[...document.querySelectorAll('button')].some(e=>e.textContent==='Connetti alla sessione')");
    await click("Connetti alla sessione");
    await wait("document.body.textContent.includes('Gateway native-instance-001')");
    assert.equal(writes.length, 1);
    assert.match(writes[0].path, /existing_1\/attach$/);
    await fill("nonce synthetic");
    await wait("[...document.querySelectorAll('button')].some(e=>e.textContent==='Invia a Hermes'&&!e.disabled)");
    await click("Invia a Hermes");
    await wait("document.body.textContent.includes('registrato')");
    assert.equal(writes.length, 2, "one initial submit, no duplicate POST");
    const first = writes[1];
    assert.match(first.path, /existing_1\/send$/);
    assert.equal(first.body.instance_id, lease.instance_id);
    assert.equal(first.body.authority_epoch, lease.authority_epoch);
    assert.equal(first.body.text, "nonce synthetic");
    assert.match(first.body.nonce, /^[A-Za-z0-9_-]{8,128}$/);

    rejectNextSend = true;
    await fill("second nonce synthetic");
    await wait("[...document.querySelectorAll('button')].some(e=>e.textContent==='Invia a Hermes'&&!e.disabled)");
    await click("Invia a Hermes");
    await wait("document.body.textContent.includes('Esito dell')");
    assert.equal(await js("document.body.textContent.includes('Gateway non disponibile per questa sessione')"), true,
      "the backend error must be readable to the owner");
    const uncertain = writes[2];
    await click("Aggiorna");
    await wait("[...document.querySelectorAll('button')].some(e=>e.textContent==='Connetti alla sessione')");
    assert.equal(writes.length, 3, "refresh may not submit an uncertain nonce");
    await click("Connetti alla sessione");
    await wait("[...document.querySelectorAll('button')].some(e=>e.textContent==='Riprova stesso invio')");
    await click("Riprova stesso invio");
    await wait("document.body.textContent.includes('registrato')");
    assert.deepEqual(writes[4].body, uncertain.body, "retry must preserve SID, text, nonce, epoch");
    assert.ok(reads.some((path) => path.endsWith("/messages")), "readback must use existing SID");
    nextStatus = "unknown";
    await fill("ambiguous receipt synthetic");
    await wait("Array.from(document.querySelectorAll(\"button\")).some(e=>e.textContent===\"Invia a Hermes\"&&!e.disabled)");
    await click("Invia a Hermes");
    await wait("document.body.textContent.includes(\"Esito incerto\")");
    const ambiguous = writes[5];
    await click("Aggiorna");
    await wait("Array.from(document.querySelectorAll(\"button\")).some(e=>e.textContent===\"Connetti alla sessione\")");
    await click("Connetti alla sessione");
    await wait("Array.from(document.querySelectorAll(\"button\")).some(e=>e.textContent===\"Riprova stesso invio\")");
    await click("Riprova stesso invio");
    await wait("document.body.textContent.includes(\"registrato\")");
    assert.deepEqual(writes[7].body, ambiguous.body, "unknown receipt must preserve same nonce and text across reconnect");
    grant = false;
    await click("Aggiorna");
    await wait("document.querySelector('textarea')?.disabled");
    assert.equal(writes.length, 8);
  } finally {
    ws?.close();
    if (target?.id) await fetch(`${process.env.PLANCIA_CDP}/json/close/${target.id}`);
    await vite.close();
  }
});
