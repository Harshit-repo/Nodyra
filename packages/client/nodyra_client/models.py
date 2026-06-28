"""Typed models mirroring the Nodyra API response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


# ── Workflows ────────────────────────────────────────────────────────────────

class WorkflowSummary(BaseModel):
    id: str
    name: str
    active: bool
    status: str  # draft | published
    latest_version: int | None = None
    folder_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    trigger_types: list[str] = []
    node_count: int | None = None


class GraphNode(BaseModel):
    id: str
    type: str
    name: str = ""
    params: dict[str, Any] = {}
    position: list[float] = [0, 0]
    disabled: bool = False


class WorkflowEdge(BaseModel):
    id: str | None = None
    source: str
    source_output: str = "main"
    target: str
    target_input: str = "main"


class WorkflowGraph(BaseModel):
    nodes: list[GraphNode] = []
    edges: list[WorkflowEdge] = []


class WorkflowDetail(BaseModel):
    id: str
    name: str
    draft_graph: WorkflowGraph | None = None
    active_version: int | None = None
    status: str = "draft"
    folder_id: str | None = None
    environment_id: str | None = None
    allow_concurrent: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Runs ─────────────────────────────────────────────────────────────────────

class RunCreated(BaseModel):
    id: str
    workflow_id: str
    status: str  # queued | running
    mode: str = "manual"


class RunSummary(BaseModel):
    id: str
    workflow_id: str
    workflow_name: str | None = None
    status: str
    mode: str = "manual"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    node_count: int | None = None


class NodeRunSummary(BaseModel):
    id: str
    node_id: str
    node_type: str
    node_name: str | None = None
    status: str
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None


class RunDetail(BaseModel):
    id: str
    workflow_id: str
    status: str
    mode: str = "manual"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    node_runs: list[NodeRunSummary] = []
    error: str | None = None


# ── Credentials ──────────────────────────────────────────────────────────────

class CredentialSummary(BaseModel):
    id: str
    name: str
    type: str
    scope: str = "global"


# ── Export ───────────────────────────────────────────────────────────────────

class ExportResult(BaseModel):
    format: str  # script | module | docker
    content: str


# ── Generic helpers ──────────────────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    limit: int = 50
    offset: int = 0
