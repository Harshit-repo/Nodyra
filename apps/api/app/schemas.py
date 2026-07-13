"""Pydantic request/response schemas for the API."""

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from nodyra.models import WorkflowGraph

SUPPORTED_PYTHON_VERSIONS = ("3.12", "3.13", "3.14")
# Interpreter → supported minor versions. cpython-ft (free-threaded) exists
# from 3.13; PyPy tracks its own version stream. Non-cpython interpreters are
# venv/uv-only: conda and pixi resolve their own interpreter builds and do not
# understand uv's "3.14t" / "pypy@3.11" request syntax.
SUPPORTED_INTERPRETERS: dict[str, tuple[str, ...]] = {
    "cpython": SUPPORTED_PYTHON_VERSIONS,
    "cpython-ft": ("3.13", "3.14"),
    "pypy": ("3.10", "3.11"),
}
# Allowlisted runtime flag keys (all boolean). "jit" → PYTHON_JIT=1,
# "lazy_imports" → PYTHON_LAZY_IMPORTS=1 on spawned workers.
SUPPORTED_RUNTIME_FLAGS = ("jit", "lazy_imports")


def _validate_runtime_flags(flags: dict) -> None:
    """Shared validation for `EnvironmentCreate.runtime_flags` and
    `EnvironmentUpdate.runtime_flags`: every key must be an allowlisted flag
    name and every value a bool. Kept as a module function so both schemas
    validate identically instead of drifting.
    """
    supported = ", ".join(SUPPORTED_RUNTIME_FLAGS)
    for key, value in flags.items():
        if key not in SUPPORTED_RUNTIME_FLAGS:
            raise ValueError(f"unsupported runtime flag {key!r}; supported flags: {supported}")
        if not isinstance(value, bool):
            raise ValueError(f"runtime flag {key!r} must be a boolean")


# Dotted module name, e.g. "my_pkg.transforms" — matches accelerate.py's own
# validation of the same shape so both layers stay in sync.
_MYPYC_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _validate_backend_config(cfg: dict, interpreter: str | None) -> None:
    """Shared validation for `EnvironmentCreate.backend_config` and
    `EnvironmentUpdate.backend_config`'s optional ``accelerate`` key
    (mypyc opt-in compilation of installed node package modules).

    Validation only runs when ``accelerate`` is present — backend_config is
    otherwise a free-form dict for backend-specific settings (index_urls etc.)
    and this function must not reject those.
    """
    if "accelerate" not in cfg:
        return
    accelerate = cfg["accelerate"]
    if not isinstance(accelerate, dict):
        raise ValueError("backend_config.accelerate must be an object")
    if "mypyc_modules" not in accelerate:
        return
    modules = accelerate["mypyc_modules"]
    if not isinstance(modules, list) or not (1 <= len(modules) <= 50):
        raise ValueError("backend_config.accelerate.mypyc_modules must be a list of 1-50 items")
    for module in modules:
        if not isinstance(module, str) or not _MYPYC_MODULE_RE.match(module):
            raise ValueError(
                f"backend_config.accelerate.mypyc_modules contains an invalid module "
                f"name: {module!r}"
            )
    if interpreter == "pypy":
        raise ValueError(
            "mypyc acceleration is CPython-only (mypyc emits CPython C-API "
            "extensions); it is not supported for interpreter='pypy'"
        )


def normalize_label_map(value: dict[str, Any] | None) -> dict[str, str] | None:
    if not value:
        return None
    labels: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            continue
        labels[key] = (
            "true"
            if raw_value is True
            else "false"
            if raw_value is False
            else str(raw_value).strip()
        )
    return labels or None


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
    execution_mode: str | None = None
    sandbox_resources: dict[str, Any] | None = None
    requirements: list[str] | None = None
    # Per-workflow wall-clock cap (seconds) for a run. None leaves it unset
    # (falls back to the server default); 0 disables the cap for this workflow.
    run_timeout_seconds: float | None = Field(default=None, ge=0)
    artifact_retention_days: int | None = Field(default=None, ge=0, le=3650)
    expected_graph_revision: int | None = Field(default=None, ge=0)
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
    graph_revision: int = 0
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
    graph_revision: int = 0
    has_unpublished_changes: bool
    environment_id: str | None
    default_runner_pool_id: str | None = None
    folder_id: str | None = None
    error_workflow_id: str | None = None
    error_alerts: dict[str, Any] = Field(default_factory=dict)
    allow_concurrent: bool = True
    execution_mode: Literal["inherit", "sandboxed", "standard"] = "inherit"
    sandbox_resources: dict[str, Any] | None = None
    requirements: list[str] = Field(default_factory=list)
    run_timeout_seconds: float | None = None
    artifact_retention_days: int | None = None
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


class WorkflowRevisionInfo(BaseModel):
    id: str
    workflow_id: str
    graph_revision: int
    origin: str
    operation: str
    summary: str = ""
    patch: dict[str, Any] | None = None
    actor_id: str | None = None
    actor_email: str | None = None
    created_at: datetime


class WorkflowPublishRequest(BaseModel):
    notes: str = ""
    update_deployments: bool = False


