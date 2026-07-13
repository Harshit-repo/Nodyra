export interface MCPConnection {
  id: string;
  name: string;
  url: string;
  transport: string;
  auth_type: string;
  auth_secret: string;
  enabled: boolean;
  allowed_tools: string[] | null;
  tool_cache: Record<string, unknown> | null;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MCPConnectionCreate {
  name: string;
  url: string;
  transport: string;
  auth_type: string;
  auth_secret?: string;
  auth_header_name?: string;
  enabled?: boolean;
  allowed_tools?: string[] | null;
}

export interface MCPConnectionUpdate {
  name?: string;
  url?: string;
  transport?: string;
  auth_type?: string;
  auth_secret?: string;
  auth_header_name?: string;
  enabled?: boolean;
  allowed_tools?: string[] | null;
}

export interface MCPToolInfo {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface MCPToolCallAuditInfo {
  id: string;
  connection_id: string;
  tool: string;
  ok: boolean;
  org_id: string | null;
  actor_id: string | null;
  actor_email: string | null;
  run_id: string | null;
  duration_ms: number | null;
  error: string | null;
  created_at: string;
}

export interface ApiTokenCreate {
  name: string;
  scopes: string[];
  expires_in_days: number;
}

export interface ApiTokenScopeInfo {
  scope: string;
  minimum_role: string;
  grantable: boolean;
}

export interface ApiTokenInfo {
  id: string;
  org_id: string;
  name: string;
  token_prefix: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface ApiTokenCreated extends ApiTokenInfo {
  token: string;
}

export interface SSOConfig {
  id?: string;
  org_id?: string;
  org_slug?: string;
  protocol: "oidc" | "saml";
  client_id?: string;
  client_secret?: string;
  discovery_url?: string;
  idp_entity_id?: string;
  idp_sso_url?: string;
  idp_certificate?: string;
  email_domain?: string;
  attribute_map?: Record<string, string>;
  jit_provisioning?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface SSODetectResponse {
  has_sso: boolean;
  org_slug?: string;
}

export interface SSOTestResult {
  status: string;
  detail: string;
}

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
  | "ai_guardrail"
  | "ai_subagent";

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
  notes: string;
  created_at: string;
  published: boolean;
  node_count: number;
}

export type DiffStatus = "added" | "removed" | "changed" | "unchanged";

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

export interface FolderInfo {
  id: string;
  name: string;
  color?: string | null;
  workflow_count: number;
  created_at: string;
  updated_at: string;
}

export type GithubSyncStatus = "synced" | "pending" | "conflict" | "error" | null;

export interface GithubSyncConfig {
  id: string;
  org_id: string;
  repo: string;
  base_path: string;
  main_branch: string;
  credential_id: string | null;
  webhook_url: string;
}

export interface GithubRepoValidation {
  accessible: boolean;
  error?: string | null;
  private?: boolean | null;
  default_branch?: string | null;
}

export interface GithubCreateRepoResponse {
  created: boolean;
  url: string;
  default_branch: string;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  active: boolean;
  version: number;
  published_version: number;
  graph_revision: number;
  has_unpublished_changes: boolean;
  node_count: number;
  environment_id: string | null;
  folder_id?: string | null;
  error_workflow_id?: string | null;
  last_run_id?: string | null;
  last_run_status?: string | null;
  last_run_started_at?: string | null;
  last_run_finished_at?: string | null;
  provider_trigger_counts?: ProviderTriggerStatusCounts;
  updated_at: string;
  created_at: string;
  github_sync_status?: GithubSyncStatus;
}

export interface WorkflowTemplateSummary {
  id: string;
  name: string;
  description: string;
  tags: string[];
}

export interface WorkflowDetail {
  id: string;
  name: string;
  active: boolean;
  version: number;
  published_version: number;
  graph_revision: number;
  has_unpublished_changes: boolean;
  environment_id: string | null;
  default_runner_pool_id?: string | null;
  error_workflow_id?: string | null;
  error_alerts?: Record<string, unknown>;
  run_timeout_seconds?: number | null;
  execution_mode?: "inherit" | "sandboxed" | "standard";
  sandbox_resources?: { memory_mb?: number; cpu?: number; tmpfs_mb?: number } | null;
  requirements: string[];
  mcp_enabled?: boolean;
  mcp_tool_name?: string | null;
  mcp_description?: string | null;
  mcp_parameters_schema?: Record<string, unknown> | null;
  provider_trigger_counts?: ProviderTriggerStatusCounts;
  graph: WorkflowGraph;
  created_at: string;
  updated_at: string;
  github_sync_status?: GithubSyncStatus;
}

export interface WorkflowEvent {
  type: string;
  workflow_id: string;
  org_id?: string;
  workflow_name?: string;
  graph_revision?: number;
  published_version?: number;
  origin?: string;
  operation?: string;
  patch?: Record<string, unknown> | null;
  ts?: string;
  node_count?: number;
  edge_count?: number;
  node_id?: string;
  source?: string;
  target?: string;
}

export interface WorkflowRevisionInfo {
  id: string;
  workflow_id: string;
  graph_revision: number;
  origin: string;
  operation: string;
  summary: string;
  patch?: Record<string, unknown> | null;
  actor_id?: string | null;
  actor_email?: string | null;
  created_at: string;
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
  interpreter: string;
  runtime_flags: Record<string, boolean>;
  build_job_id?: string | null;
  build_job_status?: string | null;
  created_at: string;
  updated_at: string;
}

export interface EnvironmentBuildJob {
  id: string;
  environment_id: string;
  reason: string;
  status: string;
  attempts: number;
  max_attempts: number;
  package_snapshot: string[];
  packages_hash: string;
  python_version: string;
  backend: string;
  interpreter: string;
  last_error: string | null;
  requested_by_email: string | null;
  lease_owner: string | null;
  lease_expires_at: string | null;
  available_at: string;
  started_at: string | null;
  finished_at: string | null;
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
  required_labels?: Record<string, string> | null;
  mode: string;
  status: string;
  /** Run-level failure reason for failures not attributable to a single
   *  node (e.g. graph cycle). Populated by GET /runs/{id}. */
  error?: string | null;
  trigger_type: string;
  started_at: string;
  finished_at: string | null;
  node_runs: NodeRunResult[];
}

export interface ArtifactInfo {
  id: string;
  run_id: string | null;
  node_id: string | null;
  name: string;
  kind: string;
  content_type: string;
  size_bytes: number;
  checksum_sha256?: string | null;
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
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface GenerateNodeResponse {
  code: string;
  node_id: string;
  node_name: string;
  input_ports: Record<string, string>;
  output_ports: Record<string, string>;
  is_template: boolean;
  warnings: string[];
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

// ---------------------------------------------------------------------------
// Agentic build loop (MS4 Slice 4D)
// ---------------------------------------------------------------------------

export interface AgenticBuildRequest {
  goal: string;
  test_data?: Record<string, unknown> | null;
  max_iterations?: number;
  failure_context?: AgenticBuildFailureContext | null;
}

export interface AgenticBuildFailureContext {
  failed_run_id?: string | null;
  failed_node_id?: string | null;
  run_error?: string | null;
  node_errors?: Record<string, string>;
  graph?: WorkflowGraph | null;
}

export interface AgenticBuildIterationStart {
  type: "iteration_start";
  iteration: number;
  action: "draft" | "fix";
}

export interface AgenticBuildGraphUpdated {
  type: "graph_updated";
  graph: WorkflowGraph;
  explanation: string;
}

export interface AgenticBuildRunStarted {
  type: "run_started";
  run_id: string;
}

export interface AgenticBuildRunFailed {
  type: "run_failed";
  run_id: string;
  errors: Array<{ node_id: string; error: string }>;
}

export interface AgenticBuildFixPlanned {
  type: "fix_planned";
  target_nodes: string[];
  diagnosis: string;
}

export interface AgenticBuildConverged {
  type: "converged";
  iterations: number;
  final_graph: WorkflowGraph;
}

export interface AgenticBuildMaxIterations {
  type: "max_iterations_reached";
  best_graph: WorkflowGraph;
  remaining_errors: Array<{ node_id: string; error: string }>;
}

export interface AgenticBuildError {
  type: "error";
  message: string;
}

export type AgenticBuildEvent =
  | AgenticBuildIterationStart
  | AgenticBuildGraphUpdated
  | AgenticBuildRunStarted
  | AgenticBuildRunFailed
  | AgenticBuildFixPlanned
  | AgenticBuildConverged
  | AgenticBuildMaxIterations
  | AgenticBuildError;

export interface AuditEvent {
  id: string;
  action: string;
  target_type: string;
  target_id: string;
  detail: string;
  created_at: string;
}

export interface AuditEventInfo {
  id: string;
  action: string;
  target_type: string;
  target_id: string;
  detail: string;
  actor_id: string | null;
  actor_email: string | null;
  session_id: string | null;
  actor_type: string;
  created_at: string;
}

export interface CustomRoleInfo {
  id: string;
  org_id: string;
  name: string;
  permissions: string[];
  created_at: string;
}

export interface CustomRoleCreate {
  name: string;
  permissions: string[];
}

export interface CustomRoleUpdate {
  name?: string;
  permissions?: string[];
}

export interface AuditLogQuery {
  user_id?: string;
  action?: string;
  resource_type?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
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

export interface RunHistoryBucket {
  bucket_start: string;
  success: number;
  error: number;
  total: number;
  avg_duration_seconds: number | null;
}

export interface RecentRun {
  id: string;
  workflow_id: string;
  status: string;
  runner_id: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface DockerWorkerConfig {
  docker_host?: string;
  docker_network?: string;
  docker_api_url?: string;
  docker_runner?: {
    cpu?: number;
    memory_mb?: number;
    pids?: number;
    max_concurrent_runs?: number;
    sandbox?: boolean;
  };
  docker_autoscale?: {
    enabled?: boolean;
    min_runners?: number;
    max_runners?: number;
    idle_seconds?: number;
  };
}

export interface RunnerPoolInfo {
  id: string;
  name: string;
  provider: string;
  provider_config: Record<string, unknown>;
  max_concurrent_runs: number;
  runner_count: number;
  online_count: number;
  ghost_count: number;
  aws_secret_configured: boolean;
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
  token_expires_at: string | null;
  ssh_host: string | null;
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
  label_mismatch_queued: number;
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

// ── Community Node Registry (MS4 Slice 4E) ────────────────────────────

export interface RegistryPackage {
  id: string;
  name: string;
  description: string;
  author: string;
  version: string;
  nodes: string[];
  install_url: string;
  pypi_package: string;
}

export interface RegistrySearchResult {
  packages: RegistryPackage[];
}

export interface RegistryInstallResponse {
  install_id: string;
  status: string;
}

export interface RegistryInstallStatus {
  install_id: string;
  status: string;
  error: string | null;
  environment_id: string;
  package_id: string;
}
