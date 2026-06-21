from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.sdk import registry
from noodle_nodes.ai_v2.agent_tools import CalculatorToolAdapter, CodeExecToolAdapter, WebSearchToolAdapter, _ast_security_check


def _result(adapter, **args) -> dict:
    return json.loads(adapter.invoke(args))


def test_calc_basic_arithmetic() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert _result(adapter, expression="2 + 2")["result"] == 4.0


def test_calc_sqrt() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert _result(adapter, expression="sqrt(144)")["result"] == 12.0


def test_calc_trig() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert round(_result(adapter, expression="sin(pi/2)")["result"], 6) == 1.0


def test_calc_blocks_import() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert "error" in _result(adapter, expression="__import__('os')")


def test_calc_division_by_zero() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert _result(adapter, expression="1/0")["error"] == "Division by zero"


def test_calc_overflow() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    out = _result(adapter, expression="10**10000")
    assert "error" in out


def test_calc_empty_expression() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert _result(adapter, expression="")["error"] == "No expression provided"


def test_calc_factorial_capped() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert "error" in _result(adapter, expression="factorial(5000)")


def test_calculator_node_registered_typed_port() -> None:
    manifest = registry.get("ai_calculator_tool").manifest
    out = next(o for o in manifest.outputs if o.name == "tool")
    assert out.data_kind == "ai_tool"


# ---------------------------------------------------------------------------
# Code Execution Tool tests
# ---------------------------------------------------------------------------


def _code(adapter, code, **extra) -> dict:
    return json.loads(adapter.invoke({"code": code, **extra}))


def _py_adapter(**kw):
    defaults = dict(name="run_code", description="", language="python",
                    allowed_modules="", timeout_seconds=10, max_output_chars=8000)
    defaults.update(kw)
    return CodeExecToolAdapter(**defaults)


def test_code_exec_simple_stdout() -> None:
    assert _code(_py_adapter(), "print(1 + 1)")["stdout"].strip() == "2"


def test_code_exec_captures_stderr() -> None:
    out = _code(_py_adapter(allowed_modules="sys"), "import sys; sys.stderr.write('boom')")
    assert "boom" in out["stderr"]


def test_code_exec_nonzero_exit_no_raise() -> None:
    out = _code(_py_adapter(allowed_modules="sys"), "import sys; sys.exit(3)")
    assert out["exit_code"] == 3


def test_code_exec_timeout_kills_process() -> None:
    out = _code(_py_adapter(timeout_seconds=1), "while True:\n    pass")
    assert "error" in out and "timed out" in out["error"].lower()


def test_ast_blocks_unlisted_import() -> None:
    with pytest.raises(PermissionError):
        _ast_security_check("import os", set())


def test_ast_blocks_eval() -> None:
    with pytest.raises(PermissionError):
        _ast_security_check("eval('1')", set())


def test_ast_blocks_dunder_import_call() -> None:
    with pytest.raises(PermissionError):
        _ast_security_check("__import__('os')", set())


def test_ast_blocks_dunder_attribute() -> None:
    with pytest.raises(PermissionError):
        _ast_security_check("().__class__.__bases__", set())


def test_ast_allows_listed_import() -> None:
    _ast_security_check("import math\nprint(math.pi)", {"math"})  # no raise


def test_code_exec_allowlist_permits_math() -> None:
    out = _code(_py_adapter(allowed_modules="math"), "import math\nprint(math.sqrt(9))")
    assert out["stdout"].strip() == "3.0"


def test_code_exec_blocked_import_returns_error() -> None:
    # allowed_modules="math" means only math is allowed; os is blocked by AST check.
    out = _code(_py_adapter(allowed_modules="math"), "import os\nprint(os.getcwd())")
    assert "error" in out


def test_code_exec_caps_output() -> None:
    out = _code(_py_adapter(max_output_chars=50), "print('x' * 5000)")
    assert out["truncated"] is True
    assert len(out["stdout"]) <= 50


def test_code_exec_node_registered_typed_port() -> None:
    manifest = registry.get("ai_code_execution_tool").manifest
    out = next(o for o in manifest.outputs if o.name == "tool")
    assert out.data_kind == "ai_tool"


# ---------------------------------------------------------------------------
# Web Search Tool tests
# ---------------------------------------------------------------------------


class _FakeHttpResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _tavily_adapter(**kw):
    defaults = dict(provider="tavily", credentials={"api_key": "k"}, name="web_search",
                    description="", max_results=5, search_depth="basic",
                    include_content=False, timeout_seconds=15)
    defaults.update(kw)
    return WebSearchToolAdapter(**defaults)


def test_web_search_missing_creds_raises() -> None:
    with pytest.raises(ValueError):
        WebSearchToolAdapter(provider="tavily", credentials={}, name="web_search",
                             description="", max_results=5, search_depth="basic",
                             include_content=False, timeout_seconds=15)


def test_web_search_duckduckgo_no_creds_ok() -> None:
    WebSearchToolAdapter(provider="duckduckgo", credentials={}, name="web_search",
                         description="", max_results=5, search_depth="basic",
                         include_content=False, timeout_seconds=15)


