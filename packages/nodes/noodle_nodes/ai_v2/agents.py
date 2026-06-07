"""AI Agent v2 executable node — engine-mediated tool loop (WP11)."""

from __future__ import annotations

import json
from typing import Any

from noodle.ai_runtime import (
    AgentActionRequest,
    AgentResumeInput,
    AIMessage,
    ChatModelAdapter,
    ChatRequest,
    ChatResponse,
    GuardrailAdapter,
    MemoryAdapter,
    MessageRole,
    OutputParserAdapter,
    ToolSchema,
)
from noodle.sdk import node
from noodle_nodes.ai_v2.tools import collect_tool_adapters

AI_CATEGORY = "AI"
TOOL_SYSTEM_PREFIX = "Noodle tools available in this run:"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _task_text(input_value: Any, prompt: str) -> str:
    if prompt.strip():
        return prompt.strip()
    if isinstance(input_value, dict):
        for key in ("task", "prompt", "chatInput", "text", "input"):
            value = input_value.get(key)
            if value:
                return _as_text(value).strip()
    return _as_text(input_value).strip()


def _session_id(input_value: Any, configured: str) -> str:
    if configured.strip():
        return configured.strip()
    if isinstance(input_value, dict):
        for key in ("session_id", "sessionId"):
            if input_value.get(key):
                return str(input_value[key])
    return "default"


def _model_name(model: ChatModelAdapter) -> str:
    try:
        config = model.as_config()
    except Exception:  # noqa: BLE001 - model adapters may omit config support
        config = {}
    return str(config.get("model") or config.get("deployment") or "")


