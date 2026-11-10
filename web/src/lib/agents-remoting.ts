/** Browser-only adapters for owner-authenticated, same-origin ControlCenter APIs.
 * Nothing in this module starts Hermes, opens a remote desktop, or stores tokens.
 */

export type AdapterFailure =
  | "unavailable" | "unauthenticated" | "permission_denied" | "offline"
  | "invalid_contract" | "invalid_identifier";

export class AdapterError extends Error {
  readonly kind: AdapterFailure;
  constructor(kind: AdapterFailure) {
    super(kind);
    this.kind = kind;
    this.name = "AdapterError";
  }
}

export type HermesCapability = {
  readable: boolean;
  attachable: boolean;
  sendable: boolean;
};

export type HermesProfile = {
  id: string;
  name: string;
  host: string;
  gateway: "running" | "stopped" | "unknown";
  version: string | null;
  capabilities: HermesCapability;
};

export type HermesSession = {
  id: string;
  title: string | null;
  updated_at: string | null;
};

export type HermesMessage = {
  role: "user" | "assistant" | "system" | "tool";
  text: string;
  timestamp: string | null;
};

/** Admission stays bound to the native Hermes gateway and original session. */
export type HermesLease = {
  profileId: string;
  sessionId: string;
  instanceId: string;
  authorityEpoch: number;
};

export type HermesAdmission = HermesLease & {
  admissionId: string;
  status: "queued" | "started" | "unknown" | "terminal";
};

export type HermesPhoto = { mime: "image/png" | "image/jpeg" | "image/gif" | "image/webp"; data_url: string };

/** Supply the same-origin `request` from api.ts to include session and CSRF. */
export type HermesWriteRequester = <T>(path: string, init?: RequestInit) => Promise<T>;

export type RemoteDesktop = {
  id: string;
  name: string;
  protocol: "rdp" | "vnc";
  state: "unconfigured" | "permission_denied" | "offline" | "ready";
  capabilities: { view: boolean; input: boolean; clipboard: boolean };
};

type FetchLike = typeof fetch;
type JsonObject = Record<string, unknown>;

const MAX_RESPONSE_BYTES = 262_144;
const MAX_PROFILES = 24;
const MAX_SESSIONS = 20;
const MAX_MESSAGES = 50;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const NONCE = /^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/;
const MAX_MEDIA_DATA_URL = 4 * Math.ceil((4 * 1024 * 1024) / 3) + 128;

function invalid(): never {
  throw new AdapterError("invalid_contract");
}

function object(value: unknown): JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject : invalid();
}

function identifier(value: unknown): string {
  return typeof value === "string" && IDENTIFIER.test(value) ? value : invalid();
}

function safeId(value: string): string {
  if (!IDENTIFIER.test(value)) throw new AdapterError("invalid_identifier");
  return encodeURIComponent(value);
}

function label(value: unknown, max = 120): string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max
    ? value : invalid();
}

function nullableText(value: unknown, max = 120): string | null {
  return value === null || value === undefined ? null
    : typeof value === "string" && value.length <= max ? value : invalid();
}

function records(value: unknown, limit: number): JsonObject[] {
  if (!Array.isArray(value) || value.length > limit) return invalid();
  return value.map(object);
}

function source(value: JsonObject, expected: string): void {
  if (value.source !== expected) invalid();
}

function grants(raw: unknown): HermesCapability {
  const obj = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as JsonObject : {};
  return {
    readable: obj.readable === true,
    attachable: obj.attachable === true,
    sendable: obj.sendable === true,
  };
}

async function read(path: string, fetcher: FetchLike, signal?: AbortSignal): Promise<JsonObject> {
  let result: Response;
  try {
    result = await fetcher(path, { credentials: "same-origin", cache: "no-store", signal });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new AdapterError("offline");
  }

  if (!result.ok) {
    if (result.status === 401) throw new AdapterError("unauthenticated");
    if (result.status === 403) throw new AdapterError("permission_denied");
    if (result.status === 404 || result.status === 501) throw new AdapterError("unavailable");
    throw new AdapterError("offline");
  }
  try {
    const body = await result.text();
    if (body.length > MAX_RESPONSE_BYTES) invalid();
    return object(JSON.parse(body) as unknown);
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof AdapterError) throw error;
    return invalid();
  }
}

