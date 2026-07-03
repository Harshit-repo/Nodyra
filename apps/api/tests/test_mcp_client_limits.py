import pytest

from app.models import MCPConnection
from app.services.mcp_client import MCPError, call_tool


async def test_call_tool_rejects_oversized_response(httpx_mock, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "mcp_max_response_bytes", 64)
    httpx_mock.add_response(
        url="https://mcp.example.com/",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "x" * 500}]},
        },
    )
    conn = MCPConnection(org_id="default", name="t", url="https://mcp.example.com/")
    with pytest.raises(MCPError, match="response too large"):
        await call_tool(conn, "echo", {}, decrypted_secret=None)
