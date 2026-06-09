import type {
  AuditEvent,
  AuthState,
  ArtifactInfo,
  AiWorkflowDraftRequest,
  AiWorkflowDraftResponse,
  ChatPublicConfig,
  ChatTurnResponse,
  Credential,
  CredentialOAuthStartResponse,
  CredentialTestResponse,
  CredentialTypeInfo,
  Environment,
  NodeManifest,
  NodeSource,
  PackageUsage,
  PinnedItem,
  CodeModule,
  CodeModuleFunctionPreview,
  Deployment,
  DeploymentCreate,
  DeploymentUpdate,
  DatasetQueryResult,
  ProviderTriggerSubscription,
  RunBatchInfo,
  RunInfo,
  RunListItem,
  RunnerInfo,
  RunnerPoolInfo,
  RegistrationTokenResponse,
  SystemSettings,
  UserAdminInfo,
  UserInfo,
  WorkflowDetail,
  WorkflowGraph,
  WorkflowPublishResponse,
  WorkflowSummary,
  WorkflowVersionInfo,
} from "./types";

const BASE = "/api";
const TOKEN_KEY = "noodle_token";
const USER_KEY = "noodle_user";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}
export function getUser(): UserInfo | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserInfo;
  } catch {
    return null;
  }
}
export function setUser(user: UserInfo | null): void {
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
  else localStorage.removeItem(USER_KEY);
}

let unauthorizedHandler: (() => void) | null = null;
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
  // Many call sites surface errors with `String(err)`. Default Error
  // stringification prepends the class name ("ApiError: …"), which leaks an
  // internal detail into user-facing toasts/banners. Return just the message.
  override toString(): string {
    return this.message;
  }
}

/**
 * Normalise any caught value into user-facing text. For `Error` (incl.
 * `ApiError`) this returns `.message`, which strips the class-name prefix that
 * `String(err)` would otherwise leak (e.g. "TypeError: Failed to fetch" →
 * "Failed to fetch", "ApiError: 403 …" → "403 …"). Use this at display sites
 * (toasts, inline banners) instead of `String(err)`.
 */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return String(err);
}

/**
 * Turn an API error `detail` payload into human-readable text.
 *
 * FastAPI returns validation errors as an array of `{loc, msg, type}` objects;
 * rendering that array verbatim dumps raw JSON at the user. Custom handlers
 * return `{message, …}` objects or plain strings. Normalise all of these to a
 * sentence; fall back to JSON only for genuinely unexpected shapes.
 */
export function formatErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : null,
      )
      .filter((m): m is string => Boolean(m));
    if (msgs.length) return msgs.join("; ");
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return JSON.stringify(detail);
}

/**
 * `fetch`, but a network-level failure (offline, DNS, CORS, server down)
 * — which rejects with a bare `TypeError: Failed to fetch` — is converted into
 * a clean `ApiError` so display sites show a human message instead of leaking
 * the class name. Intentional aborts (request `signal`) are re-thrown untouched.
 */