export async function getHermesProfiles(fetcher: FetchLike = fetch, signal?: AbortSignal): Promise<{
  profiles: HermesProfile[];
}> {
  const body = await read("/api/v1/hermes/profiles", fetcher, signal);
  source(body, "hermes-native");
  const profiles = records(body.profiles, MAX_PROFILES).map((profile): HermesProfile => ({
    id: identifier(profile.id),
    name: label(profile.name),
    host: label(profile.host),
    gateway: profile.gateway === "running" || profile.gateway === "stopped"
      ? profile.gateway : "unknown",
    version: nullableText(profile.version),
    capabilities: grants(profile.capabilities),
  }));
  if (new Set(profiles.map((profile) => profile.id)).size !== profiles.length) invalid();
  return { profiles };
}

export async function getHermesSessions(
  profileId: string, fetcher: FetchLike = fetch, signal?: AbortSignal,
): Promise<{ sessions: HermesSession[] }> {
  const id = safeId(profileId);
  const body = await read(`/api/v1/hermes/profiles/${id}/sessions?limit=20`, fetcher, signal);
  source(body, "hermes-native");
  if (body.profile_id !== profileId) invalid();
  const sessions = records(body.sessions, MAX_SESSIONS).map((item): HermesSession => ({
    id: identifier(item.id),
    title: nullableText(item.title, 200),
    updated_at: nullableText(item.updated_at, 60),
  }));
  if (new Set(sessions.map((session) => session.id)).size !== sessions.length) invalid();
  return { sessions };
}

export async function getHermesMessages(
  profileId: string, sessionId: string, fetcher: FetchLike = fetch, signal?: AbortSignal,
): Promise<{ messages: HermesMessage[] }> {
  const path = `/api/v1/hermes/profiles/${safeId(profileId)}/sessions/${safeId(sessionId)}/messages`;
  const body = await read(path, fetcher, signal);
  source(body, "hermes-native");
  if (body.profile_id !== profileId || body.session_id !== sessionId || body.redacted !== true) invalid();
  const messages = records(body.messages, MAX_MESSAGES).map((item): HermesMessage => ({
    role: item.role === "user" || item.role === "assistant" || item.role === "system" || item.role === "tool"
      ? item.role : invalid(),
    text: typeof item.text === "string" && item.text.length <= 8_192 ? item.text : invalid(),
    timestamp: nullableText(item.timestamp, 60),
  }));
  return { messages };
}

function gatewayEpoch(value: unknown): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
    ? value : invalid();
}

function nativeLease(raw: unknown, profileId: string, sessionId: string): HermesLease {
  const body = object(raw);
  source(body, "hermes-native");
  if (body.profile_id !== profileId || body.session_id !== sessionId) invalid();
  return {
    profileId, sessionId,
    instanceId: identifier(body.instance_id),
    authorityEpoch: gatewayEpoch(body.authority_epoch),
  };
}

/** Never attach unless the owner backend explicitly advertises the native grant. */
export async function attachHermesSession(
  profile: HermesProfile, session: HermesSession, requester: HermesWriteRequester,
): Promise<HermesLease> {
  if (profile.gateway !== "running" || profile.capabilities.attachable !== true) {
    throw new AdapterError("permission_denied");
  }
  const path = `/api/v1/hermes/profiles/${safeId(profile.id)}/sessions/${safeId(session.id)}/attach`;
  return nativeLease(await requester<unknown>(path, { method: "POST" }), profile.id, session.id);
}

/** Caller owns the nonce. Retry an uncertain send only with the identical nonce and text. */
export async function sendHermesPrompt(
  lease: HermesLease, capabilities: HermesCapability, nonce: string, text: string,
  requester: HermesWriteRequester, photo?: HermesPhoto,
): Promise<HermesAdmission> {
  if (capabilities.sendable !== true) throw new AdapterError("permission_denied");
  const path = `/api/v1/hermes/profiles/${safeId(lease.profileId)}/sessions/${safeId(lease.sessionId)}/send`;
  const instanceId = safeId(lease.instanceId);
  if (!NONCE.test(nonce)) throw new AdapterError("invalid_identifier");
  if (!Number.isSafeInteger(lease.authorityEpoch) || lease.authorityEpoch <= 0
    || typeof text !== "string" || !text.trim() || text.length > 4096) invalid();
  if (photo && (!/^image\/(png|jpeg|gif|webp)$/.test(photo.mime)
    || !photo.data_url.startsWith(`data:${photo.mime};base64,`)
    || photo.data_url.length > MAX_MEDIA_DATA_URL)) invalid();
  const raw = await requester<unknown>(path, { method: "POST", body: JSON.stringify({
    instance_id: instanceId, authority_epoch: lease.authorityEpoch, nonce, text,
    ...(photo ? { photo } : {}),
  }) });
  const body = object(raw);
  const returned = nativeLease(body, lease.profileId, lease.sessionId);
  if (returned.instanceId !== lease.instanceId || returned.authorityEpoch !== lease.authorityEpoch) invalid();
  const status = body.status;
  if (status !== "queued" && status !== "started" && status !== "unknown" && status !== "terminal") invalid();
  return { ...returned, admissionId: identifier(body.admission_id), status };
}

