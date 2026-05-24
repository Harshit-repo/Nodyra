import json

import requests

import noodle_nodes  # noqa: F401 - importing registers the built-in nodes
from noodle.engine import execute
from noodle.models import Edge, GraphNode, NodeStatus, WorkflowGraph
from noodle.sdk import registry


def test_expected_builtins_are_registered() -> None:
    ids = {m.id for m in registry.manifests()}
    expected = {
        "manual_trigger",
        "webhook_trigger",
        "if",
        "switch",
        "filter",
        "merge",
        "code",
        "http_request",
        "edit_fields",
        "sort",
    }
    assert expected <= ids


def test_expected_integration_nodes_are_registered() -> None:
    ids = {m.id for m in registry.manifests()}
    expected = {
        "slack_send_message",
        "discord_send_message",
        "smtp_send_email",
        "google_sheets_read",
        "google_sheets_append",
        "notion_create_page",
        "github_get_repo",
        "github_create_issue",
        "postgres_query",
        "mysql_query",
        "s3_put_object",
        "s3_get_object",
        "openai_chat",
        "anthropic_message",
        "stripe_create_customer",
        "airtable_list_records",
        "airtable_create_record",
    }
    assert expected <= ids

    manifests = {m.id: m for m in registry.manifests()}
    assert manifests["slack_send_message"].category == "Integrations"
    assert [port.name for port in manifests["slack_send_message"].inputs] == ["input"]


def test_trigger_inputs_and_node_inputs() -> None:
    triggers = {"manual_trigger", "schedule_trigger", "webhook_trigger"}
    for manifest in registry.manifests():
        if manifest.id in triggers:
            assert manifest.inputs == []
        elif manifest.id != "merge":
            assert [p.name for p in manifest.inputs] == ["input"]


async def test_trigger_into_code() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"n": 2}}),
            GraphNode(id="c", type="code", params={"code": "output = input['n'] * 10"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"] == 20


async def test_code_node_captures_variable_debug_metadata() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="c",
                type="code",
                params={
                    "code": (
                        "rows = [{'id': 1, 'name': 'Ada'}]\n"
                        "count = len(rows)\n"
                        "output = {'row_count': count, 'records': rows}"
                    )
                },
            )
        ],
    )
    result = await execute(graph, registry)
    variables = {
        variable["name"]: variable for variable in result.nodes["c"].debug["variables"]
    }
    assert variables["rows"]["length"] == 1
    assert variables["count"]["preview"] == 1
    assert variables["output"]["preview"]["row_count"] == 1


async def test_if_routes_false_branch() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"status": "open"}}),
            GraphNode(
                id="i",
                type="if",
                params={"field": "status", "operator": "equals", "value": "closed"},
            ),
            GraphNode(id="keep", type="no_op"),
        ],
        edges=[
            Edge(source="t", target="i"),
            Edge(source="i", source_output="false", target="keep"),
        ],
    )
    result = await execute(graph, registry)
    assert result.nodes["keep"].status == NodeStatus.success
    assert result.nodes["keep"].outputs["main"] == {"status": "open"}


async def test_sort_orders_items() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {}}),
            GraphNode(
                id="c",
                type="code",
                params={"code": "output = [{'n': 3}, {'n': 1}, {'n': 2}]"},
            ),
            GraphNode(
                id="s", type="sort", params={"field": "n", "order": "ascending"}
            ),
        ],
        edges=[Edge(source="t", target="c"), Edge(source="c", target="s")],
    )
    result = await execute(graph, registry)
    assert [row["n"] for row in result.nodes["s"].outputs["main"]] == [1, 2, 3]


async def test_switch_routes_via_dynamic_rules() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={"data": {"kind": "vip"}},
            ),
            GraphNode(
                id="s",
                type="switch",
                params={
                    "field": "kind",
                    "rules": {"vip": "vip", "free": "free"},
                },
                outputs_override=["vip", "free", "fallback"],
            ),
            GraphNode(id="caught", type="no_op"),
        ],
        edges=[
            Edge(source="t", target="s"),
            Edge(source="s", source_output="vip", target="caught"),
        ],
    )
    result = await execute(graph, registry)
    assert result.nodes["caught"].outputs["main"] == {"kind": "vip"}


async def test_join_and_split_round_trip() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {}}),
            GraphNode(
                id="src",
                type="code",
                params={"code": "output = ['a', 'b', 'c']"},
            ),
            GraphNode(id="j", type="join", params={"separator": "-"}),
            GraphNode(id="s", type="split", params={"separator": "-"}),
        ],
        edges=[
            Edge(source="t", target="src"),
            Edge(source="src", target="j"),
            Edge(source="j", target="s"),
        ],
    )
    result = await execute(graph, registry)
    assert result.nodes["j"].outputs["main"] == "a-b-c"
    assert result.nodes["s"].outputs["main"] == ["a", "b", "c"]