def test_web_search_formats_tavily() -> None:
    payload = {"results": [
        {"title": "T1", "url": "https://a.com", "content": "snippet one", "score": 0.9},
    ]}
    with patch("noodle_nodes.ai_v2.agent_tools.httpx.post",
               return_value=_FakeHttpResponse(payload)):
        out = json.loads(_tavily_adapter().invoke({"query": "indexing"}))
    assert out["total"] == 1
    assert out["results"][0]["url"] == "https://a.com"
    assert out["results"][0]["score"] == 0.9


def test_web_search_empty_results() -> None:
    with patch("noodle_nodes.ai_v2.agent_tools.httpx.post",
               return_value=_FakeHttpResponse({"results": []})):
        out = json.loads(_tavily_adapter().invoke({"query": "x"}))
    assert out == {"results": [], "total": 0, "provider": "tavily"}


def test_web_search_rate_limit() -> None:
    with patch("noodle_nodes.ai_v2.agent_tools.httpx.post",
               return_value=_FakeHttpResponse({}, status_code=429)):
        out = json.loads(_tavily_adapter().invoke({"query": "x"}))
    assert "error" in out


def test_web_search_truncates_snippet() -> None:
    payload = {"results": [{"title": "T", "url": "https://a.com",
                            "content": "z" * 2000, "score": 0.1}]}
    with patch("noodle_nodes.ai_v2.agent_tools.httpx.post",
               return_value=_FakeHttpResponse(payload)):
        out = json.loads(_tavily_adapter().invoke({"query": "x"}))
    assert len(out["results"][0]["snippet"]) <= 500


# ---------------------------------------------------------------------------
# Browser Tool tests
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402

from noodle_nodes.ai_v2.agent_tools import BrowserToolAdapter  # noqa: E402


def _browser(**kw):
    defaults = dict(name="browse_web", description="",
                    allowed_actions="navigate,extract,get_links",
                    wait_strategy="load", timeout_seconds=30, max_content_chars=20000)
    defaults.update(kw)
    return BrowserToolAdapter(**defaults)


def test_browser_ssrf_blocked() -> None:
    out = json.loads(asyncio.run(_browser().invoke_async(
        {"action": "navigate", "url": "http://192.168.1.1"})))
    assert "error" in out


def test_browser_disallowed_action() -> None:
    out = json.loads(asyncio.run(_browser().invoke_async(
        {"action": "fill_and_submit", "url": "https://example.com",
         "fields": {}, "submit_selector": "x"})))
    assert "error" in out and "allowed" in out["error"].lower()


def test_browser_invoke_sync_raises() -> None:
    with pytest.raises(RuntimeError):
        _browser().invoke({"action": "navigate", "url": "https://example.com"})


def test_browser_missing_url() -> None:
    out = json.loads(asyncio.run(_browser().invoke_async({"action": "navigate"})))
    assert "error" in out


# ---------------------------------------------------------------------------
# RAG Tool tests
# ---------------------------------------------------------------------------

from noodle.ai_runtime import RetrievedDocument, RetrieverAdapter  # noqa: E402
from noodle_nodes.ai_v2.agent_tools import RetrieverToolAdapter  # noqa: E402


class _FakeRetriever(RetrieverAdapter):
    def __init__(self, docs, *, raises=False):
        self._docs = docs
        self._raises = raises
        self.calls = []

    def retrieve(self, query, *, top_k=5):
        self.calls.append((query, top_k))
        if self._raises:
            raise RuntimeError("backend down")
        return self._docs[:top_k]


def _rag(retriever, **kw):
    defaults = dict(retriever=retriever, name="search_knowledge_base", description="",
                    top_k=5, max_doc_chars=2000, include_metadata=True)
    defaults.update(kw)
    return RetrieverToolAdapter(**defaults)


def test_rag_tool_formats_docs() -> None:
    docs = [RetrievedDocument(text="alpha", score=0.9, metadata={"source": "a.pdf"})]
    out = json.loads(_rag(_FakeRetriever(docs)).invoke({"query": "q"}))
    assert out["count"] == 1
    assert out["documents"][0]["index"] == 1
    assert out["documents"][0]["source"] == "a.pdf"


def test_rag_tool_empty_results() -> None:
    out = json.loads(_rag(_FakeRetriever([])).invoke({"query": "q"}))
    assert out == {"documents": [], "count": 0, "query": "q"}


def test_rag_tool_truncates_long_doc() -> None:
    docs = [RetrievedDocument(text="z" * 5000, score=0.1)]
    out = json.loads(_rag(_FakeRetriever(docs), max_doc_chars=100).invoke({"query": "q"}))
    assert len(out["documents"][0]["text"]) <= 120  # 100 + " [truncated]"


def test_rag_tool_retriever_failure_graceful() -> None:
    out = json.loads(_rag(_FakeRetriever([], raises=True)).invoke({"query": "q"}))
    assert "error" in out


def test_rag_tool_top_k_override() -> None:
    retr = _FakeRetriever([RetrievedDocument(text=str(i)) for i in range(20)])
    _rag(retr).invoke({"query": "q", "top_k": 7})
    assert retr.calls[-1][1] == 7


def test_rag_node_registered() -> None:
    manifest = registry.get("ai_rag_tool").manifest
    assert any(i.name == "retriever" and i.data_kind == "ai_retriever" for i in manifest.inputs)
    assert any(o.name == "tool" and o.data_kind == "ai_tool" for o in manifest.outputs)
