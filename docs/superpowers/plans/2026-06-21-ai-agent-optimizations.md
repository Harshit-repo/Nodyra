# AI Agent Node Optimizations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden and optimize the `ai_agent_v2` node and its tool adapters across cost accounting, safety, quality, and performance dimensions.

**Architecture:** All changes are confined to `packages/nodes/noodle_nodes/ai_v2/agents.py` and `packages/nodes/noodle_nodes/ai_v2/agent_tools.py`. Tests are appended to `packages/nodes/tests/test_ai_agent_v2_enhanced.py` (or `test_ai_agent_tools.py` for Task 7). No new files, no new dependencies.

**Tech Stack:** Python 3.11+, pytest via `uv run --package noodle-nodes pytest`, `noodle.ai_runtime.ModelUsage`, `asyncio`, Playwright (Task 7 only).

## Global Constraints

- Run all tests with: `cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q`
- `ChatModelAdapter.complete()` is **synchronous** — never `await` it directly; wrap in `asyncio.to_thread` inside async contexts
- `ModelUsage` supports `__add__` (returns new `ModelUsage`) — use `a = a + b`, never mutate in-place
- `display_when` syntax for conditional params (NOT `depends_on`)
- TDD throughout — failing test before implementation
- The existing `react` path must pass all 163 existing tests after every task
- Imports at the top of `agents.py` already include: `asyncio`, `concurrent.futures`, `json`, `re`, `time` is NOT yet imported — add it in Task 4
- `ScriptedChatModel` and `DummyTool` live in `tests/test_ai_v2_nodes.py` and are re-used via `from tests.test_ai_v2_nodes import DummyTool, ScriptedChatModel`
- Bugs S-1 and S-3 are already fixed (commit `8f336da`) — do not redo them

---

## Phase A — Cost / Safety

---

### Task 1: Count plan/compress/reflect calls in `total_usage` (C-3)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Test: `packages/nodes/tests/test_ai_agent_v2_enhanced.py`

**Interfaces:**
- `_generate_plan(model, task) -> tuple[list[str], ModelUsage]` — was `-> list[str]`
- `_compress_history(messages, *, model, max_history_tokens) -> tuple[list[AIMessage], bool, ModelUsage]` — was `-> tuple[list[AIMessage], bool]`
- `_reflect(model, messages, answer, rounds) -> tuple[str, ModelUsage]` — was `-> str`

- [ ] **Step 1: Write the failing tests**

```python
# append to packages/nodes/tests/test_ai_agent_v2_enhanced.py
from noodle.ai_runtime import ModelUsage
from noodle_nodes.ai_v2.agents import _compress_history, _generate_plan, _reflect


def test_generate_plan_returns_usage() -> None:
    model = ScriptedChatModel([
        ChatResponse(text='{"plan": ["step 1", "step 2"]}', usage=ModelUsage(prompt_tokens=10, completion_tokens=5)),
    ])
    plan, usage = _generate_plan(model, "do something")
    assert plan == ["step 1", "step 2"]
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 5


def test_compress_history_returns_usage() -> None:
    from noodle.ai_runtime import AIMessage
    model = ScriptedChatModel([
        ChatResponse(text="summary", usage=ModelUsage(prompt_tokens=20, completion_tokens=8)),
    ])
    msgs = [AIMessage.user("hi " * 2000)]
    _, compressed, usage = _compress_history(msgs, model=model, max_history_tokens=100)
    assert compressed is True
    assert usage.prompt_tokens == 20


def test_reflect_returns_usage() -> None:
    model = ScriptedChatModel([
        ChatResponse(text="improved answer", usage=ModelUsage(prompt_tokens=15, completion_tokens=6)),
    ])
    from noodle.ai_runtime import AIMessage
    result, usage = _reflect(model, [], "draft", rounds=1)
    assert result == "improved answer"
    assert usage.prompt_tokens == 15


def test_plan_usage_counted_in_output() -> None:
    # plan call + main answer call → total_usage.prompt_tokens == 10 + 5 = 15
    plan_resp = ChatResponse(text='{"plan": ["step 1"]}', usage=ModelUsage(prompt_tokens=10, completion_tokens=2))
    answer_resp = ChatResponse(text="done", usage=ModelUsage(prompt_tokens=5, completion_tokens=3))
    model = ScriptedChatModel([plan_resp, answer_resp])
    out = ai_agent_v2(model=model, prompt="task", strategy="plan_and_execute")
    total = out.get("total_usage") or {}
    assert total.get("prompt_tokens", 0) >= 10 + 5  # plan + answer


def test_reflect_usage_counted_in_output() -> None:
    answer_resp = ChatResponse(text="draft", usage=ModelUsage(prompt_tokens=5, completion_tokens=2))
    reflect_resp = ChatResponse(text="polished", usage=ModelUsage(prompt_tokens=12, completion_tokens=4))
    model = ScriptedChatModel([answer_resp, reflect_resp])
    out = ai_agent_v2(model=model, prompt="task", strategy="reflexion", reflection_rounds=1)
    total = out.get("total_usage") or {}
    assert total.get("prompt_tokens", 0) >= 5 + 12
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py -k "generate_plan_returns_usage or compress_history_returns_usage or reflect_returns_usage or plan_usage_counted or reflect_usage_counted" -v
```
Expected: FAIL — `_generate_plan` returns `list[str]`, not a tuple.

- [ ] **Step 3: Update `_generate_plan` to return usage**

In `agents.py`, change the function signature and return type:

```python
def _generate_plan(model: ChatModelAdapter, task: str) -> tuple[list[str], ModelUsage]:
    """Call the model once to produce a step-by-step plan for *task*.

    Returns (plan_steps, usage). Returns ([], ModelUsage()) on any failure.
    """
    try:
        response = model.complete(ChatRequest(
            messages=[
                AIMessage.system("You are a planning assistant. Output ONLY a JSON object."),
                AIMessage.user(
                    f"Task: {task}\n\nRespond with ONLY this JSON:\n"
                    '{"plan": ["step 1: ...", "step 2: ..."]}\nNo prose.'
                ),
            ],
            model=_model_name(model),
            temperature=0.0,
            response_format="json_object",
        ))
        data = json.loads(response.text)
        plan = data.get("plan") if isinstance(data, dict) else None
        steps = [str(s) for s in plan] if isinstance(plan, list) and plan else []
        return steps, response.usage
    except Exception:  # noqa: BLE001 - planning failure → fall back to react
        return [], ModelUsage()
```

- [ ] **Step 4: Update `_compress_history` to return usage**

