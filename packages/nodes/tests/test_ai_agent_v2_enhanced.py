# packages/nodes/tests/test_ai_agent_v2_enhanced.py
from __future__ import annotations

import json
from typing import Any

import pytest

import noodle_nodes  # noqa: F401
from noodle.ai_runtime import AIMessage, ChatResponse, ToolCall
from noodle.sdk import registry
from noodle_nodes.ai_v2 import agents as agents_mod
from noodle_nodes.ai_v2.agents import (
    PERSONA_TEMPLATES,
    _apply_persona,
    _strip_control_messages,
    ai_agent_v2,
)
from tests.test_ai_v2_nodes import DummyTool, ScriptedChatModel


def test_apply_persona_prepends_template() -> None:
    out = _apply_persona("", "data_analyst")
    assert out == PERSONA_TEMPLATES["data_analyst"]


def test_apply_persona_combines_with_custom_system() -> None:
    out = _apply_persona("Be terse.", "data_analyst")
    assert out.startswith(PERSONA_TEMPLATES["data_analyst"])
    assert out.endswith("Be terse.")


def test_apply_persona_none_is_noop() -> None:
    assert _apply_persona("Hello", "none") == "Hello"


def test_strip_control_messages_removes_prefixes() -> None:
    msgs = [
        AIMessage.system("real system"),
        AIMessage.system("__noodle_usage__\n{}"),
        AIMessage.system("__noodle_plan__\n[]"),
        AIMessage.user("hi"),
    ]
    stripped = _strip_control_messages(msgs)
    assert [m.content for m in stripped] == ["real system", "hi"]


def test_strategy_and_persona_are_top_level() -> None:
    manifest = registry.get("ai_agent_v2").manifest
    params = {p.name: p for p in manifest.params}
    assert params["strategy"].group in (None, "")
    assert params["persona"].group in (None, "")


# ---------------------------------------------------------------------------
# Task 9: retriever port, subagent ports, unified tool assembly
# ---------------------------------------------------------------------------

from noodle.ai_runtime import RetrievedDocument, RetrieverAdapter


class _FakeRetriever(RetrieverAdapter):
    def __init__(self, docs):
        self._docs = docs

    def retrieve(self, query, *, top_k=5):
        return self._docs[:top_k]


def test_retriever_port_adds_search_tool() -> None:
    model = ScriptedChatModel([ChatResponse(text="hi")])
    retr = _FakeRetriever([RetrievedDocument(text="doc")])
    ai_agent_v2(model=model, retriever=retr, prompt="hello")
    sent = model.requests[0]
    assert any(t.name == "search_knowledge_base" for t in sent.tools)


def test_retriever_tool_overridden_by_external() -> None:
    model = ScriptedChatModel([ChatResponse(text="hi")])
    retr = _FakeRetriever([RetrievedDocument(text="doc")])
    external = DummyTool("search_knowledge_base")
    ai_agent_v2(model=model, retriever=retr, tool=external, prompt="hello")
    names = [t.name for t in model.requests[0].tools]
    assert names.count("search_knowledge_base") == 1


def test_subagent_port_adds_delegate_tool() -> None:
    from noodle_nodes.ai_v2.agent_tools import SubAgentAdapter
    sub = SubAgentAdapter(name="researcher", description="d", system="",
                          model=ScriptedChatModel([ChatResponse(text="x")]))
    model = ScriptedChatModel([ChatResponse(text="hi")])
    ai_agent_v2(model=model, subagent_1=sub, prompt="hello")
    assert any(t.name == "delegate_to_researcher" for t in model.requests[0].tools)


def test_multiple_subagents_all_appear() -> None:
    from noodle_nodes.ai_v2.agent_tools import SubAgentAdapter
    sub1 = SubAgentAdapter(name="writer", description="writes", system="",
                           model=ScriptedChatModel([ChatResponse(text="x")]))
    sub2 = SubAgentAdapter(name="editor", description="edits", system="",
                           model=ScriptedChatModel([ChatResponse(text="x")]))
    model = ScriptedChatModel([ChatResponse(text="hi")])
    ai_agent_v2(model=model, subagent_1=sub1, subagent_2=sub2, prompt="hello")
    names = [t.name for t in model.requests[0].tools]
    assert "delegate_to_writer" in names
    assert "delegate_to_editor" in names


