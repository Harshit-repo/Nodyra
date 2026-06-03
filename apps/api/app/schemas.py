"""Pydantic request/response schemas for the API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from noodle.models import WorkflowGraph


class PageResponse[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None
    environment_id: str | None = None
    default_runner_pool_id: str | None = None
    graph: WorkflowGraph | None = None
    error_workflow_id: str | None = None
    error_alerts: dict[str, Any] | None = None
    allow_concurrent: bool | None = None
    # Per-workflow wall-clock cap (seconds) for a run. None leaves it unset
    # (falls back to the server default); 0 disables the cap for this workflow.
    run_timeout_seconds: float | None = Field(default=None, ge=0)


class ProviderTriggerStatusCounts(BaseModel):
    total: int = 0
    active: int = 0
    activating: int = 0
    error: int = 0
    deleted: int = 0


class ProviderTriggerSubscriptionInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    workflow_version_id: str | None = None
    node_id: str
    node_type: str
    provider: str
    trigger_key: str
    status: str
    external_id: str
    callback_url: str
    config: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    expires_at: datetime | None = None
    last_event_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowSummary(BaseModel):
    id: str
    name: str
    active: bool
    version: int
    published_version: int
    has_unpublished_changes: bool
    node_count: int
    environment_id: str | None
    error_workflow_id: str | None = None
    last_run_id: str | None = None
    last_run_status: str | None = None
    last_run_started_at: datetime | None = None
    last_run_finished_at: datetime | None = None
    provider_trigger_counts: ProviderTriggerStatusCounts = Field(
        default_factory=ProviderTriggerStatusCounts
    )
    updated_at: datetime


class WorkflowDetail(BaseModel):
    id: str
    name: str
    active: bool
    version: int
    published_version: int
    has_unpublished_changes: bool
    environment_id: str | None
    error_workflow_id: str | None = None
    error_alerts: dict[str, Any] = Field(default_factory=dict)
    allow_concurrent: bool = True
    run_timeout_seconds: float | None = None
    provider_trigger_counts: ProviderTriggerStatusCounts = Field(
        default_factory=ProviderTriggerStatusCounts
    )
    graph: WorkflowGraph
    created_at: datetime
    updated_at: datetime


class WorkflowVersionInfo(BaseModel):
    id: str
    version: int
    notes: str = ""
    created_at: datetime


class WorkflowPublishRequest(BaseModel):
    notes: str = ""
    update_deployments: bool = False


class WorkflowPublishResponse(BaseModel):
    workflow_id: str
    workflow_version_id: str
    version: int
    updated_deployments: int = 0


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    python_version: str = "3.12"
    packages: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=2000)
    runner_pool_size: int = Field(default=1, ge=0, le=32)
    runner_pool_max: int | None = Field(default=None, ge=1, le=64)
    runner_pool_id: str | None = None


class EnvironmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    runner_pool_size: int | None = Field(default=None, ge=0, le=32)
    runner_pool_max: int | None = Field(default=None, ge=1, le=64)
    # Sentinel-free: send null to unbind, omit to leave unchanged.
    runner_pool_id: str | None = Field(default=None)
    runner_pool_set: bool = Field(default=False)


class PackageRequest(BaseModel):
    package: str = Field(min_length=1, max_length=200)


class EnvironmentInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    is_global: bool
    python_version: str
    packages: list[str]
    status: str
    status_detail: str
    description: str = ""
    runner_pool_size: int = 1
    runner_pool_max: int | None = None
    effective_pool_max: int = 1
    runner_pool_id: str | None = None
    runner_pool_name: str | None = None
    worker_rss_estimate_bytes: int | None = None
    created_at: datetime
    updated_at: datetime


class SystemSettingsInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    max_concurrent_runs: int
    runner_idle_seconds: int
    run_retention_days: int
    run_retention_max_per_workflow: int
    max_output_bytes: int
    max_artifact_bytes: int
    max_artifacts_per_run: int
    app_timezone: str
    worker_rss_soft_budget_bytes: int = 0


class SystemSettingsUpdate(BaseModel):
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=1024)
    runner_idle_seconds: int | None = Field(default=None, ge=0, le=86_400)
    run_retention_days: int | None = Field(default=None, ge=0, le=3650)
    run_retention_max_per_workflow: int | None = Field(default=None, ge=0, le=100_000)
    max_output_bytes: int | None = Field(default=None, ge=0, le=10 * 1024 * 1024)
    max_artifact_bytes: int | None = Field(default=None, ge=0, le=10 * 1024 * 1024 * 1024)
    max_artifacts_per_run: int | None = Field(default=None, ge=0, le=10_000)
    app_timezone: str | None = Field(default=None, max_length=64)
    worker_rss_soft_budget_bytes: int | None = Field(
        default=None, ge=0, le=10 * 1024 * 1024 * 1024 * 1024
    )


class RunRequest(BaseModel):
    mode: str = "manual"
    targets: list[str] | None = None
    cache: dict[str, dict[str, Any]] | None = None
    parameters: dict[str, Any] | None = None
    trigger_node_id: str | None = None
    # Convenience alias for manual triggers — when the caller posts
    # ``{"data": {...}}`` we treat it as ``parameters`` so the manual_trigger
    # node's output mirrors the request body without forcing clients to
    # learn the engine's internal naming.
    data: dict[str, Any] | None = None


class RunCreated(BaseModel):
    run_id: str

    @computed_field  # type: ignore[misc]
    @property
    def id(self) -> str:
        return self.run_id


class RunCancelResponse(BaseModel):
    run_id: str
    status: str


_INTERNAL_ARTIFACT_FIELDS = {"storage_key", "storage_backend"}


def _strip_storage_fields(value: Any) -> Any:
    """Recursively remove internal artifact storage fields from a value tree."""
    if isinstance(value, dict):
        return {
            k: _strip_storage_fields(v)
            for k, v in value.items()
            if k not in _INTERNAL_ARTIFACT_FIELDS
        }
    if isinstance(value, list):
        return [_strip_storage_fields(v) for v in value]
    return value


class NodeRunInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    status: str
    output: Any = None
    error: str | None = None
    logs: list[str] | None = None
    debug: dict[str, Any] | None = None
    started_at: float | None = None
    finished_at: float | None = None
    duration_ms: int | None = None

    @model_validator(mode="after")
    def _redact_storage_internals(self) -> "NodeRunInfo":
        """Strip internal artifact storage fields from node output before
        serving them to API clients — storage_key and storage_backend are
        implementation details that must not leak through the public API."""
        self.output = _strip_storage_fields(self.output)
        return self


class RunListItem(BaseModel):
    """Compact run summary for the cross-workflow Executions page list."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    workflow_name: str | None = None
    workflow_version: int
    workflow_version_id: str | None = None
    deployment_id: str | None = None
    triggered_by_error_run_id: str | None = None
    runner_pool_id: str | None = None
    runner_id: str | None = None
    batch_id: str | None = None
    mode: str
    status: str
    trigger_type: str
    started_at: datetime
    finished_at: datetime | None


class RunInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    workflow_name: str | None = None
    workflow_version: int
    workflow_version_id: str | None = None
    deployment_id: str | None = None
    triggered_by_error_run_id: str | None = None
    runner_pool_id: str | None = None
    runner_id: str | None = None
    batch_id: str | None = None
    mode: str
    status: str
    trigger_type: str
    started_at: datetime
    finished_at: datetime | None
    node_runs: list[NodeRunInfo] = []


class ArtifactInfo(BaseModel):
    id: str
    run_id: str
    node_id: str
    name: str
    kind: str
    content_type: str
    size_bytes: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    preview: Any = None
    created_at: datetime


class DatasetQueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    limit: int = Field(default=200, ge=1, le=1000)


class DatasetQueryResult(BaseModel):
    columns: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    elapsed_ms: float


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: str = "generic"
    scope: str = "global"
    workflow_id: str | None = None
    environment_id: str | None = None
    runner_pool_id: str | None = None
    description: str = ""
    data: dict[str, str] = Field(default_factory=dict)


class CredentialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scope: str | None = None
    workflow_id: str | None = None
    environment_id: str | None = None
    runner_pool_id: str | None = None
    description: str | None = None
    data: dict[str, str] | None = None


class CredentialInfo(BaseModel):
    id: str
    name: str
    type: str
    auth_method: str | None = None
    scope: str
    workflow_id: str | None = None
    environment_id: str | None = None
    runner_pool_id: str | None = None
    description: str
    keys: list[str]
    oauth_scopes: list[str] = Field(default_factory=list)
    oauth_expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CredentialTypeFieldInfo(BaseModel):
    key: str
    label: str
    secret: bool = True
    required: bool = True
    placeholder: str = ""
    help: str = ""