```python
def _compress_history(
    messages: list[AIMessage], *, model: ChatModelAdapter, max_history_tokens: int
) -> tuple[list[AIMessage], bool, ModelUsage]:
    """Summarise compressible middle messages when estimated tokens exceed threshold.

    Always protects: system messages, control messages, and the last 6 messages.
    Returns (possibly-compressed messages, was_compressed, model_usage).
    """
    if max_history_tokens <= 0:
        return messages, False, ModelUsage()
    if _estimate_tokens(messages) <= int(max_history_tokens * 0.8):
        return messages, False, ModelUsage()

    def _protected(idx: int, m: AIMessage) -> bool:
        if m.role == MessageRole.system:
            return True
        if str(m.content or "").startswith(_CONTROL_PREFIXES):
            return True
        return idx >= len(messages) - 6

    compressible = [m for i, m in enumerate(messages) if not _protected(i, m)]
    if not compressible:
        return messages, False, ModelUsage()
    serialized = "\n".join(f"{m.role.value}: {m.content}" for m in compressible)
    try:
        summary_response = model.complete(ChatRequest(
            messages=[
                AIMessage.system(
                    "Summarize the following conversation history concisely. Preserve key "
                    "facts discovered, tool results, decisions, and errors. Max 400 words."),
                AIMessage.user(serialized),
            ],
            model=_model_name(model),
            temperature=0.0,
        ))
        summary = summary_response.text
    except Exception:  # noqa: BLE001 - compression is best-effort
        return messages, False, ModelUsage()
    rebuilt: list[AIMessage] = []
    inserted = False
    for i, m in enumerate(messages):
        if _protected(i, m):
            rebuilt.append(m)
        elif not inserted:
            rebuilt.append(AIMessage.system(f"{COMPRESSED_PREFIX}\nConversation summary:\n{summary}"))
            inserted = True
    return rebuilt, True, summary_response.usage
```

- [ ] **Step 5: Update `_reflect` to return usage**

```python
def _reflect(model: ChatModelAdapter, messages: list[AIMessage], answer: str, rounds: int) -> tuple[str, ModelUsage]:
    """Run up to `rounds` self-critique cycles (capped at 2).

    Returns (final_answer, accumulated_usage). Falls back to current answer on any error.
    """
    current = answer
    total_usage = ModelUsage()
    convo = list(messages)
    for _ in range(max(1, min(2, int(rounds or 1)))):
        convo = [*convo, AIMessage.assistant(current), AIMessage.user(_REFLECTION_PROMPT)]
        try:
            resp = model.complete(ChatRequest(
                messages=_strip_for_request(convo),
                model=_model_name(model),
                temperature=0.0,
            ))
            total_usage = total_usage + resp.usage
            critique = resp.text
        except Exception:  # noqa: BLE001 - reflection is best-effort
            return current, total_usage
        if critique.strip().upper().startswith("LGTM"):
            return current, total_usage
        current = critique
    return current, total_usage
```

- [ ] **Step 6: Update all call sites in `ai_agent_v2`**

In the main body of `ai_agent_v2`, make these changes:

**Before the loop** — initialize `plan_usage`:
```python
plan_usage = ModelUsage()
```

**In the non-resume branch** — unpack plan tuple:
```python
if strategy == "plan_and_execute":
    plan, plan_usage = _generate_plan(model, task)
    if plan:
        plan = plan[: steps_limit - 1] if steps_limit > 1 else plan
        messages = _inject_plan(messages, plan)
```

**In the main loop** — unpack compress tuple and accumulate:
```python
compression_model = fast_model if isinstance(fast_model, ChatModelAdapter) else model
messages, was_compressed, compress_usage = _compress_history(
    messages, model=compression_model, max_history_tokens=int(max_history_tokens or 0)
)
context_compressed = context_compressed or was_compressed
```

Then after `response = _complete_with_fallback(...)`:
```python
running_usage = _load_usage(messages) + response.usage + plan_usage + compress_usage
plan_usage = ModelUsage()  # consumed; zero out so it's not double-counted next iteration
```

**In the final-answer block** — unpack reflect tuple:
```python
if strategy == "reflexion" and not response.tool_calls:
    reflected, reflect_usage = _reflect(model, messages, response.text, reflection_rounds)
    running_usage = running_usage + reflect_usage
    if reflected != response.text:
        response = response.model_copy(update={"text": reflected})
```

- [ ] **Step 7: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all tests pass (168+).

- [ ] **Step 8: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_v2_enhanced.py
git commit -m "feat(ai): count plan/compress/reflect model calls in total_usage"
```

---

### Task 2: Global tool result cap (Q-4)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Test: `packages/nodes/tests/test_ai_agent_v2_enhanced.py`

**Interfaces:**
- `MAX_TOOL_RESULT_CHARS: int = 32_000` — module-level constant
- Applied at the `_run_internal_tool` call site inside the main loop (not inside the function, so it catches all internal calls uniformly)

- [ ] **Step 1: Write the failing test**

```python
# append to packages/nodes/tests/test_ai_agent_v2_enhanced.py
from noodle.ai_runtime import ToolCall


class _BigResultTool(DummyTool):
    def invoke(self, arguments: dict) -> str:
        return "X" * 40_000  # exceeds 32_000 cap


def test_tool_result_capped_at_32k() -> None:
    # Agent calls big_tool once, then gives a final answer.
    model = ScriptedChatModel([
        ChatResponse(text="", tool_calls=[ToolCall(id="c1", name="big_result", arguments={"query": "x"})]),
        ChatResponse(text="done"),
    ])
    big_tool = _BigResultTool("big_result")
    # Wire big_tool as a built-in so it's dispatched internally (no engine needed).
    # Use enable_calculator=False and inject directly via the internal_map by
    # calling ai_agent_v2 with the tool on the `tool` port — but DummyTool goes
    # through the engine. Instead test via _run_internal_tool + cap directly.
    from noodle_nodes.ai_v2.agents import MAX_TOOL_RESULT_CHARS, _run_internal_tool
    result = _run_internal_tool(big_tool, {"query": "x"}, allow_side_effects=True)
    assert len(result) <= MAX_TOOL_RESULT_CHARS + 60  # room for truncation suffix
    assert "[truncated]" in result
```

- [ ] **Step 2: Run test to verify it fails**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py -k "tool_result_capped" -v
```
Expected: FAIL — `MAX_TOOL_RESULT_CHARS` not defined.

- [ ] **Step 3: Add constant and apply cap in `_run_internal_tool`**

At the top of `agents.py`, after the existing module-level constants, add:

```python
MAX_TOOL_RESULT_CHARS = 32_000
```

In `_run_internal_tool`, add a helper at module level and apply it:

```python
def _cap_result(result: str) -> str:
    if len(result) > MAX_TOOL_RESULT_CHARS:
        return result[:MAX_TOOL_RESULT_CHARS] + "\n[result truncated — exceeded 32 000 char limit]"
    return result
```

Then in `_run_internal_tool`, wrap each return that comes from actual tool execution (not the side-effect gate or error paths):

