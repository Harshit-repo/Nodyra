import { safeGetItem, safeRemoveItem, safeSetItem } from "./safeStorage";
import type {
  AuditEvent,
  AuditEventInfo,
  AuditLogQuery,
  ApiTokenCreate,
  ApiTokenCreated,
  ApiTokenInfo,
  ApiTokenScopeInfo,
  AuthState,
  ArtifactInfo,
  AgenticBuildEvent,
  AiWorkflowDraftRequest,
  AiWorkflowDraftResponse,
  ChatPublicConfig,
  ChatTurnResponse,
  Credential,
  CredentialOAuthStartResponse,
  CredentialTestResponse,
  CredentialTypeInfo,
  Environment,
  EnvironmentBuildJob,
  FolderInfo,
  GithubSyncConfig,
  GithubRepoValidation,
  GithubCreateRepoResponse,
  LicenseInfo,
  MCPConnection,
  MCPConnectionCreate,
  MCPConnectionUpdate,
  MCPToolInfo,
  NodeManifest,
  NodeSource,
  PackageUsage,
  PinnedItem,
  CodeModule,
  CodeModuleFunctionPreview,
  CustomRoleCreate,
  CustomRoleInfo,
  CustomRoleUpdate,
  LintDiagnostic,
  GenerateNodeResponse,
  Deployment,
  DeploymentCreate,
  DeploymentUpdate,
  DatasetQueryResult,
  ProviderTriggerSubscription,
  RunBatchInfo,
  RunInfo,
  RunListItem,
  OrgInfo,
  OrgMemberInfo,
  OrgSettingsInfo,
  OrgUsageDay,
  RecentRun,
  RunHistoryBucket,
  RunnerFleetHealth,
  RunnerInfo,
  RunnerPoolInfo,
  RegistrationTokenResponse,
  SSOConfig,
  SSODetectResponse,
  SSOTestResult,
  SystemSettings,
  UserAdminInfo,
  UserInfo,
  WorkflowDetail,
  WorkflowEvent,
  WorkflowGraph,
  WorkflowPublishResponse,
  WorkflowRevisionInfo,
  WorkflowSummary,
  WorkflowVersionInfo,
  RegistryPackage,
  RegistrySearchResult,
  RegistryInstallResponse,
  RegistryInstallStatus,
} from "./types";

const BASE = "/api";
const TOKEN_KEY = "noodle_token";
const USER_KEY = "noodle_user";
const ORG_KEY = "noodle_org";
// Must match settings.csrf_cookie_name and settings.csrf_header_name defaults.
const CSRF_COOKIE_NAME = "noodle_csrf";
const CSRF_HEADER_NAME = "X-CSRF-Token";

function _getCookie(name: string): string | null {
  const entry = document.cookie.split("; ").find((row) => row.startsWith(`${name}=`));
  return entry ? decodeURIComponent(entry.split("=").slice(1).join("=")) : null;
}

/** Selected organization (multi-tenancy). Sent as X-Org-Id on every request;
 *  null means the server default org. */
export function getOrgId(): string | null {
  return safeGetItem(ORG_KEY);
}
export function setOrgId(orgId: string | null): void {
  if (orgId) safeSetItem(ORG_KEY, orgId);
  else safeRemoveItem(ORG_KEY);
}

export function getToken(): string | null {
  return safeGetItem(TOKEN_KEY);
}
export function setToken(token: string | null): void {
  if (token) safeSetItem(TOKEN_KEY, token);
  else safeRemoveItem(TOKEN_KEY);
}
export function getUser(): UserInfo | null {
  const raw = safeGetItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserInfo;
  } catch {
    return null;
  }
}
export function setUser(user: UserInfo | null): void {
  if (user) safeSetItem(USER_KEY, JSON.stringify(user));
  else safeRemoveItem(USER_KEY);
}

let unauthorizedHandler: (() => void) | null = null;
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/** Clear the session and notify the app a 401-equivalent occurred. Used by the
 *  REST 401 path and the run-stream `1008` (auth refused) close (FE-4). */
function handleUnauthorized(): void {
  setToken(null);
  setUser(null);
  unauthorizedHandler?.();
}

