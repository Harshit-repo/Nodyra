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
    assert initialize_result("2025-03-26")["protocolVersion"] == "2025-03-26"


def test_initialize_falls_back_for_unknown_version() -> None:
    assert initialize_result("1999-01-01")["protocolVersion"] == "2025-06-18"
    assert initialize_result(None)["serverInfo"]["name"] == "noodle"


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