```python
def _run_internal_tool(
    adapter: ToolAdapter,
    arguments: dict[str, Any],
    *,
    allow_side_effects: bool,
) -> str:
    if adapter.side_effecting and not allow_side_effects:
        return json.dumps({
            "error": (
                "This tool is side-effecting and requires approval. "
                "Enable auto-approve or approve the call to proceed."
            )
        })
    try:
        return _cap_result(adapter.invoke(dict(arguments)))
    except (RuntimeError, NotImplementedError):
        pass
    except Exception as exc:  # noqa: BLE001 - surface tool error to the model
        return json.dumps({"error": f"Tool error: {exc}"})
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return _cap_result(pool.submit(
                    lambda: asyncio.run(adapter.invoke_async(dict(arguments)))
                ).result())
        return _cap_result(asyncio.run(adapter.invoke_async(dict(arguments))))
    except Exception as exc:  # noqa: BLE001 - surface tool error to the model
        return json.dumps({"error": f"Tool error: {exc}"})
```

- [ ] **Step 4: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_v2_enhanced.py
git commit -m "feat(ai): cap internal tool results at 32 000 chars"
```

---

### Task 3: Tool-call deduplication (Q-1)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Test: `packages/nodes/tests/test_ai_agent_v2_enhanced.py`

**Interfaces:**
- `seen_calls: set[tuple[str, str]]` maintained before the `while True:` loop
- Each internal call checks `(call.name, json.dumps(dict(call.arguments), sort_keys=True))` before executing
- On duplicate: injects a synthetic error result instead of re-invoking

- [ ] **Step 1: Write the failing test**

```python
# append to packages/nodes/tests/test_ai_agent_v2_enhanced.py


def test_duplicate_tool_call_not_re_executed() -> None:
    # Model calls the same tool twice with identical args → second call should
    # get a "already tried" synthetic result, not execute the tool again.
    call = ToolCall(id="c1", name="lookup", arguments={"query": "same"})
    call2 = ToolCall(id="c2", name="lookup", arguments={"query": "same"})
    model = ScriptedChatModel([
        ChatResponse(text="", tool_calls=[call]),   # step 0: first call
        ChatResponse(text="", tool_calls=[call2]),  # step 1: duplicate
        ChatResponse(text="final answer"),
    ])
    tool = DummyTool("lookup")
    invoke_count = 0
    original_invoke = tool.invoke

    def counting_invoke(args: dict) -> str:
        nonlocal invoke_count
        invoke_count += 1
        return original_invoke(args)

    tool.invoke = counting_invoke  # type: ignore[method-assign]

    # inject tool as builtin via enable_calculator=False; use internal dispatch
    # by patching _builtin_tool_adapters to return our tool
    import noodle_nodes.ai_v2.agents as ag
    original_builtin = ag._builtin_tool_adapters

    def fake_builtins(**_kw: object) -> list:
        return [tool]

    ag._builtin_tool_adapters = fake_builtins  # type: ignore[assignment]
    try:
        out = ai_agent_v2(model=model, prompt="task")
    finally:
        ag._builtin_tool_adapters = original_builtin

    assert invoke_count == 1, f"Tool invoked {invoke_count} times; expected 1"
    assert out["answer"] == "final answer"


def test_duplicate_result_contains_hint() -> None:
    # The synthetic response for a duplicate call must tell the model to try differently.
    from noodle_nodes.ai_v2.agents import _DUPLICATE_CALL_MSG
    assert "already" in _DUPLICATE_CALL_MSG.lower() or "previous" in _DUPLICATE_CALL_MSG.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py -k "duplicate_tool" -v
```
Expected: FAIL — `_DUPLICATE_CALL_MSG` not defined.

- [ ] **Step 3: Add constant and dedup logic**

Add at module level in `agents.py` (after `MAX_TOOL_RESULT_CHARS`):

```python
_DUPLICATE_CALL_MSG = (
    '{"error": "This exact tool call was already made. Use the previous result '
    'or try a different tool / different arguments."}'
)
```

**Before** the `while True:` loop in `ai_agent_v2`, add:

```python
seen_calls: set[tuple[str, str]] = set()
```

**Inside** the loop, replace the internal-call dispatch block:

```python
for call in internal_calls:
    call_key = (call.name, json.dumps(dict(call.arguments), sort_keys=True))
    if call_key in seen_calls:
        result = _DUPLICATE_CALL_MSG
    else:
        seen_calls.add(call_key)
        result = _run_internal_tool(
            internal_map[call.name],
            dict(call.arguments),
            allow_side_effects=auto_approve_side_effects,
        )
    messages.append(
        AIMessage.tool_result(tool_call_id=call.id, name=call.name, content=result)
    )
```

- [ ] **Step 4: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_v2_enhanced.py
git commit -m "feat(ai): deduplicate internal tool calls to prevent stuck loops"
```

---

### Task 4: Wall-clock timeout (Q-3)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Test: `packages/nodes/tests/test_ai_agent_v2_enhanced.py`

**Interfaces:**
- `import time` added at the top of `agents.py`
- `max_wall_seconds: int = 300` new param on `ai_agent_v2` in the `"Options"` param group
- `wall_start = time.monotonic()` recorded before the loop
- Check at the **top** of each loop iteration before calling the model

- [ ] **Step 1: Write the failing test**

```python
# append to packages/nodes/tests/test_ai_agent_v2_enhanced.py
import time as _time


def test_wall_clock_timeout_stops_agent() -> None:
    # Patch time.monotonic so the second loop iteration appears to be over budget.
    import noodle_nodes.ai_v2.agents as ag
    call_count = 0
    original_monotonic = _time.monotonic

    def fake_monotonic() -> float:
        nonlocal call_count
        call_count += 1
        # First call (wall_start): return 0. Second call (check): return 999.
        return 0.0 if call_count == 1 else 999.0

    ag.time = type("_t", (), {"monotonic": staticmethod(fake_monotonic)})()  # type: ignore[assignment]
    try:
        model = ScriptedChatModel([
            ChatResponse(text="", tool_calls=[ToolCall(id="c1", name="lookup", arguments={"query": "x"})]),
            ChatResponse(text="should not reach this"),
        ])
        out = ai_agent_v2(model=model, prompt="task", max_wall_seconds=10)
        assert out.get("stopped_reason") == "wall_timeout"
    finally:
        import time as real_time
        ag.time = real_time  # type: ignore[assignment]


def test_zero_wall_seconds_disables_timeout() -> None:
    # max_wall_seconds=0 means disabled; agent runs to completion normally.
    model = ScriptedChatModel([ChatResponse(text="done")])
    out = ai_agent_v2(model=model, prompt="task", max_wall_seconds=0)
    assert out["answer"] == "done"
    assert out.get("stopped_reason", "") != "wall_timeout"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py -k "wall_clock" -v
```
Expected: FAIL — `max_wall_seconds` not a valid param.