/** Sign out: clears server-side cookies and local session state. */
export async function apiLogout(): Promise<void> {
  try {
    await request<void>("/auth/logout", { method: "POST" });
  } catch {
    // Best-effort: always clear local state even if the server call fails.
  }
  setToken(null);
  setUser(null);
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

const HTTP_FRIENDLY_STATUS: Record<number, string> = {
  400: "The request was invalid. Check your input and try again.",
  401: "Your session expired. Please sign in again.",
  403: "You don't have permission for this action.",
  404: "The requested resource was not found.",
  409: "A conflict occurred. The resource may have been recently modified.",
  422: "The submitted data was invalid. Please check your fields.",
  429: "Too many requests. Please wait a moment and try again.",
  500: "A server error occurred. Please try again or contact support.",
  502: "The service is temporarily unavailable. Please try again shortly.",
  503: "The service is temporarily unavailable. Please try again shortly.",
};

const TRACEBACK_PATTERNS = [
  /Traceback\s*\(most recent call last\)/i,
  /File\s+"[^"]*",\s*line\s+\d+/i,
  /\n\s{2,}raise\s+/i,
  /\n\w+Error:/i,
];

function looksLikeTraceback(text: string): boolean {
  return TRACEBACK_PATTERNS.some((re) => re.test(text));
}

/**
 * Translate any error into user-friendly text suitable for display in toasts and
 * inline banners. Python tracebacks, raw HTTP status codes, and generic network
 * failures are replaced with plain-language messages so non-developer operators
 * can understand what went wrong and what to do next.
 */