class OAuthCredentialTypeInfo(BaseModel):
    auth_url: str
    token_url: str
    scopes: list[str] = Field(default_factory=list)
    authorization_params: dict[str, str] = Field(default_factory=dict)


class CredentialTypeInfo(BaseModel):
    id: str
    name: str
    provider: str
    auth_method: str
    fields: list[CredentialTypeFieldInfo] = Field(default_factory=list)
    oauth: OAuthCredentialTypeInfo | None = None
    test_service: str | None = None
    documentation_url: str = ""
    default_scopes: list[str] = Field(default_factory=list)


class CredentialOAuthStartRequest(BaseModel):
    credential_type: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    scope: str = "global"
    workflow_id: str | None = None
    environment_id: str | None = None
    runner_pool_id: str | None = None
    description: str = ""
    redirect_uri: str = Field(default="", max_length=1000)
    scopes: list[str] = Field(default_factory=list)


class CredentialOAuthStartResponse(BaseModel):
    authorization_url: str
    state: str
    credential_type: str
    redirect_uri: str
    scopes: list[str]
    expires_at: datetime


class CredentialTestRequest(BaseModel):
    workflow_id: str | None = None
    environment_id: str | None = None
    runner_pool_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class CredentialTestDraftRequest(BaseModel):
    type: str
    data: dict[str, str] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class CredentialTestResponse(BaseModel):
    ok: bool
    status: str
    service: str
    message: str
    latency_ms: int
    checked_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class AuditEventInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    target_type: str
    target_id: str
    detail: str
    actor_id: str | None = None
    actor_email: str | None = None
    created_at: datetime


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str = ""
    company: str = ""
    role: str


class UserAdminInfo(UserInfo):
    created_at: datetime


class RegisterRequest(BaseModel):
    name: str = Field(default="", max_length=160)
    company: str = Field(default="", max_length=160)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class UserCreate(BaseModel):
    name: str = Field(default="", max_length=160)
    company: str = Field(default="", max_length=160)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    role: str = "viewer"


class UserUpdate(BaseModel):
    role: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    token: str
    user: UserInfo


class AuthRequiredResponse(BaseModel):
    auth_required: bool
    signed_in: bool
    registration_open: bool
    user: UserInfo | None = None


class DeploymentCreate(BaseModel):
    workflow_id: str
    name: str = Field(min_length=1, max_length=200)
    schedule_cron: str = ""
    schedule_interval: str = "hours"
    schedule_every: int = 1
    schedule_tz: str = ""
    default_parameters: dict[str, Any] = Field(default_factory=dict)
    active: bool = False
    environment_id: str | None = None
    runner_pool_id: str | None = None
    workflow_version_id: str | None = None
    error_workflow_id: str | None = None
    error_alerts: dict[str, Any] = Field(default_factory=dict)
    # Operator opt-in required when ``UNSAFE_NODE_POLICY=require_approval``
    # and the workflow contains risky nodes. Ignored otherwise.
    approve_unsafe_nodes: bool = False


class DeploymentUpdate(BaseModel):
    name: str | None = None
    schedule_cron: str | None = None
    schedule_interval: str | None = None
    schedule_every: int | None = None
    schedule_tz: str | None = None
    default_parameters: dict[str, Any] | None = None
    active: bool | None = None
    environment_id: str | None = None
    runner_pool_id: str | None = None
    workflow_version_id: str | None = None
    error_workflow_id: str | None = None
    error_alerts: dict[str, Any] | None = None
    approve_unsafe_nodes: bool = False


class DeploymentInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    name: str
    schedule_cron: str
    schedule_interval: str
    schedule_every: int
    schedule_tz: str
    default_parameters: dict[str, Any]
    active: bool
    environment_id: str | None
    runner_pool_id: str | None = None
    workflow_version_id: str | None = None
    workflow_version: int | None = None
    error_workflow_id: str | None = None
    error_alerts: dict[str, Any] = Field(default_factory=dict)
    last_fired: datetime | None
    created_at: datetime
    updated_at: datetime


class CodeModuleCreate(BaseModel):
    scope: str = "workflow"
    workflow_id: str | None = None
    environment_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    contents: str = ""
    include_undecorated: bool = False


class CodeModuleUpdate(BaseModel):
    name: str | None = None
    contents: str | None = None
    include_undecorated: bool | None = None


class CodeModuleInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: str
    workflow_id: str | None
    environment_id: str | None
    name: str
    contents: str
    include_undecorated: bool = False
    created_at: datetime
    updated_at: datetime


