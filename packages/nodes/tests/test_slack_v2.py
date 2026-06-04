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
    manifest = manifests["slack_send_message_v2"]

    assert manifest.name == "Slack Send Message"
    assert manifest.icon == "brand:slack"
    assert manifest.category == "Integrations"
    params = {param.name: param for param in manifest.params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "slack_bot"
    assert params["credentials"].credential.multi is True
    assert params["credentials"].credential.test_service == "slack_bot"
    assert params["blocks"].group == "Options"


def test_slack_v2_generated_source_is_available() -> None:
    source = getattr(registry.get("slack_send_message_v2").func, "__noodle_source__", "")

    assert "def slack_send_message_v2(" in source
    assert "credentials=None" in source
    assert "execute_registered_operation" in source
    assert "slack.message.send" in source


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
