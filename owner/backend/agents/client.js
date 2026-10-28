const form = document.getElementById("search-form");
const status = document.getElementById("status");
const results = document.getElementById("results");
const button = document.getElementById("search-button");
const indexStatus = document.getElementById("index-status");

function cookie(name) {
  const raw = document.cookie.split("; ").find((part) => part.startsWith(name + "="));
  return raw ? decodeURIComponent(raw.slice(name.length + 1)) : "";
}

function text(tag, value, className = "") {
  const el = document.createElement(tag);
  el.textContent = value == null ? "" : String(value);
  if (className) el.className = className;
  return el;
}

async function checkIndex() {
  try {
    const response = await fetch("/api/v1/agents/status", {credentials: "same-origin"});
    if (!response.ok) throw new Error("Indice Orca non raggiungibile");
    const data = await response.json();
    indexStatus.textContent = data.indexReady
      ? "Indice Orca disponibile · " + Object.entries(data.agents).map(([k,v]) => k + ": " + v).join(" · ")
      : "Indice Orca non pronto";
  } catch {
    indexStatus.textContent = "Indice Orca temporaneamente non disponibile";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  status.textContent = "Ricerca in corso…";
  results.replaceChildren();
  try {
    const response = await fetch("/api/v1/agents/search", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": cookie("__Host-controlcenter_csrf")},
      body: JSON.stringify({host: document.getElementById("host").value,
                            query: document.getElementById("query").value.trim()}),
    });
    if (!response.ok) throw new Error(response.status === 401 ? "Sessione scaduta: torna alla Panoramica." : "Ricerca non disponibile (" + response.status + ").");
    const data = await response.json();
    status.textContent = data.hits.length + " risultati trovati su " + data.host + (data.truncated ? " (altri disponibili)" : "");
    for (const hit of data.hits) {
      const card = document.createElement("article");
      card.append(text("h2", hit.title || "Sessione " + (hit.agent || "")));
      card.append(text("p", [hit.agent, hit.updatedAt, "messaggi: " + (hit.messageCount ?? "?")].filter(Boolean).join(" · "), "meta"));
      if (hit.cwd) card.append(text("p", hit.cwd, "meta"));
      if (hit.snippet) card.append(text("p", hit.snippet, "snippet"));
      results.append(card);
    }
  } catch (error) {
    status.textContent = error.message || "Ricerca non riuscita";
  } finally {
    button.disabled = false;
  }
});
void checkIndex();

const terminalSelect = document.getElementById("terminal-select");
const terminalScreen = document.getElementById("terminal-screen");
const terminalState = document.getElementById("terminal-state");
const terminalForm = document.getElementById("terminal-form");
const terminalInput = document.getElementById("terminal-input");
const terminalSend = document.getElementById("terminal-send");
let refreshing = false;
let screenRequest = 0;
let sending = false;
let writableHandles = new Set();

function updateSendButton() {
  terminalSend.disabled = sending || !writableHandles.has(terminalSelect.value);
}

async function loadLiveTerminals() {
  try {
    const response = await fetch("/api/v1/agents/terminals", {credentials: "same-origin"});
    if (!response.ok) throw new Error("Sessioni live non disponibili (" + response.status + ")");
    const data = await response.json();
    writableHandles = new Set(data.terminals
      .filter((terminal) => terminal.connected && terminal.writable)
      .map((terminal) => terminal.handle));
    const selected = terminalSelect.value;
    terminalSelect.replaceChildren(text("option", "Seleziona una sessione"));
    terminalSelect.firstChild.value = "";
    for (const terminal of data.terminals) {
      const option = text("option", terminal.agent + " · " + terminal.title +
        (terminal.connected ? "" : " (non connessa)"));
      option.value = terminal.handle;
      option.disabled = !terminal.connected;
      terminalSelect.append(option);
    }
    terminalSelect.value = data.terminals.some((entry) => entry.handle === selected && entry.connected)
      ? selected : "";
    updateSendButton();
    if (!terminalSelect.value && !sending) {
      terminalState.textContent = data.terminals.length + " sessioni originali su MiniPC";
    }
    if (!terminalSelect.value) terminalScreen.textContent = "Seleziona una sessione.";
  } catch (error) {
    terminalState.textContent = error.message || "Sessioni live non disponibili";
    writableHandles.clear();
    terminalSend.disabled = true;
  }
}

async function refreshScreen() {
  if (!terminalSelect.value || refreshing || document.visibilityState !== "visible") return;
  refreshing = true;
  const handle = terminalSelect.value;
  const request = ++screenRequest;
  try {
    const response = await fetch("/api/v1/agents/terminals/" + encodeURIComponent(handle) + "/screen",
      {credentials: "same-origin", cache: "no-store"});
    if (!response.ok) throw new Error("Schermo non disponibile (" + response.status + ")");
    const data = await response.json();
    if (terminalSelect.value !== handle || request !== screenRequest) return;
    const atBottom = terminalScreen.scrollTop + terminalScreen.clientHeight >= terminalScreen.scrollHeight - 30;
    terminalScreen.textContent = data.text || "(nessuna uscita visibile)";
    if (atBottom) terminalScreen.scrollTop = terminalScreen.scrollHeight;
  } catch (error) {
    if (terminalSelect.value === handle) terminalState.textContent = error.message || "Lettura non disponibile";
  } finally {
    refreshing = false;
  }
}

