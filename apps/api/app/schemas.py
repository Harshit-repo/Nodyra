"""Pydantic request/response schemas for the API."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from noodle.models import WorkflowGraph

SUPPORTED_PYTHON_VERSIONS = ("3.12", "3.13", "3.14")


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
    mcp_enabled: bool | None = None
    mcp_tool_name: str | None = None
    mcp_description: str | None = None
    mcp_parameters_schema: dict | None = None
    folder_id: str | None = None


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    color: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def validate_name(self) -> "FolderCreate":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("folder name cannot be blank")
        return self


class FolderRename(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    color: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def validate_name(self) -> "FolderRename":
        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValueError("folder name cannot be blank")
        return self


class FolderInfo(BaseModel):
    id: str
    name: str
    color: str | None = None
    workflow_count: int = 0
    created_at: datetime
    updated_at: datetime


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
    folder_id: str | None = None
    updated_at: datetime
    created_at: datetime
    github_sync_status: str | None = None


class WorkflowDetail(BaseModel):
    id: str
    name: str
    active: bool
    version: int
    published_version: int
    has_unpublished_changes: bool
    environment_id: str | None
    default_runner_pool_id: str | None = None
    folder_id: str | None = None
    error_workflow_id: str | None = None
    error_alerts: dict[str, Any] = Field(default_factory=dict)
    allow_concurrent: bool = True
    run_timeout_seconds: float | None = None
    mcp_enabled: bool = False
    mcp_tool_name: str | None = None
    mcp_description: str | None = None
    mcp_parameters_schema: dict | None = None
    provider_trigger_counts: ProviderTriggerStatusCounts = Field(
        default_factory=ProviderTriggerStatusCounts
    )
    graph: WorkflowGraph
    created_at: datetime
    updated_at: datetime
    github_sync_status: str | None = None


class WorkflowVersionInfo(BaseModel):
    id: str
    version: int
    notes: str = ""
    created_at: datetime
    node_count: int = 0
    published: bool = False


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
    backend: Literal["venv", "conda", "pixi"] = "venv"
    backend_config: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_python_version(self) -> "EnvironmentCreate":
        parts = self.python_version.split(".")
        minor_version = ".".join(parts[:2])
        if (
            len(parts) not in (2, 3)
            or not all(part.isdigit() for part in parts)
            or minor_version not in SUPPORTED_PYTHON_VERSIONS
        ):
            supported = ", ".join(SUPPORTED_PYTHON_VERSIONS)
            raise ValueError(
                f"unsupported Python version {self.python_version!r}; "
                f"supported versions: {supported}"
            )
        return self


class EnvironmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    runner_pool_size: int | None = Field(default=None, ge=0, le=32)
    runner_pool_max: int | None = Field(default=None, ge=1, le=64)
    # Sentinel-free: send null to unbind, omit to leave unchanged.
    runner_pool_id: str | None = Field(default=None)
    runner_pool_set: bool = Field(default=False)
    backend_config: dict | None = None


class PackageRequest(BaseModel):
    package: str = Field(min_length=1, max_length=200)


class PackageListRequest(BaseModel):
    packages: list[str] = Field(default_factory=list)


class PackageUsageEntry(BaseModel):
    workflow_id: str
    workflow_name: str
    node_id: str
    node_label: str


class PackageUsagePackage(BaseModel):
    package: str  # canonical name
    used_by: list[PackageUsageEntry]


class PackageUsageInfo(BaseModel):
    packages: list[PackageUsagePackage]


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
    backend: str = "venv"
    backend_config: dict = Field(default_factory=dict)
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
    iteration_path: list[int] | None = None

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def _dt_to_epoch(cls, v: Any) -> float | None:
        # D-12: DB now stores DateTime; keep API returning float (epoch seconds).
        if isinstance(v, datetime):
            from datetime import UTC
            if v.tzinfo is None:
                v = v.replace(tzinfo=UTC)
            return v.timestamp()
        return v

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
    parent_run_id: str | None = None
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
    parent_run_id: str | None = None
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
    run_id: str | None = None
    node_id: str | None = None
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


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(default="", max_length=80)


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # "shared" | "dedicated_pool" — owner-only (see routers/orgs.update_org).
    execution_isolation: str | None = Field(default=None, max_length=20)


class OrgInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    status: str
    # The requesting user's role within this org (None when not applicable).
    role: str | None = None


class OrgMemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    role: str = Field(default="viewer", max_length=20)


class OrgMemberUpdate(BaseModel):
    role: str = Field(max_length=20)


class OrgMemberInfo(BaseModel):
    user_id: str
    email: str
    name: str = ""
    role: str


class OrgSettingsUpdate(BaseModel):
    """Per-org quota overrides. Omitted/None fields are left unchanged;
    send -1 to clear an override back to 'inherit instance default'."""

    max_concurrent_runs: int | None = Field(default=None, ge=-1)
    executions_per_day: int | None = Field(default=None, ge=-1)
    max_map_width: int | None = Field(default=None, ge=-1)
    max_loop_iterations: int | None = Field(default=None, ge=-1)
    max_inflight_subworkflows: int | None = Field(default=None, ge=-1)
    storage_quota_bytes: int | None = Field(default=None, ge=-1)


class OrgUsageDay(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: date
    runs: int
    compute_seconds: float
    node_runs: int


class OrgSettingsInfo(BaseModel):
    """Effective limits (override or inherited). ``overridden`` lists which
    fields come from the org row rather than instance defaults."""

    org_id: str
    max_concurrent_runs: int
    executions_per_day: int
    max_map_width: int
    max_loop_iterations: int
    max_inflight_subworkflows: int
    storage_quota_bytes: int
    overridden: list[str] = []


def _validate_password_strength(v: str) -> str:
    """Reject passwords that don't meet minimum complexity requirements.

    Requires at least 8 chars, one uppercase, one lowercase, one digit, and
    one special character.  Returns the password unchanged on success.
    """
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")
    if not any(c.isupper() for c in v):
        raise ValueError("Password must contain at least one uppercase letter")
    if not any(c.islower() for c in v):
        raise ValueError("Password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in v):
        raise ValueError("Password must contain at least one digit")
    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?/~`" for c in v):
        raise ValueError("Password must contain at least one special character")
    return v


class RegisterRequest(BaseModel):
    name: str = Field(default="", max_length=160)
    company: str = Field(default="", max_length=160)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)

    _validate_password = field_validator("password")(_validate_password_strength)


class UserCreate(BaseModel):
    name: str = Field(default="", max_length=160)
    company: str = Field(default="", max_length=160)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    role: str = "viewer"

    _validate_password = field_validator("password")(_validate_password_strength)


class UserUpdate(BaseModel):
    role: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    token: str
    user: UserInfo


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=32)
    expires_in_days: int = Field(default=90, ge=1, le=365)


class ApiTokenInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    token_prefix: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiTokenCreated(ApiTokenInfo):
    token: str


class WsTicketResponse(BaseModel):
    ticket: str


class AuthRequiredResponse(BaseModel):
    auth_required: bool
    signed_in: bool
    registration_open: bool
    multi_tenancy: bool = False
    edition: str = "community"
    entitlements: list[str] = Field(default_factory=list)
    limits: dict[str, int] = Field(default_factory=dict)
    license_notice: str | None = None
    user: UserInfo | None = None


class LicenseInfo(BaseModel):
    edition: str
    customer: str | None = None
    expires_at: int | None = None
    entitlements: list[str]
    limits: dict[str, int]
    notice: str | None = None


class LicenseApply(BaseModel):
    license_key: str = Field(min_length=1)


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
    ghost_count: int = 0
    aws_secret_configured: bool = False
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
    token_expires_at: datetime | None = None
    ssh_host: str | None = None


class RunnerPoolHealth(BaseModel):
    """Live health for one pool (program A6) — feeds the pool card's health
    strip and the "no dispatcher reachable" banner."""

    pool_id: str
    provider: str
    queue_depth: int
    oldest_queued_seconds: float | None
    capacity_used: int
    capacity_total: int
    online_count: int
    runner_count: int
    success_24h: float | None  # 0..1 over runs finished in the last 24h
    dispatcher_reachable: bool
    label_mismatch_queued: int = 0


class FleetSummary(BaseModel):
    runners_online: int
    runners_total: int
    queue_depth: int
    in_flight: int
    # Providers a dispatcher is leasing right now, and providers that have
    # queued runs but nothing dispatching them (the degraded state).
    providers_dispatchable: list[str]
    providers_stuck: list[str]


class RunnerFleetHealth(BaseModel):
    fleet: FleetSummary
    pools: list[RunnerPoolHealth]


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
    # The URL a runner should dial back to. Derived from PUBLIC_API_URL when set,
    # otherwise the request's own base URL — never the web origin, which is wrong
    # for any split web/API deployment. The install snippet uses this verbatim.
    api_url: str


class RunHistoryBucket(BaseModel):
    bucket_start: datetime
    success: int
    error: int
    total: int
    avg_duration_seconds: float | None


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
    parameters: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
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
    # Optional planner overrides used by the app-wide assistant so the operator
    # can pick which BYOK provider/model builds the draft. ``None`` keeps the
    # server's existing env/credential auto-resolution.
    planner_provider: str | None = Field(default=None, max_length=40)
    planner_model: str | None = Field(default=None, max_length=120)


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
    otel_enabled: bool
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
    # Multi-tenancy (C6): per-org active counts; "quota_parked" counts queued
    # entries held back by the org's concurrency cap. None when MT is off.
    by_org: dict[str, dict[str, int]] | None = None


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
    decision: Literal["approve", "reject", "approve_all"]
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


class ChatStreamStart(BaseModel):
    """Returned when a chat turn is started but not yet awaited.

    The caller subscribes to ``/ws/runs/{run_id}`` to stream the agent's live
    tool calls and node progress, then fetches the final reply once the run
    reaches a terminal state.
    """

    run_id: str | None
    session_id: str


class ChatPublicConfig(BaseModel):
    workflow_id: str
    title: str
    placeholder: str
    initial_message: str
    require_login: bool = True


# ---------------------------------------------------------------------------
# GitHub sync schemas (Task 5)
# ---------------------------------------------------------------------------


class GithubSyncConfigCreate(BaseModel):
    repo: str = Field(min_length=3, max_length=200, pattern=r"^[\w.\-]+/[\w.\-]+$")
    base_path: str = Field(default="workflows/", max_length=200)
    main_branch: str = Field(default="main", max_length=100)
    credential_id: str | None = None


class GithubSyncConfigInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    org_id: str
    repo: str
    base_path: str
    main_branch: str
    credential_id: str | None
    webhook_url: str  # computed — not a DB column


class GithubConflictResolveRequest(BaseModel):
    side: Literal["noodle", "github"]


class GithubRepoValidation(BaseModel):
    accessible: bool
    error: str | None = None
    private: bool | None = None
    default_branch: str | None = None


class GithubCreateRepoRequest(BaseModel):
    private: bool = True
    description: str = ""


class GithubCreateRepoResponse(BaseModel):
    created: bool
    url: str
    default_branch: str