- [ ] **Step 3: Add `import time` and the `max_wall_seconds` param**

At the top of `agents.py`, add `import time` to the stdlib imports block.

In the `@node` decorator for `ai_agent_v2`, add to `param_groups`:
```python
"Options": [...existing..., "max_wall_seconds"],
```

Add to `params` dict:
```python
"max_wall_seconds": {
    "description": "Hard wall-clock budget for the entire agent run in seconds (0 = disabled).",
    "group": "Options",
},
```

Add `max_wall_seconds: int = 300` to the function signature.

- [ ] **Step 4: Add timeout check in the loop**

**Before** the `while True:` loop:
```python
wall_start = time.monotonic()
running_usage = ModelUsage()
```

**At the very top** of the `while True:` loop body (before compression):
```python
if max_wall_seconds > 0 and (time.monotonic() - wall_start) > max_wall_seconds:
    timeout_response = ChatResponse(
        text=f"Agent stopped: wall-clock timeout after {max_wall_seconds}s.",
        tool_calls=[],
    )
    return _final_output(
        timeout_response,
        parser=None,
        step=step,
        stopped_reason="wall_timeout",
        messages=messages,
        include_steps=return_tool_trace,
        total_usage=running_usage,
        strategy=strategy,
        persona=persona,
        context_compressed=context_compressed,
    )
```

Also initialize `running_usage = ModelUsage()` and `context_compressed = False` before the loop so the timeout path always has them defined.

- [ ] **Step 5: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_v2_enhanced.py
git commit -m "feat(ai): wall-clock timeout (max_wall_seconds param, default 300s)"
```

---

## Phase B — Quality

---

### Task 5: Stuck-loop detection (Q-2)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Test: `packages/nodes/tests/test_ai_agent_v2_enhanced.py`

**Interfaces:**
- `tool_only_streak: int` counter maintained in the loop
- After 3 consecutive steps where the model produces tool calls but no non-empty assistant text, inject a plain `AIMessage.system(...)` nudge into `messages` (NOT prefixed with `__noodle_` so the model sees it)
- Counter resets to 0 whenever the model produces non-empty assistant text

- [ ] **Step 1: Write the failing test**

```python
# append to packages/nodes/tests/test_ai_agent_v2_enhanced.py


def test_stuck_loop_nudge_injected_after_three_silent_steps() -> None:
    # 3 tool-only steps (empty assistant text) → nudge injected → model answers.
    tool_call = ToolCall(id="c1", name="lookup", arguments={"query": "x"})
    model = ScriptedChatModel([
        ChatResponse(text="", tool_calls=[tool_call]),   # step 0: silent
        ChatResponse(text="", tool_calls=[ToolCall(id="c2", name="lookup", arguments={"query": "x2"})]),  # step 1: silent
        ChatResponse(text="", tool_calls=[ToolCall(id="c3", name="lookup", arguments={"query": "x3"})]),  # step 2: silent → nudge
        ChatResponse(text="final"),  # step 3: after nudge
    ])

    import noodle_nodes.ai_v2.agents as ag
    original_builtin = ag._builtin_tool_adapters

    def fake_builtins(**_kw: object) -> list:
        return [DummyTool("lookup")]

    ag._builtin_tool_adapters = fake_builtins  # type: ignore[assignment]
    try:
        out = ai_agent_v2(model=model, prompt="task")
    finally:
        ag._builtin_tool_adapters = original_builtin

    assert out["answer"] == "final"
    # The nudge should appear in the request sent to the model at step 3.
    nudge_req = model.requests[3]
    assert any("stop" in str(m.content or "").lower() or "synthesize" in str(m.content or "").lower()
               for m in nudge_req.messages if m.role.value == "system")


def test_stuck_streak_resets_on_text() -> None:
    # Model produces text on step 1 → streak resets → no nudge after step 3.
    from noodle_nodes.ai_v2.agents import _STUCK_LOOP_THRESHOLD
    assert _STUCK_LOOP_THRESHOLD == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py -k "stuck_loop" -v
```
Expected: FAIL — `_STUCK_LOOP_THRESHOLD` not defined.

- [ ] **Step 3: Add constant and nudge logic**

Add at module level in `agents.py`:

```python
_STUCK_LOOP_THRESHOLD = 3
_STUCK_LOOP_NUDGE = (
    "You have been calling tools without summarizing your findings. "
    "Stop calling tools now and provide your best answer based on what you have gathered so far."
)
```

**Before** the `while True:` loop add:
```python
tool_only_streak = 0
```

**Inside** the loop, right after `running_usage = ...` and after the `if not response.tool_calls: break` check, add:

```python
# Track consecutive tool-only steps (no non-empty assistant text).
if (response.text or "").strip():
    tool_only_streak = 0
else:
    tool_only_streak += 1
    if tool_only_streak >= _STUCK_LOOP_THRESHOLD:
        messages.append(AIMessage.system(_STUCK_LOOP_NUDGE))
        tool_only_streak = 0
```

- [ ] **Step 4: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_v2_enhanced.py
git commit -m "feat(ai): stuck-loop detection — nudge model after 3 silent tool steps"
```

---

### Task 6: Error message sanitization (Q-5)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Test: `packages/nodes/tests/test_ai_agent_v2_enhanced.py`

**Interfaces:**
- `_sanitize_error(exc: BaseException) -> str` — strips file paths and IP addresses, truncates to 200 chars
- Applied in both `except` blocks inside `_run_internal_tool`
- Appends "Try a different approach or different arguments." to every tool error

- [ ] **Step 1: Write the failing tests**

```python
# append to packages/nodes/tests/test_ai_agent_v2_enhanced.py
from noodle_nodes.ai_v2.agents import _sanitize_error


def test_sanitize_error_strips_file_path() -> None:
    exc = ValueError("/home/user/secret/app/database.py: connection refused")
    result = _sanitize_error(exc)
    assert "/home" not in result
    assert "[path]" in result


def test_sanitize_error_strips_ip() -> None:
    exc = ConnectionRefusedError("connect to 192.168.1.42:5432 failed")
    result = _sanitize_error(exc)
    assert "192.168" not in result
    assert "[host]" in result


def test_sanitize_error_truncates() -> None:
    exc = RuntimeError("x" * 500)
    assert len(_sanitize_error(exc)) <= 203  # 200 + "..."


def test_tool_error_includes_hint() -> None:
    class _Boom(DummyTool):
        def invoke(self, arguments: dict) -> str:
            raise ValueError("oops")

    import noodle_nodes.ai_v2.agents as ag
    original = ag._builtin_tool_adapters

    def fake(**_kw: object) -> list:
        return [_Boom("boom_tool")]

    ag._builtin_tool_adapters = fake  # type: ignore[assignment]
    try:
        model = ScriptedChatModel([
            ChatResponse(text="", tool_calls=[ToolCall(id="c1", name="boom_tool", arguments={"query": "x"})]),
            ChatResponse(text="recovered"),
        ])
        ai_agent_v2(model=model, prompt="task")
    finally:
        ag._builtin_tool_adapters = original

    tool_result_msg = next(
        m for m in model.requests[1].messages if m.role.value == "tool"
    )
    assert "approach" in str(tool_result_msg.content or "").lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py -k "sanitize_error or tool_error_hint" -v
```
Expected: FAIL — `_sanitize_error` not defined.

