# Node Tool Mode (`$fromAI`) — Design Spec

**Date:** 2026-06-04
**Status:** Design approved; ready for implementation plan
**Topic:** Let any normal action node be attached to an AI Agent as a tool, n8n-style — with per-parameter "Fixed vs From-AI" binding via a `$fromAI` expression.

## Problem

The AI Agent v2 can use tools, but only via dedicated supplier nodes (`ai_http_tool`, `ai_workflow_tool`). There's no way to take an existing action node — Slack, Google Sheets, GitHub, HTTP Request — and let the agent call it directly, with some parameters fixed and others supplied by the model at call time. This is n8n's "tool mode + `$fromAI`" pattern.

## Goals

- Turn a normal action node into an agent tool by flipping it into **tool mode**.
- Per-parameter choice of **Fixed** (you set it) or **From AI** (the model supplies it at call time).
- Reuse the existing tool machinery: `ToolAdapter` contract, engine tool dispatch, side-effect approval gate, and run observability.
- Keep it inspectable and Python-native: the binding is a real `$fromAI(...)` expression in the graph, not hidden magic.

## Non-Goals

- Not changing the existing `ai_http_tool` / `ai_workflow_tool` / `ai_tool_bundle` nodes (they continue to work and can be mixed in).
- Not making triggers, AI sub-nodes, or control-flow nodes tool-capable.
- No streaming or multi-step tool planning changes — the agent loop is unchanged.

## Confirmed decisions

- **Entry gesture:** a "Use as tool" toggle on the node is the mechanism; a drag-from-Agent-tool-port node picker is a convenience layered on top (option C). Toggle ships first.
- **Parameter binding:** per-param **Fixed | From-AI** control, schema auto-derived from the param's own name/type/description (with optional override), backed by an inspectable `$fromAI(...)` expression (option A).
- **Tool-capable nodes:** executable action nodes (default on); triggers, AI sub-nodes (supplier/tool/output_parser), and control-flow (If/Switch/Merge/Loop) are excluded. Controlled by a `usable_as_tool` manifest flag.
- **Execution & observability:** a tool call runs the node's function deferred and is recorded as an observable step; side-effecting tools route through the existing approval gate.
- **Phasing:** Phase 1 = backend mechanism (fully testable via graph JSON / API); Phase 2 = editor UX. One spec, plan sequenced P1 → P2.

## Core mechanic

A node in **tool mode** does not execute in the data flow. Instead, when the engine reaches it, it builds a `NodeToolAdapter` and emits that adapter on an `ai_tool` output — which flows into the AI Agent's `tool` port exactly like the existing tool supplier nodes. The node's own function runs **deferred**: only when the model calls the tool.

```
Chat Trigger ─main─▶ AI Agent ──▶ …
                        ▲ tool
                        │ (ToolAdapter)
                  ┌─────┴───────────────┐
                  │ Slack node          │   tool_mode = true
                  │  channel = #alerts  │   (Fixed)
                  │  text    = $fromAI  │   (From-AI → tool arg)
                  └─────────────────────┘
   model calls send_slack({text}) → NodeToolAdapter.invoke_async →
   resolve $fromAI from args + merge fixed params → run Slack node → return output
```

## Phase 1 — Backend mechanism

### 1. `$fromAI` expression — `packages/core/noodle/expr.py`

Add `$fromAI(name, description="", type="string", default=None)` as a recognized expression function (alongside `$json`/`$input`/`$node`/`$now`). It binds to a context callable with two modes:

- **Schema-collection mode** (building the tool schema): each `$fromAI(...)` call appends `{name, description, type}` to a collector and returns `default` (or a typed placeholder). Running a node's param expressions in this mode yields the tool's argument schema.
- **Invoke mode** (model called the tool): `$fromAI(name, ...)` returns `args[name]` (the model-supplied value), falling back to `default`.

The mode + collector/args are passed through the existing expression evaluation context. `name` is required; a `$fromAI` with no name is a validation error.

### 2. `usable_as_tool` capability — `packages/core/noodle/models.py` + `sdk.py`

Add `usable_as_tool: bool` to `NodeManifest`. Default derived in `sdk.py`: `True` when `role == "executable"` and the node id is not in a small `_NON_TOOL_NODE_IDS` exclude set (control-flow/logic: `if`, `switch`, `merge`, `loop_over_items`, `filter`, `stop_and_error`, plus `code`), else `False`. Overridable via `@node(usable_as_tool=...)`. Triggers/suppliers/tools/output-parsers are `False` (non-executable).

### 3. Graph node tool mode — graph schema + editor types

