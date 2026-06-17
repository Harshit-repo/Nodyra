# Noodle Agent Node Context

Last updated: 2026-06-11

This document describes how the Noodle Agent node works, how tools and approvals
flow through the engine, and what changed in the recent agent/tool approval
update.

## Purpose

The Agent node is the main agentic orchestration node in Noodle. It takes a user
task, a chat model, optional tools, optional memory, optional parser, and
optional guardrail. It runs a model call, lets the model request tool calls, asks
the engine to dispatch those tool calls, then resumes the model with tool
results until it can produce a final answer or reaches `max_steps`.

The current Agent node implementation is:

- Node id: `ai_agent_v2`
- File: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Runtime types: `packages/core/noodle/ai_runtime.py`
- Engine dispatch: `packages/core/noodle/engine/agent.py`

## Agent Node Interface

### Inputs

The Agent node has these typed inputs:

| Input | Kind | Purpose |
| --- | --- | --- |
| `input` | `main` | User task or upstream payload. If `prompt` is blank, the agent reads common keys such as `task`, `prompt`, `chatInput`, `text`, or `input`. |
| `model` | `ai_language_model` | Required chat model adapter. The agent cannot run without it. |
| `tool` | `ai_tool` | Optional tool or list/bundle of tools. These can be dedicated AI tool nodes or normal nodes in tool mode. |
| `memory` | `ai_memory` | Optional memory adapter. Used to load and save conversation messages by session id. |
| `parser` | `ai_output_parser` | Optional output parser. Adds format instructions and parses final output. |
| `guardrail` | `ai_guardrail` | Optional guardrail adapter. Checks final model response before output. |

In the editor, the Agent node visually separates ports by role. Model and input
stay on the side; memory and tool are bottom ports so they do not crowd the
main node body.

### Output

| Output | Kind | Purpose |
| --- | --- | --- |
| `main` | `main` | Final answer object, including answer text, usage, step count, and optional tool trace. |

The usual final output shape is:

```json
{
  "answer": "Final response text",
  "step": 1,
  "provider": "openai",
  "model": "gpt-...",
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  },
  "intermediate_steps": [],
  "tool_calls_count": 0
}
```

If a parser is connected and parsing succeeds, the output also includes
`parsed`.

## Agent Parameters

| Parameter | Purpose |
| --- | --- |
| `prompt` | Agent task. If blank, the task is read from `input`. |
| `system` | Optional system instruction prepended to the conversation. |
| `session_id` | Memory key. If blank, reads `input.session_id` or `input.sessionId`, otherwise uses `default`. |
| `max_steps` | Maximum tool iterations. The node clamps this between 1 and 25. |
| `temperature` | Model sampling temperature. |
| `max_tokens` | Optional max tokens for model response. |
| `response_format` | `text` or `json_object`. |
| `side_effect_approval` | `require_approval` or `auto_approve`. Controls whether side-effecting tools pause for user approval. |
| `return_tool_trace` | When enabled, returns `intermediate_steps` and `tool_calls_count`. |
| `timeout_seconds` | Per model call timeout. |

## Core Runtime Types

Agent tool execution is represented by these core runtime models:

| Type | Purpose |
| --- | --- |
| `ToolSchema` | Name, description, and JSON schema for tool parameters. |
| `ToolCall` | A model-requested call: id, tool name, and arguments. |
| `ToolResult` | Tool result returned back to the model. |
| `AgentActionRequest` | Emitted by the agent node when the model requests tools. The engine dispatches it. |
| `AgentActionResponse` | Engine result after tool dispatch. Can be converted into `AgentResumeInput`. |
| `AgentResumeInput` | Hidden runtime input used to resume the agent node after tool dispatch. |
| `AgentApprovalRequired` | Engine signal that a side-effecting tool needs manual approval. |

Relevant file:

`packages/core/noodle/ai_runtime.py`

## Execution Flow

### 1. First agent call

The agent validates that `model` is a `ChatModelAdapter`.

It then builds messages:

1. Load memory messages if a memory adapter is connected.
2. Add configured `system` message if present.
3. Add a tool instruction system message if tools are connected.
4. Add the user task from `prompt` or `input`.

The model is called with:

- Messages
- Model name
- Temperature
- Max tokens
- Tool schemas
- Response format
- Timeout