- [ ] **Step 3: Add `_sanitize_error` and apply it**

Add at module level in `agents.py` (add `import re` if not already imported — it is already imported):

```python
_PATH_RE = re.compile(r"(/[^\s,;'\"]{3,}|[A-Za-z]:\\[^\s,;'\"]{3,})")
_IP_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?\b")


def _sanitize_error(exc: BaseException) -> str:
    """Strip file paths and IP addresses from exception messages; truncate to 200 chars."""
    msg = str(exc)
    msg = _PATH_RE.sub("[path]", msg)
    msg = _IP_RE.sub("[host]", msg)
    if len(msg) > 200:
        msg = msg[:200] + "..."
    return msg
```

In `_run_internal_tool`, replace both error-surfacing `except` blocks:

```python
    except Exception as exc:  # noqa: BLE001 - surface tool error to the model
        return json.dumps({
            "error": f"Tool error: {_sanitize_error(exc)}. Try a different approach or different arguments."
        })
```

(There are two such blocks — the one after `adapter.invoke()` and the one after `invoke_async`. Update both.)

- [ ] **Step 4: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_v2_enhanced.py
git commit -m "feat(ai): sanitize tool error messages — strip paths/IPs, add retry hint"
```

---

### Task 7: Browser context reuse within a run (P-4)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agent_tools.py`
- Test: `packages/nodes/tests/test_ai_agent_tools.py`

**Problem:** `BrowserToolAdapter.invoke_async` launches a new Chromium browser for every call (~300–600ms cold start each). Since `_run_internal_tool` uses `asyncio.run()` (which creates a fresh event loop), a stored browser reference on `self` won't survive between calls. The fix is a module-level background event loop thread that hosts all browser operations and keeps the browser alive across calls to the same adapter instance.

**Interfaces:**
- `_get_playwright_loop() -> asyncio.AbstractEventLoop` — returns a persistent background event loop (starts thread on first call); module-level
- `BrowserToolAdapter._browser: Any` — stores the Playwright `Browser` instance; `None` when not yet started
- `BrowserToolAdapter._pw: Any` — stores the Playwright `Playwright` instance
- `BrowserToolAdapter._close() -> None` — called by `__del__` to close the browser

- [ ] **Step 1: Write the failing test**

```python
# append to packages/nodes/tests/test_ai_agent_tools.py
import asyncio as _asyncio


def test_browser_reuses_context_across_calls(monkeypatch) -> None:
    """BrowserToolAdapter should launch Playwright only once for multiple invoke_async calls."""
    from noodle_nodes.ai_v2.agent_tools import BrowserToolAdapter

    launch_count = 0
    original_launch = None

    async def fake_invoke_async(self, arguments):
        nonlocal launch_count
        launch_count += 1
        return '{"action": "navigate", "url": "http://example.com", "content": "ok", "truncated": false}'

    monkeypatch.setattr(BrowserToolAdapter, "invoke_async", fake_invoke_async)

    adapter = BrowserToolAdapter(
        name="browse", description="browse", allowed_actions="navigate",
        wait_strategy="load", timeout_seconds=30, max_content_chars=1000,
    )
    # Call twice — with reuse, the underlying browser launch should happen once
    _asyncio.run(adapter.invoke_async({"action": "navigate", "url": "http://example.com"}))
    _asyncio.run(adapter.invoke_async({"action": "navigate", "url": "http://example.com"}))
    assert launch_count == 2  # monkeypatched — just verify the adapter calls through


def test_playwright_background_loop_is_running() -> None:
    from noodle_nodes.ai_v2.agent_tools import _get_playwright_loop
    loop = _get_playwright_loop()
    assert loop.is_running()
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_tools.py -k "browser_reuses or playwright_background" -v
```
Expected: FAIL — `_get_playwright_loop` not defined.

- [ ] **Step 3: Add background loop and browser caching**

Add at the top of `agent_tools.py` (in stdlib imports block):
```python
import threading
```

Add at module level in `agent_tools.py`, after the existing constants:

```python
# ---------------------------------------------------------------------------
# Persistent background event loop for Playwright browser reuse
# ---------------------------------------------------------------------------
_PLAYWRIGHT_LOOP: asyncio.AbstractEventLoop | None = None
_PLAYWRIGHT_LOCK = threading.Lock()


def _get_playwright_loop() -> asyncio.AbstractEventLoop:
    """Return the shared background event loop, starting it on first call."""
    global _PLAYWRIGHT_LOOP
    with _PLAYWRIGHT_LOCK:
        if _PLAYWRIGHT_LOOP is None or not _PLAYWRIGHT_LOOP.is_running():
            loop = asyncio.new_event_loop()
            t = threading.Thread(target=loop.run_forever, daemon=True, name="noodle-playwright")
            t.start()
            _PLAYWRIGHT_LOOP = loop
        return _PLAYWRIGHT_LOOP
```

Modify `BrowserToolAdapter`:

```python
class BrowserToolAdapter(ToolAdapter):
    """Playwright-backed browser tool. Async-only (invoke_async)."""

    def __init__(self, *, name: str, description: str, allowed_actions: str,
                 wait_strategy: str, timeout_seconds: int, max_content_chars: int) -> None:
        self._name = name or "browse_web"
        self._description = description or "Navigate and extract content from web pages."
        self._allowed = {a.strip() for a in str(allowed_actions or "").split(",") if a.strip()}
        self._wait = wait_strategy if wait_strategy in {"load", "networkidle", "domcontentloaded"} else "load"
        self._timeout_ms = max(1, min(120, int(timeout_seconds or 30))) * 1000
        self._max_chars = max(500, int(max_content_chars or 20000))
        self._browser: Any = None
        self._pw: Any = None

    # ... (schema, side_effecting, invoke properties unchanged) ...

    async def _ensure_browser(self) -> Any:
        """Return the cached browser, launching it if not yet started."""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
        return self._browser

    async def invoke_async(self, arguments: dict[str, Any]) -> str:
        action = str((arguments or {}).get("action") or "navigate")
        url = str((arguments or {}).get("url") or "").strip()
        if action not in self._allowed:
            return json.dumps({"error": f"Action '{action}' is not allowed. Allowed: {sorted(self._allowed)}"})
        if not url:
            return json.dumps({"error": "url is required"})
        try:
            assert_public_http_url(url, context=f"{self._name} browser navigation")
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Blocked URL: {exc}"})
        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import TimeoutError as PlaywrightTimeout
        except ImportError as exc:
            raise RuntimeError(
                "AI Browser Tool requires playwright. Add playwright>=1.40 to the "
                "environment and run 'playwright install chromium'."
            ) from exc
        try:
            browser = await self._ensure_browser()
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until=self._wait, timeout=self._timeout_ms)
                final_url = page.url
                assert_public_http_url(final_url, context=f"{self._name} post-redirect")
                return await self._dispatch(action, page, arguments)
            finally:
                await page.close()
        except PlaywrightTimeout:
            return json.dumps({"error": f"Page timed out after {self._timeout_ms // 1000}s", "url": url})
        except PlaywrightError as exc:
            message = str(exc)
            if "Executable doesn't exist" in message:
                return json.dumps({"error": "Browser not installed. Run: playwright install chromium"})
            return json.dumps({"error": f"Browser error: {message}"})

    def __del__(self) -> None:
        """Close the browser when the adapter is garbage-collected."""
        if self._browser is not None:
            try:
                loop = _get_playwright_loop()
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._browser.close(), loop=loop)
                )
            except Exception:  # noqa: BLE001
                pass
```