A graph node may carry `tool_mode: bool` (default false) and `tool_config: {name, description}` (the tool identity shown to the model; defaults derive from the node name + manifest description). Param values use `$fromAI(...)` expressions for From-AI params; all other params are fixed literals/expressions as today.

When `tool_mode` is true the node's effective output is a single `ai_tool` port (its normal `main` output is not produced).

### 4. Engine — `packages/core/noodle/engine.py`

When the engine processes a node with `tool_mode = true`:
- Build `schema: ToolSchema` by evaluating the node's `$fromAI` param expressions in schema-collection mode (tool name/description from `tool_config`).
- Construct a `NodeToolAdapter` and set it as the node's `ai_tool` output value (so downstream wiring to the Agent's `tool` port works unchanged). Do **not** run the node's function now.
- `NodeToolAdapter.invoke_async(arguments)`:
  - Re-evaluate the node's params in invoke mode with `arguments`, so each `$fromAI(name)` resolves to `arguments[name]`; merge with the fixed params.
  - Run the node's function via the existing node-execution path (same runtime subprocess; credentials already resolved by the host run).
  - Return the output as text/JSON for the model. Raise → returned to the model as a tool error and recorded.
  - `side_effecting`: from a manifest hint (`tool_side_effecting`, default conservative `True` for non-obviously-read nodes) so writes hit the existing approval gate. (Exact default rule finalized in the plan.)

`NodeToolAdapter` lives in `packages/core/noodle` (engine-side): building and invoking it needs the node's `NodeDef` and the engine's node-execution path, which are core. It implements the existing `ToolAdapter` interface from `noodle.ai_runtime` (`schema`, `invoke_async`, `side_effecting`), so the agent loop and approval gate need no changes.

### 5. Connection validation — backend graph validation + `apps/web/src/editor/connectionValidation.ts`

A tool-mode node's `ai_tool` output may connect to the Agent's `tool` port (and the Tool Bundle's `tool_*` inputs). Its `main` output is unavailable while in tool mode. Non-tool-capable nodes cannot enter tool mode.

## Phase 2 — Editor UX

- **"Use as tool" toggle** in the node inspector/NDV, shown only when `manifest.usable_as_tool`. Enabling sets `tool_mode`, switches the node's rendered output to `ai_tool`, and reveals the tool name/description fields.
- **Per-param Fixed | From-AI** segmented control in the param panel. Choosing From-AI writes `={{ $fromAI('<param>', '<description>', '<type>') }}` into that param (with optional override of the arg name/description); choosing Fixed restores a normal value editor. A live **"tool the model sees"** preview renders the derived schema.
- **Tool output rendering** on the node card (`ai_tool` handle) when in tool mode.
- **Drag-from-Agent-tool-port picker:** dragging from the Agent's `tool` port opens a node picker filtered to `usable_as_tool` nodes; the chosen node is created already in tool mode and wired in.

## Error handling

| Case | Behavior |
|---|---|
| `$fromAI` missing a name | Save-time validation error; node flagged in editor. |
| Tool invoke raises | Error string returned to the model + recorded as a failed tool step. |
| Side-effecting tool | Pauses on the existing approval gate unless auto-approve. |
| Node not `usable_as_tool` | "Use as tool" toggle hidden; tool_mode rejected by validation. |
| Tool-mode node left unwired | Manifest/validation warning (a tool that nothing consumes). |

## Testing

**Phase 1**
- `expr`: `$fromAI` schema-collection returns the arg list; invoke mode resolves from args; missing name → error. (unit)
- `usable_as_tool` defaults: executable action node `True`; `if`/`switch`/`code` `False`; supplier/trigger `False`. (manifest)
- engine: a tool-mode node emits a `NodeToolAdapter` whose `schema` matches its `$fromAI` params; `invoke_async` runs the node with merged fixed + AI args and returns output; side-effecting routes through approval.
- end-to-end (API/engine): Chat Trigger → Agent + a tool-mode echo/HTTP node; with a stub model that calls the tool, the tool output flows back and the agent answers.

**Phase 2**
- vitest: toggle sets tool_mode + flips the output; per-param control writes/clears the `$fromAI` expression; schema preview updates; non-tool-capable node hides the toggle.

## Success criteria

- A normal action node can be flipped to tool mode, have some params fixed and others From-AI, wired into the Agent's tool port, and called by the model with AI-supplied arguments — running the real node, with the result returned to the agent.
- Side-effecting tool calls are gated by approval and visible in run history.
- The binding is a real `$fromAI(...)` expression in the graph (inspectable, hand-editable), and the existing tool nodes still work.
