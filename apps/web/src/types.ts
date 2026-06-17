export interface CredentialParamSpec {
  type: string;
  key: string;
  label: string;
  fields: string[];
  multi?: boolean;
  test_service?: string | null;
}

export type NodeRole =
  | "executable"
  | "supplier"
  | "trigger"
  | "tool"
  | "output_parser";

export type PortDataKind =
  | "any"
  | "main"
  | "control"
  | "dataset"
  | "artifact"
  | "file"
  | "ai_language_model"
  | "ai_embedding_model"
  | "ai_memory"
  | "ai_tool"
  | "ai_output_parser"
  | "ai_retriever"
  | "ai_vector_store"
  | "ai_document_loader"
  | "ai_guardrail";

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
  /** Optional "Add option" group; null/absent = a core param shown by default. */
  group?: string | null;
  display_name?: string;
  display_when?: Record<string, unknown> | null;
  hide_when?: Record<string, unknown> | null;
  widget?: string;
  depends_on?: string[];
  load_options?: string | null;
  resource_mapper?: Record<string, unknown> | null;
  fixed_collection?: Record<string, unknown> | null;
  credential_type?: string | null;
  required_scopes?: string[];
  advanced?: boolean;
  documentation_url?: string;
  validation?: Record<string, unknown> | null;
}

export interface PortSpec {
  name: string;
  description: string;
  data_kind?: PortDataKind;
}

export interface SystemRequirement {
  name: string;
  apt?: string;
  brew?: string;
  windows?: string;
  dockerfile_hint?: string;
  note?: string;
}

export interface NodeManifest {
  id: string;
  name: string;
  category: string;
  version: string;
  description: string;
  icon: string | null;
  role?: NodeRole;
  hidden?: boolean;
  deprecated?: boolean;
  replacement_id?: string | null;
  usable_as_tool?: boolean;
  inputs: PortSpec[];
  params: ParamSpec[];
  outputs: PortSpec[];
  param_output_kinds?: Record<string, Record<string, string>>;
  requirements?: string[];
  system_requirements?: SystemRequirement[];
  // Present only on consolidated integration nodes (Google Sheets, Slack, …):
  // drives the editor's Resource → Operation selector.
  integration?: IntegrationManifest | null;
}

export interface IntegrationOperationManifest {
  id: string;
  name: string;
  description?: string;
}

export interface IntegrationResourceManifest {
  id: string;
  name: string;
  operations: IntegrationOperationManifest[];
}

export interface IntegrationManifest {
  provider: string;
  resources: IntegrationResourceManifest[];
}

export interface PackageUsageEntry {
  workflow_id: string;
  workflow_name: string;
  node_id: string;
  node_label: string;
}

export interface PackageUsagePackage {
  package: string;
  used_by: PackageUsageEntry[];
}

