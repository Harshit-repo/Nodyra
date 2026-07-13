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


class SystemRequirement(BaseModel):
    """An OS-level dependency a node needs (e.g. ghostscript, libzbar)."""

    name: str
    apt: str = ""
    brew: str = ""
    windows: str = ""
    dockerfile_hint: str = ""
    note: str = ""


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
    # Optional parameter grouping. A non-empty ``group`` marks the param as an
    # optional, "Add option"-style field the inspector tucks behind a chip;
    # params with no ``group`` are core and always shown.
    group: str | None = None
    # Rich UI/runtime metadata used by production integrations and AI nodes.
    # Defaults keep existing manifests and saved workflows backward-compatible.
    display_name: str = ""
    display_when: dict[str, Any] | None = None
    hide_when: dict[str, Any] | None = None
    widget: str = ""
    depends_on: list[str] = Field(default_factory=list)
    load_options: str | None = None
    resource_mapper: dict[str, Any] | None = None
    fixed_collection: dict[str, Any] | None = None
    credential_type: str | None = None
    required_scopes: list[str] = Field(default_factory=list)
    advanced: bool = False
    documentation_url: str = ""
    validation: dict[str, Any] | None = None


class PortDataKind(StrEnum):
    """Declares what shape of value a port produces/consumes.

    Used by the engine to validate that dataset/artifact ports actually
    receive the right kind of value, and by the UI to color handles and
    suggest compatible nodes. ``any`` is the permissive default.
    """

    any = "any"
    main = "main"
    control = "control"
    dataset = "dataset"
    artifact = "artifact"
    file = "file"
    ai_language_model = "ai_language_model"
    ai_embedding_model = "ai_embedding_model"
    ai_memory = "ai_memory"
    ai_tool = "ai_tool"
    ai_output_parser = "ai_output_parser"
    ai_retriever = "ai_retriever"
    ai_vector_store = "ai_vector_store"
    ai_document_loader = "ai_document_loader"
    ai_guardrail = "ai_guardrail"
    ai_stream = "ai_stream"
    ai_subagent = "ai_subagent"


class NodeRole(StrEnum):
    """Declares how the engine/editor should treat a node type."""

    executable = "executable"
    supplier = "supplier"
    trigger = "trigger"
    tool = "tool"
    output_parser = "output_parser"


class PortSpec(BaseModel):
    """A named input or output port of a node.

    ``data_schema`` is an optional JSON Schema dict that the engine validates
    wired input values against at execution time.  When set, every value
    arriving on this port must satisfy the schema — mismatches produce a
    clear error instead of a mysterious downstream crash.
    """

    name: str
    description: str = ""
    data_kind: PortDataKind = PortDataKind.any
    data_schema: dict | None = None


class IntegrationOperationManifest(BaseModel):
    """One selectable operation within an integration resource (e.g. "Append")."""

    id: str
    name: str
    description: str = ""


class IntegrationResourceManifest(BaseModel):
    """A resource grouping of operations within an integration (e.g. "Values")."""

    id: str
    name: str
    operations: list[IntegrationOperationManifest] = Field(default_factory=list)


class IntegrationManifest(BaseModel):
    """Resource → operation map for a consolidated integration node.

    Present only on the single node generated per integration provider (Google
    Sheets, Slack, ...). The editor reads it to render the two-level
    Resource/Operation selector; the rest of the params reshape via each
    ParamSpec's ``display_when`` once a resource+operation is chosen.
    """

    provider: str
    resources: list[IntegrationResourceManifest] = Field(default_factory=list)


class NodeManifest(BaseModel):
    """Describes a node type. Generated from the decorated function's signature."""

    id: str
    name: str
    category: str = "General"
    version: str = "1.0.0"
    description: str = ""
    icon: str | None = None
    role: NodeRole = NodeRole.executable
    hidden: bool = False
    deprecated: bool = False
    replacement_id: str | None = None
    usable_as_tool: bool = False
    tool_side_effecting: bool = True
    inputs: list[PortSpec] = Field(default_factory=list)
    params: list[ParamSpec] = Field(default_factory=list)
    outputs: list[PortSpec] = Field(default_factory=list)
    # Maps output port name → {param_name, param_value_str → data_kind}.
    # Example: {"main": {"param": "output_as_dataset", "true": "dataset", "false": "any"}}
    param_output_kinds: dict[str, dict[str, str]] = Field(default_factory=dict)
    requirements: list[str] = Field(default_factory=list)
    system_requirements: list[SystemRequirement] = Field(default_factory=list)
    # Set only on consolidated integration nodes: drives the editor's
    # Resource/Operation selector. None for ordinary nodes.
    integration: IntegrationManifest | None = None


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class GraphNode(BaseModel):
    """An instance of a node type placed in a workflow."""

    id: str
    type: str
    label: str | None = None
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
    # Per-node lifecycle hooks (Prefect-inspired). Each hook fires on a
    # specific trigger and carries an action type + config. Executed
    # best-effort — a hook failure never fails the node itself.
    hooks: list[dict[str, Any]] = Field(default_factory=list)
    # Tool mode: when true the node does not run in the data flow; the engine
    # emits a ToolAdapter on a `tool` output for an AI Agent to call.
    tool_mode: bool = False
    tool_name: str | None = None
    tool_description: str = ""


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
    waiting = "waiting"
    success = "success"
    error = "error"
    skipped = "skipped"


class RunStatus(StrEnum):
    waiting = "waiting"
    success = "success"
    error = "error"
    timed_out = "timed_out"


class NodeRunResult(BaseModel):
    node_id: str
    status: NodeStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)
    started_at: float | None = None
    finished_at: float | None = None
    # Version of the node type that executed this result (from NodeManifest.version).
    node_type_version: str = "1.0.0"


class RunResult(BaseModel):
    status: RunStatus
    nodes: dict[str, NodeRunResult] = Field(default_factory=dict)
