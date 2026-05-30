"""Shared data models: node manifests, workflow graphs, and run results."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class CredentialSpec(BaseModel):
    """UI/runtime hint for parameters that should reference stored credentials.

    When ``multi`` is True, the param expects the whole credential dict at
    runtime (every named field in ``fields``). The inspector renders a
    single "Credentials" picker rather than one per field, matching the n8n
    convention. When ``multi`` is False (default), the param resolves to a
    single field's decrypted string.

    ``test_service`` names the credential-test handler that can validate the
    stored credential (the key into ``app.services.credential_tests._TESTERS``).
    When omitted, the UI falls back to ``type`` — the common case where the
    manifest's credential type and the tester id match (``slack_bot``,
    ``github``, ...). Set explicitly when a node uses a custom credential
    type but wants to borrow another service's test handler, or pass an
    empty string to opt a credential out of test-on-save.
    """

    type: str = "generic"
    key: str = "value"
    label: str = "Credential"
    fields: list[str] = Field(default_factory=list)
    multi: bool = False
    test_service: str | None = None


class ParamSpec(BaseModel):
    """A configurable node parameter, edited in the inspector (not wired)."""

    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""
    placeholder: str = ""
    choices: list[Any] | None = None
    multiline: bool = False
    key_value: bool = False
    credential: CredentialSpec | None = None


class PortSpec(BaseModel):
    """A named input or output port of a node."""

    name: str
    description: str = ""


class NodeManifest(BaseModel):
    """Describes a node type. Generated from the decorated function's signature."""

    id: str
    name: str
    category: str = "General"
    version: str = "1.0.0"
    description: str = ""
    icon: str | None = None
    inputs: list[PortSpec] = Field(default_factory=list)
    params: list[ParamSpec] = Field(default_factory=list)
    outputs: list[PortSpec] = Field(default_factory=list)


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class GraphNode(BaseModel):
    """An instance of a node type placed in a workflow."""

    id: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    position: Position = Field(default_factory=Position)
    disabled: bool = False
    outputs_override: list[str] | None = None
    # Per-node error / retry behaviour.
    on_error: str = "stop"  # "stop" or "continue"
    retry_on_fail: bool = False
    retries: int = 1
    retry_wait_seconds: float = 0.0
    retry_backoff: bool = False
    always_output_data: bool = False
    timeout_seconds: float | None = None


class Edge(BaseModel):
    """Connects a source node's output port to a target node's input port."""

    id: str = ""
    source: str
    source_output: str = "main"
    target: str
    target_input: str = "input"


class WorkflowGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class NodeStatus(StrEnum):
    pending = "pending"
    running = "running"
    success = "success"
    error = "error"
    skipped = "skipped"


class RunStatus(StrEnum):
    success = "success"
    error = "error"


class NodeRunResult(BaseModel):
    node_id: str
    status: NodeStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)
    started_at: float | None = None
    finished_at: float | None = None


class RunResult(BaseModel):
    status: RunStatus
    nodes: dict[str, NodeRunResult] = Field(default_factory=dict)