async def test_hash_node_produces_known_digest() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": "hello"}),
            GraphNode(id="h", type="hash", params={"algorithm": "sha256"}),
        ],
        edges=[Edge(source="t", target="h")],
    )
    result = await execute(graph, registry)
    assert (
        result.nodes["h"].outputs["main"]
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


async def test_json_round_trip() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"k": 1}}),
            GraphNode(id="enc", type="to_json"),
            GraphNode(id="dec", type="from_json"),
        ],
        edges=[Edge(source="t", target="enc"), Edge(source="enc", target="dec")],
    )
    result = await execute(graph, registry)
    assert result.nodes["dec"].outputs["main"] == {"k": 1}


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.reason = "OK" if status_code < 400 else "Not Found"
        self.text = json.dumps(payload)
        self.content = self.text.encode("utf-8")

    def json(self) -> object:
        return self._payload


def test_http_request_raises_on_error_status(monkeypatch) -> None:
    def fake_request(method: str, url: str, **kwargs):  # noqa: ARG001
        return FakeResponse({"message": "Not Found"}, status_code=404)

    monkeypatch.setattr(requests, "request", fake_request)

    node_def = registry.get("http_request")
    try:
        node_def.func(url="https://api.example.test/missing")
    except RuntimeError as exc:
        assert "HTTP 404" in str(exc)
        assert "Not Found" in str(exc)
    else:
        raise AssertionError("HTTP error status should fail the node")


def test_slack_node_builds_chat_post_message_payload(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse({"ok": True, "ts": "123.456"})

    monkeypatch.setattr(requests, "request", fake_request)

    node_def = registry.get("slack_send_message")
    result = node_def.func({"fallback": "hello"}, bot_token="xoxb-token", channel="C123")

    assert result == {"ok": True, "ts": "123.456"}
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://slack.com/api/chat.postMessage"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer xoxb-token"
    assert calls[0]["kwargs"]["json"] == {
        "channel": "C123",
        "text": '{"fallback": "hello"}',
    }


def test_google_sheets_append_derives_rows_from_input(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse({"updates": {"updatedRows": 2}})

    monkeypatch.setattr(requests, "request", fake_request)

    node_def = registry.get("google_sheets_append")
    result = node_def.func(
        [{"name": "Ada", "score": 9}, {"name": "Grace", "score": 10}],
        spreadsheet_id="sheet123",
        range_name="People!A:B",
        access_token="token",
    )

    assert result == {"updates": {"updatedRows": 2}}
    assert calls[0]["url"].endswith("/sheet123/values/People!A:B:append")
    assert calls[0]["kwargs"]["params"] == {"valueInputOption": "USER_ENTERED"}
    assert calls[0]["kwargs"]["json"] == {"values": [["Ada", 9], ["Grace", 10]]}


def test_stripe_create_customer_uses_input_and_metadata(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse({"id": "cus_123"})

    monkeypatch.setattr(requests, "request", fake_request)

    node_def = registry.get("stripe_create_customer")
    result = node_def.func(
        {"email": "ada@example.com", "name": "Ada"},
        api_key="sk_test",
        metadata={"plan": "pro"},
    )

    assert result == {"id": "cus_123"}
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer sk_test"
    assert calls[0]["kwargs"]["data"] == {
        "email": "ada@example.com",
        "name": "Ada",
        "description": "",
        "metadata[plan]": "pro",
    }


def test_type_cast_nodes_handle_common_inputs() -> None:
    assert registry.get("to_int").func("3") == 3
    assert registry.get("to_int").func("3.7") == 3
    assert registry.get("to_int").func(None) == 0
    assert registry.get("to_int").func(True) == 1

    assert registry.get("to_float").func("2.5") == 2.5
    assert registry.get("to_float").func(None) == 0.0

    assert registry.get("to_str").func(42) == "42"
    assert registry.get("to_str").func(None) == ""
    assert registry.get("to_str").func({"a": 1}) == '{"a": 1}'

    assert registry.get("to_bool").func("yes") is True
    assert registry.get("to_bool").func("No") is False
    assert registry.get("to_bool").func("0") is False
    assert registry.get("to_bool").func(7) is True

    import pytest

    with pytest.raises(ValueError):
        registry.get("to_bool").func("maybe")

    assert registry.get("to_list").func("a,b,c") == ["a", "b", "c"]
    assert registry.get("to_list").func("hello", separator="") == ["hello"]
    assert registry.get("to_list").func({"k": 1}) == [{"key": "k", "value": 1}]
    assert registry.get("to_list").func([1, 2, 3]) == [1, 2, 3]
    assert registry.get("to_list").func(None) == []


def test_convert_type_dispatches_on_target() -> None:
    fn = registry.get("convert_type").func
    assert fn("3", to="int") == 3
    assert fn("2.5", to="float") == 2.5
    assert fn(7, to="string") == "7"
    assert fn("true", to="boolean") is True
    assert fn("a,b", to="list") == ["a", "b"]  # comma is the default separator
    assert fn({"a": 1}, to="json") == '{"a": 1}'
    assert fn('{"a": 1}', to="object") == {"a": 1}