### 2. Model returns no tool calls

If the model returns a normal answer:

1. Optional guardrail checks the response.
2. The assistant message is appended to memory.
3. The final output is returned on `main`.

### 3. Model returns tool calls

If the model returns tool calls:

1. The assistant message with `tool_calls` is appended.
2. The agent returns `AgentActionRequest` instead of a normal output.
3. The engine intercepts that request.
4. The engine finds connected `ToolAdapter` instances from the Agent `tool` input.
5. The engine dispatches each requested tool.
6. The engine emits tool lifecycle events for observability.
7. The engine returns `AgentActionResponse`.
8. If the node accepts the hidden runtime resume input, the engine invokes the
   same agent node again with `agent_resume`.

### 4. Resume after tool results

On resume, the agent:

1. Rebuilds the prior message list from `messages_so_far`.
2. Appends each `ToolResult` as a `role=tool` message.
3. Preserves the current step count.
4. Calls the model again.

The model can then either request more tools or produce a final answer.

## Tool Mode

Tool mode lets a normal Noodle node become an AI tool for the Agent.

When a graph node has `tool_mode=true`:

1. The node does not run in the normal data flow.
2. The engine builds a `NodeToolAdapter`.
3. The adapter is emitted from a single `tool` output.
4. That output can connect to the Agent `tool` input.
5. The real node function runs only if the model calls the tool.

Relevant files:

- `packages/core/noodle/node_tool.py`
- `packages/core/noodle/engine/node_exec.py`
- `apps/web/src/editor/NodeCard.tsx`
- `apps/web/src/editor/NodeDetails.tsx`

### Tool Arguments

Tool arguments come from either:

- Explicit `$fromAI(...)` parameter bindings, or
- Blank simple parameters inferred as required AI-provided arguments.

For example, Execute Command has a blank `command` parameter. In tool mode, the
tool schema exposes `command` as a required string argument, so the model should
call it like:

```json
{
  "command": "python --version"
}
```

Fixed node parameters remain fixed. AI-provided parameters are merged only at
tool invocation time.

### Tool Side Effects

Tool-mode nodes are conservative by default: unless a manifest says otherwise,
they are treated as side-effecting. Execute Command is side-effecting and
therefore requires approval unless the agent is configured to auto-approve or
the user uses "Approve all" during the session.

## Approval Flow

Side-effecting tools are gated by approval.

### Normal approval

When a side-effecting tool is requested and `allow_side_effects=false`:

1. The engine emits `agent_tool_approval_required`.
2. The API records a `RunApproval`.
3. The run moves to `waiting`.
4. The chat UI and run approval panel show Approve and Reject actions.
5. Approve adds the tool call id to `approved_tool_call_ids`.
6. Reject adds the tool call id to `rejected_tool_call_ids`.
7. The waiting run is requeued with `agent_action_resume`.
8. The agent resumes from the stored `AgentActionRequest`.

Relevant files:

- `packages/core/noodle/engine/agent.py`
- `apps/api/app/services/run_resume.py`
- `apps/api/app/routers/runs.py`
- `apps/web/src/editor/ChatPanel.tsx`
- `apps/web/src/RunApprovalsPanel.tsx`

### Approve All

"Approve all" means:

- Approve the currently pending tool call.
- Set `AgentActionRequest.allow_side_effects=true` for the resumed request.
- Preserve that flag through `AgentActionResponse` and `AgentResumeInput`.
- Allow later side-effecting tool calls in the same agent run without another
  manual approval.

This is not a global permanent preference. It applies to the current resumed
agent run/session path.

## Recent Changes Made

### 1. Added approve-all approval decision

Files changed:

- `apps/api/app/schemas.py`
- `apps/api/app/routers/runs.py`
- `apps/api/app/services/runner.py`
- `apps/api/app/services/run_resume.py`
- `apps/web/src/api.ts`
- `apps/web/src/editor/ChatPanel.tsx`
- `apps/web/src/RunApprovalsPanel.tsx`

API decision values are now:

```ts
type RunApprovalDecision = "approve" | "reject" | "approve_all";
```

The backend maps `approve_all` to an approved approval record, then resumes the
waiting run with `approve_all=true`.

### 2. Preserved side-effect allowance across agent resume

Files changed:

- `packages/core/noodle/ai_runtime.py`
- `packages/core/noodle/engine/agent.py`
- `packages/nodes/noodle_nodes/ai_v2/agents.py`

`AgentResumeInput` and `AgentActionResponse` now include:

```python
allow_side_effects: bool = False
```

The dispatch response keeps the flag from `AgentActionRequest`, and
`as_resume_input()` carries it back into the resumed agent node.

The Agent node now uses this resumed flag when producing later
`AgentActionRequest` objects.

### 3. Improved Agent tool instructions

File changed:

- `packages/nodes/noodle_nodes/ai_v2/agents.py`

The tool instruction system message now includes:

- Tool names
- Tool descriptions
- Argument names
- Argument types
- Required vs optional status
- A direct instruction not to call a tool with `{}` unless the tool has no
  required arguments
- A direct instruction to answer from tool results instead of returning raw JSON

This specifically helps the Execute Command tool, where the model previously
could call the tool with `{}` even though `command` was required.

### 4. Added final-answer fallback for `{}` tool responses

File changed:

- `packages/nodes/noodle_nodes/ai_v2/agents.py`

If the final model response is empty, `{}`, or `[]`, and no parser is connected,
the agent now falls back to the last tool result.

For command-like tool results, it prefers:

1. `stdout`
2. `stderr`
3. The serialized parsed result
4. Raw tool content

This addresses the observed behavior where the Agent replied with `{}` after an
approved Execute Command run.

### 5. Moved tool-mode output port to the top

Files changed:

- `apps/web/src/editor/NodeCard.tsx`
- `apps/web/src/editor/NodeCard.test.tsx`

When a node is in tool mode:

- Its only output is `tool`.
- The `tool` output handle is rendered at the top of the node.
- The visible `tool` text chip is hidden.
- The hover title still shows `tool: AI tool`.

This reduces label overlap around tool-mode nodes such as Execute Command and
AI Buffer Memory.

### 6. Added Approve All buttons

Files changed:

- `apps/web/src/editor/ChatPanel.tsx`
- `apps/web/src/RunApprovalsPanel.tsx`
- `apps/web/src/editor.css`

The chat approval prompt and run approval panel now show:

- Approve
- Approve all
- Reject

The chat prompt tracks which exact decision button is busy, so the spinner
appears on the clicked button.

### 7. Engine-level dispatch hardening (Claude review pass)

Files changed:

- `packages/core/noodle/ai_runtime.py`
- `packages/core/noodle/engine/agent.py`
- `packages/nodes/noodle_nodes/ai_v2/agents.py`
- `packages/nodes/noodle_nodes/ai_v2/providers/openai.py`

Fixes applied after reviewing the approval/resume flow:

1. **No double execution across multiple approval pauses.**
   `AgentActionRequest.completed_results` records tool results that already
   executed before an approval pause. When a request with several
   side-effecting calls pauses more than once (one approval per call), each
   resume re-dispatches the same request — previously that re-executed every
   already-approved call. Dispatch now replays recorded results instead.
2. **Missing required arguments are rejected before the approval gate.**
   If the model calls a tool without its required arguments (the `{}`
   arguments case), the engine immediately feeds back an `is_error`
   ToolResult naming the missing arguments — the tool never runs with blank
   inputs and the operator is never asked to approve an invalid call. This is
   the deterministic backstop behind the prompt instruction added in (3).
3. **`agent_tool_started` is only emitted when a tool actually executes** —
   blocked/rejected/invalid calls no longer emit a spurious started event.
4. **Parser failures get one corrective retry.** If the output parser rejects
   the final answer, the agent shows the model its reply plus the validation
   error and retries once before surfacing the parser error.
5. **OpenAI-compatible list-shaped `content` is normalized.** Providers behind
   OpenRouter can return `content` as a list of typed parts; the adapter now
   joins the text parts instead of stringifying the list into a Python repr.

## Execute Command Tool Example

Workflow:

1. Chat Trigger sends input to Agent.
2. AI Chat Model connects to Agent `model`.
3. Execute Command node has "Use as tool" enabled.
4. Execute Command `tool` output connects to Agent `tool`.
5. User asks: `Which Python version is installed?`

Expected model tool call:

```json
{
  "name": "execute_command",
  "arguments": {
    "command": "python --version"
  }
}
```

