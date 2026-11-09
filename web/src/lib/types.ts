export type Severity = "ok" | "warn" | "crit" | "unknown";

export interface OmniRouteStatus {
  policy: {
    available: boolean;
    source: string;
    sha256?: string;
    model_count?: number;
    models?: string[];
    defaults?: Record<string, { route: string; thinking_level?: string }>;
    discovery_authority?: string;
    configuration_authority?: string;
    projection_rule?: string;
  };
  observation: {
    source: string;
    result: "clean" | "changes" | "blocked" | "failed" | "unavailable";
    exit_code: number | null;
    scope_complete: boolean;
    checked_at: string | null;
    policy_revision: string | null;
    items: { host: string; component: string; result: string }[];
  };
}

export interface Signal {
  key: string;
  label: string;
  severity: Severity;
  value: number | null;
  unit: string;
  detail: string;
}

export interface Health {
  severity: Severity;
  headline: string;
  signals: Signal[];
  reason: string | null;
}

export interface DiskEntry {
  mount: string;
  total_bytes: number;
  free_bytes: number;
  used_bytes: number;
  percent: number | null;
  free_percent?: number | null;
  available_bytes?: number | null;
  available_percent?: number | null;
  reclaimable_estimate_bytes?: number | null;
  opportunistic_bytes?: number | null;
  available_basis?: string;
  available_age_s?: number | null;
  available_stale?: boolean;
  scope?: string;
  source?: string;
}

export interface ProcessEntry {
  pid: number | null;
  name: string | null;
  cpu: number | null;
  cpu_seconds?: number | null;
  mem: number | null;
}

export interface Sample {
  schema: number;
  agent_version: string;
  host_id: string;
  hostname: string;
  os: string;
  os_version: string;
  arch: string;
  sampled_at: string;
  sample_age_s: number;
  uptime_s: number | null;
  cpu: {
    percent: number | null;
    cores: number | null;
    load1: number | null;
    load5: number | null;
    load15: number | null;
    source: string;
    reason: string | null;
  };
  memory: {
    total_bytes: number | null;
    available_bytes?: number | null;
    used_bytes?: number | null;
    percent: number | null;
    pressure_used_percent?: number | null;
    swap_total_bytes?: number | null;
    swap_used_bytes?: number | null;
    source?: string;
    reason?: string | null;
  };
  disk: DiskEntry[];
  gpu: {
    name: string | null;
    utilization_percent: number | null;
    memory_used_bytes?: number | null;
    memory_total_bytes?: number | null;
    temperature_c?: number | null;
    source?: string;
    reason?: string | null;
  };
  thermal: {
    cpu_c: number | null;
    sensors: { label: string; celsius: number }[];
    pressure_level?: string | null;
    reason: string | null;
  };
  battery: {
    present: boolean;
    percent: number | null;
    charging?: boolean | null;
    state?: string | null;
    minutes_remaining?: number | null;
    reason?: string | null;
  };
  network: { rx_bps: number | null; tx_bps: number | null; reason: string | null };
  processes: ProcessEntry[];
}

export interface HostView {
  id: string;
  name: string;
  subtitle: string;
  os: string;
  role: string;
  image: string | null;
  notes: string;
  is_control_plane: boolean;
  lifecycle: "managed" | "pending";
  reachable: boolean;
  stale: boolean;
  error: string | null;
  last_success_age_s: number | null;
  sample: Sample | null;
  health: Health;
}

export interface FleetSnapshot {
  generated_at: number;
  poll_interval_s: number;
  hosts: HostView[];
  fleet: {
    total: number;
    managed_total: number;
    pending: number;
    reachable: number;
    worst_severity: Severity;
  };
}

/** Backend-projected, authenticated inventory; URL exists only with native proof. */
export interface FleetPort {
  host_id: string;
  host_name: string;
  status: "online" | "offline" | "pending" | "unknown";
  sampled_at: string | null;
  tailnet: "on" | "off" | "not verified";
  port: number | null;
  protocol: "tcp";
  bind: string | null;
  process: string | null;
  service: string | null;
  web: boolean;
  url: string | null;
  links: { scope: "tailnet"; url: string }[];
}

export interface HistoryPoint {
  t: number;
  cpu: number | null;
  memory: number | null;
  disk: number | null;
  storage_available: number | null;
  gpu: number | null;
  temp: number | null;
  battery: number | null;
  reachable: boolean;
}