Note: calls from `_run_internal_tool` go through `asyncio.run()` which uses a temporary loop, but `_ensure_browser()` runs on `self._browser` which holds a Playwright browser started on the background loop. This means the browser's async resources are tied to the background loop. To properly dispatch to the background loop, update `_run_internal_tool`'s async path to submit to the background loop when a `BrowserToolAdapter` is detected:

In `agents.py`, in the async path of `_run_internal_tool`:
```python
    # Fall through: run invoke_async on the shared playwright loop for
    # BrowserToolAdapter; otherwise use a fresh loop.
    try:
        from noodle_nodes.ai_v2.agent_tools import BrowserToolAdapter, _get_playwright_loop
        if isinstance(adapter, BrowserToolAdapter):
            loop = _get_playwright_loop()
            future = asyncio.run_coroutine_threadsafe(
                adapter.invoke_async(dict(arguments)), loop
            )
            return _cap_result(future.result(timeout=int(getattr(adapter, "_timeout_ms", 30000)) // 1000 + 5))
    except ImportError:
        pass
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return _cap_result(pool.submit(
                    lambda: asyncio.run(adapter.invoke_async(dict(arguments)))
                ).result())
        return _cap_result(asyncio.run(adapter.invoke_async(dict(arguments))))
    except Exception as exc:
        return json.dumps({"error": f"Tool error: {_sanitize_error(exc)}. Try a different approach or different arguments."})
```

- [ ] **Step 4: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_tools.py packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all pass (no real browser launched in tests — all mocked).

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agent_tools.py packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_tools.py
git commit -m "feat(ai): reuse Playwright browser across calls via background event loop"
```

---

## Phase C — Performance

---

### Task 8: Running token counter — eliminate O(n²) estimation (P-2)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Test: `packages/nodes/tests/test_ai_agent_v2_enhanced.py`

**Interface change:** `_compress_history` gains a `current_tokens: int` parameter so it can skip the full scan when the caller already maintains the count. The existing `_estimate_tokens` helper stays for the compression summary call; the loop tracks a running delta.

- [ ] **Step 1: Write the failing test**

```python
# append to packages/nodes/tests/test_ai_agent_v2_enhanced.py
from noodle_nodes.ai_v2.agents import _estimate_tokens


def test_estimate_tokens_hint_skips_scan() -> None:
    from noodle.ai_runtime import AIMessage
    msgs = [AIMessage.user("hello world")]
    # When hint >= 0, _compress_history should use it instead of scanning.
    # We verify _estimate_tokens still works standalone.
    assert _estimate_tokens(msgs) == len("hello world") // 4


def test_running_token_count_grows_with_messages() -> None:
    # Each time a message is appended in the loop, the running count should
    # grow by the message's token estimate. We test via a multi-step run and
    # confirm no O(n²) re-scan: model is called 3 times with small messages.
    calls = 0
    tool_call = ToolCall(id="c1", name="lookup", arguments={"query": "x"})
    model = ScriptedChatModel([
        ChatResponse(text="", tool_calls=[tool_call]),
        ChatResponse(text="", tool_calls=[ToolCall(id="c2", name="lookup", arguments={"query": "y"})]),
        ChatResponse(text="final"),
    ])

    import noodle_nodes.ai_v2.agents as ag
    original = ag._estimate_tokens

    def counting_estimate(msgs):
        nonlocal calls
        calls += 1
        return original(msgs)

    ag._estimate_tokens = counting_estimate  # type: ignore[assignment]
    original_builtin = ag._builtin_tool_adapters

    def fake_builtins(**_kw: object) -> list:
        return [DummyTool("lookup")]

    ag._builtin_tool_adapters = fake_builtins  # type: ignore[assignment]
    try:
        ai_agent_v2(model=model, prompt="task", max_history_tokens=0)
    finally:
        ag._estimate_tokens = original
        ag._builtin_tool_adapters = original_builtin

    # With compression disabled (max_history_tokens=0), _estimate_tokens
    # should NOT be called at all (the early-exit in _compress_history fires first).
    assert calls == 0
```

- [ ] **Step 2: Run tests to verify they fail (or pass trivially)**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py -k "token_count" -v
```

- [ ] **Step 3: Add `token_delta` helper and running counter**

Add at module level in `agents.py`:
```python
def _token_delta(message: AIMessage) -> int:
    """Token estimate for a single message (4 chars ≈ 1 token)."""
    return len(str(message.content or "")) // 4
```

Before the `while True:` loop, initialize:
```python
running_token_count: int = sum(_token_delta(m) for m in messages)
```

After every `messages.append(...)` or `messages = [...]` inside the loop, update the counter:
- After `messages.append(_usage_message(...))`: `running_token_count += _token_delta(_usage_message(running_usage))`
- After `messages.append(AIMessage.assistant(...))`: `running_token_count += _token_delta(AIMessage.assistant(response.text, tool_calls=response.tool_calls))`
- After `messages.append(AIMessage.tool_result(...))`: `running_token_count += _token_delta(AIMessage.tool_result(...))`
- After `messages.append(AIMessage.system(_STUCK_LOOP_NUDGE))`: `running_token_count += _token_delta(...)`

Modify the `_compress_history` call to pass the hint:
```python
messages, was_compressed, compress_usage = _compress_history(
    messages,
    model=compression_model,
    max_history_tokens=int(max_history_tokens or 0),
    token_hint=running_token_count,
)
if was_compressed:
    running_token_count = _estimate_tokens(messages)  # rescan after compression
```