async function safeFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (err) {
    if ((err as { name?: string })?.name === "AbortError") throw err;
    throw new ApiError(
      0,
      "Could not reach the server. Check your connection and try again.",
      err,
    );
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const baseHeaders: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) baseHeaders.Authorization = `Bearer ${token}`;
  const headers = {
    ...baseHeaders,
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const resp = await safeFetch(BASE + path, { ...init, headers });
  if (resp.status === 401) {
    setToken(null);
    setUser(null);
    unauthorizedHandler?.();
    throw new Error("401 Unauthorized");
  }
  if (!resp.ok) {
    let detail: unknown = resp.statusText;
    try {
      const body = (await resp.json()) as { detail?: unknown };
      if (body.detail !== undefined && body.detail !== null) detail = body.detail;
    } catch {
      /* response had no JSON body */
    }
    throw new ApiError(resp.status, `${resp.status} ${formatErrorDetail(detail)}`, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export interface WorkflowPatch {
  name?: string;
  active?: boolean;
  environment_id?: string;
  graph?: WorkflowGraph;
  error_workflow_id?: string | null;
  error_alerts?: Record<string, unknown>;
  run_timeout_seconds?: number | null;
}

type Page<T> = { items: T[]; total: number; limit: number; offset: number };

async function requestList<T>(path: string, init?: RequestInit): Promise<T[]> {
  const data = await request<T[] | Page<T>>(path, init);
  if (Array.isArray(data)) return data;
  if (data && Array.isArray((data as Page<T>).items)) return (data as Page<T>).items;
  return [];
}

export const api = {
  nodes: () => request<NodeManifest[]>("/nodes"),
  nodeSource: (nodeType: string) =>
    request<NodeSource>(`/nodes/${encodeURIComponent(nodeType)}/source`),
  listWorkflows: () => requestList<WorkflowSummary>("/workflows"),
  createWorkflow: (name: string) =>
    request<WorkflowDetail>("/workflows", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  getWorkflow: (id: string) => request<WorkflowDetail>(`/workflows/${id}`),
  updateWorkflow: (id: string, patch: WorkflowPatch) =>
    request<WorkflowDetail>(`/workflows/${id}`, {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  publishWorkflow: (
    id: string,
    body: { notes?: string; update_deployments?: boolean } = {},
  ) =>
    request<WorkflowPublishResponse>(`/workflows/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  aiWorkflowDraft: (id: string, body: AiWorkflowDraftRequest, signal?: AbortSignal) =>
    request<AiWorkflowDraftResponse>(`/workflows/${id}/ai-draft`, {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    }),
  deleteWorkflow: (id: string) =>
    request<void>(`/workflows/${id}`, { method: "DELETE" }),
  listWorkflowVersions: (workflowId: string) =>
    request<WorkflowVersionInfo[]>(`/workflows/${workflowId}/versions`),
  listWorkflowProviderTriggers: (workflowId: string, includeDeleted = true) =>
    request<ProviderTriggerSubscription[]>(
      `/workflows/${workflowId}/provider-triggers?include_deleted=${includeDeleted ? "true" : "false"}`,
    ),
  restoreWorkflowVersion: (workflowId: string, versionId: string) =>
    request<WorkflowDetail>(`/workflows/${workflowId}`, {
      method: "PUT",
      body: JSON.stringify({ restore_version_id: versionId }),
    }),

  listEnvironments: () => request<Environment[]>("/environments"),
  getEnvironment: (id: string) => request<Environment>(`/environments/${id}`),
  listBackends: () =>
    request<{
      platform: string;
      venv: { available: boolean; version: string | null; managed: boolean };
      conda: { available: boolean; version: string | null; managed: boolean };
      pixi: { available: boolean; version: string | null; managed: boolean };
      docker: { available: boolean; version: string | null; managed: boolean };
    }>("/environments/backends"),
  createEnvironment: (body: {
    name: string;
    python_version?: string;
    packages?: string[];
    description?: string;
    runner_pool_size?: number;
    runner_pool_max?: number | null;
    runner_pool_id?: string | null;
    backend?: string;
    backend_config?: Record<string, unknown>;
  }) =>
    request<Environment>("/environments", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateEnvironment: (
    id: string,
    body: {
      name?: string;
      description?: string;
      runner_pool_size?: number;
      runner_pool_max?: number | null;
      runner_pool_id?: string | null;
      runner_pool_set?: boolean;
    },
  ) =>
    request<Environment>(`/environments/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteEnvironment: (id: string) =>
    request<void>(`/environments/${id}`, { method: "DELETE" }),

  getSystemSettings: () => request<SystemSettings>("/system-settings"),
  updateSystemSettings: (body: Partial<SystemSettings>) =>
    request<SystemSettings>("/system-settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  addPackage: (id: string, pkg: string) =>
    request<Environment>(`/environments/${id}/packages`, {
      method: "POST",
      body: JSON.stringify({ package: pkg }),
    }),
  removePackage: (id: string, pkg: string) =>
    request<Environment>(
      `/environments/${id}/packages/${encodeURIComponent(pkg)}`,
      { method: "DELETE" },
    ),
  rebuildEnvironment: (id: string) =>
    request<Environment>(`/environments/${id}/rebuild`, { method: "POST" }),
  setPackages: (id: string, packages: string[]) =>
    request<Environment>(`/environments/${id}/packages`, {
      method: "PUT",
      body: JSON.stringify({ packages }),
    }),
  packageUsage: (id: string) =>
    request<PackageUsage>(`/environments/${id}/package-usage`),

  runWorkflow: (
    id: string,
    body: {
      mode?: string;
      targets?: string[];
      cache?: Record<string, Record<string, unknown>>;
      trigger_node_id?: string;
    },
  ) =>
    request<{ run_id: string }>(`/workflows/${id}/run`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  sendChatMessage: (
    workflowId: string,
    message: string,
    sessionId: string,
  ) =>
    request<ChatTurnResponse>(`/workflows/${workflowId}/chat`, {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId }),
    }),
  startChatTurn: (workflowId: string, message: string, sessionId: string) =>
    request<{ run_id: string | null; session_id: string }>(
      `/workflows/${workflowId}/chat/stream`,
      {
        method: "POST",
        body: JSON.stringify({ message, session_id: sessionId }),
      },
    ),
  chatTurnResult: (workflowId: string, runId: string, sessionId: string) =>
    request<ChatTurnResponse>(
      `/workflows/${workflowId}/chat/result/${runId}?session_id=${encodeURIComponent(sessionId)}`,
    ),
  getPublicChatConfig: (workflowId: string) =>
    request<ChatPublicConfig>(`/chat/p/${workflowId}`),
  sendPublicChatMessage: (
    workflowId: string,
    message: string,
    sessionId: string,
    token?: string,
  ) =>
    request<ChatTurnResponse>(
      `/chat/p/${workflowId}${token ? `?token=${encodeURIComponent(token)}` : ""}`,
      {
        method: "POST",
        body: JSON.stringify({ message, session_id: sessionId }),
    }),
  getRun: (runId: string) => request<RunInfo>(`/runs/${runId}`),
  listRunArtifacts: (runId: string) =>
    request<ArtifactInfo[]>(`/runs/${runId}/artifacts`),
  getArtifact: (artifactId: string) =>
    request<ArtifactInfo>(`/artifacts/${artifactId}`),
  deleteArtifact: (artifactId: string) =>
    request<void>(`/artifacts/${artifactId}`, { method: "DELETE" }),
  queryDataset: (artifactId: string, sql: string, limit = 200) =>
    request<DatasetQueryResult>(`/artifacts/${artifactId}/query`, {
      method: "POST",
      body: JSON.stringify({ sql, limit }),
    }),

  previewExpression: (body: {
    value: string;
    json?: unknown;
    inputs?: Record<string, unknown>;
    nodes?: Record<string, unknown>;
  }) =>
    request<{
      result: unknown;
      error: string | null;
      parts: Array<
        | { kind: "text"; value: string }
        | { kind: "expr"; raw: string; value: unknown }
        | { kind: "error"; raw: string; error: string }
      >;
    }>("/expression-preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listRuns: (id: string) => requestList<RunInfo>(`/workflows/${id}/runs`),
  listAllRuns: (filters: {
    workflow_id?: string;
    status?: string;
    trigger_type?: string;
    since?: string;
    until?: string;
    limit?: number;
    offset?: number;
  } = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== null && value !== "") {
        params.set(key, String(value));
      }
    }
    const query = params.toString();
    return requestList<RunListItem>(`/runs${query ? `?${query}` : ""}`);
  },
  cancelRun: (runId: string) =>
    request<{ run_id: string; status: string }>(`/runs/${runId}/cancel`, {
      method: "POST",
    }),
  rerunRun: (runId: string) =>
    request<{ run_id: string }>(`/runs/${runId}/rerun`, { method: "POST" }),
  retryRun: (runId: string) =>
    request<{ run_id: string }>(`/runs/${runId}/retry`, { method: "POST" }),

  listDeployments: (workflowId?: string) =>
    request<Deployment[]>(
      `/deployments${workflowId ? `?workflow_id=${workflowId}` : ""}`,
    ),
  createDeployment: (body: DeploymentCreate) =>
    request<Deployment>("/deployments", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateDeployment: (id: string, body: DeploymentUpdate) =>
    request<Deployment>(`/deployments/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteDeployment: (id: string) =>
    request<void>(`/deployments/${id}`, { method: "DELETE" }),
  runDeployment: (id: string) =>
    request<{ run_id: string }>(`/deployments/${id}/run`, { method: "POST" }),
  listDeploymentRuns: (id: string) =>
    request<RunListItem[]>(`/deployments/${id}/runs`),

  // ---- Code modules (upload-to-nodes) ----
  listCodeModules: (filters: {
    workflow_id?: string;
    scope?: string;
    environment_id?: string;
    visible_to_workflow?: string;
  } = {}) => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
    }
    const q = params.toString();
    return request<CodeModule[]>(`/code-modules${q ? `?${q}` : ""}`);
  },
  createCodeModule: (body: {
    scope: string;
    workflow_id?: string | null;
    environment_id?: string | null;
    name: string;
    contents?: string;
    include_undecorated?: boolean;
  }) =>
    request<CodeModule>("/code-modules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateCodeModule: (
    id: string,
    body: { name?: string; contents?: string; include_undecorated?: boolean },
  ) =>
    request<CodeModule>(`/code-modules/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteCodeModule: (id: string) =>
    request<void>(`/code-modules/${id}`, { method: "DELETE" }),
  previewCodeModule: (id: string) =>
    request<CodeModuleFunctionPreview>(`/code-modules/${id}/preview`),
  starterGraph: (moduleId: string) =>
    request<WorkflowGraph>(`/code-modules/${moduleId}/starter-graph`, {
      method: "POST",
    }),
  workflowCustomNodeManifests: (workflowId: string) =>
    request<NodeManifest[]>(`/code-modules/manifests/workflow/${workflowId}`),

  listCredentials: () => request<Credential[]>("/credentials"),
  listCredentialTypes: () => request<CredentialTypeInfo[]>("/credentials/types"),
  startCredentialOAuth: (body: {
    credential_type: string;
    name: string;
    scope?: string;
    workflow_id?: string | null;
    environment_id?: string | null;
    runner_pool_id?: string | null;
    description?: string;
    redirect_uri?: string;
    scopes?: string[];
  }) =>
    request<CredentialOAuthStartResponse>("/credentials/oauth/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createCredential: (body: {
    name: string;
    type: string;
    scope?: string;
    workflow_id?: string | null;
    environment_id?: string | null;
    runner_pool_id?: string | null;
    description?: string;
    data: Record<string, string>;
  }) =>
    request<Credential>("/credentials", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  testCredential: (
    id: string,
    body: {
      workflow_id?: string | null;
      environment_id?: string | null;
      runner_pool_id?: string | null;
      context?: Record<string, unknown>;
    } = {},
  ) =>
    request<CredentialTestResponse>(`/credentials/${id}/test`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  testCredentialDraft: (body: {
    type: string;
    data: Record<string, string>;
    context?: Record<string, unknown>;
  }) =>
    request<CredentialTestResponse>("/credentials/test-draft", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  dynamicOptions: (loaderId: string, params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request<{
      loader_id: string;
      options: { value: string; label: string; description?: string }[];
    }>(`/nodes/dynamic-options/${loaderId}${qs ? `?${qs}` : ""}`);
  },
  refreshCredential: (id: string) =>
    request<Credential>(`/credentials/${id}/refresh`, { method: "POST" }),
  deleteCredential: (id: string) =>
    request<void>(`/credentials/${id}`, { method: "DELETE" }),

  listAudit: () => requestList<AuditEvent>("/audit"),

  lastWebhook: (path: string) =>
    request<unknown>(`/webhook-test/${encodeURIComponent(path)}/last`),
  clearWebhook: (path: string) =>
    request<void>(`/webhook-test/${encodeURIComponent(path)}/last`, {
      method: "DELETE",
    }),

  authRequired: () => request<AuthState>("/auth/required"),
  login: (email: string, password: string) =>
    request<{ token: string; user: UserInfo }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  register: (body: {
    name: string;
    company: string;
    email: string;
    password: string;
  }) =>
    request<{ token: string; user: UserInfo }>("/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listUsers: () => request<UserAdminInfo[]>("/auth/users"),
  createUser: (body: {
    name?: string;
    company?: string;
    email: string;
    password: string;
    role: string;
  }) =>
    request<UserAdminInfo>("/auth/users", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateUserRole: (id: string, role: string) =>
    request<UserAdminInfo>(`/auth/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
  deleteUser: (id: string) =>
    request<void>(`/auth/users/${id}`, { method: "DELETE" }),

  listPinned: (workflowId: string) =>
    request<PinnedItem[]>(`/workflows/${workflowId}/pinned`),
  pinNode: (workflowId: string, nodeId: string, payload: unknown) =>
    request<PinnedItem>(`/workflows/${workflowId}/pinned/${nodeId}`, {
      method: "PUT",
      body: JSON.stringify({ payload }),
    }),
  unpinNode: (workflowId: string, nodeId: string) =>
    request<void>(`/workflows/${workflowId}/pinned/${nodeId}`, {
      method: "DELETE",
    }),
  // --- Ops dashboard --------------------------------------------------------
  runtimeMode: () => request<RuntimeModeStatus>("/ops/runtime-mode"),
  queueStats: () => request<QueueStats>("/ops/queue"),
  runTimeline: (runId: string) => request<RunTimeline>(`/runs/${runId}/timeline`),
  runApprovals: (runId: string) =>
    request<RunApprovalInfo[]>(`/runs/${runId}/approvals`),
  decideRunApproval: (
    runId: string,
    approvalId: string,
    decision: "approve" | "reject",
    reason = "",
  ) =>
    request<RunApprovalInfo>(`/runs/${runId}/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, reason }),
    }),
  runDebugSnapshot: (runId: string) =>
    request<RunDebugSnapshot>(`/runs/${runId}/debug-snapshot`),
};

export async function uploadArtifact(file: File): Promise<ArtifactInfo> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const body = new FormData();
  body.append("file", file);
  const resp = await safeFetch(`${BASE}/artifacts/upload`, {
    method: "POST",
    headers,
    body,
  });
  if (resp.status === 401) {
    setToken(null);
    setUser(null);
    unauthorizedHandler?.();
    throw new Error("401 Unauthorized");
  }
  if (!resp.ok) {
    let detail: unknown = resp.statusText;
    try {
      const parsed = (await resp.json()) as { detail?: unknown };
      if (parsed.detail !== undefined && parsed.detail !== null) detail = parsed.detail;
    } catch {
      /* no JSON body */
    }
    throw new ApiError(resp.status, `${resp.status} ${formatErrorDetail(detail)}`, detail);
  }
  return (await resp.json()) as ArtifactInfo;
}

// --- Ops dashboard types -----------------------------------------------------

export interface RuntimeModeStatus {
  mode: string;
  database_dialect: string;
  queue_backend: string;
  scheduler_role: string;
  webhook_role: string;
  artifact_backend: string;
  runner_providers: string[];
  allow_insecure: boolean;
  warnings: string[];
}

export interface QueueStats {
  queued: number;
  leased: number;
  running: number;
  waiting: number;
  completed: number;
  failed: number;
  dead_lettered: number;
  cancelled: number;
  oldest_queued_age_seconds: number | null;
}

export interface RunTimelineEvent {
  type: string;
  ts: string | null;
  data: Record<string, unknown>;
}

export interface RunTimeline {
  run_id: string;
  status: string;
  events: RunTimelineEvent[];
}

export interface RunApprovalInfo {
  id: string;
  run_id: string;
  approval_key: string;
  status: string;
  node_id: string | null;
  agent_node_id: string | null;
  step: number;
  max_steps: number | null;
  tool_call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  message: string;
  requested_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  reason: string;
}

export interface RunDebugSnapshot {
  run_id: string;
  workflow_id: string;
  workflow_version: number | null;
  workflow_version_id: string | null;
  status: string;
  graph: WorkflowGraph;
  failed_node_id: string | null;
  upstream_cache: Record<string, unknown>;
  node_errors: Record<string, string>;
}

export function runEventsUrl(runId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const token = getToken();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}//${window.location.host}/ws/runs/${runId}${query}`;
}

/** Subscribe to a run's live events with auto-reconnect.
 *
 * Returns a handle whose ``close()`` tears down the socket and prevents
 * further reconnects. The backoff schedule (1s → 2s → 4s → 8s, capped at
 * 5 attempts) handles transient drops without spinning forever. The
 * broker replays buffered events on subscribe, so a mid-run reconnect
 * doesn't lose the run's history.
 *
 * ``onReconnecting`` fires on each failed attempt so the UI can surface a
 * "reconnecting…" state. ``onClosed`` fires once when the socket is
 * permanently closed (either ``close()`` was called or we exhausted
 * retries).
 */
export interface RunStreamHandle {
  close: () => void;
}

const RUN_STREAM_BACKOFF_MS = [1000, 2000, 4000, 8000];

export function subscribeToRunEvents(
  runId: string,
  handlers: {
    onMessage: (data: unknown) => void;
    onReconnecting?: (attempt: number) => void;
    onClosed?: () => void;
  },
): RunStreamHandle {
  let socket: WebSocket | null = null;
  let attempt = 0;
  let closedByCaller = false;

  function open(): void {
    if (closedByCaller) return;
    socket = new WebSocket(runEventsUrl(runId));
    socket.onmessage = (event) => {
      // Reset the backoff once any message arrives — the connection is healthy.
      attempt = 0;
      try {
        handlers.onMessage(JSON.parse(event.data as string));
      } catch {
        /* malformed event — drop */
      }
    };
    socket.onclose = (event) => {
      socket = null;
      if (closedByCaller) {
        handlers.onClosed?.();
        return;
      }
      // 1000 (normal) or 1008 (auth refused) → no point reconnecting.
      if (event.code === 1000 || event.code === 1008) {
        handlers.onClosed?.();
        return;
      }
      if (attempt >= RUN_STREAM_BACKOFF_MS.length) {
        handlers.onClosed?.();
        return;
      }
      const delay = RUN_STREAM_BACKOFF_MS[attempt];
      attempt += 1;
      handlers.onReconnecting?.(attempt);
      window.setTimeout(open, delay);
    };
  }

  open();

  return {
    close() {
      closedByCaller = true;
      if (socket && socket.readyState === WebSocket.OPEN) socket.close(1000);
      else if (socket) socket.close();
    },
  };
}

// ---------------------------------------------------------------------------
// Runner pools
// ---------------------------------------------------------------------------

export const runnerPoolsApi = {
  list: () => request<RunnerPoolInfo[]>("/runner-pools"),

  create: (body: {
    name: string;
    provider?: string;
    provider_config?: Record<string, unknown>;
    max_concurrent_runs?: number;
  }) =>
    request<RunnerPoolInfo>("/runner-pools", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  get: (poolId: string) => request<RunnerPoolInfo>(`/runner-pools/${poolId}`),

  update: (
    poolId: string,
    body: { name?: string; provider_config?: Record<string, unknown>; max_concurrent_runs?: number }
  ) =>
    request<RunnerPoolInfo>(`/runner-pools/${poolId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  delete: (poolId: string) =>
    request<void>(`/runner-pools/${poolId}`, { method: "DELETE" }),

  listRunners: (poolId: string) =>
    request<RunnerInfo[]>(`/runner-pools/${poolId}/runners`),

  deleteRunner: (poolId: string, runnerId: string) =>
    request<void>(`/runner-pools/${poolId}/runners/${runnerId}`, { method: "DELETE" }),

  updateRunner: (
    poolId: string,
    runnerId: string,
    body: {
      name?: string;
      max_concurrent_runs?: number;
      capabilities?: Record<string, unknown>;
    },
  ) =>
    request<RunnerInfo>(`/runner-pools/${poolId}/runners/${runnerId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  createRegistrationToken: (
    poolId: string,
    body?: {
      name?: string;
      max_concurrent_runs?: number;
      capabilities?: Record<string, unknown>;
    },
  ) =>
    request<RegistrationTokenResponse>(`/runner-pools/${poolId}/registration-tokens`, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),

  sshOnboard: (
    poolId: string,
    body: {
      host: string;
      port?: number;
      username: string;
      auth_method: "key" | "password";
      password?: string;
      private_key?: string;
      passphrase?: string;
      name?: string;
      max_concurrent_runs?: number;
      capabilities?: Record<string, unknown>;
      api_url?: string;
      use_systemd?: boolean;
    }
  ) =>
    request<{ runner_id: string; runner_name: string; install_log: string }>(
      `/runner-pools/${poolId}/ssh-onboard`,
      { method: "POST", body: JSON.stringify(body) }
    ),

  createBatchRun: (
    workflowId: string,
    body: {
      runner_pool_id?: string | null;
      parameters: Record<string, unknown>[];
      trigger_node_id?: string | null;
    }
  ) =>
    request<{ batch_id: string; run_ids: string[]; total: number }>(
      `/runner-pools/workflows/${workflowId}/batch-runs`,
      { method: "POST", body: JSON.stringify(body) }
    ),

  getBatchRun: (batchId: string) =>
    request<RunBatchInfo>(`/runner-pools/run-batches/${batchId}`),

  cancelBatchRun: (batchId: string) =>
    request<{ batch_id: string; cancelled_runs: number }>(
      `/runner-pools/run-batches/${batchId}/cancel`,
      { method: "POST" }
    ),
};