export interface PackageUsage {
  packages: PackageUsagePackage[];
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
  tool_mode?: boolean;
  tool_name?: string | null;
  tool_description?: string;
  label?: string;
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

export interface ProviderTriggerStatusCounts {
  total: number;
  active: number;
  activating: number;
  error: number;
  deleted: number;
}

export interface ProviderTriggerSubscription {
  id: string;
  workflow_id: string;
  workflow_version_id?: string | null;
  node_id: string;
  node_type: string;
  provider: string;
  trigger_key: string;
  status: string;
  external_id: string;
  callback_url: string;
  config: Record<string, unknown>;
  error: string;
  expires_at?: string | null;
  last_event_at?: string | null;
  created_at: string;
  updated_at: string;
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
  provider_trigger_counts?: ProviderTriggerStatusCounts;
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
  default_runner_pool_id?: string | null;
  error_workflow_id?: string | null;
  error_alerts?: Record<string, unknown>;
  run_timeout_seconds?: number | null;
  mcp_enabled?: boolean;
  mcp_tool_name?: string | null;
  mcp_description?: string | null;
  mcp_parameters_schema?: Record<string, unknown> | null;
  provider_trigger_counts?: ProviderTriggerStatusCounts;
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
  runner_pool_id: string | null;
  runner_pool_name: string | null;
  worker_rss_estimate_bytes: number | null;
  backend: string;
  backend_config: Record<string, unknown>;
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
  iteration_path?: number[] | null;
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
  include_undecorated: boolean;
  created_at: string;
  updated_at: string;
}

export interface CodeModuleFunctionShape {
  name: string;
  inputs: string[];
  params: string[];
  outputs: string[];
  decorated: boolean;
  wires: Record<string, string>;
}

export interface NodeSource {
  node_type: string;
  name: string;
  kind: "builtin" | "user";
  editable: boolean;
  module_id: string | null;
  func_name: string;
  source: string;
  fork_source: string;
}

export interface CodeModuleFunctionPreview {
  registered: string[];
  functions: CodeModuleFunctionShape[];
  skipped: { name: string; reason: string }[];
  syntax_error: string | null;
  explicit_mode: boolean;
  imports: string[];
  missing_in_env: string[];
  environment_id: string | null;
  environment_name: string | null;
}

export interface LintDiagnostic {
  line: number;
  column: number;
  code: string | null;
  message: string;
  severity: "error" | "warning";
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
  // Agent tool-call events (flat fields emitted by the engine) used to light up
  // the agent's connected model / memory / tool sub-nodes live on the canvas.
  agent_node_id?: string;
  tool_name?: string;
  tool_call_id?: string;
  tool_calls?: Array<{
    id?: string;
    name?: string;
    tool_call_id?: string;
    tool_name?: string;
  }>;
  step?: number;
  // Present on node_started / node_finished events emitted from inside a loop
  // body: the nested iteration index path (outer-to-inner). Lets the canvas
  // show per-node iteration progress instead of flickering once per iteration.
  iteration_path?: number[] | null;
  // node_chunk events: an incremental output fragment (e.g. an LLM token)
  // streamed while the node is still running, plus its stream channel.
  delta?: string;
  channel?: string;
  // node_chunk reset: a retry signals the client to discard the failed
  // attempt's streamed text before the new attempt streams.
  reset?: boolean;
}

export interface Credential {
  id: string;
  name: string;
  type: string;
  auth_method?: string | null;
  scope: string;
  workflow_id: string | null;
  environment_id: string | null;
  runner_pool_id: string | null;
  description: string;
  keys: string[];
  oauth_scopes?: string[];
  oauth_expires_at?: string | null;
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

export interface CredentialTypeFieldInfo {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  placeholder: string;
  help: string;
}

export interface OAuthCredentialTypeInfo {
  auth_url: string;
  token_url: string;
  scopes: string[];
  authorization_params: Record<string, string>;
}

export interface CredentialTypeInfo {
  id: string;
  name: string;
  provider: string;
  auth_method: string;
  fields: CredentialTypeFieldInfo[];
  oauth: OAuthCredentialTypeInfo | null;
  test_service: string | null;
  documentation_url: string;
  default_scopes: string[];
}

export interface CredentialOAuthStartResponse {
  authorization_url: string;
  state: string;
  credential_type: string;
  redirect_uri: string;
  scopes: string[];
  expires_at: string;
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
  planner_provider?: string | null;
  planner_model?: string | null;
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
  multi_tenancy: boolean;
  edition?: string;
  entitlements?: string[];
  limits?: Record<string, number>;
  license_notice?: string | null;
  user: UserInfo | null;
}

export interface LicenseInfo {
  edition: string;
  customer: string | null;
  expires_at: number | null;
  entitlements: string[];
  limits: Record<string, number>;
  notice: string | null;
}

export interface OrgInfo {
  id: string;
  name: string;
  slug: string;
  status: string;
  /** The signed-in user's role within this org. */
  role: string | null;
}

export interface OrgMemberInfo {
  user_id: string;
  email: string;
  name: string;
  role: string;
}

export interface OrgSettingsInfo {
  org_id: string;
  max_concurrent_runs: number;
  executions_per_day: number;
  max_map_width: number;
  max_loop_iterations: number;
  max_inflight_subworkflows: number;
  storage_quota_bytes: number;
  /** Field names whose value is an org override (vs inherited default). */
  overridden: string[];
}

export interface OrgUsageDay {
  day: string;
  runs: number;
  compute_seconds: number;
  node_runs: number;
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

export interface RunnerPoolHealth {
  pool_id: string;
  provider: string;
  queue_depth: number;
  oldest_queued_seconds: number | null;
  capacity_used: number;
  capacity_total: number;
  online_count: number;
  runner_count: number;
  success_24h: number | null;
  dispatcher_reachable: boolean;
}

export interface FleetSummary {
  runners_online: number;
  runners_total: number;
  queue_depth: number;
  in_flight: number;
  providers_dispatchable: string[];
  providers_stuck: string[];
}

export interface RunnerFleetHealth {
  fleet: FleetSummary;
  pools: RunnerPoolHealth[];
}

export interface RegistrationTokenResponse {
  token: string;
  runner_id: string;
  expires_at: string;
  /** URL the runner should dial back to (from the API, not the SPA origin). */
  api_url: string;
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

export interface ChatTurnResponse {
  run_id: string | null;
  reply: string;
  session_id: string;
  status: "success" | "error" | "timeout";
}

export interface ChatPublicConfig {
  workflow_id: string;
  title: string;
  placeholder: string;
  initial_message: string;
  require_login: boolean;
}
