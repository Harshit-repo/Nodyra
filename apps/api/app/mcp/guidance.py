"""LLM-facing workflow authoring guidance for Nodyra MCP clients."""

from typing import Any

GUIDE_VERSION = "2026-07-05"

GRAPH_CONTRACT: dict[str, Any] = {
    "graph_shape": {"nodes": "array[node]", "edges": "array[edge]"},
    "node_shape": {
        "id": "Stable unique id within the graph. Prefer snake_case.",
        "type": "Node type id from list_node_types/get_node_type.",
        "label": "Optional display label.",
        "params": "Node parameters matching get_node_type(node_type).params.",
        "position": "Optional canvas position, e.g. {'x': 300, 'y': 0}.",
        "outputs_override": (
            "Optional dynamic output names. Required for router-style nodes such "
            "as api_endpoint when route outputs are not fixed manifest ports."
        ),
    },
    "edge_shape": {
        "source": "Source node id.",
        "source_output": "Source output port. Use 'main' unless get_node_type or outputs_override says otherwise.",
        "target": "Target node id.",
        "target_input": "Target input port. Use 'input' for most processing nodes.",
    },
    "default_ports": {
        "source_output": "main",
        "target_input": "input",
    },
}

AUTHORING_SEQUENCE: list[dict[str, Any]] = [
    {
        "step": "Discover",
        "tools": ["get_workflow_authoring_guide", "search_node_catalog", "get_node_contracts"],
        "goal": "Choose built-in nodes before writing code.",
    },
    {
        "step": "Create",
        "tools": ["create_workflow"],
        "goal": "Create an empty workflow and keep its workflow_id.",
    },
    {
        "step": "Inspect",
        "tools": ["get_node_type", "suggest_node_config"],
        "goal": "Read required params, ports, requirements, examples, and node-specific pitfalls.",
    },
    {
        "step": "Assemble",
        "tools": ["set_workflow_graph", "preview_workflow_patch", "apply_workflow_patch"],
        "goal": "Build a complete draft graph with one trigger and connected edges.",
    },
    {
        "step": "Validate",
        "tools": ["validate_workflow_graph", "validate_graph"],
        "goal": "Catch missing triggers, bad ports, package gaps, cycles, and invalid params before running.",
    },
    {
        "step": "Test",
        "tools": ["run_workflow", "get_run", "get_run_events", "get_node_run"],
        "goal": "Run the draft with representative parameters and inspect failures at node level.",
    },
    {
        "step": "Ship",
        "tools": ["publish_workflow", "toggle_workflow", "create_schedule", "enable_mcp_tool"],
        "goal": "Publish only after tests pass; add schedules or MCP exposure when needed.",
    },
]

IMPORTANT_TOOLS: list[dict[str, str]] = [
    {
        "name": "get_workflow_authoring_guide",
        "when_to_use": "At the start of any workflow-building task.",
        "common_mistake_prevented": "Skipping graph conventions, publish rules, or schedule rules.",
    },
    {
        "name": "search_node_catalog",
        "when_to_use": "When deciding which node type can solve a step.",
        "common_mistake_prevented": "Writing custom code when a built-in node exists.",
    },
    {
        "name": "get_node_contracts",
        "when_to_use": "When an LLM needs compact, practical definitions for several nodes.",
        "common_mistake_prevented": "Using raw manifests without examples or pitfalls.",
    },
    {
        "name": "get_node_type",
        "when_to_use": "Before placing any exact node type.",
        "common_mistake_prevented": "Missing required params or wiring to invalid ports.",
    },
    {
        "name": "validate_workflow_graph",
        "when_to_use": "Before running or publishing a draft workflow.",
        "common_mistake_prevented": "Publishing graphs with missing packages, cycles, or bad connections.",
    },
    {
        "name": "run_workflow",
        "when_to_use": "After validation, using realistic trigger parameters.",
        "common_mistake_prevented": "Publishing untested workflows.",
    },
    {
        "name": "publish_workflow",
        "when_to_use": "After validation and successful draft run.",
        "common_mistake_prevented": "Assuming draft changes are live in production.",
    },
    {
        "name": "create_schedule",
        "when_to_use": "When a workflow must run on a production cadence.",
        "common_mistake_prevented": "Assuming a schedule_trigger node alone creates a durable schedule.",
    },
    {
        "name": "enable_mcp_tool",
        "when_to_use": "When a workflow should become a callable MCP tool.",
        "common_mistake_prevented": "Exposing a workflow without a clear parameter schema.",
    },
]

