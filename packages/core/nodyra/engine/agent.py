"""Agent action-request dispatch: tool indexing, approval gating, step events."""

import json
import time
from collections.abc import Iterable
from typing import Any

from nodyra.ai_runtime import (
    AgentActionRequest,
    AgentActionResponse,
    AgentApprovalRequired,
    AgentStepEvent,
    ToolAdapter,
    ToolResult,
)
from nodyra.engine.types import EventCallback

# Hard cap on the number of agent-loop iterations that resolve_agent_actions
# will attempt before raising RuntimeError.  Guards against a buggy node that
# always returns AgentActionRequest(step=0) with no tool_calls — the existing
# max_steps check only fires when the node itself increments the step counter.
_MAX_AGENT_LOOP_ITERATIONS: int = 200


def _collect_tool_adapters(value: Any) -> list[ToolAdapter]:
    """Collect ToolAdapter instances from a wired agent input value."""
    if isinstance(value, ToolAdapter):
        return [value]
    if isinstance(value, (list, tuple, set, frozenset)):
        tools: list[ToolAdapter] = []
        for item in value:
            tools.extend(_collect_tool_adapters(item))
        return tools
    if isinstance(value, dict):
        tools: list[ToolAdapter] = []
        for key in ("tool", "tools"):
            if key in value:
                tools.extend(_collect_tool_adapters(value[key]))
        return tools
    return []


def _tool_index(tool_values: Iterable[Any]) -> dict[str, ToolAdapter]:
    index: dict[str, ToolAdapter] = {}
    for value in tool_values:
        for tool in _collect_tool_adapters(value):
            name = str(tool.schema.name or "").strip()
            if not name:
                continue
            if name in index and index[name] is not tool:
                raise ValueError(f"duplicate AI tool name: {name}")
            index[name] = tool
    return index


def _tool_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _agent_approval_key(agent_node_id: str, step: int, call_name: str, call_id: str) -> str:
    raw = "|".join([agent_node_id, str(step), call_id, call_name])
    return raw[:240]


def _missing_required_arguments(tool: ToolAdapter, arguments: dict[str, Any]) -> list[str]:
    """Required schema arguments that are absent or blank in ``arguments``.

    Caught here — before the approval gate — so an operator is never asked to
    approve a call the tool can't run, and the model gets a recoverable error
    naming exactly what's missing instead of a silent bad invocation.
    """
    required = tool.schema.parameters.required or []
    missing: list[str] = []
    for name in required:
        value = arguments.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    return missing


