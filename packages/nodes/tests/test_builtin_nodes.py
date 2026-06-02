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
        "error_trigger",
        "if",
        "switch",
        "filter",
        "merge",
        "loop_over_items",
        "stop_and_error",
        "respond_to_webhook",
        "code",
        "http_request",
        "graphql_request",
        "jwt",
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
    assert manifests["openai_chat"].category == "AI"
    assert manifests["anthropic_message"].category == "AI"
    assert manifests["slack_send_message"].category == "Integrations"
    assert [port.name for port in manifests["slack_send_message"].inputs] == ["input"]
    bot_token = next(
        param for param in manifests["slack_send_message"].params if param.name == "bot_token"
    )
    assert bot_token.type == "credential"
    assert bot_token.credential is not None
    assert bot_token.credential.type == "slack_bot"
    assert bot_token.credential.key == "bot_token"

    smtp_params = {param.name: param for param in manifests["smtp_send_email"].params}
    assert "username" not in smtp_params
    assert "password" not in smtp_params
    smtp_credentials = smtp_params["credentials"]
    assert smtp_credentials.type == "credential"
    assert smtp_credentials.credential is not None
    assert smtp_credentials.credential.multi is True
    assert smtp_credentials.credential.fields == ["username", "password"]

    mysql_params = {param.name: param for param in manifests["mysql_query"].params}
    assert "username" not in mysql_params
    assert "password" not in mysql_params
    mysql_credentials = mysql_params["credentials"]
    assert mysql_credentials.type == "credential"
    assert mysql_credentials.credential is not None
    assert mysql_credentials.credential.multi is True
    assert mysql_credentials.credential.fields == ["username", "password"]


def test_trigger_inputs_and_node_inputs() -> None:
    triggers = {"manual_trigger", "schedule_trigger", "webhook_trigger", "error_trigger"}
    for manifest in registry.manifests():
        if manifest.id in triggers:
            assert manifest.inputs == []
        else:
            # Non-trigger nodes have at least one input. Most use the single
            # "input" port; some (merge, build_report) expose several named
            # ports, so only assert that inputs exist.
            assert len(manifest.inputs) >= 1


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