def _tool_schemas(tool_value: Any) -> list[ToolSchema]:
    schemas: list[ToolSchema] = []
    seen: set[str] = set()
    for adapter in collect_tool_adapters(tool_value):
        name = str(adapter.schema.name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        schemas.append(adapter.schema)
    return schemas


def _tool_instruction(tools: list[ToolSchema]) -> AIMessage | None:
    names = ", ".join(schema.name for schema in tools if schema.name)
    if not names:
        return None
    return AIMessage.system(
        f"{TOOL_SYSTEM_PREFIX} {names}. "
        "When the user's request can be fulfilled by a connected tool, call the "
        "relevant tool instead of saying you cannot access external systems or "
        "run commands. Ask for missing tool arguments if needed."
    )


def _with_tool_instruction(
    messages: list[AIMessage],
    tools: list[ToolSchema],
) -> list[AIMessage]:
    instruction = _tool_instruction(tools)
    if instruction is None:
        return messages
    if any(
        message.role == MessageRole.system
        and message.content.startswith(TOOL_SYSTEM_PREFIX)
        for message in messages
    ):
        return messages
    insert_at = 0
    while insert_at < len(messages) and messages[insert_at].role == MessageRole.system:
        insert_at += 1
    return [*messages[:insert_at], instruction, *messages[insert_at:]]


def _without_tool_instruction(messages: list[AIMessage]) -> list[AIMessage]:
    return [
        message
        for message in messages
        if not (
            message.role == MessageRole.system
            and message.content.startswith(TOOL_SYSTEM_PREFIX)
        )
    ]


def _memory_messages(
    memory: Any,
    *,
    session_id: str,
    system: str,
) -> list[AIMessage]:
    messages: list[AIMessage] = []
    if isinstance(memory, MemoryAdapter):
        messages = memory.load(session_id=session_id)
        if not messages and session_id != "__seed__":
            messages = memory.load(session_id="__seed__")
    if system.strip():
        messages = [AIMessage.system(system.strip()), *messages]
    return messages


def _messages_from_resume(resume: AgentResumeInput) -> list[AIMessage]:
    messages = list(resume.messages_so_far)
    for result in resume.tool_results:
        messages.append(
            AIMessage.tool_result(
                tool_call_id=result.tool_call_id,
                name=result.name,
                content=result.content,
            )
        )
    return messages


def _intermediate_steps(messages: list[AIMessage]) -> list[dict[str, Any]]:
    """Reconstruct an ordered tool-call trace from the conversation history.

    Pairs each assistant ``tool_calls`` entry with its matching ``role=tool``
    result message so downstream nodes (and the chat UI) can render exactly
    what the agent did without replaying the run's event stream.
    """
    results_by_id: dict[str, AIMessage] = {
        message.tool_call_id: message
        for message in messages
        if message.role == MessageRole.tool and message.tool_call_id
    }
    steps: list[dict[str, Any]] = []
    index = 0
    for message in messages:
        if message.role != MessageRole.assistant or not message.tool_calls:
            continue
        for call in message.tool_calls:
            result = results_by_id.get(call.id)
            steps.append(
                {
                    "index": index,
                    "tool": call.name,
                    "tool_call_id": call.id,
                    "arguments": dict(call.arguments),
                    "result": result.content if result is not None else None,
                    "status": "pending" if result is None else "success",
                }
            )
            index += 1
    return steps


def _final_output(
    response: ChatResponse,
    *,
    parser: Any,
    step: int,
    stopped_reason: str = "",
    messages: list[AIMessage] | None = None,
    include_steps: bool = True,
) -> dict[str, Any]:
    checked = response
    parsed: Any = None
    if isinstance(parser, OutputParserAdapter):
        parsed = parser.parse(checked.text)
    output: dict[str, Any] = {
        "answer": checked.text,
        "step": step,
        "provider": checked.provider,
        "model": checked.model,
        "usage": checked.usage.model_dump(),
    }
    if parsed is not None:
        output["parsed"] = parsed
    if stopped_reason:
        output["stopped_reason"] = stopped_reason
    if include_steps:
        steps = _intermediate_steps(messages or [])
        output["intermediate_steps"] = steps
        output["tool_calls_count"] = len(steps)
    return output


@node(
    name="Agent",
    id="ai_agent_v2",
    category=AI_CATEGORY,
    role="executable",
    icon="ai",
    inputs=["input", "model", "tool", "memory", "parser", "guardrail"],
    input_kinds={
        "input": "main",
        "model": "ai_language_model",
        "tool": "ai_tool",
        "memory": "ai_memory",
        "parser": "ai_output_parser",
        "guardrail": "ai_guardrail",
    },
    outputs=["main"],
    output_kinds={"main": "main"},
    param_groups={
        "Options": [
            "system",
            "session_id",
            "max_steps",
            "temperature",
            "max_tokens",
            "response_format",
            "return_tool_trace",
            "timeout_seconds",
        ],
    },
    params={
        "prompt": {
            "widget": "textarea",
            "description": "Agent task. Blank uses input.task, input.prompt, or input.",
        },
        "system": {
            "widget": "textarea",
            "description": "Optional system instruction.",
            "group": "Options",
        },
        "session_id": {
            "description": "Conversation memory key. Blank uses input.session_id or default.",
            "group": "Options",
        },
        "max_steps": {
            "description": "Maximum tool iterations.",
            "group": "Options",
        },
        "temperature": {
            "description": "Sampling temperature.",
            "group": "Options",
        },
        "max_tokens": {
            "description": "Maximum response tokens.",
            "group": "Options",
        },
        "response_format": {
            "choices": ["text", "json_object"],
            "description": "Model response format.",
            "group": "Options",
        },
        "side_effect_approval": {
            "choices": ["require_approval", "auto_approve"],
            "display_name": "Tool approval",
            "description": "Require manual approval or automatically approve write-capable tools.",
        },
        "return_tool_trace": {
            "widget": "toggle",
            "display_name": "Return tool trace",
            "description": "Include the ordered tool-call trace (intermediate_steps) in the output.",
            "group": "Options",
        },
        "timeout_seconds": {
            "description": "HTTP timeout per model call.",
            "group": "Options",
        },
    },
)
def ai_agent_v2(
    input: Any = None,
    model: Any = None,
    tool: Any = None,
    memory: Any = None,
    parser: Any = None,
    guardrail: Any = None,
    prompt: str = "",
    system: str = "",
    session_id: str = "",
    max_steps: int = 4,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    response_format: str = "text",
    side_effect_approval: str = "require_approval",
    return_tool_trace: bool = True,
    timeout_seconds: int = 75,
    **runtime: Any,
) -> dict[str, Any] | AgentActionRequest:
    """Run an AI agent loop whose tool calls are dispatched by the engine."""
    if not isinstance(model, ChatModelAdapter):
        raise ValueError("ai_agent_v2: connect an AI Chat Model to the model port")

    steps_limit = max(1, min(25, int(max_steps or 4)))
    tool_schemas = _tool_schemas(tool)
    resume = runtime.get("agent_resume")
    if isinstance(resume, AgentResumeInput):
        messages = _with_tool_instruction(
            _messages_from_resume(resume),
            tool_schemas,
        )
        step = resume.step
    else:
        sid = _session_id(input, session_id)
        task = _task_text(input, prompt)
        if not task:
            raise ValueError("ai_agent_v2: prompt or input task is required")
        if isinstance(parser, OutputParserAdapter) and parser.format_instructions:
            task = f"{task}\n\n{parser.format_instructions}"
        messages = _with_tool_instruction(
            _memory_messages(memory, session_id=sid, system=system),
            tool_schemas,
        )
        messages.append(AIMessage.user(task))
        step = 0

    request = ChatRequest(
        messages=messages,
        model=_model_name(model),
        temperature=float(temperature),
        max_tokens=max_tokens,
        tools=tool_schemas,
        response_format="json_object" if response_format == "json_object" else "text",
        timeout_seconds=int(timeout_seconds or 75),
    )
    response = model.complete(request)

    if response.tool_calls:
        messages.append(
            AIMessage.assistant(response.text, tool_calls=response.tool_calls)
        )
        if step >= steps_limit:
            return _final_output(
                response,
                parser=None,
                step=step,
                stopped_reason="max_steps",
                messages=messages,
                include_steps=return_tool_trace,
            )
        legacy_allow = bool(runtime.get("allow_side_effects"))
        auto_approve_side_effects = (
            str(side_effect_approval or "").strip() == "auto_approve"
        ) or legacy_allow
        return AgentActionRequest(
            tool_calls=response.tool_calls,
            messages_so_far=messages,
            step=step,
            max_steps=steps_limit,
            allow_side_effects=auto_approve_side_effects,
        )

    if isinstance(guardrail, GuardrailAdapter):
        response = guardrail.check(response)

    messages.append(AIMessage.assistant(response.text))
    sid = _session_id(input, session_id)
    if isinstance(memory, MemoryAdapter):
        memory.save(session_id=sid, messages=_without_tool_instruction(messages))
    return _final_output(
        response,
        parser=parser,
        step=step,
        messages=messages,
        include_steps=return_tool_trace,
    )