/** `file.attach` is Hermes' own session-scoped attachment writer. It returns a
 * context reference for the next native prompt, without moving session authority. */
export async function stageHermesFile(
  lease: HermesLease, capabilities: HermesCapability, name: string,
  kind: "file" | "voice", dataUrl: string, requester: HermesWriteRequester,
): Promise<string> {
  if (!capabilities.sendable) throw new AdapterError("permission_denied");
  if (!name || name.length > 120 || /[\/\\\x00-\x1f]/.test(name)
    || !/^data:[a-zA-Z0-9.+-]+\/[a-zA-Z0-9.+-]+;base64,/.test(dataUrl)
    || dataUrl.length > MAX_MEDIA_DATA_URL || (kind === "voice" && !dataUrl.startsWith("data:audio/"))) invalid();
  const path = `/api/v1/hermes/profiles/${safeId(lease.profileId)}/sessions/${safeId(lease.sessionId)}/stage`;
  const raw = await requester<unknown>(path, { method: "POST", body: JSON.stringify({
    instance_id: safeId(lease.instanceId), authority_epoch: lease.authorityEpoch,
    name, kind, data_url: dataUrl,
  }) });
  const result = nativeLease(raw, lease.profileId, lease.sessionId);
  if (result.instanceId !== lease.instanceId || result.authorityEpoch !== lease.authorityEpoch) invalid();
  const ref = object(raw).ref_text;
  if (typeof ref !== "string" || !ref.startsWith("@file:")
    || ref.length > 2048 || /[\r\n\x00]/.test(ref)) invalid();
  return ref;
}

export async function getRemoteDesktops(fetcher: FetchLike = fetch, signal?: AbortSignal): Promise<{
  status: RemoteDesktop["state"];
  desktops: RemoteDesktop[];
}> {
  const body = await read("/api/v1/desktops", fetcher, signal);
  if (body.donor !== "apache-guacamole-1.6.0") invalid();
  const status = body.status === "ready" || body.status === "offline"
    || body.status === "permission_denied" ? body.status : "unconfigured";
  const desktops = records(body.desktops, 12).map((item): RemoteDesktop => {
    const protocol = item.protocol === "rdp" || item.protocol === "vnc" ? item.protocol : invalid();
    const state = status === "ready" && item.state === "ready" ? "ready"
      : item.state === "permission_denied" ? "permission_denied"
        : item.state === "offline" ? "offline" : "unconfigured";
    const raw = item.capabilities && typeof item.capabilities === "object" && !Array.isArray(item.capabilities)
      ? item.capabilities as JsonObject : {};
    const view = state === "ready" && raw.view === true;
    return {
      id: identifier(item.id), name: label(item.name), protocol, state,
      capabilities: { view, input: view && raw.input === true, clipboard: view && raw.clipboard === true },
    };
  });
  if (new Set(desktops.map((desktop) => desktop.id)).size !== desktops.length) invalid();
  return { status, desktops };
}

export function adapterMessage(error: unknown): string {
  const kind = error instanceof AdapterError ? error.kind : "offline";
  switch (kind) {
    case "unauthenticated": return "Sessione owner non autenticata. Accedi e riprova.";
    case "permission_denied": return "Accesso negato dalla policy owner.";
    case "invalid_contract": return "Risposta del backend incompatibile; nessun dato operativo utilizzabile.";
    case "invalid_identifier": return "Identificatore della sessione non valido.";
    case "unavailable": return "Integrazione non disponibile su questo server.";
    default: return "Servizio offline o irraggiungibile.";
  }
}