export function userFriendlyError(err: unknown): string {
  if (err instanceof ApiError) {
    const statusMsg = HTTP_FRIENDLY_STATUS[err.status];
    const detail = typeof err.detail === "string" ? err.detail : null;
    if (statusMsg) {
      if (detail && !looksLikeTraceback(detail)) {
        return `${statusMsg} ${detail}`;
      }
      return statusMsg;
    }
    if (detail && !looksLikeTraceback(detail)) {
      return detail;
    }
    if (looksLikeTraceback(String(err.detail ?? ""))) {
      return "An unexpected error occurred. Please try again or contact support.";
    }
  }
  if (err instanceof TypeError && err.message === "Failed to fetch") {
    return "Could not reach the server. Check your connection and try again.";
  }
  const message = errorMessage(err);
  if (looksLikeTraceback(message)) {
    return "An unexpected error occurred. Please try again or contact support.";
  }
  return message;
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

/** Auth/tenancy headers shared by every API request: Bearer when a token is
 * stored, otherwise the CSRF double-submit header for cookie-auth mode, plus
 * the active org. Content-Type is NOT set here — FormData uploads must let
 * the browser pick the multipart boundary. */
function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  } else {
    // Cookie-auth mode: Bearer is absent but a CSRF cookie may be present.
    // Echo it in the request header so the server-side CSRF double-submit
    // check passes for state-changing requests.
    const csrfToken = _getCookie(CSRF_COOKIE_NAME);
    if (csrfToken) headers[CSRF_HEADER_NAME] = csrfToken;
  }
  const orgId = getOrgId();
  if (orgId) headers["X-Org-Id"] = orgId;
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = {
    "Content-Type": "application/json",
    ...authHeaders(),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const resp = await safeFetch(BASE + path, { ...init, headers });
  if (resp.status === 401) {
    handleUnauthorized();
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
  environment_id?: string | null;
  default_runner_pool_id?: string | null;
  graph?: WorkflowGraph;
  expected_graph_revision?: number | null;
  error_workflow_id?: string | null;
  error_alerts?: Record<string, unknown>;
  run_timeout_seconds?: number | null;
  mcp_enabled?: boolean;
  mcp_tool_name?: string | null;
  mcp_description?: string | null;
  mcp_parameters_schema?: Record<string, unknown> | null;
  folder_id?: string | null;
}

type Page<T> = { items: T[]; total: number; limit: number; offset: number };

async function requestList<T>(path: string, init?: RequestInit): Promise<T[]> {
  const data = await request<T[] | Page<T>>(path, init);
  if (Array.isArray(data)) return data;
  if (data && Array.isArray((data as Page<T>).items)) return (data as Page<T>).items;
  return [];
}

/** Fetches a complete paginated collection. The workflows dashboard performs
 * client-side search/status aggregation, so silently dropping records after
 * the API's default page would make every count and filter incorrect. */
async function requestAllPages<T>(path: string, pageSize = 500): Promise<T[]> {
  const separator = path.includes("?") ? "&" : "?";
  const first = await request<T[] | Page<T>>(
    `${path}${separator}limit=${pageSize}&offset=0`,
  );
  if (Array.isArray(first)) return first;
  if (!first || !Array.isArray(first.items)) return [];
  if (first.items.length >= first.total) return first.items;

  const offsets: number[] = [];
  const step = Math.max(1, first.limit || pageSize);
  for (let offset = step; offset < first.total; offset += step) offsets.push(offset);

  // F-06: cap concurrent page requests to avoid overwhelming the API server.
  const MAX_CONCURRENT = 5;
  const pages: (T[] | Page<T>)[] = [];
  for (let i = 0; i < offsets.length; i += MAX_CONCURRENT) {
    const batch = offsets.slice(i, i + MAX_CONCURRENT);
    const batchPages = await Promise.all(
      batch.map((offset) =>
        request<T[] | Page<T>>(`${path}${separator}limit=${step}&offset=${offset}`),
      ),
    );
    pages.push(...batchPages);
  }
  const all = [
    ...first.items,
    ...pages.flatMap((page) => Array.isArray(page) ? page : page.items),
  ];
  return Array.from(
    new Map(
      all.map((item, index) => [
        typeof item === "object" && item && "id" in item
          ? String((item as { id: unknown }).id)
          : String(index),
        item,
      ]),
    ).values(),
  );
}

export const api = {
  nodes: () => request<NodeManifest[]>("/nodes"),
  nodeSource: (nodeType: string) =>
    request<NodeSource>(`/nodes/${encodeURIComponent(nodeType)}/source`),
  listWorkflows: () => requestAllPages<WorkflowSummary>("/workflows"),
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

  listFolders: () => request<FolderInfo[]>("/folders"),
  createFolder: (name: string) =>
    request<FolderInfo>("/folders", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  updateFolder: (id: string, patch: { name?: string; color?: string | null }) =>
    request<FolderInfo>(`/folders/${id}`, {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  deleteFolder: (id: string) =>
    request<void>(`/folders/${id}`, { method: "DELETE" }),

  listWorkflowVersions: (workflowId: string) =>
    request<WorkflowVersionInfo[]>(`/workflows/${workflowId}/versions`),
  listWorkflowRevisions: (workflowId: string, limit = 50) =>
    request<WorkflowRevisionInfo[]>(
      `/workflows/${workflowId}/revisions?limit=${encodeURIComponent(String(limit))}`,
    ),
  getVersionGraph: (workflowId: string, versionId: string) =>
    request<{ graph: WorkflowGraph }>(`/workflows/${workflowId}/versions/${versionId}/graph`),
  listWorkflowProviderTriggers: (workflowId: string, includeDeleted = true) =>
    request<ProviderTriggerSubscription[]>(
      `/workflows/${workflowId}/provider-triggers?include_deleted=${includeDeleted ? "true" : "false"}`,
    ),
  restoreWorkflowVersion: (workflowId: string, versionId: string) =>
    request<WorkflowDetail>(`/workflows/${workflowId}`, {
      method: "PUT",
      body: JSON.stringify({ restore_version_id: versionId }),
    }),

  listEnvironments: () => requestAllPages<Environment>("/environments"),
  getEnvironment: (id: string) => request<Environment>(`/environments/${id}`),
  listEnvironmentBuildJobs: (id: string, limit = 50, offset = 0) =>
    request<{
      items: EnvironmentBuildJob[];
      total: number;
      limit: number;
      offset: number;
    }>(
      `/environments/${id}/build-jobs?limit=${encodeURIComponent(String(limit))}&offset=${encodeURIComponent(String(offset))}`,
    ),
  getEnvironmentBuildJob: (environmentId: string, buildJobId: string) =>
    request<EnvironmentBuildJob>(
      `/environments/${environmentId}/build-jobs/${buildJobId}`,
    ),
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
  getLicense: () => request<LicenseInfo>("/system-settings/license"),
  applyLicense: (license_key: string) =>
    request<LicenseInfo>("/system-settings/license", {
      method: "PUT",
      body: JSON.stringify({ license_key }),
    }),
  removeLicense: () =>
    request<LicenseInfo>("/system-settings/license", { method: "DELETE" }),
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
  listRunArtifacts: (runId: string, nodeId?: string) =>
    request<ArtifactInfo[]>(`/runs/${runId}/artifacts${nodeId ? `?node_id=${encodeURIComponent(nodeId)}` : ""}`),
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
    requestAllPages<Deployment>(
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
    requestAllPages<RunListItem>(`/deployments/${id}/runs`),

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
  formatCode: (code: string) =>
    request<{ code: string; changed: boolean; error: string | null }>(
      "/code-modules/format",
      { method: "POST", body: JSON.stringify({ code }) },
    ),
  lintCode: (code: string) =>
    request<{ diagnostics: LintDiagnostic[]; linter: string }>(
      "/code-modules/lint",
      { method: "POST", body: JSON.stringify({ code }) },
    ),
  generateNode: (body: { description: string; scope: string; scope_id?: string | null }) =>
    request<GenerateNodeResponse>("/code-modules/generate-node", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listCredentials: () => requestAllPages<Credential>("/credentials"),
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
  startListen: (path: string) =>
    request<{ listening: boolean; ttl_seconds: number }>(
      `/webhook-test/${encodeURIComponent(path)}/listen`,
      { method: "POST" },
    ),
  stopListen: (path: string) =>
    request<void>(`/webhook-test/${encodeURIComponent(path)}/listen`, {
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
  listApiTokens: () => request<ApiTokenInfo[]>("/auth/api-tokens"),
  listApiTokenScopes: () => request<ApiTokenScopeInfo[]>("/auth/api-token-scopes"),
  createApiToken: (body: ApiTokenCreate) =>
    request<ApiTokenCreated>("/auth/api-tokens", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  revokeApiToken: (tokenId: string) =>
    request<void>(`/auth/api-tokens/${tokenId}`, { method: "DELETE" }),
  listMyOrgs: () => request<OrgInfo[]>("/me/orgs"),
  createOrg: (body: { name: string; slug?: string }) =>
    request<OrgInfo>("/orgs", { method: "POST", body: JSON.stringify(body) }),
  listOrgMembers: () => request<OrgMemberInfo[]>("/orgs/current/members"),
  addOrgMember: (body: { email: string; role: string }) =>
    request<OrgMemberInfo>("/orgs/current/members", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateOrgMember: (userId: string, role: string) =>
    request<OrgMemberInfo>(`/orgs/current/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
  removeOrgMember: (userId: string) =>
    request<void>(`/orgs/current/members/${userId}`, { method: "DELETE" }),
  getOrgSettings: (orgId: string) =>
    request<OrgSettingsInfo>(`/orgs/${orgId}/settings`),
  updateOrgSettings: (orgId: string, body: Record<string, number>) =>
    request<OrgSettingsInfo>(`/orgs/${orgId}/settings`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  getOrgUsage: (orgId: string, days = 14) =>
    request<OrgUsageDay[]>(`/orgs/${orgId}/usage?days=${days}`),
  listUsers: () => requestAllPages<UserAdminInfo>("/auth/users"),
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
  // --- GitHub sync ----------------------------------------------------------
  getGithubSyncConfig: (): Promise<GithubSyncConfig | null> =>
    request<GithubSyncConfig | null>("/github-sync/config"),

  upsertGithubSyncConfig: (body: {
    repo: string;
    base_path?: string;
    main_branch?: string;
    credential_id?: string | null;
  }): Promise<GithubSyncConfig> =>
    request<GithubSyncConfig>("/github-sync/config", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  deleteGithubSyncConfig: (): Promise<void> =>
    request<void>("/github-sync/config", { method: "DELETE" }),

  getGithubWebhookSecret: (): Promise<{ webhook_secret: string }> =>
    request<{ webhook_secret: string }>("/github-sync/config/webhook-secret"),

  triggerManualPull: (workflowId: string): Promise<{ status: string; job_id: string }> =>
    request<{ status: string; job_id: string }>(
      `/workflows/${workflowId}/github-pull`,
      { method: "POST", body: JSON.stringify({}) },
    ),

  resolveGithubConflict: (workflowId: string, side: "noodle" | "github"): Promise<void> =>
    request<void>(`/workflows/${workflowId}/github-conflict/resolve`, {
      method: "POST",
      body: JSON.stringify({ side }),
    }),

  validateGithubRepo: (): Promise<GithubRepoValidation> =>
    request<GithubRepoValidation>("/github-sync/repo/validate"),

  createGithubRepo: (body: { private?: boolean; description?: string }): Promise<GithubCreateRepoResponse> =>
    request<GithubCreateRepoResponse>("/github-sync/repo", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // --- MCP Connections ---------------------------------------------------------
  listMcpConnections: () =>
    request<MCPConnection[]>("/mcp-connections"),

  createMcpConnection: (body: MCPConnectionCreate) =>
    request<MCPConnection>("/mcp-connections", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getMcpConnection: (id: string) =>
    request<MCPConnection>(`/mcp-connections/${id}`),

  updateMcpConnection: (id: string, body: MCPConnectionUpdate) =>
    request<MCPConnection>(`/mcp-connections/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteMcpConnection: (id: string) =>
    request<void>(`/mcp-connections/${id}`, { method: "DELETE" }),

  syncMcpConnection: (id: string) =>
    request<{ tools_discovered: number; tools: MCPToolInfo[] }>(
      `/mcp-connections/${id}/sync`,
      { method: "POST" },
    ),

  getMcpTools: (id: string) =>
    request<MCPToolInfo[]>(`/mcp-connections/${id}/tools`),

  // --- SSO ------------------------------------------------------------------
  getSSOConfig: () => request<SSOConfig | null>("/admin/sso"),

  upsertSSOConfig: (config: Partial<SSOConfig>) =>
    request<{ status: string; protocol: string; org_id: string }>("/admin/sso", {
      method: "POST",
      body: JSON.stringify(config),
    }),

  deleteSSOConfig: () =>
    request<void>("/admin/sso", { method: "DELETE" }),

  testSSOConnection: (config: Partial<SSOConfig>) =>
    request<SSOTestResult>("/admin/sso/test", {
      method: "POST",
      body: JSON.stringify(config),
    }),

  detectSSO: (email: string) =>
    request<SSODetectResponse>(`/auth/sso/detect?email=${encodeURIComponent(email)}`),

  ssoAuthorize: (orgSlug: string) =>
    `/auth/sso/start?org_slug=${encodeURIComponent(orgSlug)}`,

  // --- Ops dashboard --------------------------------------------------------
  runtimeMode: () => request<RuntimeModeStatus>("/ops/runtime-mode"),
  queueStats: () => request<QueueStats>("/ops/queue"),
  runTimeline: (runId: string) => request<RunTimeline>(`/runs/${runId}/timeline`),
  runApprovals: (runId: string) =>
    request<RunApprovalInfo[]>(`/runs/${runId}/approvals`),
  decideRunApproval: (
    runId: string,
    approvalId: string,
    decision: RunApprovalDecision,
    reason = "",
  ) =>
    request<RunApprovalInfo>(`/runs/${runId}/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, reason }),
    }),
  runDebugSnapshot: (runId: string) =>
    request<RunDebugSnapshot>(`/runs/${runId}/debug-snapshot`),

  // --- Custom roles (ADVANCED_RBAC feature) ----------------------------------

  listCustomRoles: () =>
    request<CustomRoleInfo[]>("/admin/custom-roles"),

  getCustomRole: (id: string) =>
    request<CustomRoleInfo>(`/admin/custom-roles/${id}`),

  createCustomRole: (body: CustomRoleCreate) =>
    request<CustomRoleInfo>("/admin/custom-roles", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateCustomRole: (id: string, body: CustomRoleUpdate) =>
    request<CustomRoleInfo>(`/admin/custom-roles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteCustomRole: (id: string) =>
    request<void>(`/admin/custom-roles/${id}`, { method: "DELETE" }),

  listPermissions: () =>
    request<string[]>("/admin/permissions"),

  // --- Audit logs (AUDIT_LOGS feature) ---------------------------------------

  listAuditLogs: (params: AuditLogQuery) => {
    const qs = new URLSearchParams();
    if (params.user_id) qs.set("user_id", params.user_id);
    if (params.action) qs.set("action", params.action);
    if (params.resource_type) qs.set("resource_type", params.resource_type);
    if (params.from) qs.set("from", params.from);
    if (params.to) qs.set("to", params.to);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.offset) qs.set("offset", String(params.offset));
    return request<Page<AuditEventInfo>>(`/admin/audit-logs?${qs.toString()}`);
  },

  // --- KMS (EXTERNAL_KMS feature) --------------------------------------------

  kmsHealth: () =>
    request<{ status: string; provider: string }>("/admin/kms/health"),

  // --- Community Node Registry (MS4 Slice 4E) --------------------------------

  searchRegistry: (q?: string) => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    const qs = params.toString();
    return request<RegistrySearchResult>(
      `/node-registry/search${qs ? `?${qs}` : ""}`,
    );
  },

  getRegistryPackage: (id: string) =>
    request<RegistryPackage>(`/node-registry/packages/${encodeURIComponent(id)}`),

  installRegistryPackage: (body: {
    package_id: string;
    environment_id: string;
  }) =>
    request<RegistryInstallResponse>("/node-registry/install", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getRegistryInstallStatus: (installId: string) =>
    request<RegistryInstallStatus>(`/node-registry/installs/${installId}`),

  // --- Agentic Build (MS4 Slice 4D) -----------------------------------------

  /** Start an agentic build loop and return an EventSource for SSE events. */
  startAgenticBuild: (
    workflowId: string,
    body: { goal: string; test_data?: Record<string, unknown> | null; max_iterations?: number },
    onEvent: (event: AgenticBuildEvent) => void,
    onError: (error: Error) => void,
    onClose: () => void,
  ): EventSource => {
    const url = `${BASE}/workflows/${workflowId}/agentic-build`;
    const headers = authHeaders();
    const controller = new AbortController();

    // SSE via EventSource doesn't support POST + custom headers directly, so
    // we POST with fetch and consume the body as a ReadableStream.
    void (async () => {
      try {
        const resp = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...headers,
          },
          body: JSON.stringify({
            goal: body.goal,
            test_data: body.test_data ?? null,
            max_iterations: body.max_iterations ?? 5,
          }),
          signal: controller.signal,
        });
        if (!resp.ok) {
          let detail = resp.statusText;
          try {
            const json = (await resp.json()) as { detail?: unknown };
            if (json.detail) detail = String(json.detail);
          } catch {
            /* ignore */
          }
          onError(new Error(`${resp.status} ${detail}`));
          return;
        }
        const reader = resp.body?.getReader();
        if (!reader) {
          onError(new Error("Response body is not readable"));
          return;
        }
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const parsed = JSON.parse(line.slice(6)) as AgenticBuildEvent;
                onEvent(parsed);
              } catch {
                // ignore malformed JSON chunks
              }
            }
          }
        }
      } catch (err) {
        if ((err as Error)?.name === "AbortError") return;
        onError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        onClose();
      }
    })();

    return {
      close: () => controller.abort(),
    } as EventSource;
  },
};

export async function uploadArtifact(file: File): Promise<ArtifactInfo> {
  // Same auth headers as request() — in cookie-auth mode the CSRF header is
  // required or the server's CSRF gate rejects the upload with 403.
  const headers = authHeaders();
  const body = new FormData();
  body.append("file", file);
  const resp = await safeFetch(`${BASE}/artifacts/upload`, {
    method: "POST",
    headers,
    body,
  });
  if (resp.status === 401) {
    handleUnauthorized();
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
  /** Multi-tenancy: per-org active counts; "quota_parked" = queued entries
   *  held back by the org's concurrency cap. Absent when MT is off. */
  by_org?: Record<string, Record<string, number>> | null;
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

export type RunApprovalDecision = "approve" | "reject" | "approve_all";

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

function _runEventsBaseUrl(runId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/runs/${runId}`;
}

function _workflowEventsBaseUrl(workflowId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/workflows/${workflowId}`;
}

/** @deprecated Use subscribeToRunEvents — it handles ticket-based auth. */
export function runEventsUrl(runId: string): string {
  const token = getToken();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${_runEventsBaseUrl(runId)}${query}`;
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

  async function open(): Promise<void> {
    if (closedByCaller) return;
    // Fetch a single-use WS ticket so the token doesn't appear in server
    // access logs (?token= query param is visible there; ?ticket= is not).
    // A ticket is needed for BOTH auth modes: bearer (localStorage token)
    // and cookie sessions (httpOnly cookie — getToken() is null but the
    // ticket endpoint authenticates via the cookie). Only a fully anonymous
    // client (auth disabled in dev) skips it; calling it anonymously would
    // 401 and trip the global unauthorized handler.
    let wsUrl = _runEventsBaseUrl(runId);
    const bearerToken = getToken();
    const hasSession = Boolean(bearerToken) || getUser() !== null;
    if (hasSession) {
      try {
        const { ticket } = await request<{ ticket: string }>("/auth/ws-ticket", {
          method: "POST",
        });
        wsUrl += `?ticket=${encodeURIComponent(ticket)}`;
      } catch {
        // Legacy fallback — only possible with a bearer token; a cookie
        // session has nothing to put in the URL and connects unauthenticated
        // (the server will close 1008 and route back to login).
        if (bearerToken) wsUrl += `?token=${encodeURIComponent(bearerToken)}`;
      }
    }
    socket = new WebSocket(wsUrl);
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
        // 1008 means the session expired/was rejected mid-stream — route the
        // user back to login instead of leaving the rest of the UI in a stale
        // signed-in state until the next REST call 401s (FE-4).
        if (event.code === 1008) handleUnauthorized();
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

export function subscribeToWorkflowEvents(
  workflowId: string,
  handlers: {
    onMessage: (data: WorkflowEvent) => void;
    onReconnecting?: (attempt: number) => void;
    onClosed?: () => void;
  },
): RunStreamHandle {
  let socket: WebSocket | null = null;
  let attempt = 0;
  let closedByCaller = false;

  async function open(): Promise<void> {
    if (closedByCaller) return;
    let wsUrl = _workflowEventsBaseUrl(workflowId);
    const params = new URLSearchParams();
    const orgId = getOrgId();
    if (orgId) params.set("org_id", orgId);
    const bearerToken = getToken();
    const hasSession = Boolean(bearerToken) || getUser() !== null;
    if (hasSession) {
      try {
        const { ticket } = await request<{ ticket: string }>("/auth/ws-ticket", {
          method: "POST",
        });
        params.set("ticket", ticket);
      } catch {
        if (bearerToken) params.set("token", bearerToken);
      }
    }
    const query = params.toString();
    if (query) wsUrl += `?${query}`;
    socket = new WebSocket(wsUrl);
    socket.onmessage = (event) => {
      attempt = 0;
      try {
        handlers.onMessage(JSON.parse(event.data as string) as WorkflowEvent);
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
      if (event.code === 1000 || event.code === 1008) {
        if (event.code === 1008) handleUnauthorized();
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

  health: () => request<RunnerFleetHealth>("/runner-pools/health"),

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

  drainRunner: (poolId: string, runnerId: string, draining: boolean) =>
    request<RunnerInfo>(`/runner-pools/${poolId}/runners/${runnerId}/drain`, {
      method: "POST",
      body: JSON.stringify({ draining }),
    }),

  restartRunner: (poolId: string, runnerId: string) =>
    request<{ runner_id: string; log: string }>(
      `/runner-pools/${poolId}/runners/${runnerId}/restart`,
      { method: "POST" }
    ),

  cleanupGhosts: (poolId: string) =>
    request<{ pool_id: string; deleted: number }>(
      `/runner-pools/${poolId}/cleanup-ghosts`,
      { method: "POST" }
    ),

  getRunHistory: (poolId: string, hours = 24, buckets = 24) =>
    request<RunHistoryBucket[]>(
      `/runner-pools/${poolId}/run-history?hours=${hours}&buckets=${buckets}`
    ),

  getRecentRuns: (poolId: string, limit = 50) =>
    request<RecentRun[]>(`/runner-pools/${poolId}/recent-runs?limit=${limit}`),
};