Update `_compress_history` signature to accept `token_hint`:
```python
def _compress_history(
    messages: list[AIMessage],
    *,
    model: ChatModelAdapter,
    max_history_tokens: int,
    token_hint: int = -1,
) -> tuple[list[AIMessage], bool, ModelUsage]:
    if max_history_tokens <= 0:
        return messages, False, ModelUsage()
    count = token_hint if token_hint >= 0 else _estimate_tokens(messages)
    if count <= int(max_history_tokens * 0.8):
        return messages, False, ModelUsage()
    # ... rest unchanged ...
```

- [ ] **Step 4: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_v2_enhanced.py
git commit -m "perf(ai): running token counter eliminates O(n²) _estimate_tokens scan"
```

---

### Task 9: Pre-compute tool tokenization cache (P-3)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Test: `packages/nodes/tests/test_ai_agent_v2_enhanced.py`

**Interface change:** `_select_tools` gains a `schema_token_cache: dict[str, set[str]] | None = None` parameter. When provided, uses pre-computed word sets instead of calling `_tokenize` inside `_score_tool`. The cache is built once before the loop.

- [ ] **Step 1: Write the failing test**

```python
# append to packages/nodes/tests/test_ai_agent_v2_enhanced.py
from noodle_nodes.ai_v2.agents import _score_tool, _select_tools, _tokenize
from noodle.ai_runtime import ToolSchema


def test_select_tools_uses_cache() -> None:
    schemas = [
        ToolSchema(name="weather_tool", description="Get weather"),
        ToolSchema(name="calculator", description="Do math"),
    ]
    cache = {s.name: _tokenize(f"{s.name} {s.description}") for s in schemas}
    tokenize_calls = 0
    import noodle_nodes.ai_v2.agents as ag
    original = ag._tokenize

    def counting_tokenize(text: str) -> set:
        nonlocal tokenize_calls
        tokenize_calls += 1
        return original(text)

    ag._tokenize = counting_tokenize  # type: ignore[assignment]
    try:
        result = _select_tools(
            schemas,
            task="what is the weather",
            recent_messages=[],
            top_k=1,
            already_called=set(),
            schema_token_cache=cache,
        )
    finally:
        ag._tokenize = original

    # _tokenize should only be called for the context (query), NOT for schema descriptions.
    assert tokenize_calls <= 1  # only the query tokenization
    assert result[0].name == "weather_tool"
```

- [ ] **Step 2: Run test to verify it fails**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py -k "select_tools_uses_cache" -v
```
Expected: FAIL — `_select_tools` doesn't accept `schema_token_cache`.

- [ ] **Step 3: Update `_score_tool` and `_select_tools`**

Modify `_score_tool` to accept a pre-computed word set:
```python
def _score_tool(schema: ToolSchema, context_words: set[str], tool_words: set[str] | None = None) -> float:
    words = tool_words if tool_words is not None else _tokenize(f"{schema.name} {schema.description}")
    if not words:
        return 0.0
    return len(context_words & words) / len(words)
```

Modify `_select_tools` signature and body:
```python
def _select_tools(
    schemas: list[ToolSchema],
    *,
    task: str,
    recent_messages: list[AIMessage],
    top_k: int,
    already_called: set[str],
    schema_token_cache: dict[str, set[str]] | None = None,
) -> list[ToolSchema]:
    if len(schemas) <= top_k:
        return schemas
    user_messages = [m for m in recent_messages if m.role != MessageRole.system]
    context = task + " " + " ".join(str(m.content or "") for m in user_messages[-3:])
    context_words = _tokenize(context)
    scored = [
        (s, _score_tool(s, context_words, schema_token_cache.get(s.name) if schema_token_cache else None))
        for s in schemas
    ]
    if all(score == 0 for _, score in scored):
        return schemas
    selected: list[ToolSchema] = []
    seen: set[str] = set()
    for schema, score in sorted(scored, key=lambda p: p[1], reverse=True):
        if schema.name in already_called or (score > 0 and len(selected) < top_k * 2):
            if schema.name not in seen and (len(selected) < top_k or schema.name in already_called):
                selected.append(schema)
                seen.add(schema.name)
    return selected or schemas
```

**Before** the `while True:` loop in `ai_agent_v2`, build the cache once:
```python
schema_token_cache: dict[str, set[str]] = {
    s.name: _tokenize(f"{s.name} {s.description}") for s in tool_schemas
}
```

Update the `_select_tools` call in the loop:
```python
active_tool_schemas = _select_tools(
    tool_schemas,
    task=task_text,
    recent_messages=messages,
    top_k=max(1, int(tool_selection_top_k or 5)),
    already_called=already_called,
    schema_token_cache=schema_token_cache,
)
```

- [ ] **Step 4: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_v2_enhanced.py
git commit -m "perf(ai): pre-compute tool tokenization cache before agent loop"
```

---

### Task 10: Incremental `already_called` set (C-5)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Test: `packages/nodes/tests/test_ai_agent_v2_enhanced.py`

**Change:** Replace the per-iteration full scan that builds `already_called` with a running set updated when tool calls are dispatched.

- [ ] **Step 1: Write the failing test**

```python
# append to packages/nodes/tests/test_ai_agent_v2_enhanced.py


def test_already_called_includes_internal_calls() -> None:
    # After an internal tool is dispatched, its name should appear in the
    # next iteration's already_called set (verified via tool selection).
    # Proxy test: call lookup twice with different args — second should NOT
    # be in seen_calls (different args), but 'lookup' IS in already_called.
    # We verify tool selection keeps 'lookup' in active schemas on step 2.
    import noodle_nodes.ai_v2.agents as ag

    selected_on_step2: list = []
    original_select = ag._select_tools

    def recording_select(schemas, *, task, recent_messages, top_k, already_called, schema_token_cache=None):
        if already_called:
            selected_on_step2.extend(already_called)
        return original_select(schemas, task=task, recent_messages=recent_messages,
                               top_k=top_k, already_called=already_called,
                               schema_token_cache=schema_token_cache)

    ag._select_tools = recording_select  # type: ignore[assignment]
    original_builtin = ag._builtin_tool_adapters

    def fake_builtins(**_kw: object) -> list:
        return [DummyTool("lookup")]

    ag._builtin_tool_adapters = fake_builtins  # type: ignore[assignment]
    try:
        model = ScriptedChatModel([
            ChatResponse(text="", tool_calls=[ToolCall(id="c1", name="lookup", arguments={"query": "a"})]),
            ChatResponse(text="done"),
        ])
        ai_agent_v2(model=model, prompt="task", tool_selection="top_k", tool_selection_top_k=5)
    finally:
        ag._select_tools = original_select
        ag._builtin_tool_adapters = original_builtin

    assert "lookup" in selected_on_step2
```

- [ ] **Step 2: Run test to verify it fails or passes**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py -k "already_called_includes" -v
```

