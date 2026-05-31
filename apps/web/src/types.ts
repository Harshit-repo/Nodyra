export interface CredentialParamSpec {
  type: string;
  key: string;
  label: string;
  fields: string[];
  multi?: boolean;
}

export interface ParamSpec {
  name: string;
  type: string;
  required: boolean;
  default: unknown;
  description: string;
  placeholder: string;
  choices: unknown[] | null;
  multiline: boolean;
  key_value: boolean;
  credential?: CredentialParamSpec | null;
}

export interface PortSpec {
  name: string;
  description: string;
  data_kind?: "any" | "control" | "dataset" | "artifact" | "file";
}

export interface NodeManifest {
  id: string;
  name: string;
  category: string;
  version: string;
  description: string;
  icon: string | null;
  inputs: PortSpec[];
  params: ParamSpec[];
  outputs: PortSpec[];
}

export interface GraphNode {
  id: string;
  type: string;
  params: Record<string, unknown>;
  position: { x: number; y: number };
  disabled: boolean;
  outputs_override: string[] | null;
  on_error: string;
  retry_on_fail: boolean;
  retries: number;
  retry_wait_seconds: number;
  retry_backoff: boolean;
  always_output_data: boolean;
  timeout_seconds: number | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  source_output: string;
  target: string;
  target_input: string;
}

export interface WorkflowGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface WorkflowVersionInfo {
  id: string;
  version: number;
  graph: WorkflowGraph;
  created_at: string;
  published: boolean;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  active: boolean;
  version: number;
  published_version: number;
  has_unpublished_changes: boolean;
  node_count: number;
  environment_id: string | null;
  error_workflow_id?: string | null;
  last_run_id?: string | null;
  last_run_status?: string | null;
  last_run_started_at?: string | null;
  last_run_finished_at?: string | null;
  updated_at: string;
}

export interface WorkflowDetail {
  id: string;
  name: string;
  active: boolean;
  version: number;
  published_version: number;
  has_unpublished_changes: boolean;
  environment_id: string | null;
  error_workflow_id?: string | null;
  error_alerts?: Record<string, unknown>;
  run_timeout_seconds?: number | null;
  graph: WorkflowGraph;
  created_at: string;
  updated_at: string;
}

export interface WorkflowPublishResponse {
  workflow_id: string;
  workflow_version_id: string;
  version: number;
  updated_deployments: number;
}

export interface Environment {
  id: string;
  name: string;
  is_global: boolean;
  python_version: string;
  packages: string[];
  status: string;
  status_detail: string;
  description: string;
  runner_pool_size: number;
  runner_pool_max: number | null;
  effective_pool_max: number;
  worker_rss_estimate_bytes: number | null;
  created_at: string;
  updated_at: string;
}

export interface SystemSettings {
  max_concurrent_runs: number;
  runner_idle_seconds: number;
  run_retention_days: number;
  run_retention_max_per_workflow: number;
  max_output_bytes: number;
  max_artifact_bytes: number;
  max_artifacts_per_run: number;
  app_timezone: string;
  worker_rss_soft_budget_bytes: number;
}

export interface NodeRunResult {
  node_id: string;
  status: string;
  output: unknown;
  error: string | null;
  logs?: string[] | null;
  debug?: NodeRunDebug | null;
  started_at?: number | null;
  finished_at?: number | null;
  duration_ms?: number | null;
}

export interface NodeVariableInfo {
  name: string;
  type: string;
  summary?: string;
  shape?: number[];
  length?: number;
  columns?: string[];
  dtypes?: Record<string, string>;
  preview?: unknown;
}

export interface NodeRunDebug {
  variables?: NodeVariableInfo[];
}

export interface RunInfo {
  id: string;
  workflow_id: string;
  workflow_version: number;
  workflow_version_id?: string | null;
  deployment_id?: string | null;
  triggered_by_error_run_id?: string | null;
  mode: string;
  status: string;
  trigger_type: string;
  started_at: string;
  finished_at: string | null;
  node_runs: NodeRunResult[];
}

export interface ArtifactInfo {
  id: string;
  run_id: string;
  node_id: string;
  name: string;
  kind: string;
  content_type: string;
  size_bytes: number;
  metadata: Record<string, unknown>;
  preview: unknown;
  created_at: string;
}

export interface DatasetQueryResult {
  columns: { name: string; type: string }[];
  rows: Record<string, unknown>[];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
}

export interface Deployment {
  id: string;
  workflow_id: string;
  name: string;
  schedule_cron: string;
  schedule_interval: string;
  schedule_every: number;
  schedule_tz: string;
  default_parameters: Record<string, unknown>;
  active: boolean;
  environment_id: string | null;
  workflow_version_id: string | null;
  workflow_version: number | null;
  error_workflow_id: string | null;
  error_alerts: Record<string, unknown>;
  last_fired: string | null;
  created_at: string;
  updated_at: string;
}

