"""MCP transport boundaries exercised through actual httpx request handling."""

from __future__ import annotations

import asyncio
import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models import MCPConnection
from app.services import mcp_client
from app.services.mcp_client import (
    MCPError,
    MCPPolicyError,
    MCPToolError,
    MCPTransportError,
    call_tool,
    discover_catalog,
    discover_tools,
)

TOOL = {
    "name": "write_record",
    "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
    "annotations": {"readOnlyHint": False},
}
CAPABILITIES = {"tools": {"listChanged": True}}


@pytest.fixture
def connection():
    return MCPConnection(
        id="transport-test", org_id="default", name="transport",
        url="https://mcp.example.com/mcp", auth_type="none", headers={},
    )


class Server:
    """An MCP peer that enforces negotiation/session binding and records writes."""

    def __init__(self, httpx_mock, *, session=True):
        self.messages = []
        self.calls = 0
        self.closed = False
        self.session = session
        self.tools = [copy.deepcopy(TOOL)]
        self.capabilities = copy.deepcopy(CAPABILITIES)
        self.protocol_version = "2025-06-18"
        self.result = {"content": [{"type": "text", "text": "saved"}]}
        self.override = None
        httpx_mock.add_callback(self.respond, is_reusable=True)

    def respond(self, request):
        if request.method == "DELETE":
            assert request.headers["mcp-session-id"] == "session-1"
            self.closed = True
            return httpx.Response(204)
        message = json.loads(request.content)
        self.messages.append(message)
        assert request.headers["accept"] == "application/json, text/event-stream"
        assert request.headers["accept-encoding"] == "identity"
        assert "authorization" not in request.headers
        if message.get("method") == "initialize":
            assert "mcp-session-id" not in request.headers
            return httpx.Response(200, headers={"Mcp-Session-Id": "session-1"} if self.session else {}, json={
                "jsonrpc": "2.0", "id": message["id"], "result": {
                    "protocolVersion": self.protocol_version, "capabilities": self.capabilities,
                    "serverInfo": {"name": "test-peer", "version": "1"},
                },
            })
        assert request.headers["mcp-protocol-version"] == self.protocol_version
        if self.session:
            assert request.headers["mcp-session-id"] == "session-1"
        if message.get("method") == "notifications/initialized" or "method" not in message:
            return httpx.Response(202)
        if self.override is not None:
            overridden = self.override(message)
            if overridden is not None:
                return overridden
        if message["method"] == "tools/list":
            result = {"tools": self.tools}
        else:
            assert message["method"] == "tools/call"
            self.calls += 1
            result = self.result
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})


async def test_catalog_negotiates_protocol_and_returns_capabilities(connection, httpx_mock):
    peer = Server(httpx_mock)
    catalog = await discover_catalog(connection, decrypted_secret=None)
    assert catalog == {"tools": [TOOL], "capabilities": CAPABILITIES,
                       "server_info": {"name": "test-peer", "version": "1"},
                       "protocol_version": "2025-06-18"}
    assert [message["method"] for message in peer.messages] == ["initialize", "notifications/initialized", "tools/list"]
    assert peer.closed


async def test_call_validates_same_session_contract_and_keeps_structured_content(connection, httpx_mock):
    peer = Server(httpx_mock)
    peer.result = {"content": [{"type": "text", "text": "human-readable receipt"}],
                   "structuredContent": {"record_id": "record-7"}}
    result = await call_tool(connection, TOOL["name"], {"value": "hello"}, decrypted_secret=None,
                             expected_tool=TOOL, expected_capabilities=CAPABILITIES)
    assert result == {"record_id": "record-7"}
    assert [message["method"] for message in peer.messages] == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert peer.calls == 1
    assert peer.closed


async def test_gateway_can_preserve_full_mcp_envelope(connection, httpx_mock):
    peer = Server(httpx_mock, session=False)
    peer.result["structuredContent"] = {"receipt": "provider-123"}
    assert await call_tool(connection, "write_record", {}, decrypted_secret=None, return_envelope=True) == peer.result


