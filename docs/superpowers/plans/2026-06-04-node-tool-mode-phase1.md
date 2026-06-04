# Node Tool Mode (`$fromAI`) — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend mechanism so a normal action node flagged `tool_mode` becomes an AI Agent tool — its parameters split into Fixed vs `$fromAI`-supplied — without running in the data flow until the model calls it.

**Architecture:** A `$fromAI(...)` expression function (two modes: schema-collection and invoke). A `tool_mode` flag on the graph node. The engine, when it reaches a tool-mode node, builds a `NodeToolAdapter` (schema derived from the node's `$fromAI` params) and emits it on a `tool` output instead of executing the node; the adapter runs the node's function, deferred, when the agent invokes it — reusing the existing `ToolAdapter` / agent-dispatch / approval machinery.

**Tech Stack:** Python 3.12, pytest, `uv` workspace. All work is in `packages/core` and `packages/nodes` (no frontend — Phase 2).

**Spec:** `docs/superpowers/specs/2026-06-04-node-tool-mode-design.md`

**Scope:** Phase 1 only (backend, engine-testable). Phase 2 (editor toggle, per-param control, drag-from-port picker, connection-validation UX) is a separate plan written after Phase 1 lands.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `packages/core/noodle/expr.py` | `$fromAI` alias + `from_ai_binding` callable (collect/resolve) | Modify |
| `packages/core/tests/test_expr.py` | `$fromAI` unit tests (create if absent) | Create/Modify |
| `packages/core/noodle/models.py` | `usable_as_tool`/`tool_side_effecting` on `NodeManifest`; `tool_mode`/`tool_name`/`tool_description` on `GraphNode` | Modify |
| `packages/core/noodle/sdk.py` | Default `usable_as_tool` by role + exclude set, in both manifest-build paths | Modify |
| `packages/core/noodle/node_tool.py` | `NodeToolAdapter` + `build_node_tool_adapter` + `TOOL_MODE_OUTPUT` | Create |
| `packages/core/tests/test_node_tool.py` | Adapter unit tests | Create |
| `packages/core/noodle/engine.py` | Tool-mode interception in `_run_node` | Modify |
| `packages/core/tests/test_node_tool_engine.py` | Agent-loop integration test | Create |
| `packages/nodes/tests/test_builtin_nodes.py` | `usable_as_tool` manifest assertions | Modify |

Commands run from repo root `D:\noodle`. A pre-existing Pydantic `json`-field `UserWarning` is expected in every run and is not a failure.

---

## Task 1: `$fromAI` expression function

**Files:**
- Modify: `packages/core/noodle/expr.py` (`_ALIASES` ~line 23; add `from_ai_binding` near `build_context` ~line 277)
- Test: `packages/core/tests/test_expr.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_expr.py` (or append if it exists):

```python
from noodle.expr import build_context, evaluate, from_ai_binding


def test_from_ai_schema_collection_records_args() -> None:
    collector: dict = {}
    ctx = build_context()
    ctx["_from_ai"] = from_ai_binding(collector=collector)
    # In schema mode the call returns the default and records the arg.
    result = evaluate("{{ $fromAI('city', 'City name', 'string') }}", ctx)
    assert result is None
    assert collector == {
        "city": {"name": "city", "description": "City name", "type": "string"}
    }


def test_from_ai_invoke_mode_resolves_from_args() -> None:
    ctx = build_context()
    ctx["_from_ai"] = from_ai_binding(ai_args={"city": "Berlin"})
    assert evaluate("{{ $fromAI('city') }}", ctx) == "Berlin"


def test_from_ai_invoke_mode_uses_default_when_missing() -> None:
    ctx = build_context()
    ctx["_from_ai"] = from_ai_binding(ai_args={})
    assert evaluate("{{ $fromAI('city', 'desc', 'string', 'NONE') }}", ctx) == "NONE"


def test_from_ai_requires_a_name() -> None:
    ctx = build_context()
    ctx["_from_ai"] = from_ai_binding(collector={})
    # A blank name surfaces as a friendly expr error string, not a crash.
    assert "expr error" in evaluate("{{ $fromAI('') }}", ctx)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_expr.py -q`
Expected: FAIL — `ImportError: cannot import name 'from_ai_binding'`.

- [ ] **Step 3: Implement**

In `packages/core/noodle/expr.py`, add `$fromAI` to `_ALIASES`:

```python
_ALIASES = (
    ("$json", "_json"),
    ("$input", "_input"),
    ("$node", "_node"),
    ("$now", "_now"),
    ("$fromAI", "_from_ai"),
)
```

Add this function just after `build_context` (~line 290):

```python
def from_ai_binding(
    *,
    collector: dict[str, dict[str, str]] | None = None,
    ai_args: dict[str, Any] | None = None,
):
    """Build the ``$fromAI(name, description="", type="string", default=None)``
    callable for the expression context.

    * ``collector`` set → *schema-collection mode*: each call records the arg
      and returns ``default`` (so the expression evaluates harmlessly while we
      derive the tool's schema).
    * ``ai_args`` set → *invoke mode*: each call returns the model-supplied
      argument value (falling back to ``default``).
    """

    def _from_ai(
        name: Any,
        description: Any = "",
        type: Any = "string",  # noqa: A002 - mirrors the $fromAI('name','desc','type') call shape
        default: Any = None,
    ) -> Any:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("$fromAI requires a non-empty name")
        key = name.strip()
        if collector is not None:
            collector[key] = {
                "name": key,
                "description": str(description or ""),
                "type": str(type or "string"),
            }
            return default
        if ai_args is not None:
            return ai_args.get(key, default)
        return default

    return _from_ai
```

Note: `$fromAI` must be called positionally (`$fromAI('city','desc','string')`) — the expression validator does not allow keyword arguments. A blank name raises `ValueError`, which `_eval_one` already converts into a friendly `[expr error: ...]` string.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_expr.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/noodle/expr.py packages/core/tests/test_expr.py
git commit -m "feat(expr): add \$fromAI expression for AI-supplied tool params"
```

---

## Task 2: `usable_as_tool` + `tool_side_effecting` manifest flags

**Files:**
- Modify: `packages/core/noodle/models.py` (`NodeManifest` ~line 112; `GraphNode` ~line 135)
- Modify: `packages/core/noodle/sdk.py` (AST path ~line 419–485; runtime decorator path ~line 750–809)
- Test: `packages/nodes/tests/test_builtin_nodes.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/nodes/tests/test_builtin_nodes.py`:

```python
def test_usable_as_tool_defaults() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    # Action/integration nodes are tool-capable.
    assert manifests["http_request"].usable_as_tool is True
    assert manifests["edit_fields"].usable_as_tool is True
    # Control-flow and code are excluded.
    assert manifests["if"].usable_as_tool is False
    assert manifests["switch"].usable_as_tool is False
    assert manifests["code"].usable_as_tool is False
    # Non-executable roles are never tool-capable.
    assert manifests["manual_trigger"].usable_as_tool is False
    assert manifests["chat_trigger"].usable_as_tool is False
    assert manifests["ai_chat_model_openai"].usable_as_tool is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/nodes/tests/test_builtin_nodes.py::test_usable_as_tool_defaults -q`
Expected: FAIL — `AttributeError: 'NodeManifest' object has no attribute 'usable_as_tool'`.

- [ ] **Step 3: Implement the manifest fields**

In `packages/core/noodle/models.py`, add to `NodeManifest` (after `replacement_id` ~line 124):

```python
    usable_as_tool: bool = False
    tool_side_effecting: bool = True
```

And add to `GraphNode` (after `timeout_seconds` ~line 153):

```python
    tool_mode: bool = False
    tool_name: str | None = None
    tool_description: str = ""
```

- [ ] **Step 4: Implement the default in both `sdk.py` build paths**

In `packages/core/noodle/sdk.py`, add this module-level constant near the top (after imports):

```python
# Executable nodes that should NOT be offered as agent tools (control flow,
# raw code). Everything else with role "executable" is tool-capable by default.
_NON_TOOL_NODE_IDS = frozenset({
    "if", "switch", "merge", "loop_over_items", "filter",
    "stop_and_error", "code",
})


def _default_usable_as_tool(node_id: str, role: str) -> bool:
    return role == "executable" and node_id not in _NON_TOOL_NODE_IDS
```

In the **AST manifest path** (around line 419–485, where `role`, `replacement_id`, `inputs` are read from `kwargs`), read the flags and pass them to the `NodeManifest(...)` constructor:

```python
    usable_as_tool = kwargs.get("usable_as_tool")
    if usable_as_tool is None:
        usable_as_tool = _default_usable_as_tool(node_id, role)
    else:
        usable_as_tool = bool(usable_as_tool)
    tool_side_effecting = bool(kwargs.get("tool_side_effecting", True))
```

Then add `usable_as_tool=usable_as_tool, tool_side_effecting=tool_side_effecting,` to that path's `NodeManifest(...)` call (the one near line 476–485). Use the real local variable name for the node id in that scope (it is `node_id`).

In the **runtime decorator path** (`def node(...)` ~line 750 and `_build_manifest(...)` call ~line 792): add `usable_as_tool: bool | None = None` and `tool_side_effecting: bool = True` to the `node(...)` signature, and pass to `_build_manifest`:

```python
            usable_as_tool=(
                _default_usable_as_tool(node_id, role)
                if usable_as_tool is None
                else bool(usable_as_tool)
            ),
            tool_side_effecting=tool_side_effecting,
```

Then update `_build_manifest` (around line 207–273) to accept `usable_as_tool: bool` and `tool_side_effecting: bool` params and set them on the `NodeManifest(...)` it constructs (the call near line 262). Add both params to its signature.

Note: confirm `_build_manifest`'s signature and the two `NodeManifest(...)` construction sites by reading them; set both new fields on each. The `node_id` variable is in scope in both paths.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest packages/nodes/tests/test_builtin_nodes.py::test_usable_as_tool_defaults -q`
Expected: PASS.

- [ ] **Step 6: Run the broader node + core suites (no regressions)**

Run: `uv run pytest packages/core/tests packages/nodes/tests -q`
Expected: PASS (manifest shape change is additive).

- [ ] **Step 7: Commit**

```bash
git add packages/core/noodle/models.py packages/core/noodle/sdk.py packages/nodes/tests/test_builtin_nodes.py
git commit -m "feat(sdk): usable_as_tool/tool_side_effecting manifest flags + GraphNode tool_mode"
```

---

## Task 3: `NodeToolAdapter` + `build_node_tool_adapter`

**Files:**
- Create: `packages/core/noodle/node_tool.py`
- Test: `packages/core/tests/test_node_tool.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_node_tool.py`:

```python
import asyncio

from noodle.models import GraphNode
from noodle.node_tool import TOOL_MODE_OUTPUT, build_node_tool_adapter
from noodle.sdk import NodeRegistry, node


def _registry_with_echo() -> NodeRegistry:
    reg = NodeRegistry()

    @node(name="Echo Upper", id="echo_upper", registry=reg,
          params={"text": {}, "prefix": {}})
    def echo_upper(text: str = "", prefix: str = "") -> dict:
        return {"shout": prefix + text.upper()}

    return reg


def test_tool_mode_output_constant() -> None:
    assert TOOL_MODE_OUTPUT == "tool"


def test_build_adapter_derives_schema_from_from_ai_params() -> None:
    reg = _registry_with_echo()
    gnode = GraphNode(
        id="t", type="echo_upper", tool_mode=True,
        tool_name="shout", tool_description="Shout text",
        params={
            "prefix": ">> ",  # Fixed
            "text": "{{ $fromAI('text', 'what to shout', 'string') }}",  # From-AI
        },
    )
    adapter = build_node_tool_adapter(reg.get("echo_upper"), gnode)
    assert adapter.schema.name == "shout"
    assert adapter.schema.description == "Shout text"
    assert list(adapter.schema.parameters.properties.keys()) == ["text"]
    assert adapter.schema.parameters.required == ["text"]
    assert adapter.side_effecting is True  # default conservative


def test_adapter_invoke_runs_node_with_fixed_plus_ai_args() -> None:
    reg = _registry_with_echo()
    gnode = GraphNode(
        id="t", type="echo_upper", tool_mode=True,
        params={
            "prefix": ">> ",
            "text": "{{ $fromAI('text', 'what to shout', 'string') }}",
        },
    )
    adapter = build_node_tool_adapter(reg.get("echo_upper"), gnode)
    out = asyncio.run(adapter.invoke_async({"text": "hello"}))
    # Output is JSON-encoded since the node returns a dict.
    assert out == '{"shout": ">> HELLO"}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_node_tool.py -q`
Expected: FAIL — `ModuleNotFoundError: noodle.node_tool`.

- [ ] **Step 3: Implement**

Create `packages/core/noodle/node_tool.py`:

```python
"""Run a normal node as an AI Agent tool ("tool mode").

A graph node flagged ``tool_mode`` does not execute in the data flow. The engine
turns it into a :class:`NodeToolAdapter` whose schema is derived from the node's
``$fromAI(...)`` parameter bindings; the node's own function runs only when the
agent calls the tool.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from noodle.ai_runtime import ToolAdapter, ToolParameterSchema, ToolSchema
from noodle.expr import build_context, evaluate, from_ai_binding

TOOL_MODE_OUTPUT = "tool"


def _build_schema(node_def: Any, graph_node: Any) -> ToolSchema:
    collector: dict[str, dict[str, str]] = {}
    ctx = build_context()
    ctx["_from_ai"] = from_ai_binding(collector=collector)
    evaluate(dict(graph_node.params), ctx)  # populates collector via $fromAI calls
    properties: dict[str, Any] = {}
    for arg_name, spec in collector.items():
        prop: dict[str, Any] = {"type": spec["type"] or "string"}
        if spec["description"]:
            prop["description"] = spec["description"]
        properties[arg_name] = prop
    manifest = node_def.manifest
    name = (graph_node.tool_name or manifest.id).strip()
    description = (
        graph_node.tool_description or manifest.description or manifest.name
    ).strip()
    return ToolSchema(
        name=name,
        description=description,
        parameters=ToolParameterSchema(
            properties=properties, required=list(collector.keys())
        ),
    )


def _as_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


class NodeToolAdapter(ToolAdapter):
    """Adapter that runs a tool-mode node's function on demand."""

    def __init__(
        self, *, node_def: Any, params: dict[str, Any],
        schema: ToolSchema, side_effecting: bool,
    ) -> None:
        self._node_def = node_def
        self._params = dict(params)
        self._schema = schema
        self._side_effecting = side_effecting

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    @property
    def side_effecting(self) -> bool:
        return self._side_effecting

    def _resolved_kwargs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ctx = build_context()
        ctx["_from_ai"] = from_ai_binding(ai_args=arguments)
        resolved = evaluate(self._params, ctx)
        nd = self._node_def
        if nd.accepts_var_keyword or not nd.param_names:
            return dict(resolved)
        return {k: v for k, v in resolved.items() if k in nd.param_names}

    def invoke(self, arguments: dict[str, Any]) -> str:
        if self._node_def.is_async:
            raise RuntimeError(
                f"{self._schema.name}: async tool node requires invoke_async"
            )
        return _as_text(self._node_def.func(**self._resolved_kwargs(arguments)))

    async def invoke_async(self, arguments: dict[str, Any]) -> str:
        kwargs = self._resolved_kwargs(arguments)
        nd = self._node_def
        if nd.is_async:
            return _as_text(await nd.func(**kwargs))
        return _as_text(await asyncio.to_thread(nd.func, **kwargs))


def build_node_tool_adapter(node_def: Any, graph_node: Any) -> NodeToolAdapter:
    """Construct the adapter for a ``tool_mode`` graph node."""
    schema = _build_schema(node_def, graph_node)
    side_effecting = bool(getattr(node_def.manifest, "tool_side_effecting", True))
    return NodeToolAdapter(
        node_def=node_def,
        params=graph_node.params,
        schema=schema,
        side_effecting=side_effecting,
    )
```

Note: `node_def` is the value returned by `registry.get(type)`; it has `.func`, `.is_async`, `.param_names`, `.accepts_var_keyword`, and `.manifest` (confirmed in `engine.py::_run_node`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_node_tool.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/core/noodle/node_tool.py packages/core/tests/test_node_tool.py
git commit -m "feat(core): NodeToolAdapter — run a tool-mode node deferred as an agent tool"
```

---

## Task 4: Engine interception in `_run_node`

**Files:**
- Modify: `packages/core/noodle/engine.py` (imports near top; `_run_node` insertion after the `graph_node.disabled` block ~line 863, before `kwargs: dict[str, Any] = {}` ~line 865)
- Test: `packages/core/tests/test_node_tool_engine.py`

- [ ] **Step 1: Write the failing integration test**

Create `packages/core/tests/test_node_tool_engine.py`. Reuse the agent/stub-model scaffolding from `packages/core/tests/test_engine_agent_actions.py` (read lines 1–120): copy its imports, its stub `ChatModelAdapter` subclass, and its agent-node registration pattern into this file, then add the tool-mode node and graph below. The point of this test: a `tool_mode` node wired into the agent's `tool` port is invoked by the model and its real function runs.

```python
import asyncio

from noodle.models import Edge, GraphNode, WorkflowGraph
from noodle.node_tool import TOOL_MODE_OUTPUT
from noodle.sdk import NodeRegistry, node
# ... plus the agent-loop imports copied from test_engine_agent_actions.py
# (ToolCall, ChatRequest/ChatResponse, AIMessage, the stub model, execute, etc.)


def _build_registry() -> NodeRegistry:
    reg = NodeRegistry()

    @node(name="Echo Upper", id="echo_upper", registry=reg, params={"text": {}})
    def echo_upper(text: str = "") -> dict:
        return {"shout": text.upper()}

    # Register an "agent" node whose stub model first calls the `shout` tool
    # with {"text": "hello"}, then returns a final answer. Copy the stub-model +
    # agent-node definition from test_engine_agent_actions.py and set the model
    # to emit: ToolCall(id="c1", name="shout", arguments={"text": "hello"}).
    # ... (agent node registration here, per the copied scaffold) ...
    return reg


async def _run() -> object:
    reg = _build_registry()
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t", type="echo_upper", tool_mode=True, tool_name="shout",
                params={"text": "{{ $fromAI('text', 'what to shout', 'string') }}"},
            ),
            GraphNode(id="a", type="agent"),
        ],
        edges=[Edge(source="t", source_output=TOOL_MODE_OUTPUT, target="a", target_input="tool")],
    )
    return await execute(graph, reg)


