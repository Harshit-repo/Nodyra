"""MCP prompts registry — pre-built prompt templates for workflow operations."""

from typing import Any

_PROMPTS: list[dict[str, Any]] = [
    {
        "name": "build_workflow",
        "description": "Step-by-step guide for building a new Nodyra workflow via the MCP builder tools.",
        "arguments": [
            {"name": "description", "description": "What the workflow should do.", "required": True}
        ],
    },
    {
        "name": "debug_run",
        "description": "Diagnostic guide for investigating a failed or stuck workflow run.",
        "arguments": [
            {"name": "run_id", "description": "The run ID to debug.", "required": True}
        ],
    },
    {
        "name": "optimize_workflow",
        "description": "Review guide for improving an existing workflow's reliability and structure.",
        "arguments": [
            {"name": "workflow_id", "description": "The workflow to optimize.", "required": True}
        ],
    },
]


def list_prompts() -> list[dict[str, Any]]:
    return list(_PROMPTS)


def get_prompt(name: str, arguments: dict[str, str]) -> dict[str, Any] | None:
    """Return {description, messages} or None when name is unknown."""
    definition = next((p for p in _PROMPTS if p["name"] == name), None)
    if definition is None:
        return None
    missing = [
        item["name"]
        for item in definition.get("arguments", [])
        if item.get("required") and not arguments.get(item["name"], "").strip()
    ]
    if missing:
        raise ValueError(f"Missing required prompt arguments: {', '.join(missing)}")
    if name == "build_workflow":
        desc = arguments.get("description", "")
        text = (
            f"You are building a Nodyra workflow. Goal: {desc}\n\n"
            "Production workflow-authoring sequence:\n"
            "1. get_workflow_authoring_guide with the goal and detail='standard' to load graph rules, trigger recipes, and common mistakes.\n"
            "2. search_node_catalog and get_node_contracts to choose node types and read params, ports, examples, and pitfalls.\n"
            "3. create_workflow to create the empty workflow.\n"
            "4. suggest_node_config or get_node_type before placing each node.\n"
            "5. Assemble the graph with set_workflow_graph, or preview_workflow_patch then apply_workflow_patch for edits.\n"
            "6. validate_workflow_graph before running; fix missing packages, invalid ports, missing triggers, or cycles.\n"
            "7. run_workflow with use_draft=true and realistic parameters; inspect failures with get_run, get_run_events, and get_node_run.\n"
            "8. publish_workflow only after tests pass and human approval is explicit.\n"
            "9. If it must run automatically, create_schedule/update_schedule after publishing. If it should be callable by other agents, enable_mcp_tool with a clear JSON Schema.\n\n"
            "Important graph rules:\n"
            "- Use source_output='main' and target_input='input' unless the node contract says otherwise.\n"
            "- For api_endpoint route branches, set outputs_override to each routes[].output value and connect edges from those output names.\n"
            "- A non-empty cron value takes precedence over interval/every; clear cron when interval/every should control cadence.\n"
            "- Published production webhooks and schedules use published workflow versions, not unsaved draft changes.\n"
        )
    elif name == "debug_run":
        run_id = arguments.get("run_id", "")
        text = (
            f"Debugging run {run_id!r}.\n\n"
            f"Steps:\n"
            f"1. get_run run_id={run_id!r} — check per-node statuses and error messages.\n"
            f"2. get_run_events run_id={run_id!r} — inspect the full event log for pre-execution errors.\n"
            f"3. Identify the failing node_id and its error text.\n"
            f"4. get_node_type on the failing node's type to review its required params.\n"
            f"5. patch_node to fix the params, then run_workflow use_draft=true to retry.\n"
        )
    elif name == "optimize_workflow":
        wf_id = arguments.get("workflow_id", "")
        text = (
            f"Optimizing workflow {wf_id!r}.\n\n"
            f"Steps:\n"
            f"1. get_workflow workflow_id={wf_id!r} — review the current graph.\n"
            f"2. get_workflow_stats workflow_id={wf_id!r} — check success rate and run counts.\n"
            f"3. list_runs workflow_id={wf_id!r} status=error — find recent failures.\n"
            f"4. get_run_events on a failed run to diagnose the root cause.\n"
            f"5. patch_node or add_node/remove_node to restructure as needed.\n"
            f"6. validate_graph to confirm changes, then publish_workflow.\n"
        )
    return {
        "description": definition["description"],
        "messages": [
            {"role": "user", "content": {"type": "text", "text": text}}
        ],
    }