class CodeModuleFunctionShape(BaseModel):
    """Per-function summary of how parameters will appear on the node card."""

    name: str
    inputs: list[str] = Field(default_factory=list)
    params: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    # True when this function carries an ``@node`` decorator.
    decorated: bool = False
    # Declared incoming wiring (input-port name → "<source_id>" /
    # "<source_id>.<output>"), only present for decorated functions.
    wires: dict[str, str] = Field(default_factory=dict)


class CodeModuleFunctionPreview(BaseModel):
    """What the upload preview surfaces after parsing/registering the file."""

    registered: list[str] = Field(default_factory=list)
    # Per-function inputs vs params so the UI can show users which args
    # become wired ports (required, no default) and which become inspector
    # fields (defaulted).
    functions: list[CodeModuleFunctionShape] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    syntax_error: str | None = None
    # True when the file uses ``@node`` decorators (explicit mode), so the UI
    # can offer the "also include undecorated functions" toggle.
    explicit_mode: bool = False
    # Top-level imports the file declares (stdlib filtered out).
    imports: list[str] = Field(default_factory=list)
    # Subset of ``imports`` that aren't in the workflow's env packages list.
    missing_in_env: list[str] = Field(default_factory=list)
    # The env we checked against (so the UI can show "missing in Global"
    # rather than make the user guess). Null when the workflow has no env.
    environment_id: str | None = None
    environment_name: str | None = None


class RunnerPoolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider: str = "agent"
    provider_config: dict[str, Any] = Field(default_factory=dict)
    max_concurrent_runs: int = Field(default=4, ge=1, le=1024)


class RunnerPoolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    provider_config: dict[str, Any] | None = None
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=1024)


class RunnerPoolInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    provider: str
    provider_config: dict[str, Any]
    max_concurrent_runs: int
    runner_count: int = 0
    online_count: int = 0
    created_at: datetime
    updated_at: datetime


class RunnerInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    pool_id: str
    name: str
    status: str
    capabilities: dict[str, Any]
    last_seen_at: datetime | None
    current_runs: int
    max_concurrent_runs: int
    cached_env_ids: list[str]
    created_at: datetime
    updated_at: datetime


class RegistrationTokenRequest(BaseModel):
    """Optional machine details captured when minting a token.

    All fields default to the legacy auto-values so existing clients (and the
    CLI's bare ``--token`` path) keep working untouched.
    """

    name: str | None = Field(default=None, max_length=200)
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=64)
    capabilities: dict[str, Any] | None = None


class RegistrationTokenResponse(BaseModel):
    token: str
    runner_id: str
    expires_at: datetime


class RunnerUpdate(BaseModel):
    """Editable machine metadata for an existing runner row."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=64)
    capabilities: dict[str, Any] | None = None


class SSHOnboardRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1)
    auth_method: str = "key"  # "key" | "password"
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None
    name: str | None = None
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=64)
    capabilities: dict[str, Any] | None = None
    api_url: str | None = None  # URL the runner connects back to
    use_systemd: bool = True


class SSHOnboardResponse(BaseModel):
    runner_id: str
    runner_name: str
    install_log: str


class RunBatchCreate(BaseModel):
    runner_pool_id: str | None = None
    parameters: list[dict[str, Any]] = Field(min_length=1)
    trigger_node_id: str | None = None


class RunBatchInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    deployment_id: str | None
    runner_pool_id: str | None
    status: str
    total_runs: int
    succeeded_runs: int
    failed_runs: int
    cancelled_runs: int
    created_at: datetime
    finished_at: datetime | None


class PinPayload(BaseModel):
    payload: Any


class PinnedItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    payload: Any
    updated_at: datetime


class AiWorkflowDraftRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    apply: bool = False
    mode: str = Field(default="draft", pattern="^(draft|fix)$")
    current_graph: WorkflowGraph | None = None
    failed_run_id: str | None = None
    failed_node_id: str | None = None
    error: str | None = Field(default=None, max_length=8000)
    fix_strategy: str = Field(default="minimal", pattern="^(minimal|replacement)$")


class AiWorkflowDraftResponse(BaseModel):
    workflow_id: str
    graph: WorkflowGraph
    assumptions: list[str] = Field(default_factory=list)
    missing_credentials: list[str] = Field(default_factory=list)
    required_packages: list[str] = Field(default_factory=list)
    explanation: str = ""
    mode: str = "draft"
    change_summary: list[str] = Field(default_factory=list)
    confidence: str = "medium"
    focus_node_id: str | None = None
    planner: str = "llm"


class RuntimeModeStatus(BaseModel):
    """Active runtime topology, surfaced so the API and UI can show whether a
    deployment is running in local or production mode and where it silently
    falls back to local-only behaviour."""

    mode: str
    database_dialect: str
    queue_backend: str
    scheduler_role: str
    webhook_role: str
    artifact_backend: str
    runner_providers: list[str]
    allow_insecure: bool
    warnings: list[str]


class QueueStats(BaseModel):
    """Aggregated run-queue health for the ops/backpressure surface.

    ``oldest_queued_age_seconds`` is the wait of the oldest entry still in
    ``queued``; ``None`` when the queue is empty. Counts mirror
    ``RunQueueEntry.status``.
    """

    queued: int = 0
    leased: int = 0
    running: int = 0
    waiting: int = 0
    completed: int = 0
    failed: int = 0
    dead_lettered: int = 0
    cancelled: int = 0
    oldest_queued_age_seconds: float | None = None


class DrainRequest(BaseModel):
    """Toggle the run-queue dispatcher's drain mode."""

    draining: bool