TRIGGER_RECIPES: dict[str, dict[str, Any]] = {
    "manual_trigger": {
        "use_for": "Human-invoked workflows and draft testing.",
        "production_notes": [
            "run_workflow parameters are delivered to the trigger node.",
            "params.data is useful as a default example payload.",
        ],
        "minimum_node": {
            "id": "manual_start",
            "type": "manual_trigger",
            "params": {"data": {"example": True}},
            "position": {"x": 0, "y": 0},
        },
    },
    "webhook_trigger": {
        "use_for": "One HTTP endpoint mapped to one workflow branch.",
        "production_notes": [
            "Publish and activate the workflow before using /webhook/{path}.",
            "Use /webhook-test/{path} only for draft testing.",
            "If auth_type is bearer, provide a bearer credential/token parameter accepted by the node contract.",
            "response_mode 'Last Node' waits for the final node output and returns it to the caller.",
        ],
        "minimum_node": {
            "id": "webhook_start",
            "type": "webhook_trigger",
            "params": {
                "path": "orders/intake",
                "http_method": "POST",
                "response_mode": "Last Node",
            },
            "position": {"x": 0, "y": 0},
        },
    },
    "api_endpoint": {
        "use_for": "REST-style workflow APIs with multiple methods or sub-routes.",
        "production_notes": [
            "Each route has an output name. Set node.outputs_override to exactly those output names.",
            "Connect one edge per route output, e.g. source_output='lookup'.",
            "Path params are delivered on input.params to the branch node.",
            "Publish and activate before using /webhook/{base_path}/...",
        ],
        "minimum_node": {
            "id": "api_start",
            "type": "api_endpoint",
            "params": {
                "base_path": "inventory",
                "response_mode": "Last Node",
                "routes": [
                    {"method": "GET", "path": "/{sku}", "output": "lookup"},
                    {"method": "POST", "path": "/reserve", "output": "reserve"},
                ],
            },
            "outputs_override": ["lookup", "reserve"],
            "position": {"x": 0, "y": 0},
        },
    },
    "schedule_trigger": {
        "use_for": "Time-based workflows.",
        "production_notes": [
            "Use cron OR interval/every. If cron is non-empty, it takes precedence.",
            "Clear cron when switching to interval/every.",
            "For durable production cadence, publish the workflow and create_schedule or update_schedule.",
            "Use IANA timezones such as UTC or Australia/Sydney.",
        ],
        "minimum_node": {
            "id": "schedule_start",
            "type": "schedule_trigger",
            "params": {"interval": "minutes", "every": 5, "cron": "", "tz": "UTC"},
            "position": {"x": 0, "y": 0},
        },
    },
}

COMMON_MISTAKES: list[dict[str, str]] = [
    {
        "mistake": "Connecting to a dynamic route output without outputs_override.",
        "fix": "For api_endpoint, set outputs_override to every routes[].output value and use those names in edges.",
    },
    {
        "mistake": "Using a draft graph in production.",
        "fix": "Call publish_workflow after successful draft tests; production webhooks and schedules use published snapshots.",
    },
    {
        "mistake": "Expecting schedule_trigger alone to create a durable schedule.",
        "fix": "Publish the workflow, then call create_schedule or update_schedule for production cadence.",
    },
    {
        "mistake": "Changing interval/every while leaving an old cron value set.",
        "fix": "Set schedule_cron or node params.cron to an empty string when interval/every should control cadence.",
    },
    {
        "mistake": "Omitting expected_graph_revision during multi-step edits.",
        "fix": "Pass graph_revision from get_workflow/list_workflows so stale edits fail instead of overwriting UI changes.",
    },
    {
        "mistake": "Publishing before validating packages.",
        "fix": "Call validate_workflow_graph and resolve missing_packages with environment tools before publishing.",
    },
    {
        "mistake": "Calling a workflow exposed as MCP without a parameter schema.",
        "fix": "Use enable_mcp_tool with a JSON Schema that describes the workflow input object.",
    },
]

PRODUCTION_CHECKLIST: list[str] = [
    "Workflow has exactly the intended trigger entry points.",
    "Every node type was selected from list_node_types/search_node_catalog.",
    "Every node with dynamic outputs has outputs_override matching its outgoing edges.",
    "validate_workflow_graph passes and missing_packages is empty.",
    "run_workflow succeeds with representative payloads.",
    "Node failures have been inspected with get_node_run or get_run_events.",
    "publish_workflow was called after the successful draft run.",
    "Schedules, webhooks, and MCP tool exposure were tested through their production paths.",
    "Human approval was explicit for production-impacting MCP tools.",
]

DETAIL_LEVELS = {"compact", "standard", "full"}


