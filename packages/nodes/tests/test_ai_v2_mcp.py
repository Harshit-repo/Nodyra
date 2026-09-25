import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from nodyra.ai_runtime import ToolAdapter, ToolSchema
from nodyra_nodes.ai_v2 import mcp as mcp_module
from nodyra_nodes.ai_v2.mcp import (
    McpServerConfig,
    McpToolAdapter,
    _config_from_credentials,
    mcp_call_tool,
    mcp_list_tools,
    mcp_tools,
)


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="echo",
                    description="Echo text back",
                    inputSchema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                ),
                SimpleNamespace(name="other", description=None, inputSchema=None),
            ]
        )

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if name == "structured":
            return SimpleNamespace(
                content=[], structuredContent={"ok": True}, isError=False
            )
        if name == "boom":
            return SimpleNamespace(
                content=[SimpleNamespace(text="it broke")],
                structuredContent=None,
                isError=True,
            )
        return SimpleNamespace(
            content=[SimpleNamespace(text=f"echo:{arguments.get('text', '')}")],
            structuredContent=None,
            isError=False,
        )


@pytest.fixture
def fake_transport(monkeypatch):
    session = FakeSession()

    @asynccontextmanager
    async def _fake(config):
        yield session

    monkeypatch.setattr(mcp_module, "_mcp_session", _fake)
    return session


CREDS = {"url": "https://example.com/mcp", "auth_token": "tok"}


def test_config_from_credentials_builds_headers() -> None:
    config = _config_from_credentials(
        {"url": "https://x/mcp", "auth_token": "abc", "headers_json": '{"X-A": "1"}'}
    )
    assert config.url == "https://x/mcp"
    assert config.headers["Authorization"] == "Bearer abc"
    assert config.headers["X-A"] == "1"


def test_config_requires_url() -> None:
    with pytest.raises(ValueError):
        _config_from_credentials({"auth_token": "abc"})


async def test_mcp_session_blocks_private_target(monkeypatch) -> None:
    @asynccontextmanager
    async def _blocked_transport(*args, **kwargs):
        raise AssertionError("private target should be blocked before transport")
        yield

    monkeypatch.setattr(mcp_module, "streamablehttp_client", _blocked_transport)
    with pytest.raises(ValueError, match="private"):
        async with mcp_module._mcp_session(
            McpServerConfig(url="http://127.0.0.1:8000/mcp")
        ):
            pass


async def test_mcp_tools_returns_adapters(fake_transport) -> None:
    adapters = await mcp_tools(credentials=CREDS)
    assert len(adapters) == 2
    assert all(isinstance(a, ToolAdapter) for a in adapters)
    echo = next(a for a in adapters if a.schema.name == "echo")
    assert echo.side_effecting is True
    assert echo.schema.parameters.required == ["text"]


async def test_mcp_tools_filter(fake_transport) -> None:
    adapters = await mcp_tools(credentials=CREDS, tool_filter="echo")
    assert [a.schema.name for a in adapters] == ["echo"]


async def test_adapter_invoke_async(fake_transport) -> None:
    adapters = await mcp_tools(credentials=CREDS)
    echo = next(a for a in adapters if a.schema.name == "echo")
    assert await echo.invoke_async({"text": "hi"}) == "echo:hi"


def test_adapter_sync_invoke_raises() -> None:
    adapter = McpToolAdapter(
        config=_config_from_credentials(CREDS),
        schema=ToolSchema(name="echo", description="d"),
        side_effecting=True,
    )
    with pytest.raises(RuntimeError):
        adapter.invoke({})


async def test_call_tool_node_parses_json(fake_transport) -> None:
    result = await mcp_call_tool(
        credentials=CREDS, tool_name="structured", arguments={"a": 1}
    )
    assert result == {"ok": True}
    assert fake_transport.calls == [("structured", {"a": 1})]


async def test_call_tool_node_error_raises(fake_transport) -> None:
    with pytest.raises(RuntimeError, match="it broke"):
        await mcp_call_tool(credentials=CREDS, tool_name="boom")


async def test_call_tool_accepts_json_string_arguments(fake_transport) -> None:
    await mcp_call_tool(
        credentials=CREDS, tool_name="echo", arguments=json.dumps({"text": "x"})
    )
    assert fake_transport.calls[-1] == ("echo", {"text": "x"})


async def test_list_tools_node(fake_transport) -> None:
    listing = await mcp_list_tools(credentials=CREDS)
    assert listing[0]["name"] == "echo"
    assert listing[0]["input_schema"]["type"] == "object"

@pytest.fixture(autouse=True)
def _blocked_egress_posture(monkeypatch):
    """These tests verify the BLOCKED posture of nodyra_nodes.http_security;
    single-tenant API processes default to allowing private egress (mirroring
    workers), so pin the env explicitly."""
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "0")

