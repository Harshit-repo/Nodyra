from app.mcp.protocol import (
    initialize_result,
    jsonrpc_error,
    jsonrpc_result,
    tool_result,
)


def test_jsonrpc_result_envelope() -> None:
    out = jsonrpc_result(7, {"ok": True})
    assert out == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}


def test_jsonrpc_error_envelope() -> None:
    out = jsonrpc_error(7, -32601, "Method not found", data={"method": "x"})
    assert out["error"]["code"] == -32601
    assert out["error"]["data"] == {"method": "x"}


def test_initialize_echoes_known_version() -> None:
    result = initialize_result("2025-03-26")
    assert result["protocolVersion"] == "2025-03-26"
    assert "get_workflow_authoring_guide" in result["instructions"]


def test_initialize_falls_back_for_unknown_version() -> None:
    assert initialize_result("1999-01-01")["protocolVersion"] == "2025-11-25"
    assert initialize_result(None)["serverInfo"]["name"] == "nodyra"


def test_tool_result_wraps_dict_with_structured_content() -> None:
    out = tool_result({"a": 1})
    assert out["isError"] is False
    assert out["structuredContent"] == {"a": 1}
    assert out["content"][0]["type"] == "text"
    assert '"a": 1' in out["content"][0]["text"]


def test_tool_result_error_is_plain_text() -> None:
    out = tool_result("boom", is_error=True)
    assert out["isError"] is True
    assert "structuredContent" not in out

def test_tool_result_sanitizes_non_finite_floats() -> None:
    """A run output containing NaN must reach the caller as JSON-safe nulls in
    both the text and the structured content — not a literal ``NaN`` token."""
    out = tool_result({"rows": [{"statistic": float("nan")}, {"ok": 1.0}]})
    assert out["isError"] is False
    assert out["structuredContent"]["rows"][0]["statistic"] is None
    assert "NaN" not in out["content"][0]["text"]