def test_new_ports_registered() -> None:
    manifest = registry.get("ai_agent_v2").manifest
    names = {i.name: i.data_kind for i in manifest.inputs}
    assert names["fast_model"] == "ai_language_model"
    assert names["retriever"] == "ai_retriever"
    assert names["subagent_1"] == "ai_subagent"
    assert names["subagent_2"] == "ai_subagent"
    assert names["subagent_3"] == "ai_subagent"


# ---------------------------------------------------------------------------
# Task 10: dual-model routing
# ---------------------------------------------------------------------------

from noodle.ai_runtime import AgentResumeInput, ToolResult


def test_dual_model_step0_uses_main() -> None:
    main = ScriptedChatModel([ChatResponse(text="final")])
    fast = ScriptedChatModel([ChatResponse(text="fast")])
    ai_agent_v2(model=main, fast_model=fast, prompt="hi")
    assert len(main.requests) == 1 and len(fast.requests) == 0


def test_dual_model_step_gt0_uses_fast() -> None:
    main = ScriptedChatModel([ChatResponse(text="should not be called")])
    fast = ScriptedChatModel([ChatResponse(text="fast answer")])
    resume = AgentResumeInput(
        tool_results=[ToolResult(tool_call_id="c1", name="lookup", content="r")],
        messages_so_far=[AIMessage.user("hi")],
        step=1, max_steps=4,
    )
    out = ai_agent_v2(model=main, fast_model=fast, prompt="hi", agent_resume=resume)
    assert len(fast.requests) == 1
    assert out["answer"] == "fast answer"


def test_dual_model_fast_failure_falls_back(monkeypatch) -> None:
    class _BoomModel(ScriptedChatModel):
        def complete(self, request):
            raise RuntimeError("fast model down")
    main = ScriptedChatModel([ChatResponse(text="recovered")])
    fast = _BoomModel([ChatResponse(text="never")])
    resume = AgentResumeInput(
        tool_results=[ToolResult(tool_call_id="c1", name="lookup", content="r")],
        messages_so_far=[AIMessage.user("hi")],
        step=1, max_steps=4,
    )
    out = ai_agent_v2(model=main, fast_model=fast, prompt="hi", agent_resume=resume)
    assert out["answer"] == "recovered"


# ---------------------------------------------------------------------------
# Task 11: accumulated usage + cost tracking
# ---------------------------------------------------------------------------

from noodle.ai_runtime import ModelUsage


def _resp(text="", tool_calls=None, prompt=10, completion=5):
    return ChatResponse(
        text=text,
        tool_calls=tool_calls or [],
        usage=ModelUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        ),
    )


def test_total_usage_single_step() -> None:
    model = ScriptedChatModel([_resp(text="done", prompt=10, completion=5)])
    out = ai_agent_v2(model=model, prompt="hi")
    assert out["total_usage"]["prompt_tokens"] == 10
    assert out["total_usage"]["completion_tokens"] == 5
    assert out["steps_taken"] == 1
    assert out["strategy"] == "react"


def test_usage_message_not_sent_to_model() -> None:
    model = ScriptedChatModel([_resp(text="done")])
    ai_agent_v2(model=model, prompt="hi")
    for msg in model.requests[0].messages:
        assert not str(msg.content or "").startswith("__noodle_usage__")


def test_usage_accumulates_across_resume() -> None:
    # First call returns a tool call → AgentActionRequest carries usage forward.
    model = ScriptedChatModel(
        [_resp(tool_calls=[ToolCall(id="c1", name="lookup", arguments={})], prompt=10, completion=5)]
    )
    action = ai_agent_v2(model=model, tool=DummyTool("lookup"), prompt="hi", max_steps=4)
    usage_msgs = [
        m for m in action.messages_so_far
        if str(m.content or "").startswith("__noodle_usage__")
    ]
    assert usage_msgs, "usage carried in messages_so_far"
    payload = json.loads(usage_msgs[0].content.split("\n", 1)[1])
    assert payload["prompt_tokens"] == 10