@pytest.mark.parametrize("change", ["required", "permissions", "removed", "capabilities"])
async def test_contract_drift_prevents_every_external_call(connection, httpx_mock, change):
    peer = Server(httpx_mock)
    if change == "required":
        peer.tools[0]["inputSchema"]["required"].append("scope")
    elif change == "permissions":
        peer.tools[0]["annotations"]["readOnlyHint"] = True
    elif change == "removed":
        peer.tools = []
    else:
        peer.capabilities["resources"] = {}
    with pytest.raises(MCPPolicyError) as error:
        await call_tool(connection, TOOL["name"], {}, decrypted_secret=None,
                        expected_tool=TOOL, expected_capabilities=CAPABILITIES)
    assert error.value.execution_attempted is False
    assert peer.calls == 0
    assert all(message["method"] != "tools/call" for message in peer.messages)
    assert peer.closed


async def test_description_change_does_not_revoke_approved_contract(connection, httpx_mock):
    peer = Server(httpx_mock)
    peer.tools[0]["description"] = "Improved documentation"
    await call_tool(connection, TOOL["name"], {}, decrypted_secret=None, expected_tool=TOOL)
    assert peer.calls == 1


@pytest.mark.parametrize("rpc_error", [False, True])
async def test_remote_error_is_failed_and_secrets_are_not_audited(connection, httpx_mock, monkeypatch, rpc_error):
    peer = Server(httpx_mock)
    secret = "upstream-secret-do-not-record"
    peer.result = {"isError": True, "content": [{"type": "text", "text": secret}]}
    if rpc_error:
        peer.override = lambda message: httpx.Response(200, json={
            "jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": secret, "data": {"token": secret}},
        })
    audit = AsyncMock()
    monkeypatch.setattr(mcp_client, "_commit_mcp_tool_audit", audit)
    with pytest.raises(MCPToolError) as error:
        await call_tool(connection, "write_record", {}, decrypted_secret=None)
    assert error.value.execution_attempted is True
    assert secret not in str(error.value)
    assert audit.await_args.kwargs["ok"] is False
    assert secret not in audit.await_args.kwargs["error"]


@pytest.mark.parametrize("envelope", [
    [], {"jsonrpc": "1.0", "id": 2, "result": {}},
    {"jsonrpc": "2.0", "id": 999, "result": {}},
    {"jsonrpc": "2.0", "id": True, "result": {}},
    {"jsonrpc": "2.0", "id": 2, "result": {}, "error": {}},
    {"jsonrpc": "2.0", "id": 2, "result": []},
    {"jsonrpc": "2.0", "id": 2, "error": {"message": "missing code"}},
])
async def test_invalid_rpc_envelopes_never_report_success(connection, httpx_mock, envelope):
    peer = Server(httpx_mock)
    peer.override = lambda message: httpx.Response(200, json=envelope)
    with pytest.raises(MCPTransportError) as error:
        await call_tool(connection, "write_record", {}, decrypted_secret=None)
    assert error.value.execution_attempted


@pytest.mark.parametrize("result", [
    {"content": {}, "isError": False}, {"content": [], "isError": "false"},
    {"content": [None]}, {"content": [{"type": "text"}]},
    {"content": [], "structuredContent": "not-an-object"},
    {"content": [], "_meta": "not-an-object"},
])
async def test_malformed_tool_results_fail_closed(connection, httpx_mock, result):
    peer = Server(httpx_mock)
    peer.result = result
    with pytest.raises(MCPError):
        await call_tool(connection, "write_record", {}, decrypted_secret=None)


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    async def aclose(self):
        self.closed = True


async def test_stream_cap_aborts_before_buffering_entire_response(connection, httpx_mock, monkeypatch):
    peer = Server(httpx_mock)
    monkeypatch.setattr(mcp_client.settings, "mcp_max_response_bytes", 512)
    stream = ChunkedStream([b"a" * 300, b"b" * 300, b"never-read" * 10000])
    peer.override = lambda message: httpx.Response(200, headers={"content-type": "application/json"}, stream=stream)
    with pytest.raises(MCPTransportError, match="too large"):
        await call_tool(connection, "write_record", {}, decrypted_secret=None)
    assert stream.read == 2
    assert stream.closed