async def test_code_node_error_surfaces_traceback() -> None:
    """User-code exceptions must include a traceback so operators can locate
    the failing line, not just ``NameError: name 'undefined_thing' is not defined``."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="c", type="code", params={"code": "output = undefined_thing"}),
        ],
    )
    result = await execute(graph, registry)
    err = result.nodes["c"].error or ""
    assert "NameError" in err
    assert "undefined_thing" in err
    # Traceback marker; format_exc always includes this header for the active exc.
    assert "Traceback" in err


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


async def test_loop_over_items_routes_each_item_and_done_summary() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": [{"id": 1}, {"id": 2}]}),
            GraphNode(id="loop", type="loop_over_items"),
            GraphNode(id="each", type="no_op"),
            GraphNode(id="done", type="no_op"),
        ],
        edges=[
            Edge(source="t", target="loop"),
            Edge(source="loop", source_output="item", target="each"),
            Edge(source="loop", source_output="done", target="done"),
        ],
    )
    result = await execute(graph, registry)
    assert result.nodes["each"].outputs["main"] == [{"id": 1}, {"id": 2}]
    assert result.nodes["done"].outputs["main"] == {"items": [{"id": 1}, {"id": 2}], "count": 2}


async def test_stop_and_error_fails_workflow_with_message() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"reason": "bad"}}),
            GraphNode(id="stop", type="stop_and_error", params={"message": "forced failure"}),
        ],
        edges=[Edge(source="t", target="stop")],
    )
    result = await execute(graph, registry)
    assert result.status == "error"
    assert "forced failure" in result.nodes["stop"].error


def test_error_trigger_normalizes_error_payload() -> None:
    result = registry.get("error_trigger").func(
        error={"message": "boom", "node_id": "n1", "workflow_id": "w1", "run_id": "r1"}
    )
    assert result == {
        "message": "boom",
        "node_id": "n1",
        "workflow_id": "w1",
        "run_id": "r1",
        "raw": {"message": "boom", "node_id": "n1", "workflow_id": "w1", "run_id": "r1"},
    }


def test_respond_to_webhook_wraps_body_headers_and_status() -> None:
    result = registry.get("respond_to_webhook").func(
        {"ok": True}, status_code=202, headers={"X-Test": "yes"}, body_field="payload"
    )
    assert result == {"status_code": 202, "headers": {"X-Test": "yes"}, "body": {"ok": True}}


def test_jwt_sign_and_verify_round_trip() -> None:
    jwt_node = registry.get("jwt").func
    token = jwt_node({"sub": "user_1"}, operation="sign", secret="secret", algorithm="HS256")
    assert isinstance(token, str)
    decoded = jwt_node(token, operation="verify", secret="secret", algorithm="HS256")
    assert decoded["sub"] == "user_1"
    assert decoded["header"]["alg"] == "HS256"


def test_graphql_request_builds_expected_payload(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse({"data": {"viewer": {"login": "ada"}}})

    monkeypatch.setattr(requests, "request", fake_request)

    result = registry.get("graphql_request").func(
        url="https://api.example.test/graphql",
        query="query Viewer { viewer { login } }",
        variables={"first": 1},
        headers={"Authorization": "Bearer token"},
    )
    assert result == {"data": {"viewer": {"login": "ada"}}}
    assert calls[0]["method"] == "POST"
    assert calls[0]["kwargs"]["json"] == {
        "query": "query Viewer { viewer { login } }",
        "variables": {"first": 1},
    }


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


def test_smtp_node_uses_single_credentials_and_html_body(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeSMTP:
        def __init__(self, host=None, port=0, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            calls.append({"event": "init", "host": host, "port": port})
            # Real smtplib.SMTP connects in __init__ when a host is given, which
            # is what sets _host for STARTTLS. Mirror that here.
            if host:
                self.connect(host, port)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            calls.append({"event": "exit"})

        def connect(self, host: str, port: int):
            calls.append({"event": "connect", "host": host, "port": port})

        def ehlo(self):
            calls.append({"event": "ehlo"})

        def starttls(self, context=None):  # noqa: ANN001
            calls.append({"event": "starttls", "context": context})

        def login(self, username: str, password: str):
            calls.append({"event": "login", "username": username, "password": password})

        def send_message(self, message):
            calls.append({"event": "send_message", "message": message})

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    node_def = registry.get("smtp_send_email")
    result = node_def.func(
        host="smtp.example.test",
        port=587,
        credentials={"username": "user@example.test", "password": "app-password"},
        from_email="from@example.test",
        to_email="to@example.test",
        subject="Hi",
        body_format="html",
        body="<strong>Hello</strong>",
    )

    assert result == {
        "sent": True,
        "to": ["to@example.test"],
        "from": "from@example.test",
        "subject": "Hi",
        "body_format": "html",
        "html_preview": "<strong>Hello</strong>",
    }
    assert {"event": "connect", "host": "smtp.example.test", "port": 587} in calls
    assert any(call["event"] == "starttls" for call in calls)
    assert {
        "event": "login",
        "username": "user@example.test",
        "password": "app-password",
    } in calls
    sent = next(call["message"] for call in calls if call["event"] == "send_message")
    assert sent.get_content_maintype() == "multipart"
    assert "text/html" in sent.as_string()


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


def test_convert_fields_converts_selected_columns() -> None:
    fn = registry.get("convert_fields").func
    rows = [
        {"id": "1", "price": "19.99", "active": "true", "tags": "a,b"},
        {"id": "2", "price": "25.50", "active": "false", "name": "Ada"},
    ]

    result = fn(
        rows,
        conversions={
            "id": "int",
            "price": "float",
            "active": "boolean",
            "tags": "list",
        },
    )

    assert result == [
        {"id": 1, "price": 19.99, "active": True, "tags": ["a", "b"]},
        {"id": 2, "price": 25.5, "active": False, "name": "Ada"},
    ]
    assert rows[0]["id"] == "1"


def test_convert_fields_handles_single_object_and_errors() -> None:
    fn = registry.get("convert_fields").func
    assert fn({"count": "3"}, conversions={"count": "int"}) == {"count": 3}

    import pytest

    with pytest.raises(ValueError, match="field 'count'"):
        fn({"count": "nope"}, conversions={"count": "int"})

    with pytest.raises(ValueError, match="expected an object or list of objects"):
        fn("not a row", conversions={"count": "int"})