def test_total_usage_accumulates_multi_step() -> None:
    """Tokens from two steps sum correctly in the final output."""
    main = ScriptedChatModel([ChatResponse(text="should not be called")])
    fast = ScriptedChatModel([_resp(text="final answer", prompt=20, completion=8)])
    # Build a resume input that already carries step-1 usage (15 prompt + 7 completion).
    prior_usage = ModelUsage(prompt_tokens=15, completion_tokens=7, total_tokens=22)
    from noodle_nodes.ai_v2.agents import _usage_message
    resume = AgentResumeInput(
        tool_results=[ToolResult(tool_call_id="c1", name="lookup", content="r")],
        messages_so_far=[AIMessage.user("hi"), _usage_message(prior_usage)],
        step=1,
        max_steps=4,
    )
    out = ai_agent_v2(model=main, fast_model=fast, prompt="hi", agent_resume=resume)
    # 15 + 20 = 35 prompt, 7 + 8 = 15 completion
    assert out["total_usage"]["prompt_tokens"] == 35
    assert out["total_usage"]["completion_tokens"] == 15
    assert out["steps_taken"] == 2


def test_persona_in_output() -> None:
    model = ScriptedChatModel([_resp(text="done")])
    out = ai_agent_v2(model=model, prompt="hi", persona="data_analyst")
    assert out["persona"] == "data_analyst"


# ---------------------------------------------------------------------------
# Task 12: built-in tool toggles
# ---------------------------------------------------------------------------


def test_enable_calculator_adds_tool() -> None:
    model = ScriptedChatModel([ChatResponse(text="hi")])
    ai_agent_v2(model=model, prompt="hi", enable_calculator=True)
    assert any(t.name == "calculate" for t in model.requests[0].tools)


def test_enable_code_execution_adds_tool() -> None:
    model = ScriptedChatModel([ChatResponse(text="hi")])
    ai_agent_v2(model=model, prompt="hi", enable_code_execution=True)
    assert any(t.name == "run_code" for t in model.requests[0].tools)


def test_enable_web_search_missing_creds_raises() -> None:
    model = ScriptedChatModel([ChatResponse(text="hi")])
    with pytest.raises(ValueError):
        ai_agent_v2(model=model, prompt="hi", enable_web_search=True,
                    web_search_provider="tavily", web_search_credentials=None)


def test_builtin_overridden_by_external() -> None:
    model = ScriptedChatModel([ChatResponse(text="hi")])
    ai_agent_v2(model=model, prompt="hi", enable_calculator=True, tool=DummyTool("calculate"))
    names = [t.name for t in model.requests[0].tools]
    assert names.count("calculate") == 1


def test_disabled_builtins_absent() -> None:
    model = ScriptedChatModel([ChatResponse(text="hi")])
    ai_agent_v2(model=model, prompt="hi")
    names = [t.name for t in model.requests[0].tools]
    assert "calculate" not in names
    assert "run_code" not in names
    assert "web_search" not in names


# ---------------------------------------------------------------------------
# Task 12a: hybrid tool dispatch (internal vs engine-mediated)
# ---------------------------------------------------------------------------


def test_internal_calculator_dispatched_in_node() -> None:
    # Model calls calculate, then answers. No external tool port, so the engine
    # never runs — the node must dispatch the calculator itself and return a dict.
    model = ScriptedChatModel([
        ChatResponse(text="", tool_calls=[ToolCall(id="c1", name="calculate", arguments={"expression": "6*7"})]),
        ChatResponse(text="The answer is 42."),
    ])
    out = ai_agent_v2(model=model, prompt="what is 6*7", enable_calculator=True,
                      side_effect_approval="auto_approve")
    assert not hasattr(out, "tool_calls")  # not an AgentActionRequest
    assert out["answer"] == "The answer is 42."
    steps = out["intermediate_steps"]
    assert any(s["tool"] == "calculate" and '"result": 42' in (s["result"] or "") for s in steps)


def test_external_tool_still_engine_mediated() -> None:
    # An external tool-port call must still return an AgentActionRequest.
    model = ScriptedChatModel([
        ChatResponse(text="", tool_calls=[ToolCall(id="c1", name="lookup", arguments={"query": "x"})]),
    ])
    out = ai_agent_v2(model=model, tool=DummyTool("lookup"), prompt="go",
                      side_effect_approval="auto_approve")
    assert hasattr(out, "tool_calls")  # AgentActionRequest
    assert out.tool_calls[0].name == "lookup"