export interface ActionEntry {
  key: string;
  label: string;
  description: string;
  destructive: boolean;
  available: boolean;
  reason: string | null;
  fix: string | null;
}

export interface Capabilities {
  host_id: string;
  probed_at: number;
  privileged: boolean;
  transport_ok: boolean;
  restart_agent_proven: boolean;
  probe_detail: string;
  actions: Record<string, ActionEntry>;
}

export interface Receipt {
  id: string;
  host_id: string;
  action: string;
  started_at: number;
  finished_at: number | null;
  outcome: string;
  detail: string | null;
  payload: Record<string, unknown>;
}

export interface ProfileEntry {
  browser: string;
  email: string;
  label: string;
  policy_label?: string;
  name_policy_ok?: boolean;
  alias: string;
  category: string;
  directory: string;
  profile_key: string;
  /** Canonical browser/account identity shared by matching fleet hosts. */
  identity_key: string;
  status?: "available";
  path_redacted?: boolean;
}

export interface ProfileInventory {
  host: { id: string; label: string; runtime?: string; image?: string | null };
  online: boolean;
  status?: "available" | "unavailable";
  freshness?: "fresh" | "cached" | "stale";
  observed_at?: number;
  error_code?: string;
  profiles: ProfileEntry[];
}

export interface ProfileAccount {
  identity_key: string;
  browser: string;
  email: string;
  label: string;
  browsers: string[];
  instance_count?: number;
}

export interface ProfileFleet {
  schema_version: number;
  freshness?: "fresh" | "cached" | "stale";
  observed_at?: number;
  inventories: ProfileInventory[];
  accounts: ProfileAccount[];
}

export interface NotionItem {
  id: string;
  title: string;
  due: string | null;
  overdue: boolean;
  status: string | null;
  priority: string | null;
  type: string | null;
  url: string | null;
}

export interface ControlCenterSummary {
  generated_at: number;
  notion: {
    available: boolean;
    reason: string | null;
    items: NotionItem[];
    has_more?: boolean;
    counts: { due_or_overdue?: number; overdue?: number; focus?: number };
  };
  obsidian: {
    available: boolean;
    reason: string | null;
    vault?: string;
    recent: { title: string; path: string; modified: number; size: number }[];
    tasks: { slug: string; title: string; status: string; modified: number }[];
  };
  manifests: CanonicalManifestSnapshot;
}

export interface CanonicalManifestSnapshot {
  available: boolean;
  state: string;
  reason: string | null;
  generated_at?: number;
  sources: { name: string; path: string; available: boolean }[];
  factory: {
    repo?: string;
    schema_version?: number;
    targets: { id: string; kind: string; operation: string; bundle: string }[];
  };
  services: {
    id: string;
    repo?: string;
    source_revision?: string;
    runtime?: string;
    status: string;
  }[];
  notion: { state: string; owner: string; adapter: string; write: string };
  obsidian: { state: string; owner: string; adapter: string; write: string };
  news: { state: string; reason: string };
  provider_gate: {
    state: string;
    expected_tool_count: number | null;
    observed_tool_count: number | null;
    observed_at?: string;
    drift: string;
    action: string;
    owner_host: string;
  };
  propagation: {
    available: boolean;
    state: string;
    reason: string | null;
    entries: {
      id: string;
      name: string;
      version: number | null;
      status: string;
      source_valid: boolean;
      source_digest: { path: string; expected: string | null; actual: string; match: boolean };
      rendered: boolean;
      projections: {
        id: string;
        status: string;
        digest: { path: string; expected: string | null; actual: string; match: boolean };
        aligned: boolean;
      }[];
      applied: string;
      runtime: string;
      runtime_proven: boolean;
      drift: string[];
      limitations: string[];
    }[];
  };
}

export interface WorkspaceSource {
  kind: "notion" | "obsidian";
  state: "healthy" | "offline" | "error" | "partial" | string;
  available: boolean;
  reason: string | null;
  fetched_at: number;
  freshness_s: number;
  stale_after_s: number;
  workspace?: string;
  workspace_id?: string;
  title?: string;
  data_source_id?: string;
  url?: string;
  vault?: string;
  source_key?: "actions";
  edit_surface?: "records";
}

