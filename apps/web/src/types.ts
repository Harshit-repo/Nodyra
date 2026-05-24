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
}

export interface PortSpec {
  name: string;
  description: string;
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

export interface WorkflowSummary {
  id: string;
  name: string;
  active: boolean;
  version: number;
  node_count: number;
  environment_id: string | null;
  updated_at: string;
}

export interface WorkflowDetail {
  id: string;
  name: string;
  active: boolean;
  version: number;
  environment_id: string | null;
  graph: WorkflowGraph;
  created_at: string;
  updated_at: string;
}

export interface Environment {
  id: string;
  name: string;
  is_global: boolean;
  python_version: string;
  packages: string[];
  status: string;
  status_detail: string;
  created_at: string;
  updated_at: string;
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
  mode: string;
  status: string;
  trigger_type: string;
  started_at: string;
  finished_at: string | null;
  node_runs: NodeRunResult[];
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

export interface CodeModuleFunctionPreview {
  registered: string[];
  skipped: { name: string; reason: string }[];
  syntax_error: string | null;
}

export interface RunListItem {
  id: string;
  workflow_id: string;
  workflow_name: string | null;
  workflow_version: number;
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
  keys: string[];
  created_at: string;
  updated_at: string;
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
  role: string;
}

export interface AuthState {
  auth_required: boolean;
  signed_in: boolean;
  user: UserInfo | null;
}