def workflow_authoring_guide(goal: str = "", detail: str = "standard") -> dict[str, Any]:
    """Return a stable, model-readable guide for creating workflows through MCP."""
    detail = detail if detail in DETAIL_LEVELS else "standard"
    payload: dict[str, Any] = {
        "guide_version": GUIDE_VERSION,
        "goal": goal,
        "summary": (
            "Build Nodyra workflows by discovering node contracts, assembling a graph, "
            "validating, running the draft, then publishing or deploying."
        ),
        "authoring_sequence": AUTHORING_SEQUENCE,
        "graph_contract": GRAPH_CONTRACT,
        "important_tools": IMPORTANT_TOOLS,
        "production_checklist": PRODUCTION_CHECKLIST,
    }
    if detail in {"standard", "full"}:
        payload["trigger_recipes"] = TRIGGER_RECIPES
        payload["common_mistakes"] = COMMON_MISTAKES
    if detail == "full":
        payload["edge_port_rules"] = [
            "Use source_output='main' and target_input='input' unless the node contract says otherwise.",
            "For router nodes, route output names are branch ports and must appear in outputs_override.",
            "Do not invent node types or port names. Validate before saving or publishing.",
        ]
        payload["safe_editing_rules"] = [
            "Prefer preview_workflow_patch before apply_workflow_patch for existing workflows.",
            "Use expected_graph_revision on write tools whenever editing an existing workflow.",
            "Do not call publish_workflow, create_schedule, or destructive tools without explicit human approval.",
        ]
    return payload


def _param_payload(param: Any) -> dict[str, Any]:
    return {
        "name": getattr(param, "name", ""),
        "type": getattr(param, "type", ""),
        "required": bool(getattr(param, "required", False)),
        "default": getattr(param, "default", None),
        "description": getattr(param, "description", ""),
        "choices": getattr(param, "choices", None),
    }


def _port_payload(port: Any) -> dict[str, Any]:
    if hasattr(port, "model_dump"):
        return port.model_dump(mode="json")
    return {
        "name": getattr(port, "name", ""),
        "type": getattr(port, "type", ""),
        "description": getattr(port, "description", ""),
    }


def node_llm_guidance(manifest: Any) -> dict[str, Any]:
    node_type = getattr(manifest, "id", "")
    guidance: dict[str, Any] = {
        "usage": "Read params and ports before placing this node. Use default edge ports unless listed otherwise.",
        "default_edge": {"source_output": "main", "target_input": "input"},
        "pitfalls": [],
    }
    if node_type in TRIGGER_RECIPES:
        recipe = TRIGGER_RECIPES[node_type]
        guidance.update(
            {
                "usage": recipe["use_for"],
                "production_notes": recipe["production_notes"],
                "example_node": recipe["minimum_node"],
            }
        )
    elif node_type == "code":
        guidance.update(
            {
                "usage": "Run Python against the upstream value.",
                "production_notes": [
                    "The variable input contains the upstream payload.",
                    "Assign the final value to output.",
                    "For multiple branch outputs, assign variables named output_<port_name>.",
                    "Keep code deterministic and validate it with run_workflow before publishing.",
                ],
                "example_node": {
                    "id": "transform",
                    "type": "code",
                    "params": {"code": "payload = input or {}\noutput = {'received': payload}"},
                    "position": {"x": 300, "y": 0},
                },
            }
        )
    elif node_type == "filter":
        guidance.update(
            {
                "usage": "Route or stop data based on a field comparison.",
                "production_notes": [
                    "Check the node ports with get_node_type before wiring true/false branches.",
                    "Use run_workflow payloads that exercise both accepted and rejected cases.",
                ],
            }
        )
    return guidance


def compact_node_contract(manifest: Any, *, include_examples: bool = True) -> dict[str, Any]:
    """Return a compact node definition optimized for LLM workflow authors."""
    guidance = node_llm_guidance(manifest)
    if not include_examples:
        guidance = {key: value for key, value in guidance.items() if key != "example_node"}
    return {
        "id": getattr(manifest, "id", ""),
        "name": getattr(manifest, "name", ""),
        "category": getattr(manifest, "category", ""),
        "description": getattr(manifest, "description", ""),
        "requirements": list(getattr(manifest, "requirements", None) or []),
        "parameters": [_param_payload(param) for param in getattr(manifest, "params", [])],
        "inputs": [_port_payload(port) for port in getattr(manifest, "inputs", [])],
        "outputs": [_port_payload(port) for port in getattr(manifest, "outputs", [])],
        "graph_node_shape": {
            "id": "unique_node_id",
            "type": getattr(manifest, "id", ""),
            "params": "object matching parameters",
            "position": {"x": 0, "y": 0},
        },
        "llm_guidance": guidance,
    }
