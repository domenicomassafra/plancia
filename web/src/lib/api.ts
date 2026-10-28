import type {
  Capabilities,
  ControlCenterSummary,
  FleetSnapshot,
  HistoryPoint,
  OmniRouteStatus,
  ProfileFleet,
  Receipt,
  WorkspaceActionItemOperationPacket,
  WorkspaceActionItemOperationApplyResult,
  WorkspaceNotion,
  WorkspaceNotionRollbackResult,
  WorkspaceObsidian,
  WorkspaceSnapshot,
} from "./types";

function actionItemOperationEnvelope(rowId: string, changes: Record<string, unknown>) {
  return {
    actor: "Domenico", surface: "signal-deck", caller_type: "operator_ui",
    agent_runtime: "", overlay_id: null,
    idempotency_key: `signal-deck:action-item.edit:${rowId}:${crypto.randomUUID()}`,
    provenance: { client: "signal-deck", flow: "action-item.edit", fields: Object.keys(changes).sort() },
  };
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)__Host-controlcenter_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

type AuthFailureBody = {
  detail?: { code?: string; message?: string; retryable?: boolean } | string;
};

function isSessionFailure(body: AuthFailureBody | null): boolean {
  if (!body || typeof body.detail !== "object" || body.detail === null) return false;
  return body.detail.code === "session_required" || body.detail.code === "session_expired";
}