class DeadLetterEntry(BaseModel):
    """One row in the dead-letter listing for the ops UI / API.

    Mirrors ``RunQueueEntry`` plus the parent ``Run.workflow_id`` so the
    operator can decide whether to replay or discard without an extra
    workflow lookup.
    """

    run_id: str
    workflow_id: str
    status: str
    attempts: int
    max_attempts: int
    queue_reason: str = ""
    last_error: str | None = None
    available_at: datetime | None = None


class DeadLetterListResponse(BaseModel):
    entries: list[DeadLetterEntry]
    total: int


class DeadLetterReplayResponse(BaseModel):
    """Result of bulk-replaying dead-letter entries."""

    replayed: list[str]
    skipped: list[str] = []


class RunTimelineEvent(BaseModel):
    """One ordered event in a run's lifecycle.

    ``ts`` is the event's wall-clock time when known. ``data`` carries
    type-specific payload (e.g. ``{"node_id": ..., "duration_ms": ...}``).
    """

    type: str
    ts: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class RunTimeline(BaseModel):
    run_id: str
    status: str
    events: list[RunTimelineEvent]


class RunApprovalInfo(BaseModel):
    """A side-effecting AI tool call waiting for, or carrying, a decision."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    approval_key: str
    status: str
    node_id: str | None = None
    agent_node_id: str | None = None
    step: int
    max_steps: int | None = None
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    requested_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    reason: str = ""


class RunApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=4000)
    resolved_by: str | None = Field(default=None, max_length=120)


class RunReplayResponse(BaseModel):
    """Result of replaying a terminal run via the durable queue.

    ``previous_status`` is the queue entry's status before replay
    (``failed``/``dead_lettered``/``cancelled``); the entry is now back in
    ``queued`` and will be dispatched on the next loop tick.
    """

    run_id: str
    previous_status: str
    status: str = "queued"


class RunReplayRequest(BaseModel):
    """Body for ``POST /runs/{run_id}/replay``.

    ``from_node_id`` enables replay-from-failure: the engine is seeded with
    the prior run's successful upstream NodeRun outputs and execution is
    restricted to ``from_node_id`` plus its forward descendants. Omit to
    replay the whole run from scratch.
    """

    from_node_id: str | None = None


class RunDebugSnapshot(BaseModel):
    """Editor-side payload for the 'Debug in editor' flow.

    Bundles the exact graph the failed run executed against, the failing node
    id, and a per-node cache of successful upstream outputs. The editor pins
    those outputs so the author can iterate on the failing node without
    re-running the whole upstream chain, then drives `POST /runs/{id}/replay`
    with `from_node_id` to actually re-execute.
    """

    run_id: str
    workflow_id: str
    workflow_version: int
    workflow_version_id: str | None = None
    status: str
    graph: dict[str, Any]
    failed_node_id: str | None
    upstream_cache: dict[str, Any] = Field(default_factory=dict)
    node_errors: dict[str, str] = Field(default_factory=dict)


class ChatTurnRequest(BaseModel):
    message: str
    session_id: str


class ChatTurnResponse(BaseModel):
    run_id: str | None
    reply: str
    session_id: str
    status: str
