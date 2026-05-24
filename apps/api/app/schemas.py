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


class WorkflowSummary(BaseModel):
    id: str
    name: str
    active: bool
    version: int
    node_count: int
    environment_id: str | None
    updated_at: datetime


class WorkflowDetail(BaseModel):
    id: str
    name: str
    active: bool
    version: int
    environment_id: str | None
    graph: WorkflowGraph
    created_at: datetime
    updated_at: datetime


class WorkflowVersionInfo(BaseModel):
    version: int
    created_at: datetime


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    python_version: str = "3.12"
    packages: list[str] = Field(default_factory=list)


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
    created_at: datetime
    updated_at: datetime


class RunRequest(BaseModel):
    mode: str = "manual"
    targets: list[str] | None = None
    cache: dict[str, dict[str, Any]] | None = None
    parameters: dict[str, Any] | None = None


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
    mode: str
    status: str
    trigger_type: str
    started_at: datetime
    finished_at: datetime | None
    node_runs: list[NodeRunInfo] = []


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: str = "generic"
    data: dict[str, str] = Field(default_factory=dict)


class CredentialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    data: dict[str, str] | None = None


class CredentialInfo(BaseModel):
    id: str
    name: str
    type: str
    keys: list[str]
    created_at: datetime
    updated_at: datetime


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
    role: str


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    token: str
    user: UserInfo


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


class DeploymentUpdate(BaseModel):
    name: str | None = None
    schedule_cron: str | None = None
    schedule_interval: str | None = None
    schedule_every: int | None = None
    schedule_tz: str | None = None
    default_parameters: dict[str, Any] | None = None
    active: bool | None = None
    environment_id: str | None = None


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


class CodeModuleFunctionPreview(BaseModel):
    """What the upload preview surfaces after parsing/registering the file."""

    registered: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    syntax_error: str | None = None


class PinPayload(BaseModel):
    payload: Any


class PinnedItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    payload: Any
    updated_at: datetime
