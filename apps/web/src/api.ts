import type {
  AuditEvent,
  AuthState,
  ArtifactInfo,
  AiWorkflowDraftResponse,
  Credential,
  CredentialTestResponse,
  Environment,
  NodeManifest,
  PinnedItem,
  CodeModule,
  CodeModuleFunctionPreview,
  Deployment,
  DeploymentCreate,
  DeploymentUpdate,
  RunInfo,
  RunListItem,
  SystemSettings,
  UserAdminInfo,
  UserInfo,
  WorkflowDetail,
  WorkflowGraph,
  WorkflowPublishResponse,
  WorkflowSummary,
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
  const resp = await fetch(BASE + path, { ...init, headers });
  if (resp.status === 401) {
    setToken(null);
    setUser(null);
    unauthorizedHandler?.();
    throw new Error("401 Unauthorized");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = (await resp.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* response had no JSON body */
    }
    throw new Error(`${resp.status} ${detail}`);
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
}

export const api = {
  nodes: () => request<NodeManifest[]>("/nodes"),
  listWorkflows: () => request<WorkflowSummary[]>("/workflows"),
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
  aiWorkflowDraft: (
    id: string,
    body: { prompt: string; apply?: boolean },
  ) =>
    request<AiWorkflowDraftResponse>(`/workflows/${id}/ai-draft`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteWorkflow: (id: string) =>
    request<void>(`/workflows/${id}`, { method: "DELETE" }),

  listEnvironments: () => request<Environment[]>("/environments"),
  createEnvironment: (body: {
    name: string;
    python_version?: string;
    packages?: string[];
    description?: string;
    runner_pool_size?: number;
    runner_pool_max?: number | null;
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
  getRun: (runId: string) => request<RunInfo>(`/runs/${runId}`),
  listRunArtifacts: (runId: string) =>
    request<ArtifactInfo[]>(`/runs/${runId}/artifacts`),
  getArtifact: (artifactId: string) =>
    request<ArtifactInfo>(`/artifacts/${artifactId}`),
  deleteArtifact: (artifactId: string) =>
    request<void>(`/artifacts/${artifactId}`, { method: "DELETE" }),
  listRuns: (id: string) => request<RunInfo[]>(`/workflows/${id}/runs`),
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
    return request<RunListItem[]>(`/runs${query ? `?${query}` : ""}`);
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
  }) =>
    request<CodeModule>("/code-modules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateCodeModule: (
    id: string,
    body: { name?: string; contents?: string },
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
  deleteCredential: (id: string) =>
    request<void>(`/credentials/${id}`, { method: "DELETE" }),

  listAudit: () => request<AuditEvent[]>("/audit"),

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
};

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