class WorkflowPublishResponse(BaseModel):
    workflow_id: str
    workflow_version_id: str
    version: int
    updated_deployments: int = 0


class WorkflowCheckCase(BaseModel):
    name: str = Field(default="Workflow check", min_length=1, max_length=200)
    input_data: dict[str, Any] = Field(default_factory=dict)
    expected_outputs: dict[str, Any] = Field(default_factory=dict)
    assertions: list[str] = Field(default_factory=list)


class WorkflowChecksSaveRequest(BaseModel):
    checks: list[WorkflowCheckCase] = Field(min_length=1, max_length=50)
    replace: bool = True


class WorkflowCheckInfo(WorkflowCheckCase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    status: str = "untested"
    last_result: dict[str, Any] | None = None
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowCheckRunResult(BaseModel):
    check: WorkflowCheckInfo
    passed: bool
    status: str
    failures: list[str] = Field(default_factory=list)
    node_outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)


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
    interpreter: Literal["cpython", "cpython-ft", "pypy"] = "cpython"
    runtime_flags: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_python_version(self) -> "EnvironmentCreate":
        parts = self.python_version.split(".")
        minor_version = ".".join(parts[:2])
        supported_for_interpreter = SUPPORTED_INTERPRETERS[self.interpreter]
        if (
            len(parts) not in (2, 3)
            or not all(part.isdigit() for part in parts)
            or minor_version not in supported_for_interpreter
        ):
            supported = ", ".join(supported_for_interpreter)
            raise ValueError(
                f"unsupported Python version {self.python_version!r} for interpreter "
                f"{self.interpreter!r}; supported versions: {supported}"
            )
        if self.interpreter != "cpython":
            # uv's free-threaded/PyPy request syntax ("3.14t", "pypy@3.11") is
            # minor-only; there is no patch pin to append.
            if len(parts) != 2:
                raise ValueError(
                    "free-threaded CPython and PyPy environments must use a minor "
                    "version (e.g. '3.14'), not a patch pin"
                )
            if self.backend != "venv":
                raise ValueError(
                    "free-threaded CPython and PyPy environments require the venv backend"
                )
        _validate_runtime_flags(self.runtime_flags)
        if self.interpreter == "pypy" and self.runtime_flags.get("jit"):
            raise ValueError("the 'jit' flag is CPython-only; PyPy always JIT-compiles")
        _validate_backend_config(self.backend_config, self.interpreter)
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
    # interpreter is deliberately absent: create-only, like python_version.
    runtime_flags: dict | None = None

    @model_validator(mode="after")
    def validate_runtime_flags(self) -> "EnvironmentUpdate":
        if self.runtime_flags is not None:
            _validate_runtime_flags(self.runtime_flags)
        if self.backend_config is not None:
            # interpreter is unknown at this layer (create-only field, not part
            # of the update body); the pypy-rejection half of
            # _validate_backend_config is re-checked in update_environment
            # once the env row's real interpreter is loaded.
            _validate_backend_config(self.backend_config, None)
        return self


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


class EnvironmentBuildJobInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    environment_id: str
    reason: str
    status: str
    attempts: int
    max_attempts: int
    package_snapshot: list[str] = Field(default_factory=list)
    packages_hash: str = ""
    python_version: str = ""
    backend: str = ""
    interpreter: str = ""
    last_error: str | None = None
    requested_by_email: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    available_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


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
    interpreter: str = "cpython"
    runtime_flags: dict = Field(default_factory=dict)
    build_job_id: str | None = None
    build_job_status: str | None = None
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
    sandbox: bool = False
    required_labels: dict[str, Any] | None = Field(default=None, max_length=16)
    # Convenience alias for manual triggers — when the caller posts
    # ``{"data": {...}}`` we treat it as ``parameters`` so the manual_trigger
    # node's output mirrors the request body without forcing clients to
    # learn the engine's internal naming.
    data: dict[str, Any] | None = None

    @field_validator("required_labels")
    @classmethod
    def _validate_required_labels(cls, value: dict[str, Any] | None) -> dict[str, str] | None:
        return normalize_label_map(value)


class RunCreated(BaseModel):
    run_id: str

    @computed_field  # type: ignore[misc]
    @property
    def id(self) -> str:
        return self.run_id


class NodeTestRequest(BaseModel):
    """Ephemeral single-node execution request.

    ``cache`` uses the same node-output envelope shape as ``RunRequest.cache``:
    ``{"upstream_node_id": {"port": value}}``. ``inputs`` is an editor
    convenience map keyed by the target node's input port; the router projects
    those values onto the target node's direct upstream edge outputs.
    """

    inputs: dict[str, Any] | None = None
    cache: dict[str, dict[str, Any]] | None = None
    use_pinned: bool = True
    use_draft: bool = True


