"""MCP prompts registry — pre-built prompt templates for workflow operations."""

from typing import Any

_PROMPTS: list[dict[str, Any]] = [
    {
        "name": "build_workflow",
        "description": "Step-by-step guide for building a new Noodle workflow via the MCP builder tools.",
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
    return _PROMPTS


def get_prompt(name: str, arguments: dict[str, str]) -> dict[str, Any] | None:
    """Return {description, messages} or None when name is unknown."""
    if name == "build_workflow":
        desc = arguments.get("description", "")
        text = (
            f"You are building a Noodle workflow. Goal: {desc}\n\n"
            "Steps:\n"
            "1. list_node_types — discover available node types.\n"
            "2. create_workflow — create the workflow.\n"
            "3. get_node_type — inspect each node's required params before placing it.\n"
            "4. add_node — add each node individually (safer than set_workflow_graph).\n"
            "5. add_edge — connect nodes in execution order.\n"
            "6. validate_graph — confirm the graph is structurally valid.\n"
            "7. run_workflow (use_draft=true) — test the draft.\n"
            "8. publish_workflow — publish once the run succeeds.\n"
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
    else:
        return None

    return {
        "description": next(p["description"] for p in _PROMPTS if p["name"] == name),
        "messages": [
            {"role": "user", "content": {"type": "text", "text": text}}
        ],
    }
