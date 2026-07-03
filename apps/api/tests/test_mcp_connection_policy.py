import pytest
from fastapi import HTTPException

from app.models import MCPConnection
from app.routers.mcp_connections import _validated_allowed_tools
from app.services.mcp_client import ensure_tool_allowed


def _conn(**kw) -> MCPConnection:
    return MCPConnection(
        org_id="default",
        name="t",
        url="http://mcp.example/mcp",
        **kw,
    )


def test_disabled_connection_rejected():
    with pytest.raises(ValueError, match="disabled"):
        ensure_tool_allowed(_conn(enabled=False), "echo")


def test_allowlist_blocks_unlisted_tool():
    conn = _conn(allowed_tools=["echo", "search"])
    ensure_tool_allowed(conn, "echo")
    with pytest.raises(ValueError, match="not in this connection's allowed tools"):
        ensure_tool_allowed(conn, "delete_everything")


def test_null_allowlist_allows_all():
    ensure_tool_allowed(_conn(allowed_tools=None), "anything")


def test_malformed_allowlist_fails_closed():
    # A stored string must NOT degrade to substring matching ("tool" is a
    # substring of "deploy_tool") — the call is refused outright.
    conn = _conn(allowed_tools="deploy_tool")
    with pytest.raises(ValueError, match="malformed allowed_tools"):
        ensure_tool_allowed(conn, "tool")


def test_validated_allowed_tools_accepts_null_and_string_lists():
    assert _validated_allowed_tools(None) is None
    assert _validated_allowed_tools(["echo", " search "]) == ["echo", "search"]


@pytest.mark.parametrize(
    "bad",
    ["echo", {"echo": True}, ["echo", ""], ["echo", 42], [None], 7],
)
def test_validated_allowed_tools_rejects_non_string_lists(bad):
    with pytest.raises(HTTPException) as exc_info:
        _validated_allowed_tools(bad)
    assert exc_info.value.status_code == 422
