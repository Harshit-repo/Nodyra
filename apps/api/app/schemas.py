"""Pydantic request/response schemas for the API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from noodle.models import WorkflowGraph


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None
    environment_id: str | None = None
    graph: WorkflowGraph | None = None
    error_workflow_id: str | None = None
    error_alerts: dict[str, Any] | None = None


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


class EnvironmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    runner_pool_size: int | None = Field(default=None, ge=0, le=32)
    runner_pool_max: int | None = Field(default=None, ge=1, le=64)


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


class RunCreated(BaseModel):
    run_id: str


class RunCancelResponse(BaseModel):
    run_id: str
    status: str


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
    mode: str
    status: str
    trigger_type: str
    started_at: datetime
    finished_at: datetime | None


class RunInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    workflow_version: int
    workflow_version_id: str | None = None
    deployment_id: str | None = None
    triggered_by_error_run_id: str | None = None
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
    scope: str
    workflow_id: str | None = None
    environment_id: str | None = None
    runner_pool_id: str | None = None
    description: str
    keys: list[str]
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CredentialTestRequest(BaseModel):
    workflow_id: str | None = None
    environment_id: str | None = None
    runner_pool_id: str | None = None
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
    workflow_version_id: str | None = None
    error_workflow_id: str | None = None
    error_alerts: dict[str, Any] = Field(default_factory=dict)


class DeploymentUpdate(BaseModel):
    name: str | None = None
    schedule_cron: str | None = None
    schedule_interval: str | None = None
    schedule_every: int | None = None
    schedule_tz: str | None = None
    default_parameters: dict[str, Any] | None = None
    active: bool | None = None
    environment_id: str | None = None
    workflow_version_id: str | None = None
    error_workflow_id: str | None = None
    error_alerts: dict[str, Any] | None = None


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


class CodeModuleUpdate(BaseModel):
    name: str | None = None
    contents: str | None = None


class CodeModuleInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: str
    workflow_id: str | None
    environment_id: str | None
    name: str
    contents: str
    created_at: datetime
    updated_at: datetime


class CodeModuleFunctionShape(BaseModel):
    """Per-function summary of how parameters will appear on the node card."""

    name: str
    inputs: list[str] = Field(default_factory=list)
    params: list[str] = Field(default_factory=list)


class CodeModuleFunctionPreview(BaseModel):
    """What the upload preview surfaces after parsing/registering the file."""

    registered: list[str] = Field(default_factory=list)
    # Per-function inputs vs params so the UI can show users which args
    # become wired ports (required, no default) and which become inspector
    # fields (defaulted).
    functions: list[CodeModuleFunctionShape] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    syntax_error: str | None = None
    # Top-level imports the file declares (stdlib filtered out).
    imports: list[str] = Field(default_factory=list)
    # Subset of ``imports`` that aren't in the workflow's env packages list.
    missing_in_env: list[str] = Field(default_factory=list)
    # The env we checked against (so the UI can show "missing in Global"
    # rather than make the user guess). Null when the workflow has no env.
    environment_id: str | None = None
    environment_name: str | None = None


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


class AiWorkflowDraftResponse(BaseModel):
    workflow_id: str
    graph: WorkflowGraph
    assumptions: list[str] = Field(default_factory=list)
    missing_credentials: list[str] = Field(default_factory=list)
    required_packages: list[str] = Field(default_factory=list)
    explanation: str = ""
