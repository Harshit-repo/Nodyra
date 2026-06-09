from typing import Any
from unittest.mock import MagicMock

import pytest

import noodle_nodes  # noqa: F401 - importing registers provider nodes
from noodle.sdk import registry
from noodle_nodes.integrations_v2.errors import ProviderError
from noodle_nodes.integrations_v2.providers.slack import operations


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def _run(resource: str, operation: str, **kwargs: Any) -> Any:
    """Invoke the consolidated Slack node for a given resource + operation."""
    return registry.get("slack").func(resource=resource, operation=operation, **kwargs)


def test_slack_v2_node_is_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}
    # The per-operation nodes are consolidated into a single "slack" node.
    assert "slack_send_message_v2" not in manifests
    assert "slack_list_users_v2" not in manifests

    node = manifests["slack"]
    assert node.name == "Slack"
    assert node.icon == "brand:slack"
    assert node.category == "Integrations"
    assert node.usable_as_tool is True
    assert node.integration is not None

    resources = {r.id: [op.id for op in r.operations] for r in node.integration.resources}
    assert resources["message"] == ["send", "reply_thread", "update", "delete"]
    assert resources["channel"] == ["list"]
    assert resources["user"] == ["list"]

    params = {param.name: param for param in node.params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "slack_bot"
    assert params["credentials"].credential.multi is True
    assert params["credentials"].credential.test_service == "slack_bot"
    # The channel field is a real dynamic dropdown driven by the credential.
    assert params["channel"].load_options == "slack.list_channels"
    assert "credentials" in params["channel"].depends_on
    assert params["blocks"].group == "Options"


def test_slack_v2_generated_source_is_available() -> None:
    source = getattr(registry.get("slack").func, "__noodle_source__", "")

    assert "def slack(" in source
    assert "execute_integration_operation" in source
    assert "resource" in source
    assert "operation" in source


def test_slack_send_message_v2_builds_payload(monkeypatch) -> None:
    transport = _mock_transport({"ok": True, "ts": "123.456"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = _run(
        "message",
        "send",
        input={"fallback": "hello"},
        credentials={"bot_token": "xoxb-token"},
        channel="C123",
        blocks='[{"type": "section"}]',
        thread_ts="111.222",
    )

    assert result == {"ok": True, "ts": "123.456"}
    transport.request.assert_called_once_with(
        "POST",
        "/chat.postMessage",
        operation="send_message",
        json_body={
            "channel": "C123",
            "text": '{"fallback": "hello"}',
            "blocks": [{"type": "section"}],
            "thread_ts": "111.222",
        },
    )


def test_slack_update_message_v2_builds_payload(monkeypatch) -> None:
    transport = _mock_transport({"ok": True, "ts": "123.456"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = _run(
        "message",
        "update",
        input=None,
        credentials={"bot_token": "xoxb-token"},
        channel="C123",
        ts="123.456",
        text="Updated",
        blocks='[{"type": "section"}]',
    )

    assert result == {"ok": True, "ts": "123.456"}
    transport.request.assert_called_once_with(
        "POST",
        "/chat.update",
        operation="update_message",
        json_body={
            "channel": "C123",
            "ts": "123.456",
            "text": "Updated",
            "blocks": [{"type": "section"}],
        },
    )


def test_slack_reply_in_thread_v2_builds_payload(monkeypatch) -> None:
    transport = _mock_transport({"ok": True, "ts": "123.789"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = _run(
        "message",
        "reply_thread",
        input=None,
        credentials={"bot_token": "xoxb-token"},
        channel="C123",
        thread_ts="123.456",
        text="Thread reply",
        reply_broadcast=True,
    )

    assert result == {"ok": True, "ts": "123.789"}
    transport.request.assert_called_once_with(
        "POST",
        "/chat.postMessage",
        operation="reply_in_thread",
        json_body={
            "channel": "C123",
            "thread_ts": "123.456",
            "text": "Thread reply",
            "reply_broadcast": True,
        },
    )


def test_slack_delete_message_v2_builds_payload(monkeypatch) -> None:
    transport = _mock_transport({"ok": True, "ts": "123.456"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = _run(
        "message",
        "delete",
        input={"ignored": True},
        credentials={"bot_token": "xoxb-token"},
        channel="C123",
        ts="123.456",
    )

    assert result == {"ok": True, "ts": "123.456"}
    transport.request.assert_called_once_with(
        "POST",
        "/chat.delete",
        operation="delete_message",
        json_body={"channel": "C123", "ts": "123.456"},
    )


def test_slack_add_reaction_v2_builds_payload(monkeypatch) -> None:
    transport = _mock_transport({"ok": True})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = _run(
        "reaction",
        "add",
        input=None,
        credentials={"bot_token": "xoxb-token"},
        channel="C123",
        timestamp="123.456",
        reaction_name=":thumbsup:",
    )

    assert result == {"ok": True}
    transport.request.assert_called_once_with(
        "POST",
        "/reactions.add",
        operation="add_reaction",
        json_body={"channel": "C123", "timestamp": "123.456", "name": "thumbsup"},
    )


def test_slack_list_channels_v2_paginates(monkeypatch) -> None:
    transport = MagicMock()
    transport.request.side_effect = [
        {
            "ok": True,
            "channels": [{"id": "C1"}, {"id": "C2"}],
            "response_metadata": {"next_cursor": "next"},
        },
        {
            "ok": True,
            "channels": [{"id": "C3"}],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = _run(
        "channel",
        "list",
        input=None,
        credentials={"bot_token": "xoxb-token"},
        max_results=3,
    )

    assert result == {"channels": [{"id": "C1"}, {"id": "C2"}, {"id": "C3"}], "count": 3}
    assert transport.request.call_count == 2
    assert transport.request.call_args_list[0].kwargs["params"]["exclude_archived"] == "true"
    assert transport.request.call_args_list[1].kwargs["params"]["cursor"] == "next"


def test_slack_list_users_v2_filters_deleted(monkeypatch) -> None:
    transport = _mock_transport(
        {
            "ok": True,
            "members": [
                {"id": "U1", "deleted": False},
                {"id": "U2", "deleted": True},
            ],
            "response_metadata": {"next_cursor": ""},
        }
    )
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = _run(
        "user",
        "list",
        input=None,
        credentials={"bot_token": "xoxb-token"},
    )

    assert result == {"members": [{"id": "U1", "deleted": False}], "count": 1}
    transport.request.assert_called_once()
    assert transport.request.call_args[0] == ("GET", "/users.list")
    assert transport.request.call_args.kwargs["operation"] == "list_users"


def test_slack_open_direct_message_v2_builds_payload(monkeypatch) -> None:
    transport = _mock_transport({"ok": True, "channel": {"id": "D123"}})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = _run(
        "conversation",
        "open_dm",
        input=None,
        credentials={"bot_token": "xoxb-token"},
        users=["U1", "U2"],
        return_im=False,
    )

    assert result == {"ok": True, "channel": {"id": "D123"}}
    transport.request.assert_called_once_with(
        "POST",
        "/conversations.open",
        operation="open_direct_message",
        json_body={"users": "U1,U2", "return_im": False},
    )


def test_slack_send_message_v2_rejects_slack_api_error(monkeypatch) -> None:
    transport = _mock_transport({"ok": False, "error": "channel_not_found"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    with pytest.raises(ProviderError, match="channel_not_found"):
        operations.send_message(
            credentials={"bot_token": "xoxb-token"},
            channel="C404",
            text="Hello",
        )


def test_slack_send_message_v2_requires_channel() -> None:
    with pytest.raises(ValueError, match="channel"):
        operations.send_message(credentials={"bot_token": "xoxb-token"}, channel="")


def test_slack_update_message_v2_requires_payload() -> None:
    with pytest.raises(ValueError, match="text, blocks, or input"):
        operations.update_message(
            credentials={"bot_token": "xoxb-token"},
            channel="C123",
            ts="123.456",
        )