async def test_sse_handles_notifications_ping_and_chunked_response_without_waiting_for_close(connection, httpx_mock):
    peer = Server(httpx_mock)
    stream = ChunkedStream([
        b': heartbeat\r\n\r\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\r\n\r\n',
        b'data: {"jsonrpc":"2.0","id":"ping-1","method":"ping"}\n\n',
        b'event: message\ndata: {"jsonrpc":"2.0","id":2,',
        b'"result":{"content":[{"type":"text","text":"saved"}]}}\n\n',
        b"never consumed",
    ])
    peer.override = lambda message: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    assert await call_tool(connection, "write_record", {}, decrypted_secret=None) == "saved"
    assert stream.read == 4
    assert stream.closed
    assert any(message.get("id") == "ping-1" and message.get("result") == {} for message in peer.messages)


async def test_sse_disconnect_before_result_is_ambiguous_and_never_retried(connection, httpx_mock):
    peer = Server(httpx_mock)
    peer.override = lambda message: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b": heartbeat\n\n")
    with pytest.raises(MCPTransportError, match="without a response") as error:
        await call_tool(connection, "write_record", {}, decrypted_secret=None)
    assert error.value.execution_attempted
    assert sum(message["method"] == "tools/call" for message in peer.messages) == 1


async def test_pagination_fetches_all_pages_and_binds_cursors(connection, httpx_mock):
    peer = Server(httpx_mock)
    other = {**TOOL, "name": "other"}
    def pages(message):
        cursor = message["params"].get("cursor")
        result = {"tools": [TOOL], "nextCursor": "page-2"} if cursor is None else {"tools": [other]}
        if cursor is not None:
            assert cursor == "page-2"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})
    peer.override = pages
    assert await discover_tools(connection, decrypted_secret=None) == [TOOL, other]


@pytest.mark.parametrize("failure", ["duplicate", "cycle", "page_limit", "tool_limit"])
async def test_discovery_is_bounded_and_unambiguous(connection, httpx_mock, monkeypatch, failure):
    peer = Server(httpx_mock)
    monkeypatch.setattr(mcp_client, "_MAX_DISCOVERY_PAGES", 2)
    monkeypatch.setattr(mcp_client, "_MAX_DISCOVERY_TOOLS", 2)
    def pages(message):
        number = message["id"]
        tools = [{**TOOL, "name": f"tool{number}"}]
        cursor = str(number)
        if failure == "duplicate":
            tools = [TOOL]
        elif failure == "cycle":
            cursor = "same-cursor"
        elif failure == "tool_limit":
            tools = [{**TOOL, "name": f"tool{i}"} for i in range(3)]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": number,
                                         "result": {"tools": tools, "nextCursor": cursor}})
    peer.override = pages
    with pytest.raises(MCPError, match={"duplicate": "Duplicate", "cycle": "cycle", "page_limit": "page limit", "tool_limit": "tool limit"}[failure]):
        await discover_tools(connection, decrypted_secret=None)


@pytest.mark.parametrize("tool", [
    {"name": "bad name", "inputSchema": {"type": "object"}},
    {"name": "bad", "inputSchema": {"type": "array"}},
    {"name": "bad", "inputSchema": {"type": "object", "required": "not-an-array"}},
    {**TOOL, "annotations": {"readOnlyHint": "true"}},
    {**TOOL, "outputSchema": []},
])
async def test_malformed_manifests_never_enter_catalog(connection, httpx_mock, tool):
    peer = Server(httpx_mock)
    peer.tools = [tool]
    with pytest.raises(MCPError):
        await discover_tools(connection, decrypted_secret=None)


