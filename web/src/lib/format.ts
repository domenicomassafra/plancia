import type { Receipt, Severity } from "./types";

export const SEVERITY_TEXT: Record<Severity, string> = {
  ok: "text-signal-ok",
  warn: "text-signal-warn",
  crit: "text-signal-crit",
  unknown: "text-signal-unknown",
};

export const SEVERITY_BG: Record<Severity, string> = {
  ok: "bg-signal-ok",
  warn: "bg-signal-warn",
  crit: "bg-signal-crit",
  unknown: "bg-signal-unknown",
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  ok: "Regolare",
  warn: "Attenzione",
  crit: "Critico",
  unknown: "Sconosciuto",
};

export function exactTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("it-IT", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

const RECEIPT_ACTION_LABELS: Record<string, string> = {
  reboot: "Riavvio computer",
  "reboot-preview": "Anteprima riavvio computer",
  shutdown: "Spegnimento computer",
  "shutdown-preview": "Anteprima spegnimento computer",
  "restart-agent": "Riavvio agente",
  "restart-agent-preview": "Anteprima riavvio agente",
  "profile-delete": "Profilo eliminato",
  "profile-metadata": "Profilo aggiornato",
  "notion-edit-preview": "Anteprima Actions",
  "notion-edit-apply": "Modifica Actions",
  "daily-os-preview": "Anteprima Diario quotidiano · fonte ritirata",
  "daily-os-apply": "Modifica Diario quotidiano · fonte ritirata",
  "daily-checkin-preview": "Anteprima Check-in · fonte ritirata",
  "daily-close-preview": "Anteprima Chiusura · fonte ritirata",
  "daily.checkin": "Check-in quotidiano · fonte ritirata",
  "daily.close": "Chiusura quotidiana · fonte ritirata",
};

const RECEIPT_OUTCOME_LABELS: Record<string, string> = {
  succeeded: "Completata",
  dispatched: "Inviata",
  running: "In corso",
  interrupted: "Interrotta",
  failed: "Fallita",
  "dry-run": "Solo anteprima",
  "manual-gate": "Conferma richiesta",
  "rolled-back": "Ripristinata",
};

export function receiptActionLabel(action: string): string {
  return RECEIPT_ACTION_LABELS[action] ?? action;
}

export function receiptOutcomeLabel(outcome: string): string {
  return RECEIPT_OUTCOME_LABELS[outcome] ?? outcome;
}

export interface ReceiptEvidence {
  label: string;
  value: string;
}

function receiptPayloadText(receipt: Receipt, key: string): string | null {
  const value = receipt.payload[key];
  if (typeof value !== "string" && typeof value !== "number") return null;
  const text = String(value).trim();
  return text ? text.slice(0, 200) : null;
}

/** Project audit identifiers only; never expose preimages, journals or arbitrary payloads. */
export function receiptEvidence(receipt: Receipt): ReceiptEvidence[] {
  const evidence: ReceiptEvidence[] = [];
  const source = receiptPayloadText(receipt, "source_key");
  if (source === "actions") evidence.push({ label: "Fonte", value: "Notion · Actions" });
  if (source === "daily") evidence.push({ label: "Fonte", value: "DB Daily Log · ritirata" });

  const fields = [
    ["registry_id", "Registry applicazione"],
    ["rollback_registry_id", "Registry ripristino"],
    ["receipt_sha", "Digest ricevuta"],
    ["request_digest", "Digest richiesta"],
    ["packet_id", "Packet"],
    ["operation_id", "Operazione"],
  ] as const;
  fields.forEach(([key, label]) => {
    const value = receiptPayloadText(receipt, key);
    if (value) evidence.push({ label, value });
  });

  if (/drift|conflitt|preimage|non integra|cambiat[ao] dopo/i.test(receipt.detail ?? "")) {
    evidence.push({ label: "Verifica", value: "Conflitto o drift rilevato" });
  }
  return evidence;
}

/** A metric that was never measured must not read as a healthy zero. */
export function metric(value: number | null | undefined, unit = "", digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)}${unit}`;
}

export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  let current = value;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current.toFixed(current >= 100 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function bitrate(bytesPerSecond: number | null | undefined): string {
  if (bytesPerSecond === null || bytesPerSecond === undefined) return "—";
  return `${bytes(bytesPerSecond)}/s`;
}

export function uptime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}g ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function relativeTime(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "—";
  const delta = Date.now() / 1000 - epochSeconds;
  if (delta < 60) return `${Math.max(0, Math.round(delta))}s fa`;
  if (delta < 3600) return `${Math.round(delta / 60)}m fa`;
  if (delta < 86400) return `${Math.round(delta / 3600)}h fa`;
  return `${Math.round(delta / 86400)}g fa`;
}

export function dueLabel(due: string | null): string {
  if (!due) return "senza data";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(due);
  const date = match
    ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
    : new Date(due);
  if (Number.isNaN(date.getTime())) return "data non valida";
  const today = new Date();
  const asDay = (value: Date) =>
    Date.UTC(value.getFullYear(), value.getMonth(), value.getDate()) / 86400000;
  const delta = asDay(date) - asDay(today);
  if (delta === 0) return "oggi";
  if (delta === -1) return "ieri";
  if (delta < 0) return `${Math.abs(delta)} giorni fa`;
  if (delta === 1) return "domani";
  return date.toLocaleDateString("it-IT", { day: "numeric", month: "short" });
}

export function osLabel(os: string): string {
  if (os === "darwin") return "macOS";
  if (os === "linux") return "Linux";
  if (os === "windows") return "Windows";
  return os;
}

/** Keep transport details in receipts/logs, not in the operator's main view. */
export function hostConnectivityMessage(error: string | null | undefined): string {
  const value = (error ?? "").toLowerCase();
  if (!value) return "Nessun campione recente.";
  if (value.includes("connecttimeout") || value.includes("timed out")) {
    return "Il computer non risponde al momento. Verifica che sia acceso e connesso a Tailscale.";
  }
  if (value.includes("connecterror") || value.includes("connection refused")) {
    return "L'agente non è disponibile. Verifica che FleetProbe sia attivo sul computer.";
  }
  if (value.includes("401") || value.includes("rejected")) {
    return "L'agente ha rifiutato il collegamento. Controlla la configurazione di FleetProbe.";
  }
  return "Nessun campione recente. Verifica la connessione e l'agente del computer.";
}

/** Turn backend/source diagnostics into bounded operator copy without leaking implementation noise. */
export function sourceReason(reason: string | null | undefined): string {
  const raw = (reason ?? "").trim();
  if (!raw) return "Fonte non disponibile.";
  const value = raw.toLowerCase();
  if (value.includes("object_not_found") || value.includes("could not find data_source")) {
    return "La fonte configurata non è disponibile nel workspace corrente.";
  }
  if (value.includes("non-primary workspace")) {
    return "La sessione Notion non corrisponde al workspace primario.";
  }
  if (value.includes("notion cli not found")) {
    return "Il connettore Notion non è installato sull'host owner.";
  }
  if (value.includes("retired") || value.includes("ritirat")) {
    return "Fonte ritirata dal workspace attivo.";
  }
  return raw.replace(/^(RuntimeError|ValueError|FileNotFoundError):\s*/i, "").slice(0, 180);
}

export function sampleAgeLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "mai";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s fa`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m fa`;
  return `${Math.round(seconds / 3600)}h fa`;
}

export function measurementLabel(
  value: number | null | undefined,
  reason: string | null | undefined,
): string {
  if (value !== null && value !== undefined && Number.isFinite(value)) return "";
  const text = (reason ?? "").toLowerCase();
  if (text.includes("powermetrics") || text.includes("thermalzone") || text.includes("sensor")) {
    return "sensore non disponibile";
  }
  return "non misurato";
}

export function thermalLabel(
  value: number | null | undefined,
  pressure: string | null | undefined,
  reason: string | null | undefined,
): string {
  if (value !== null && value !== undefined) return `${value.toFixed(1)}°C`;
  if (pressure) {
    const normalized = pressure.toLowerCase();
    if (normalized === "nominal") return "pressione normale";
    if (normalized === "moderate") return "pressione moderata";
    if (normalized === "heavy") return "pressione alta";
    if (normalized === "trapping") return "limitazione termica";
    return `pressione ${normalized}`;
  }
  return measurementLabel(value, reason);
}