export interface WorkspaceNotionColumn {
  id: string;
  name: string;
  type: string;
  editable: boolean;
  options: string[];
}

export interface WorkspaceNotionRow {
  id: string;
  title: string;
  url: string | null;
  last_edited_time: string | null;
  properties: Record<string, unknown>;
  property_types: Record<string, string>;
}

export interface WorkspaceNotion {
  source: WorkspaceSource;
  registry: { data_source_id: string; required_for_write: boolean };
  schema: WorkspaceNotionColumn[];
  rows: WorkspaceNotionRow[];
  has_more: boolean;
  next_cursor: string | null;
  fetched_at: number;
}

export interface WorkspaceNote {
  kind: "note";
  title: string;
  path: string;
  modified: number;
  size: number;
  metadata: Record<string, string>;
  open_uri: string;
}

export interface WorkspaceSelectedNote extends WorkspaceNote {
  content: string;
  content_truncated: boolean;
  links: { title: string; path: string; open_uri: string }[];
  backlinks: { title: string; path: string; open_uri: string }[];
}

export interface WorkspaceObsidian {
  source: WorkspaceSource;
  nodes: WorkspaceNote[];
  results: WorkspaceNote[];
  selected: WorkspaceSelectedNote | null;
  allowlisted_roots: string[];
}

export interface WorkspaceExternalContext {
    state: "declared" | "proven" | "manual" | "deferred" | "unproven";
    available: boolean;
    generated_at: number;
    write: "none";
    canon: false;
    reason: string | null;
    sources: {
      id: "notion" | "obsidian" | "ordinary-chatgpt-project";
      owner: string;
      state: "declared" | "proven" | "manual" | "deferred" | "unproven";
      adapter: string;
      receipt_id: string | null;
      reason: string | null;
    }[];
    agents_projects: {
      available: boolean;
      state: "proven" | "manual" | "deferred" | "unproven";
      reason: string | null;
      generated_at: number;
      write: "none";
      agents: WorkspaceAgentProjectEvidence[];
      projects: WorkspaceAgentProjectEvidence[];
    };
}

export interface WorkspaceSnapshot {
  generated_at: string;
  notion: WorkspaceNotion;
  obsidian: WorkspaceObsidian;
  receipts: Receipt[];
  external_context: WorkspaceExternalContext;
}

export interface WorkspaceAgentProjectEvidence {
  id: string;
  name: string;
  kind: "agent-profile" | "chatgpt-project-skill";
  source: WorkspaceEvidence;
  generated: WorkspaceEvidence;
  runtime: WorkspaceEvidence;
  digest: string | null;
}

export interface WorkspaceEvidence {
  state: "source-proven" | "generated" | "runtime-proven" | "manual" | "deferred" | "unproven";
  reason: string | null;
}

export interface WorkspaceNotionPacket {
  packet_id: string;
  data_source_id: string;
  row_id: string;
  preimage_hash: string;
  schema_fetched_at: number;
  before: Record<string, unknown>;
  changes: Record<string, unknown>;
  properties_patch: Record<string, unknown>;
  rollback: Record<string, unknown>;
  registry: { data_source_id: string; status: string };
  status: "dry-run";
  apply_allowed: boolean;
  manual_gate: string;
  receipt_id: string;
}

export interface WorkspaceNotionRollbackResult {
  ok: boolean;
  status: "rolled-back" | "already-rolled-back";
  mutation_count: number;
  rollback_registry_id: string | null;
  receipt_id: string;
  receipt_sha: string;
}

export interface WorkspaceActionItemOperationPacket extends WorkspaceNotionPacket {
  operation_type: "action-item.edit";
  operation_id: string;
  idempotency_key: string;
  request_digest: string;
  preview: { status: "dry-run"; mutation_count: 0 };
}

export type WorkspaceActionItemOperationAppliedResult =
  {
    ok: boolean;
    receipt_id: string;
    row: WorkspaceNotionRow;
    registry_id: string | null;
    status: "applied" | "already-applied";
    mutation_count: number;
  };

export type WorkspaceActionItemOperationApplyResult =
  | WorkspaceActionItemOperationAppliedResult
  | {
      ok: true;
      operation_type: "action-item.edit";
      operation_id: string;
      status: "already-rolled-back";
      mutation_count: 0;
      receipt_id: string;
      receipt_sha: string;
    };