async def test_schema_boolean_properties_and_union_types_convert_without_crashing():
    manifest = mcp_client.mcp_tool_to_node_manifest({"name": "flexible", "inputSchema": {
        "type": "object", "properties": {"anything": True, "maybe": {"type": ["string", "null"]}},
    }}, "connection")
    assert manifest["input_kinds"] == {"anything": "any", "maybe": "any"}


def test_contract_strips_schema_docs_without_stripping_named_properties_or_const_data():
    tool = {**TOOL, "inputSchema": {"type": "object", "title": "Tool form", "properties": {
        "description": {"type": "string", "description": "Help text"},
        "title": {"type": "object", "const": {"description": "security-relevant-data"}},
    }}, "_meta": {"permissions": ["write"]}}
    contract = mcp_client.tool_contract(tool)
    assert "title" not in contract["inputSchema"]
    assert contract["inputSchema"]["properties"]["description"] == {"type": "string"}
    assert contract["inputSchema"]["properties"]["title"]["const"] == {"description": "security-relevant-data"}
    assert contract["_meta"] == {"permissions": ["write"]}


async def test_compressed_response_is_rejected_before_decompression(connection, httpx_mock):
    peer = Server(httpx_mock)
    stream = ChunkedStream([b"potential compressed bomb"])
    peer.override = lambda message: httpx.Response(200, headers={"content-type": "application/json", "content-encoding": "gzip"}, stream=stream)
    with pytest.raises(MCPTransportError, match="Compressed"):
        await call_tool(connection, "write_record", {}, decrypted_secret=None)
    assert stream.read == 0
    assert stream.closed


async def test_total_deadline_ends_a_never_finishing_event_stream(connection, httpx_mock):
    peer = Server(httpx_mock)
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b": heartbeat\n\n"
                await asyncio.sleep(0.005)
    peer.override = lambda message: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=SlowStream())
    with pytest.raises(MCPTransportError, match="timed out") as error:
        await call_tool(connection, "write_record", {}, decrypted_secret=None, timeout_seconds=1.0)
    # The clock bounds even a responsive server that never returns its outcome.
    assert error.value.execution_attempted
    assert sum(message["method"] == "tools/call" for message in peer.messages) == 1


@pytest.mark.parametrize("payload", [
    b'{"jsonrpc":"2.0","id":2,"id":2,"result":{"content":[]}}',
    b'{"jsonrpc":"2.0","id":2,"result":{"content":[],"structuredContent":{"bad":NaN}}}',
])
async def test_duplicate_keys_and_nonfinite_json_are_rejected(connection, httpx_mock, payload):
    peer = Server(httpx_mock)
    peer.override = lambda message: httpx.Response(200, headers={"content-type": "application/json"}, content=payload)
    with pytest.raises(MCPTransportError, match="Invalid MCP JSON"):
        await call_tool(connection, "write_record", {}, decrypted_secret=None)


@pytest.mark.parametrize("capabilities", [{}, {"tools": []}, {"tools": {"listChanged": "yes"}}, {"tools": {}, "resources": "yes"}])
async def test_invalid_capability_negotiation_never_dispatches(connection, httpx_mock, capabilities):
    peer = Server(httpx_mock)
    peer.capabilities = capabilities
    with pytest.raises(MCPTransportError) as error:
        await call_tool(connection, "write_record", {}, decrypted_secret=None)
    assert not error.value.execution_attempted
    assert peer.calls == 0


def test_auth_headers_cannot_inject_a_session_or_override_transport(connection):
    connection.auth_type = "header"
    for name in ("Mcp-Session-Id", "MCP-Protocol-Version", "Host", "Accept-Encoding"):
        with pytest.raises(MCPPolicyError):
            mcp_client._build_auth_headers(connection, f"{name}:malicious")


async def test_unsupported_negotiated_version_never_dispatches(connection, httpx_mock):
    peer = Server(httpx_mock)
    peer.protocol_version = "2099-01-01"
    with pytest.raises(MCPTransportError, match="protocol version") as error:
        await call_tool(connection, "write_record", {}, decrypted_secret=None)
    assert not error.value.execution_attempted
    assert peer.calls == 0
    assert peer.closed