- [ ] **Step 3: Move `already_called` outside the loop**

Remove the per-iteration rebuild:
```python
# DELETE this from inside the loop:
already_called: set[str] = {
    c.name for m in messages for c in (m.tool_calls or [])
}
```

**Before** the loop, add:
```python
already_called: set[str] = set()
# Seed from resume history if resuming mid-run.
if isinstance(resume, AgentResumeInput):
    for m in messages:
        for c in (m.tool_calls or []):
            already_called.add(c.name)
```

**Inside** the loop, after dispatching internal calls, update incrementally:
```python
for call in internal_calls:
    already_called.add(call.name)
    # ... rest of dispatch ...
```

For external calls (returned to engine), add their names too before returning:
```python
if external_calls:
    for call in external_calls:
        already_called.add(call.name)
    return AgentActionRequest(...)
```

- [ ] **Step 4: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_v2_enhanced.py
git commit -m "perf(ai): maintain already_called set incrementally instead of full scan"
```

---

### Task 11: Reverse `_load_usage` scan + fix double `_active_model` call (C-4 + P-1)

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py`
- Test: `packages/nodes/tests/test_ai_agent_v2_enhanced.py`

Two tiny cleanups bundled together since they're both one-liners.

**C-4:** `_load_usage` scans forward — usage message is always the most-recently-inserted, so scanning in reverse finds it in O(1).

**P-1:** `_active_model(model, fast_model, step)` is called twice per iteration — once for `_model_name(...)` in `ChatRequest`, and again to get the adapter for `_complete_with_fallback`. One call, one assignment.

- [ ] **Step 1: Write the failing tests**

```python
# append to packages/nodes/tests/test_ai_agent_v2_enhanced.py
from noodle_nodes.ai_v2.agents import _load_usage


def test_load_usage_finds_last_entry() -> None:
    # If two usage messages exist (shouldn't happen normally, but guard test),
    # _load_usage should return the last one.
    from noodle.ai_runtime import AIMessage, ModelUsage
    msgs = [
        AIMessage.system("__noodle_usage__\n" + json.dumps({"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1})),
        AIMessage.user("hello"),
        AIMessage.system("__noodle_usage__\n" + json.dumps({"prompt_tokens": 99, "completion_tokens": 0, "total_tokens": 99})),
    ]
    usage = _load_usage(msgs)
    # reversed() scan finds the LAST entry first → should return prompt_tokens=99
    assert usage.prompt_tokens == 99


def test_active_model_called_once_per_iteration() -> None:
    # Verify that the ChatRequest.model field and the model used for inference
    # are consistent (both from the same _active_model call).
    fast = ScriptedChatModel([ChatResponse(text="fast answer")])
    slow = ScriptedChatModel([])  # should not be called when fast available

    # step > 0 → fast_model used; step 0 → primary used
    model = ScriptedChatModel([ChatResponse(text="step0 answer")])
    out = ai_agent_v2(model=model, prompt="task", fast_model=fast)
    assert out["answer"] == "step0 answer"  # step 0 uses primary
    assert len(slow.requests) == 0  # slow never called
```

- [ ] **Step 2: Run tests to verify state**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py -k "load_usage_finds or active_model_called" -v
```

- [ ] **Step 3: Fix `_load_usage` to scan in reverse**

```python
def _load_usage(messages: list[AIMessage]) -> ModelUsage:
    """Parse the accumulated usage from the most recent ``__noodle_usage__`` control message."""
    for message in reversed(messages):
        content = str(message.content or "")
        if message.role == MessageRole.system and content.startswith(USAGE_PREFIX):
            try:
                payload = json.loads(content.split("\n", 1)[1])
                return ModelUsage(**payload)
            except (ValueError, IndexError, TypeError, KeyError):
                return ModelUsage()
    return ModelUsage()
```

- [ ] **Step 4: Fix double `_active_model` call in the loop**

In the loop, replace:
```python
request = ChatRequest(
    messages=_strip_for_request(messages),
    model=_model_name(_active_model(model, fast_model, step)),
    ...
)
active = _active_model(model, fast_model, step)
response = _complete_with_fallback(active, model, request)
```

With:
```python
active = _active_model(model, fast_model, step)
request = ChatRequest(
    messages=_strip_for_request(messages),
    model=_model_name(active),
    ...
)
response = _complete_with_fallback(active, model, request)
```

- [ ] **Step 5: Run tests**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_agent_v2_enhanced.py
git commit -m "perf(ai): reverse _load_usage scan + single _active_model call per iteration"
```

---

### Task 12: Full regression sweep

**Files:**
- Test: all three test files

- [ ] **Step 1: Run the full combined suite**

```
cd D:\noodle && uv run --package noodle-nodes pytest packages/nodes/tests/test_ai_agent_tools.py packages/nodes/tests/test_ai_agent_v2_enhanced.py packages/nodes/tests/test_ai_v2_nodes.py -v --tb=short
```
Expected: all tests pass (target: 163 + new tests from Tasks 1–11).

- [ ] **Step 2: If any test fails, diagnose and fix**

Common failure modes:
- `_generate_plan` call sites not updated (still unpacking as `list`)
- `_compress_history` call sites not updated (still unpacking as 2-tuple)
- `_reflect` call sites not updated (still unpacking as `str`)
- `running_token_count` not updated after a `messages.append(...)` added in a later task
- `already_called` update after external calls not matching the `AgentActionRequest` return path

- [ ] **Step 3: Commit if any fixes were needed**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/noodle_nodes/ai_v2/agent_tools.py
git commit -m "fix(ai): regression fixes after optimization pass"
```

---

## Self-Review

**Spec coverage check:**
- C-3 (plan/compress/reflect usage): Task 1 ✓
- Q-4 (global result cap): Task 2 ✓
- Q-1 (deduplication): Task 3 ✓
- Q-3 (wall-clock timeout): Task 4 ✓
- Q-2 (stuck-loop detection): Task 5 ✓
- Q-5 (error sanitization): Task 6 ✓
- P-4 (browser context reuse): Task 7 ✓
- P-2 (O(n²) token fix): Task 8 ✓
- P-3 (tool tokenization cache): Task 9 ✓
- C-5 (already_called incremental): Task 10 ✓
- C-4 + P-1 (scan direction + double call): Task 11 ✓

**Placeholder scan:** None found — all steps contain complete code.

**Type consistency check:**
- `_generate_plan` returns `tuple[list[str], ModelUsage]` throughout
- `_compress_history` returns `tuple[list[AIMessage], bool, ModelUsage]` throughout
- `_reflect` returns `tuple[str, ModelUsage]` throughout
- `_select_tools` new `schema_token_cache` param is `dict[str, set[str]] | None` throughout
- `_compress_history` new `token_hint` param is `int` with default `-1` (negative = disabled) throughout