def test_mixed_internal_external_returns_external_only() -> None:
    model = ScriptedChatModel([
        ChatResponse(text="", tool_calls=[
            ToolCall(id="c1", name="calculate", arguments={"expression": "1+1"}),
            ToolCall(id="c2", name="lookup", arguments={"query": "x"}),
        ]),
    ])
    out = ai_agent_v2(model=model, tool=DummyTool("lookup"), prompt="go",
                      enable_calculator=True, side_effect_approval="auto_approve")
    assert hasattr(out, "tool_calls")
    # Only the external call is handed to the engine; the calculator result is
    # already in messages_so_far as a tool result.
    assert [c.name for c in out.tool_calls] == ["lookup"]
    assert any(m.role.value == "tool" and m.name == "calculate" for m in out.messages_so_far)


def test_internal_side_effect_requires_approval() -> None:
    # Code execution is side-effecting; without approval the node returns an
    # approval-required tool result rather than running code.
    model = ScriptedChatModel([
        ChatResponse(text="", tool_calls=[ToolCall(id="c1", name="run_code", arguments={"code": "print(1)"})]),
        ChatResponse(text="ok"),
    ])
    out = ai_agent_v2(model=model, prompt="run", enable_code_execution=True,
                      side_effect_approval="require_approval")
    steps = out["intermediate_steps"]
    assert any("approval" in (s["result"] or "").lower() for s in steps)


# ---------------------------------------------------------------------------
# Task 13: context compression
# ---------------------------------------------------------------------------


def test_compression_disabled_by_default() -> None:
    model = ScriptedChatModel([ChatResponse(text="done")])
    out = ai_agent_v2(model=model, prompt="hi")
    assert out["context_compressed"] is False


def test_compression_triggers_over_threshold() -> None:
    # Long resumed history + tiny threshold → compression model call happens.
    long_history = [AIMessage.user("x" * 4000) for _ in range(8)]
    resume = AgentResumeInput(
        tool_results=[ToolResult(tool_call_id="c1", name="lookup", content="r")],
        messages_so_far=long_history, step=1, max_steps=4,
    )
    # 2 responses: [0] compression summary, [1] final answer
    model = ScriptedChatModel([ChatResponse(text="SUMMARY"), ChatResponse(text="final")])
    out = ai_agent_v2(model=model, prompt="hi", agent_resume=resume, max_history_tokens=100)
    assert out["context_compressed"] is True


def test_compression_failure_graceful() -> None:
    class _FailFirst(ScriptedChatModel):
        def __init__(self):
            super().__init__([ChatResponse(text="final")])
            self._first = True
        def complete(self, request):
            if self._first:
                self._first = False
                raise RuntimeError("compress failed")
            return super().complete(request)
    long_history = [AIMessage.user("x" * 4000) for _ in range(8)]
    resume = AgentResumeInput(
        tool_results=[ToolResult(tool_call_id="c1", name="lookup", content="r")],
        messages_so_far=long_history, step=1, max_steps=4,
    )
    out = ai_agent_v2(model=_FailFirst(), prompt="hi", agent_resume=resume, max_history_tokens=100)
    assert out["answer"] == "final"
    assert out["context_compressed"] is False


# ---------------------------------------------------------------------------
# Task 14: semantic tool selection (top-K lexical scoring)
# ---------------------------------------------------------------------------


def test_tool_selection_all_sends_everything() -> None:
    model = ScriptedChatModel([ChatResponse(text="hi")])
    tools = [DummyTool(f"tool_{i}") for i in range(8)]
    ai_agent_v2(model=model, tool=tools, prompt="anything", tool_selection="all")
    assert len(model.requests[0].tools) == 8


def test_tool_selection_top_k_limits() -> None:
    model = ScriptedChatModel([ChatResponse(text="hi")])
    # Names chosen so only some overlap the task wording.
    tools = [DummyTool("weather_lookup"), DummyTool("stock_price"),
             DummyTool("translate_text"), DummyTool("send_email"),
             DummyTool("calendar_create")]
    ai_agent_v2(model=model, tool=tools, prompt="what is the weather and stock price today",
                tool_selection="top_k", tool_selection_top_k=2)
    sent = {t.name for t in model.requests[0].tools}
    assert len(sent) <= 4  # top_k plus zero-overlap fallback cap
    assert "weather_lookup" in sent or "stock_price" in sent