Expected approval behavior:

1. Run pauses for Execute Command approval.
2. User clicks Approve or Approve all.
3. Engine invokes Execute Command.
4. Tool result returns stdout/stderr/returncode.
5. Agent resumes and answers with the Python version.

If the model still returns `{}` as the final answer, the fallback returns
`stdout`, for example:

```text
Python 3.12.8
```

## Observability Events

The engine emits these agent/tool events:

| Event | Meaning |
| --- | --- |
| `agent_action_requested` | Agent returned tool calls for engine dispatch. |
| `agent_tool_started` | A specific tool call started. |
| `agent_tool_approval_required` | A side-effecting tool needs approval. |
| `agent_tool_auto_approved` | A side-effecting tool was allowed without pausing. |
| `agent_tool_finished` | A tool call completed or errored. |
| `agent_action_completed` | All requested tool calls finished. |
| `agent_tool_approval_decided` | User approved/rejected an approval record. |
| `agent_resume_prepared` | API prepared replay seed for a waiting run. |

These events feed timeline views, chat agent-step rendering, and run approval
records.

## Tests Added Or Updated

Python:

- `packages/core/tests/test_engine_agent_actions.py`
  - Confirms `allow_side_effects` survives into resume input.
- `packages/nodes/tests/test_ai_v2_nodes.py`
  - Confirms tool instructions include required argument details.
  - Confirms approve-all survives follow-up tool calls.
  - Confirms `{}` final response falls back to Execute Command stdout.
- `apps/api/tests/test_runs.py`
  - Confirms `approve_all` sets resumed request `allow_side_effects=true`.
  - Confirms `agent_resume_prepared` includes `approve_all=true`.

Frontend:

- `apps/web/src/editor/ChatPanel.test.tsx`
  - Confirms Approve all appears in the chat approval prompt.
- `apps/web/src/editor/NodeCard.test.tsx`
  - Confirms tool-mode output handle is on top.
  - Confirms visible `tool` tag is hidden.

## Verification Run

The following checks passed after the changes:

```text
cd packages/core
..\..\.venv\Scripts\python.exe -m pytest tests\test_engine_agent_actions.py -q
10 passed

cd packages/nodes
..\..\.venv\Scripts\python.exe -m pytest tests\test_ai_v2_nodes.py -q
56 passed

cd apps/api
..\..\.venv\Scripts\python.exe -m pytest tests\test_runs.py -q
34 passed

cd apps/web
npm run test -- ChatPanel.test.tsx NodeCard.test.tsx
7 passed

cd apps/web
npm run typecheck
passed

cd apps/web
npm run build
passed
```

`npm run build` still reports the existing large chunk warning for the editor
and Plotly chunks. That warning is not introduced by these changes.

## Current Limitations And Notes

- "Approve all" applies to the current resumed agent run, not all future runs.
- A model can still choose poor tool arguments, but the prompt now gives it the
  required schema details and explicitly warns against `{}` calls.
- The `{}` final-answer fallback only applies when no parser is connected. If a
  parser is connected, parser behavior remains authoritative.
- Tool-mode nodes are deferred tools. They will not behave like normal data-flow
  nodes while tool mode is enabled.
- Side-effecting behavior is conservative. If a node does not explicitly mark
  itself read-only, it should be treated as requiring approval.

## Key Files

| Area | File |
| --- | --- |
| Agent node | `packages/nodes/noodle_nodes/ai_v2/agents.py` |
| Runtime types | `packages/core/noodle/ai_runtime.py` |
| Engine tool dispatch | `packages/core/noodle/engine/agent.py` |
| Tool-mode adapter | `packages/core/noodle/node_tool.py` |
| Tool-mode execution hook | `packages/core/noodle/engine/node_exec.py` |
| Approval API schema | `apps/api/app/schemas.py` |
| Approval decision route | `apps/api/app/routers/runs.py` |
| Approval resume | `apps/api/app/services/run_resume.py` |
| Runner wrapper | `apps/api/app/services/runner.py` |
| Chat approval UI | `apps/web/src/editor/ChatPanel.tsx` |
| Run approval panel | `apps/web/src/RunApprovalsPanel.tsx` |
| Tool-mode node port rendering | `apps/web/src/editor/NodeCard.tsx` |

