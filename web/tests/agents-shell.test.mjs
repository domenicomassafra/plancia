import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import "./orca-chat-contract.test.ts";

// Bundle the real shell and domain components without a browser session or backend.
const directory = mkdtempSync(join(process.cwd(), "node_modules/.agents-shell-test-"));
after(() => rmSync(directory, { recursive: true, force: true }));
await build({
  entryPoints: ["src/App.tsx", "src/views/AgentsSection.tsx", "src/views/RemoteDesktopView.tsx", "src/views/GithubView.tsx"],
  outdir: directory,
  outbase: "src",
  bundle: true,
  platform: "node",
  format: "esm",
  external: ["react", "react-dom"],
  loader: { ".svg": "dataurl" },
  logLevel: "silent",
});
const { default: App } = await import(pathToFileURL(join(directory, "App.js")).href);
const { AgentsSection } = await import(pathToFileURL(join(directory, "views/AgentsSection.js")).href);
const { RemoteDesktopView } = await import(pathToFileURL(join(directory, "views/RemoteDesktopView.js")).href);
const { GithubView } = await import(pathToFileURL(join(directory, "views/GithubView.js")).href);

test("Agenti integra chat Hermes/Orca e Desktop senza navigazione globale duplicata", () => {
  const html = renderToStaticMarkup(createElement(AgentsSection));
  assert.doesNotMatch(html, /<nav\b/);
  assert.match(html, /aria-label="Sezioni Agenti"/);
  assert.match(html, /aria-pressed="true"[^>]*>App Hermes<\/button>/);
  assert.match(html, /aria-pressed="false"[^>]*>Cronologia redatta<\/button>/);
  assert.match(html, /aria-pressed="false"[^>]*>Pi · Orca<\/button>/);
  assert.match(html, /aria-pressed="false"[^>]*>Desktop<\/button>/);
  assert.match(html, /role="region" aria-label="App Hermes"/);
  assert.match(html, /<section class="space-y-4" aria-label="App Hermes ufficiale">/);
  assert.match(html, /<section hidden="" aria-label="Sessioni Hermes">/);
  assert.match(html, /<section hidden="" aria-label="Cronologia Pi e Orca">/);
  assert.match(html, /<section hidden="" aria-label="Desktop autorizzati">/);
  assert.match(html, /Chat Hermes/);
  assert.match(html, /Verifica disponibilità/,
    "initial render must wait for the API before claiming the service is unconfigured");
  assert.doesNotMatch(html, /<iframe\b/, "no Hermes iframe without isolated service configuration");
  assert.doesNotMatch(html, /Desktop remoto · Apache Guacamole/,
    "unvisited Desktop must not mount its adapter");
  assert.doesNotMatch(html, /href="\/agents"/, "Orca history stays embedded in Plancia");
});

test("il caricamento Desktop resta prudente senza aprire una connessione", () => {
  const desktop = renderToStaticMarkup(createElement(RemoteDesktopView));
  assert.match(desktop, /Verifica del servizio remoto in corso/);
  assert.doesNotMatch(desktop, /<iframe|<video|<canvas/i);
});

test("GitHub mostra i filtri e richiede un dettaglio selezionato prima delle azioni", () => {
  const html = renderToStaticMarkup(createElement(GithubView));
  assert.match(html, /aria-label="GitHub"/);
  assert.match(html, /Tutti i repository/);
  assert.match(html, /Pull request/);
  assert.match(html, /Seleziona un elemento per leggere dettagli/);
  assert.doesNotMatch(html, /Pubblica commento|Squash merge · head verificato/);
  assert.doesNotMatch(html, /GH_PROJECT_TOKEN|GH_TOKEN|localStorage/);
});

test("App rende le chat nella voce Agenti e GitHub nella navigazione globale", () => {
  globalThis.window = { location: { hash: "#agents" } };
  const html = renderToStaticMarkup(createElement(App));
  const navs = [...html.matchAll(/<nav\b[^>]*aria-label="Navigazione principale"[^>]*>(.*?)<\/nav>/gs)];
  assert.equal(navs.length, 2, "sidebar e dock mobile");
  for (const [, content] of navs) {
    assert.equal((content.match(/<button\b/g) ?? []).length, 7);
    assert.match(content, /GitHub/);
    assert.doesNotMatch(content, /Desktop/);
  }
  assert.match(html, /aria-label="Sezioni Agenti"/);
  assert.match(html, /Chat Hermes/);
  assert.match(html, /Pi · Orca/);
  assert.match(html, /aria-pressed="false"[^>]*>Desktop<\/button>/);
});

test("la navigazione globale disattiva i consumer remoti delle sottosezioni Agenti", () => {
  const app = readFileSync("src/App.tsx", "utf8");
  const section = readFileSync("src/views/AgentsSection.tsx", "utf8");
  assert.match(app, /<AgentsSection\s+active=\{view === "agents"\}\s*\/>/,
    "a hidden Agenti section must stop its remote consumers");
  assert.match(section, /<OrcaChatPanel\s+active=\{active && subView === "orca"\}\s*\/>/,
    "Orca 2.5s polling must stop whenever its global section is hidden");
  assert.match(section, /<RemoteDesktopView\s+active=\{active && subView === "desktop"\}\s*\/>/,
    "Guacamole must disconnect when leaving the global Agenti section");
});

test("HostDetail riceve lo stato offline dalla connessione reale della shell", () => {
  const source = ts.createSourceFile("App.tsx", readFileSync("src/App.tsx", "utf8"),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const hostDetails = [];
  function scan(node) {
    if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(source) === "HostDetail") {
      hostDetails.push(node);
    }
    ts.forEachChild(node, scan);
  }
  scan(source);
  assert.equal(hostDetails.length, 1, "the shell has a single modal host detail");
  const prop = hostDetails[0].attributes.properties.find(
    (attribute) => ts.isJsxAttribute(attribute) && attribute.name.getText(source) === "connectionOffline",
  );
  assert.ok(prop && ts.isJsxExpression(prop.initializer));
  assert.equal(prop.initializer.expression.getText(source),
    'status === "offline" || status === "connecting"');
});