def test_tool_selection_fallback_when_all_zero() -> None:
    model = ScriptedChatModel([ChatResponse(text="hi")])
    tools = [DummyTool("alpha"), DummyTool("beta"), DummyTool("gamma")]
    ai_agent_v2(model=model, tool=tools, prompt="zzzzz qqqqq",
                tool_selection="top_k", tool_selection_top_k=1)
    assert len(model.requests[0].tools) == 3  # no overlap → send all


# ---------------------------------------------------------------------------
# Task 15: plan-and-execute strategy
# ---------------------------------------------------------------------------


def test_plan_and_execute_injects_plan() -> None:
    plan_json = json.dumps({"plan": ["step 1: search", "step 2: summarize"]})
    model = ScriptedChatModel([ChatResponse(text=plan_json), ChatResponse(text="final answer")])
    out = ai_agent_v2(model=model, prompt="research X", strategy="plan_and_execute")
    # Second request (execution) should contain the plan text.
    exec_request = model.requests[1]
    joined = " ".join(str(m.content or "") for m in exec_request.messages)
    assert "step 1: search" in joined
    assert out["answer"] == "final answer"


def test_plan_and_execute_bad_json_falls_back() -> None:
    model = ScriptedChatModel([ChatResponse(text="not json at all"), ChatResponse(text="final")])
    out = ai_agent_v2(model=model, prompt="do X", strategy="plan_and_execute")
    assert out["answer"] == "final"  # ran as react, no crash


def test_plan_message_not_sent_as_user() -> None:
    plan_json = json.dumps({"plan": ["a", "b"]})
    model = ScriptedChatModel([ChatResponse(text=plan_json), ChatResponse(text="final")])
    ai_agent_v2(model=model, prompt="x", strategy="plan_and_execute")
    for m in model.requests[1].messages:
        assert not str(m.content or "").startswith("__noodle_plan__")


# ---------------------------------------------------------------------------
# Task 16: reflexion strategy (self-critique rounds)
# ---------------------------------------------------------------------------


def test_reflexion_lgtm_keeps_original() -> None:
    model = ScriptedChatModel([ChatResponse(text="my answer"), ChatResponse(text="LGTM")])
    out = ai_agent_v2(model=model, prompt="q", strategy="reflexion", reflection_rounds=1)
    assert out["answer"] == "my answer"


def test_reflexion_improves_answer() -> None:
    model = ScriptedChatModel([ChatResponse(text="rough draft"), ChatResponse(text="polished answer")])
    out = ai_agent_v2(model=model, prompt="q", strategy="reflexion", reflection_rounds=1)
    assert out["answer"] == "polished answer"


def test_reflexion_rounds_capped_at_two() -> None:
    model = ScriptedChatModel([
        ChatResponse(text="v0"), ChatResponse(text="v1"), ChatResponse(text="v2"),
        ChatResponse(text="v3 should never be requested"),
    ])
    ai_agent_v2(model=model, prompt="q", strategy="reflexion", reflection_rounds=2)
    # 1 initial + 2 reflection = 3 requests max
    assert len(model.requests) == 3


# ---------------------------------------------------------------------------
# Task 17: param-group wiring verification + untrusted-tool-result notice
# ---------------------------------------------------------------------------

from noodle.ai_runtime import ToolParameterSchema


def test_param_groups_present() -> None:
    manifest = registry.get("ai_agent_v2").manifest
    groups = {p.group for p in manifest.params if p.group}
    assert {"Strategy", "Context", "Built-in Tools", "Retriever", "Options"} <= groups


def test_tool_instruction_warns_about_untrusted_results() -> None:
    from noodle_nodes.ai_v2.agents import _tool_instruction
    from noodle.ai_runtime import ToolSchema, ToolParameterSchema
    msg = _tool_instruction([ToolSchema(name="x", description="d", parameters=ToolParameterSchema())])
    assert "untrusted" in msg.content.lower()


def test_backwards_compatible_default_run() -> None:
    model = ScriptedChatModel([ChatResponse(text="hello")])
    out = ai_agent_v2(model=model, prompt="hi")
    assert out["answer"] == "hello"
    assert out["strategy"] == "react"
    assert out["context_compressed"] is False