def test_tool_mode_node_is_invoked_by_agent() -> None:
    result = asyncio.run(_run())
    # Assert (mirroring test_engine_agent_actions.py's assertion style) that the
    # tool ran and produced "HELLO" — e.g. the agent's recorded tool result or
    # final answer contains "HELLO".
    assert "HELLO" in repr(result)
```

Finalize the agent-node + stub-model details and the exact assertion by copying the working pattern from `test_engine_agent_actions.py` (the `EchoTool`/agent/model there). The tool name in the model's `ToolCall` MUST be `"shout"` to match `tool_name` above.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_node_tool_engine.py -q`
Expected: FAIL — the `tool_mode` node currently executes normally (its `text` param `$fromAI` resolves with no `_from_ai` in context → expr error) and never becomes a tool, so the agent can't call `shout`.

- [ ] **Step 3: Implement the interception**

In `packages/core/noodle/engine.py`, add the import near the other `noodle.*` imports (by line 35):

```python
from noodle.node_tool import TOOL_MODE_OUTPUT, build_node_tool_adapter
```

In `_run_node`, insert this block **after** the `if graph_node.disabled:` block returns (right after line 863) and **before** `kwargs: dict[str, Any] = {}` (line 865):

```python
        if getattr(graph_node, "tool_mode", False):
            # Tool-mode node: don't run in the data flow. Emit a ToolAdapter on
            # the `tool` output so it flows into the AI Agent's tool port; the
            # node's function runs deferred when the agent invokes the tool.
            try:
                adapter = build_node_tool_adapter(node_def, graph_node)
            except Exception as exc:  # noqa: BLE001 - surface a clean node error
                run_status = RunStatus.error
                await finish(
                    NodeRunResult(
                        node_id=nid, status=NodeStatus.error,
                        error=f"{type(exc).__name__}: {exc}",
                        started_at=started, finished_at=time.time(),
                    )
                )
                return
            outputs = {TOOL_MODE_OUTPUT: adapter}
            node_outputs[nid] = outputs
            await finish(
                NodeRunResult(
                    node_id=nid, status=NodeStatus.success, outputs=outputs,
                    started_at=started, finished_at=time.time(),
                    node_type_version=node_def.manifest.version,
                )
            )
            return
```