class NodeTestResponse(BaseModel):
    workflow_id: str
    node_id: str
    status: str
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)
    started_at: float | None = None
    finished_at: float | None = None
    duration_ms: int | None = None
    cached_node_ids: list[str] = Field(default_factory=list)


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
        """Resolve any offloaded-output marker, then strip internal artifact
        storage fields before serving to API clients.

        Large outputs are persisted as a ``{"__output_ref": key}`` marker by the
        output store; resolve it back to the real value here so the UI shows the
        node's output, not the storage marker. ``storage_key``/``storage_backend``
        remain implementation details that must not leak through the public API.
        """
        from app.services.data_ref import resolve_ref

        self.output = _strip_storage_fields(resolve_ref(self.output))
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
    required_labels: dict[str, str] | None = None
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
    required_labels: dict[str, str] | None = None
    mode: str
    status: str
    # Run-level failure reason. Populated for failures that aren't attributable
    # to a single node (e.g. graph-validation errors like a cycle, or a
    # run_error emitted before any node executes) so the run-detail view can
    # show *why* a run failed without the caller having to fetch the timeline.
    error: str | None = None
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
    checksum_sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    preview: Any = None
    created_at: datetime


class ArtifactListResponse(BaseModel):
    items: list[ArtifactInfo]
    total: int


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
    session_id: str | None = None
    actor_type: str = "user"
    created_at: datetime


class CustomRoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    permissions: list[str] = Field(default_factory=list)


class CustomRoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    permissions: list[str] | None = None


class CustomRoleInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    name: str
    permissions: list[str]
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
    """Reject passwords that don't meet the public minimum length contract."""
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")
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

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("token name cannot be blank")
        return cleaned

    @field_validator("scopes")
    @classmethod
    def _validate_scopes(cls, value: list[str]) -> list[str]:
        cleaned = [scope.strip() for scope in value if scope.strip()]
        if not cleaned:
            raise ValueError("at least one scope is required")
        return cleaned


class ApiTokenScopeInfo(BaseModel):
    scope: str
    minimum_role: str
    grantable: bool


class ApiTokenInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
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
    metadata: dict = Field(default_factory=dict)


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
    # ``module_metadata`` is the model's Python attribute (``metadata`` is
    # reserved by SQLAlchemy's Declarative API).
    metadata: dict = Field(default_factory=dict, validation_alias="module_metadata")
    created_at: datetime
    updated_at: datetime


class GenerateNodeRequest(BaseModel):
    """Request to generate a custom @node function from a description."""

    description: str = Field(min_length=1, max_length=2000)
    scope: Literal["environment", "global"] = "environment"
    scope_id: str | None = Field(default=None, max_length=32)


class GenerateNodeResponse(BaseModel):
    """Generated code + metadata returned before the user decides to save."""

    code: str
    node_id: str
    node_name: str
    input_ports: dict[str, str] = Field(default_factory=dict)
    output_ports: dict[str, str] = Field(default_factory=dict)
    is_template: bool = False
    warnings: list[str] = Field(default_factory=list)


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
    required_labels: dict[str, Any] | None = Field(default=None, max_length=16)

    @field_validator("required_labels")
    @classmethod
    def _validate_required_labels(cls, value: dict[str, Any] | None) -> dict[str, str] | None:
        return normalize_label_map(value)


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
    mode: str = Field(default="draft", pattern="^(draft|fix|refine)$")
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
    # Multi-turn refinement (mode="refine")
    conversation_history: list[dict] | None = None
    target_node_ids: list[str] | None = None


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


class AgenticBuildFailureContext(BaseModel):
    """Diagnostic context for starting agentic build from a failed run."""

    failed_run_id: str | None = Field(default=None, max_length=128)
    failed_node_id: str | None = Field(default=None, max_length=256)
    run_error: str | None = Field(default=None, max_length=8000)
    node_errors: dict[str, str] = Field(default_factory=dict, max_length=100)
    graph: dict[str, Any] | None = None


class AgenticBuildRequest(BaseModel):
    """Request body for POST /workflows/{id}/agentic-build.

    Launches an autonomous build loop: AI drafts the workflow, runs it with
    test data, inspects results, repairs failing nodes, and repeats until
    convergence or max_iterations.  Hard-capped at 5 iterations to bound
    LLM cost.
    """

    goal: str = Field(min_length=1, max_length=4000)
    test_data: dict | None = None
    max_iterations: int = Field(default=5, ge=1, le=5)
    failure_context: AgenticBuildFailureContext | None = None


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
    replica_safe: bool = True
    replica_unsafe_reasons: list[str] = Field(default_factory=list)
    allow_insecure: bool
    otel_enabled: bool
    warnings: list[str]


class MCPToolCallAuditInfo(BaseModel):
    """Recent external MCP tool call, backed by AuditEvent rows."""

    id: str
    connection_id: str
    tool: str
    ok: bool
    org_id: str | None = None
    actor_id: str | None = None
    actor_email: str | None = None
    run_id: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    created_at: datetime


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


class DispatcherCapacity(BaseModel):
    id: str
    role: str
    providers: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    available_slots: int | None = None
    max_slots: int | None = None
    last_seen: float | None = None


class QueueCapacity(BaseModel):
    queued: int = 0
    leased: int = 0
    running: int = 0
    local_available_slots: int = 0
    local_max_slots: int = 0
    dispatchers: list[DispatcherCapacity] = Field(default_factory=list)
    label_blocked_queued: int = 0


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
    side: Literal["nodyra", "github"]


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
