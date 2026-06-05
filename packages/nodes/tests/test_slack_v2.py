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


def test_slack_v2_node_is_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}
    expected = {
        "slack_send_message_v2": ("Slack Send Message", True),
        "slack_reply_in_thread_v2": ("Slack Reply In Thread", True),
        "slack_update_message_v2": ("Slack Update Message", True),
        "slack_delete_message_v2": ("Slack Delete Message", True),
        "slack_add_reaction_v2": ("Slack Add Reaction", True),
        "slack_list_channels_v2": ("Slack List Channels", False),
        "slack_list_users_v2": ("Slack List Users", False),
        "slack_open_direct_message_v2": ("Slack Open Direct Message", True),
    }

    for node_id, (name, side_effecting) in expected.items():
        manifest = manifests[node_id]
        assert manifest.name == name
        assert manifest.icon == "brand:slack"
        assert manifest.category == "Integrations"
        assert manifest.usable_as_tool is True
        assert manifest.tool_side_effecting is side_effecting

    params = {param.name: param for param in manifests["slack_send_message_v2"].params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "slack_bot"
    assert params["credentials"].credential.multi is True
    assert params["credentials"].credential.test_service == "slack_bot"
    assert params["blocks"].group == "Options"


def test_slack_v2_generated_source_is_available() -> None:
    source = getattr(registry.get("slack_update_message_v2").func, "__noodle_source__", "")

    assert "def slack_update_message_v2(" in source
    assert "credentials=None" in source
    assert "execute_registered_operation" in source
    assert "slack.message.update" in source


def test_slack_send_message_v2_builds_payload(monkeypatch) -> None:
    transport = _mock_transport({"ok": True, "ts": "123.456"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = registry.get("slack_send_message_v2").func(
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

    result = registry.get("slack_update_message_v2").func(
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

    result = registry.get("slack_reply_in_thread_v2").func(
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

    result = registry.get("slack_delete_message_v2").func(
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

    result = registry.get("slack_add_reaction_v2").func(
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

    result = registry.get("slack_list_channels_v2").func(
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

    result = registry.get("slack_list_users_v2").func(
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

    result = registry.get("slack_open_direct_message_v2").func(
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