This bypasses the normal param-evaluation, `_validate_output_kinds`, and function call — correct, because the tool-mode node's effective output is a single `tool` port carrying the adapter.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_node_tool_engine.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/noodle/engine.py packages/core/tests/test_node_tool_engine.py
git commit -m "feat(engine): emit a tool adapter for tool_mode nodes instead of running them"
```

---

## Task 5: Regression, lint, final verification

- [ ] **Step 1: Full core + nodes suites**

Run: `uv run pytest packages/core/tests packages/nodes/tests -q`
Expected: all PASS (pre-existing Pydantic `UserWarning` only).

- [ ] **Step 2: API suite (manifest is served via /nodes; ensure no break)**

Run: `cd apps/api && uv run pytest tests/test_nodes.py -q`
Expected: PASS.

- [ ] **Step 3: Lint the changed files**

Run: `uv run ruff check packages/core/noodle/expr.py packages/core/noodle/models.py packages/core/noodle/sdk.py packages/core/noodle/node_tool.py packages/core/noodle/engine.py packages/core/tests/test_expr.py packages/core/tests/test_node_tool.py packages/core/tests/test_node_tool_engine.py packages/nodes/tests/test_builtin_nodes.py`
Expected: `All checks passed!` (fix anything introduced).

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -A -- packages/core packages/nodes
git commit -m "chore: node tool mode phase 1 lint cleanup" || echo "nothing to commit"
```

Note: scope the `git add` to `packages/core packages/nodes` (a parallel effort edits other paths — never `git add -A` at the repo root).

---

## Notes for the implementer

- **Two manifest-build paths exist in `sdk.py`** (an AST extractor ~line 419 and the runtime `node()` decorator ~line 750 → `_build_manifest`). Set the new flags in **both**, or the API-served manifest and the AST preview will disagree.
- **`$fromAI` is positional-only** (the expression validator rejects keyword args). The Phase 2 toggle will generate positional calls.
- **Tool-mode bypasses output-kind validation** by design (the node's manifest declares `main`, not `tool`); intercepting early and returning is what makes that safe.
- **Credentials:** a tool-mode node's credential params are fixed values resolved by the host run before the graph executes (same as any node), so the deferred `invoke` already has resolved credentials in `self._params`.
- **Parallel effort:** another effort is actively editing `apps/web` and `apps/api` credential files. Stage only the specific files in each task's commit; never `git add -A` at the repo root.