async function bootstrapSession(): Promise<void> {
  const response = await fetch("/api/v1/session", {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Session bootstrap failed: ${response.status}`);
}

export async function request<T>(path: string, init?: RequestInit, retrySession = true): Promise<T> {
  const method = init?.method ?? "GET";
  const headers = new Headers(init?.headers);
  if (method !== "GET") {
    headers.set("Content-Type", "application/json");
    headers.set("X-CSRF-Token", csrfToken());
  }
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    let body: AuthFailureBody | null = null;
    try {
      body = (await response.json()) as AuthFailureBody;
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail?.message) detail = body.detail.message;
    } catch {
      /* keep the status line */
    }
    if (response.status === 401 && retrySession && isSessionFailure(body)) {
      await bootstrapSession();
      return request<T>(path, init, false);
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

const PROFILE_CACHE_TTL_MS = 45_000;
let profileCache: { fleet: ProfileFleet; fetchedAt: number } | null = null;
let profileRequest: Promise<ProfileFleet> | null = null;
let profileRequestRefresh = false;
let profileRevision = 0;
let profileRequestRevision = 0;

function profiles(refresh = false): Promise<ProfileFleet> {
  const now = Date.now();
  if (!refresh && profileCache && now - profileCache.fetchedAt < PROFILE_CACHE_TTL_MS) return Promise.resolve(profileCache.fleet);
  if (profileRequest) {
    if (profileRequestRevision === profileRevision && (!refresh || profileRequestRefresh)) return profileRequest;
    // An ordinary or invalidated in-flight load cannot satisfy a newer refresh.
    return profileRequest.then(() => profiles(refresh), () => profiles(refresh));
  }
  const revision = profileRevision;
  profileRequestRevision = revision;
  profileRequestRefresh = refresh;
  const pending = request<ProfileFleet>(`/api/profiles${refresh ? "?refresh=true" : ""}`)
    .then((fleet) => {
      if (revision === profileRevision) profileCache = { fleet, fetchedAt: Date.now() };
      return fleet;
    })
    .finally(() => {
      if (profileRequest === pending) {
        profileRequest = null;
        profileRequestRefresh = false;
      }
    });
  profileRequest = pending;
  return pending;
}

export const api = {
  fleet: () => request<FleetSnapshot>("/api/fleet"),
  omniroute: () => request<OmniRouteStatus>("/api/omniroute"),
  history: (hostId: string, minutes = 60) =>
    request<{ points: HistoryPoint[] }>(`/api/fleet/${hostId}/history?minutes=${minutes}`),
  capabilities: (hostId: string, refresh = false) =>
    request<Capabilities>(`/api/actions/${hostId}${refresh ? "?refresh=true" : ""}`),
  previewAction: (hostId: string, action: string) =>
    request<{ receipt_id: string; preview: { status: "dry-run"; mutation_count: 0; requires_confirmation: boolean } }>(
      `/api/actions/${hostId}/${action}/preview`, { method: "POST" },
    ),
  dispatch: (hostId: string, action: string, confirmation: string, idempotencyKey: string) =>
    request<{ receipt_id: string; replayed: boolean; outcome?: string }>(`/api/actions/${hostId}/${action}`, {
      method: "POST",
      body: JSON.stringify({ confirmation, idempotency_key: idempotencyKey }),
    }),
  receipts: (limit = 100) => request<{ receipts: Receipt[] }>(`/api/receipts?limit=${Math.max(1, Math.min(200, limit))}`),
  receipt: (receiptId: string) =>
    request<{ receipt: Receipt }>(`/api/receipts/${encodeURIComponent(receiptId)}`),
  profiles,
  invalidateProfiles: () => {
    profileCache = null;
    profileRevision += 1;
  },
  openProfile: (hostId: string, browser: string, email: string, directory: string) =>
    request<{ ok: boolean; error_code?: string; receipt_id?: string }>("/api/profiles/open", {
      method: "POST",
      body: JSON.stringify({ host_id: hostId, browser, email, directory }),
    }),
  deleteProfile: (
    hostId: string,
    browser: string,
    email: string,
    directory: string,
  ) =>
    request<{
      ok: boolean;
      status?: "deleted" | "already_absent";
      deleted_count?: number;
      error_code?: string;
      receipt_id?: string;
      host_results?: Record<string, { ok: boolean; error_code?: string }>;
    }>("/api/profiles/delete", {
      method: "POST",
      body: JSON.stringify({
        host_id: hostId,
        browser,
        email,
        directory,
        confirmed: true,
      }),
    }),
  deleteProfileGroup: (
    browser: string,
    email: string,
    planFingerprint: string,
  ) =>
    request<{
      ok: boolean;
      status?: "deleted" | "already_absent";
      deleted_count?: number;
      error_code?: string;
      receipt_id?: string;
      host_results?: Record<string, { ok: boolean; error_code?: string }>;
    }>("/api/profiles/delete-group", {
      method: "POST",
      body: JSON.stringify({ browser, email, confirmed: true, plan_fingerprint: planFingerprint }),
    }),
  previewProfileGroupDelete: (browser: string, email: string) =>
    request<{
      ok: boolean;
      status?: "ready" | "already_absent";
      error_code?: string;
      plan_fingerprint?: string;
      targets?: {
        host_id: string;
        host_label: string;
        browser: string;
        email: string;
        directory: string;
      }[];
    }>("/api/profiles/delete-group/preview", {
      method: "POST",
      body: JSON.stringify({ browser, email }),
    }),
  updateProfileMetadata: (
    hostId: string,
    browser: string,
    email: string,
    directory: string,
    alias: string,
    category: string,
  ) =>
    request<{
      ok: boolean;
      receipt_id: string;
      alias: string;
      category: string;
    }>("/api/profiles/metadata", {
      method: "PUT",
      body: JSON.stringify({
        host_id: hostId,
        browser,
        email,
        directory,
        alias,
        category,
      }),
    }),
  previewProfileRename: (
    browser: string,
    email: string,
    category: string,
    targetHosts?: string[],
  ) =>
    request<{
      ok: boolean;
      status?: "ready";
      error_code?: string;
      desired_label?: string;
      plan_fingerprint?: string;
      targets?: {
        host_id: string;
        host_label: string;
        browser: string;
        email: string;
        directory: string;
        label: string;
        desired_label: string;
      }[];
      unavailable_hosts?: string[];
    }>("/api/profiles/rename/preview", {
      method: "POST",
      body: JSON.stringify({ browser, email, category, target_hosts: targetHosts }),
    }),
  renameProfile: (browser: string, email: string, category: string, targetHosts?: string[], planFingerprint?: string) =>
    request<{
      ok: boolean;
      error_code?: string;
      desired_label?: string;
      receipt_id?: string;
      plan_fingerprint?: string;
    }>("/api/profiles/rename", {
      method: "POST",
      body: JSON.stringify({ browser, email, category, target_hosts: targetHosts, plan_fingerprint: planFingerprint }),
    }),
  previewProfileNameSync: () =>
    request<{
      ok: boolean;
      status: "converged" | "ready" | "conflict" | "blocked";
      error_code?: string;
      target_count: number;
      plan_fingerprint?: string;
      host_counts?: Record<string, number>;
      browser_counts?: Record<string, number>;
      unavailable_hosts?: string[];
      complete?: boolean;
      conflicts?: { browser?: string; browsers?: string[]; email?: string; reason?: string; categories?: string[] }[];
      targets?: {
        host_id: string;
        host_label: string;
        browser: string;
        email: string;
        directory: string;
        label: string;
        desired_label: string;
      }[];
    }>("/api/profiles/names/preview"),
  syncProfileNames: (planFingerprint: string) =>
    request<{
      ok: boolean;
      status: "synced" | "converged" | "partial_offline" | "partial" | "unproven" | "blocked";
      error_code?: string;
      renamed_count?: number;
      complete?: boolean;
      receipt_id?: string;
      unavailable_hosts?: string[];
      host_results?: Record<string, { ok: boolean; status?: string; renamed_count?: number; error_code?: string }>;
    }>("/api/profiles/names/sync", {
      method: "POST",
      body: JSON.stringify({ confirmed: true, plan_fingerprint: planFingerprint }),
    }),
  summary: (refresh = false) =>
    request<ControlCenterSummary>(`/api/summary${refresh ? "?refresh=true" : ""}`),
  workspace: (refresh = false) =>
    request<WorkspaceSnapshot>(`/api/workspace${refresh ? "?refresh=true" : ""}`),
  workspaceNotion: (cursor = "") =>
    request<WorkspaceNotion>(
      `/api/workspace/notion${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
    ),
  workspaceObsidian: (q = "", folder = "", note = "") =>
    request<WorkspaceObsidian>(
      `/api/workspace/obsidian?q=${encodeURIComponent(q)}&folder=${encodeURIComponent(folder)}&note=${encodeURIComponent(note)}`,
    ),
  workspaceActionItemOperationPreview: (rowId: string, changes: Record<string, unknown>, preimageHash = "") =>
    request<WorkspaceActionItemOperationPacket>("/api/workspace/operations/action-item.edit/preview", {
      method: "POST",
      body: JSON.stringify({ row_id: rowId, changes, preimage_hash: preimageHash, ...actionItemOperationEnvelope(rowId, changes) }),
    }),
  workspaceActionItemOperationApply: (packet: WorkspaceActionItemOperationPacket, confirmation: string) =>
    request<WorkspaceActionItemOperationApplyResult>("/api/workspace/operations/action-item.edit/apply", {
      method: "POST",
      body: JSON.stringify({ row_id: packet.row_id, changes: packet.changes, preimage_hash: packet.preimage_hash, operation_id: packet.operation_id, packet_id: packet.packet_id, idempotency_key: packet.idempotency_key, confirmation, actor: "Domenico", surface: "signal-deck", caller_type: "operator_ui", agent_runtime: "", overlay_id: null }),
    }),
  workspaceActionItemOperationRollback: (packet: WorkspaceActionItemOperationPacket, confirmation: string) =>
    request<WorkspaceNotionRollbackResult>("/api/workspace/operations/action-item.edit/rollback", {
      method: "POST",
      body: JSON.stringify({ operation_id: packet.operation_id, idempotency_key: packet.idempotency_key, confirmation, actor: "Domenico", surface: "signal-deck", caller_type: "operator_ui", agent_runtime: "", overlay_id: null }),
    }),
};

export type StreamStatus = "connecting" | "live" | "polling" | "offline";

export function isTerminalStreamError(message: unknown): boolean {
  if (typeof message !== "object" || message === null) return false;
  const candidate = message as { type?: unknown; retryable?: unknown };
  return candidate.type === "error" && candidate.retryable === false;
}

function streamAuthCode(message: unknown): string {
  if (typeof message !== "object" || message === null) return "";
  const candidate = message as { type?: unknown; code?: unknown };
  return candidate.type === "error" && typeof candidate.code === "string" ? candidate.code : "";
}

/**
 * Live fleet updates over the authenticated WebSocket, with an HTTP polling
 * fallback so the dashboard degrades instead of freezing.
 */
export function subscribeFleet(
  onSnapshot: (snapshot: FleetSnapshot) => void,
  onStatus: (status: StreamStatus) => void,
): () => void {
  let socket: WebSocket | null = null;
  let pollTimer: number | null = null;
  let retryTimer: number | null = null;
  let closed = false;
  let recoveringSession = false;
  let socketOpen = false;
  let attempt = 0;

  const startPolling = () => {
    if (pollTimer !== null) return;
    onStatus("polling");
    const tick = async () => {
      try {
        const snapshot = await api.fleet();
        // The polling reply may outlive an unsubscribe or a WebSocket reconnect.
        if (closed || socketOpen) return;
        onSnapshot(snapshot);
        onStatus("polling");
      } catch {
        if (!closed && !socketOpen) onStatus("offline");
      }
    };
    void tick();
    pollTimer = window.setInterval(tick, 6000);
  };

  const stopPolling = () => {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const connect = () => {
    if (closed) return;
    // Do not paint "connecting" while the polling fallback is already
    // delivering snapshots; the reconnect attempt is silent in that state.
    if (pollTimer === null) onStatus("connecting");
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const connectedSocket = new WebSocket(`${protocol}://${window.location.host}/api/stream`);
    socket = connectedSocket;

    connectedSocket.onopen = () => {
      if (closed || socket !== connectedSocket) return;
      socketOpen = true;
      attempt = 0;
      stopPolling();
      onStatus("live");
    };
    connectedSocket.onmessage = (event) => {
      if (closed || socket !== connectedSocket) return;
      try {
        const message = JSON.parse(event.data);
        const authCode = streamAuthCode(message);
        if (authCode === "session_required" || authCode === "session_expired") {
          recoveringSession = true;
          connectedSocket.close();
          void bootstrapSession()
            .then(() => {
              recoveringSession = false;
              if (!closed) {
                attempt = 0;
                connect();
              }
            })
            .catch(() => {
              recoveringSession = false;
              if (!closed) startPolling();
            });
          return;
        }
        if (isTerminalStreamError(message)) {
          closed = true;
          onStatus("offline");
          connectedSocket.close();
          return;
        }
        if (message.type === "snapshot") onSnapshot(message.data as FleetSnapshot);
      } catch {
        /* ignore malformed frame */
      }
    };
    connectedSocket.onerror = () => {
      if (socket === connectedSocket) connectedSocket.close();
    };
    connectedSocket.onclose = () => {
      if (socket !== connectedSocket) return;
      socketOpen = false;
      if (closed) return;
      if (recoveringSession) return;
      startPolling();
      attempt += 1;
      const delay = Math.min(30000, 1000 * 2 ** Math.min(attempt, 5));
      retryTimer = window.setTimeout(connect, delay);
    };
  };

  connect();

  return () => {
    closed = true;
    stopPolling();
    if (retryTimer !== null) window.clearTimeout(retryTimer);
    socket?.close();
  };
}