terminalSelect.addEventListener("change", () => {
  screenRequest++;
  updateSendButton();
  terminalScreen.textContent = terminalSelect.value ? "Connessione alla sessione…" : "Seleziona una sessione.";
  void refreshScreen();
});

terminalForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const handle = terminalSelect.value;
  const message = terminalInput.value;
  if (sending || !writableHandles.has(handle) || !message.trim()) return;
  sending = true;
  updateSendButton();
  terminalState.textContent = "Invio al terminale…";
  try {
    const response = await fetch("/api/v1/agents/terminals/" + encodeURIComponent(handle) + "/send", {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": cookie("__Host-controlcenter_csrf")},
      body: JSON.stringify({text: message}),
    });
    if (!response.ok) throw new Error("Invio non confermato (" + response.status + ")");
    const receipt = await response.json();
    if (terminalSelect.value === handle) {
      terminalState.textContent = receipt.submissionObserved
        ? "Messaggio acquisito dalla sessione; verifica la risposta."
        : "Input accettato; verifica nello schermo che il turno sia iniziato.";
      if (terminalInput.value === message) terminalInput.value = "";
      void refreshScreen();
    }
  } catch (error) {
    if (terminalSelect.value === handle) {
      terminalState.textContent = (error.message || "Errore") + ". Controlla lo schermo prima di riprovare.";
    }
  } finally {
    sending = false;
    updateSendButton();
  }
});

void loadLiveTerminals();
window.setInterval(() => {
  if (document.visibilityState === "visible") {
    void loadLiveTerminals();
    void refreshScreen();
  }
}, 4000);

const codexForm = document.getElementById("codex-form");
const codexAccount = document.getElementById("codex-account");
const codexModel = document.getElementById("codex-model");
const codexStart = document.getElementById("codex-start");
const codexState = document.getElementById("codex-state");
const codexSessions = document.getElementById("codex-sessions");
let codexAccounts = [];
let codexCreating = false;
const codexSelected = () => codexAccounts.find(a => a.id === codexAccount.value);
function updateCodexModels() {
  const account = codexSelected();
  codexModel.replaceChildren(text("option", account ? "Seleziona un modello" : "Seleziona un account"));
  codexModel.firstChild.value = "";
  for (const model of account?.models || []) {
    const option = text("option", model);
    option.value = model;
    codexModel.append(option);
  }
  codexStart.disabled = true;
  if (account && !account.models.length) codexState.textContent = "Nessun modello verificato per questo account.";
}
async function loadCodexAccounts() {
  try {
    const r = await fetch("/api/v1/agents/codex/accounts", {credentials: "same-origin"});
    if (!r.ok) throw Error("Inventario Codex non disponibile (" + r.status + ")");
    codexAccounts = (await r.json()).accounts || [];
    codexAccount.replaceChildren(text("option", "Seleziona un account"));
    codexAccount.firstChild.value = "";
    for (const account of codexAccounts) {
      const option = text("option", account.label);
      option.value = account.id;
      codexAccount.append(option);
    }
    codexState.textContent = codexAccounts.length ? "Seleziona l’account e il modello." : "Nessun account disponibile sul MiniPC.";
    updateCodexModels();
  } catch (error) {
    codexState.textContent = error.message || "Inventario non disponibile";
    codexStart.disabled = true;
  }
}
async function refreshCodexSessions() {
  try {
    const r = await fetch("/api/v1/agents/codex/sessions", {credentials: "same-origin"});
    if (!r.ok) return;
    codexSessions.replaceChildren();
    for (const s of (await r.json()).sessions.slice(-10)) {
      const account = codexAccounts.find(a => a.id === s.accountId);
      codexSessions.append(text("p", s.handle + " · " + (account?.label || "Codex") + " · " + s.model));
    }
  } catch { /* Terminali Orca rimangono intatti. */ }
}
codexAccount.addEventListener("change", updateCodexModels);
codexModel.addEventListener("change", () => {
  codexStart.disabled = codexCreating || !codexSelected()?.models.includes(codexModel.value);
});
codexForm.addEventListener("submit", async event => {
  event.preventDefault();
  const account = codexSelected();
  const model = codexModel.value;
  if (codexCreating || !account || !account.models.includes(model)) return;
  codexCreating = true;
  codexStart.disabled = true;
  codexState.textContent = "Apertura terminale Codex…";
  try {
    const r = await fetch("/api/v1/agents/codex/sessions", {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": cookie("__Host-controlcenter_csrf")},
      body: JSON.stringify({accountId: account.id, model, operationId: crypto.randomUUID()}),
    });
    if (!r.ok) throw Error("Apertura non confermata (" + r.status + ")");
    const created = await r.json();
    codexState.textContent = "Terminale aperto: " + account.label + " · " + model;
    await loadLiveTerminals();
    terminalSelect.value = created.handle;
    updateSendButton();
    void refreshScreen();
    void refreshCodexSessions();
  } catch (error) {
    codexState.textContent = (error.message || "Errore") + ". Controlla i terminali prima di riprovare.";
  } finally {
    codexCreating = false;
    codexStart.disabled = !codexSelected()?.models.includes(codexModel.value);
  }
});
void loadCodexAccounts().then(refreshCodexSessions);