async def _dispatch_agent_action_request(
    request: AgentActionRequest,
    *,
    agent_node_id: str,
    tool_values: Iterable[Any],
    emit: EventCallback,
    pause_on_approval: bool = False,
) -> AgentActionResponse:
    max_steps = max(1, int(request.max_steps or 1))
    step = max(0, int(request.step or 0))
    if step >= max_steps:
        raise RuntimeError(f"agent reached max_steps={max_steps}")

    tools = _tool_index(tool_values)
    await emit(
        {
            **AgentStepEvent(
                type="agent_action_requested",
                agent_node_id=agent_node_id,
                step=step,
                max_steps=max_steps,
            ).model_dump(exclude_none=True),
            "tool_calls": [
                call.model_dump(exclude_none=True) for call in request.tool_calls
            ],
        }
    )

    completed_by_id = {
        result.tool_call_id: result for result in (request.completed_results or [])
    }
    results: list[ToolResult] = []
    for call in request.tool_calls:
        prior = completed_by_id.get(call.id)
        if prior is not None:
            # Executed before an earlier approval pause in this same request —
            # replay the recorded result instead of running the tool again.
            # Its lifecycle events were already emitted when it actually ran.
            results.append(prior)
            continue
        started = time.time()
        tool = tools.get(call.name)
        approval_key = _agent_approval_key(agent_node_id, step, call.name, call.id)
        approved_call_ids = set(request.approved_tool_call_ids or [])
        rejected_call_ids = set(request.rejected_tool_call_ids or [])
        missing_args = (
            _missing_required_arguments(tool, call.arguments)
            if tool is not None
            else []
        )
        if tool is None:
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"Unknown tool: {call.name}",
                is_error=True,
            )
        elif missing_args:
            # Checked before the approval gate: the operator should never be
            # asked to approve a call the tool can't run, and the model gets a
            # recoverable error naming exactly what to fix.
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=(
                    f"Invalid call to tool {call.name!r}: missing required "
                    f"argument(s): {', '.join(missing_args)}. Call the tool "
                    "again with every required argument filled in."
                ),
                is_error=True,
            )
        elif call.id in rejected_call_ids:
            # An operator denied this side-effecting call. Feed the denial back
            # to the agent as a tool error so it can recover gracefully (e.g.
            # apologise or pick another approach) instead of the run hanging.
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"Tool {call.name!r} was denied by the operator.",
                is_error=True,
            )
        elif (
            tool.side_effecting
            and not request.allow_side_effects
            and call.id not in approved_call_ids
        ):
            message = f"Tool {call.name!r} requires approval before running."
            await emit(
                {
                    **AgentStepEvent(
                        type="agent_tool_approval_required",
                        agent_node_id=agent_node_id,
                        step=step,
                        max_steps=max_steps,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        approval_key=approval_key,
                        status="blocked",
                        message=message,
                    ).model_dump(exclude_none=True),
                    "arguments": call.arguments,
                }
            )
            if pause_on_approval:
                raise AgentApprovalRequired(
                    # Carry the results of calls that already ran (including
                    # ones replayed from earlier pauses) so the post-approval
                    # re-dispatch replays them instead of executing them again.
                    request=request.model_copy(
                        update={"completed_results": list(results)}
                    ),
                    tool_call=call,
                    approval_key=approval_key,
                    message=message,
                )
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=message,
                is_error=True,
            )
        else:
            await emit(
                {
                    **AgentStepEvent(
                        type="agent_tool_started",
                        agent_node_id=agent_node_id,
                        step=step,
                        max_steps=max_steps,
                        tool_call_id=call.id,
                        tool_name=call.name,
                    ).model_dump(exclude_none=True),
                    "arguments": call.arguments,
                }
            )
            if tool.side_effecting and request.allow_side_effects:
                await emit(
                    {
                        **AgentStepEvent(
                            type="agent_tool_auto_approved",
                            agent_node_id=agent_node_id,
                            step=step,
                            max_steps=max_steps,
                            tool_call_id=call.id,
                            tool_name=call.name,
                            approval_key=approval_key,
                            status="approved",
                            message="Side-effecting tool auto-approved by agent setting.",
                        ).model_dump(exclude_none=True),
                        "arguments": call.arguments,
                    }
                )
            try:
                content = await tool.invoke_async(dict(call.arguments))
                result = ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=_tool_content(content),
                )
            except Exception as exc:  # noqa: BLE001 - tool failures go back to the model
                result = ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=f"{type(exc).__name__}: {exc}",
                    is_error=True,
                )

        duration_ms = int((time.time() - started) * 1000)
        await emit(
            {
                **AgentStepEvent(
                    type="agent_tool_finished",
                    agent_node_id=agent_node_id,
                    step=step,
                    max_steps=max_steps,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    status="error" if result.is_error else "success",
                ).model_dump(exclude_none=True),
                "tool_result": result.model_dump(),
                "duration_ms": duration_ms,
            }
        )
        results.append(result)

    next_step = step + 1
    response = AgentActionResponse(
        tool_results=results,
        messages_so_far=list(request.messages_so_far),
        step=next_step,
        max_steps=max_steps,
        allow_side_effects=bool(request.allow_side_effects),
    )
    await emit(
        {
            **AgentStepEvent(
                type="agent_action_completed",
                agent_node_id=agent_node_id,
                step=next_step,
                max_steps=max_steps,
                status="success",
            ).model_dump(exclude_none=True),
            "tool_results": [result.model_dump() for result in results],
        }
    )
    return response
