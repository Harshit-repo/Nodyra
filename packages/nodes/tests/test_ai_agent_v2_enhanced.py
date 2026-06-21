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