export interface DeploymentCreate {
  workflow_id: string;
  name: string;
  schedule_cron?: string;
  schedule_interval?: string;
  schedule_every?: number;
  schedule_tz?: string;
  default_parameters?: Record<string, unknown>;
  active?: boolean;
  environment_id?: string | null;
  workflow_version_id?: string | null;
  error_workflow_id?: string | null;
  error_alerts?: Record<string, unknown>;
  approve_unsafe_nodes?: boolean;
}

export interface DeploymentUpdate {
  name?: string;
  schedule_cron?: string;
  schedule_interval?: string;
  schedule_every?: number;
  schedule_tz?: string;
  default_parameters?: Record<string, unknown>;
  active?: boolean;
  environment_id?: string | null;
  workflow_version_id?: string | null;
  error_workflow_id?: string | null;
  error_alerts?: Record<string, unknown>;
  approve_unsafe_nodes?: boolean;
}

export interface CodeModule {
  id: string;
  scope: string;
  workflow_id: string | null;
  environment_id: string | null;
  name: string;
  contents: string;
  created_at: string;
  updated_at: string;
}

export interface CodeModuleFunctionShape {
  name: string;
  inputs: string[];
  params: string[];
}

export interface CodeModuleFunctionPreview {
  registered: string[];
  functions: CodeModuleFunctionShape[];
  skipped: { name: string; reason: string }[];
  syntax_error: string | null;
  imports: string[];
  missing_in_env: string[];
  environment_id: string | null;
  environment_name: string | null;
}

export interface RunListItem {
  id: string;
  workflow_id: string;
  workflow_name: string | null;
  workflow_version: number;
  workflow_version_id?: string | null;
  deployment_id?: string | null;
  triggered_by_error_run_id?: string | null;
  mode: string;
  status: string;
  trigger_type: string;
  started_at: string;
  finished_at: string | null;
}

export interface RunEvent {
  type: string;
  node_id?: string;
  status?: string;
  outputs?: unknown;
  error?: string;
  run_id?: string;
  logs?: string[];
  debug?: NodeRunDebug | null;
  started_at?: number | null;
  finished_at?: number | null;
  duration_ms?: number | null;
}

export interface Credential {
  id: string;
  name: string;
  type: string;
  scope: string;
  workflow_id: string | null;
  environment_id: string | null;
  runner_pool_id: string | null;
  description: string;
  keys: string[];
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CredentialTestResponse {
  ok: boolean;
  status: string;
  service: string;
  message: string;
  latency_ms: number;
  checked_at: string;
  details: Record<string, unknown>;
}

export type AiDraftMode = "draft" | "fix";
export type AiFixStrategy = "minimal" | "replacement";

export interface AiWorkflowDraftRequest {
  prompt: string;
  apply?: boolean;
  mode?: AiDraftMode;
  current_graph?: WorkflowGraph;
  failed_run_id?: string | null;
  failed_node_id?: string | null;
  error?: string | null;
  fix_strategy?: AiFixStrategy;
}

export interface AiWorkflowDraftResponse {
  workflow_id: string;
  graph: WorkflowGraph;
  assumptions: string[];
  missing_credentials: string[];
  required_packages: string[];
  explanation: string;
  mode: AiDraftMode;
  change_summary: string[];
  confidence: "low" | "medium" | "high";
  focus_node_id: string | null;
  planner: "llm" | "deterministic_fallback" | string;
}

export interface AuditEvent {
  id: string;
  action: string;
  target_type: string;
  target_id: string;
  detail: string;
  created_at: string;
}

export interface PinnedItem {
  node_id: string;
  payload: unknown;
  updated_at: string;
}

export interface UserInfo {
  id: string;
  email: string;
  name: string;
  company: string;
  role: string;
}

export interface UserAdminInfo extends UserInfo {
  created_at: string;
}

export interface AuthState {
  auth_required: boolean;
  signed_in: boolean;
  registration_open: boolean;
  user: UserInfo | null;
}

export interface RunnerPoolInfo {
  id: string;
  name: string;
  provider: string;
  provider_config: Record<string, unknown>;
  max_concurrent_runs: number;
  runner_count: number;
  online_count: number;
  created_at: string;
  updated_at: string;
}

export interface RunnerInfo {
  id: string;
  pool_id: string;
  name: string;
  status: string;
  capabilities: Record<string, unknown>;
  last_seen_at: string | null;
  current_runs: number;
  max_concurrent_runs: number;
  cached_env_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface RegistrationTokenResponse {
  token: string;
  runner_id: string;
  expires_at: string;
}

export interface RunBatchInfo {
  id: string;
  workflow_id: string;
  deployment_id: string | null;
  runner_pool_id: string | null;
  status: string;
  total_runs: number;
  succeeded_runs: number;
  failed_runs: number;
  cancelled_runs: number;
  created_at: string;
  finished_at: string | null;
}
