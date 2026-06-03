from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from noodle_nodes.integrations_v2.providers.github import triggers as github_triggers
from noodle_nodes.integrations_v2.specs import (
    ProviderTriggerActivationContext,
    ProviderTriggerDeactivationContext,
    ProviderTriggerRequest,
)


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if method == "POST":
            return {"id": 12345}
        return {"status_code": 204}


def _signed_headers(raw_body: bytes, secret: str, event: str = "push") -> dict[str, str]:
    signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": signature,
    }


def test_github_trigger_creates_and_deletes_repository_webhook(monkeypatch) -> None:
    transport = FakeTransport()
    monkeypatch.setattr(github_triggers, "_transport", lambda _credentials: transport)

    subscription = github_triggers.activate_repository_webhook(
        ProviderTriggerActivationContext(
            workflow_id="wf",
            workflow_version_id="ver",
            node_id="github",
            callback_url="https://api.example.com/provider-webhook/sub",
            params={
                "credentials": {"token": "ghp_test"},
                "owner": "octocat",
                "repo": "hello-world",
                "events": "push, pull_request",
                "webhook_secret": "secret",
            },
        )
    )

    assert subscription.external_id == "12345"
    method, path, kwargs = transport.calls[0]
    assert method == "POST"
    assert path == "/repos/octocat/hello-world/hooks"
    assert kwargs["json_body"]["events"] == ["push", "pull_request"]
    assert kwargs["json_body"]["config"]["url"].endswith("/provider-webhook/sub")
    assert kwargs["json_body"]["config"]["secret"] == "secret"

    github_triggers.deactivate_repository_webhook(
        ProviderTriggerDeactivationContext(
            workflow_id="wf",
            workflow_version_id="ver",
            node_id="github",
            external_id=subscription.external_id,
            params={
                "credentials": {"token": "ghp_test"},
                "owner": "octocat",
                "repo": "hello-world",
            },
        )
    )

    assert transport.calls[-1][0] == "DELETE"
    assert transport.calls[-1][1] == "/repos/octocat/hello-world/hooks/12345"


def test_github_trigger_verifies_signature_and_normalizes_event() -> None:
    secret = "secret"
    body = {
        "repository": {"full_name": "octocat/hello-world"},
        "sender": {"login": "octocat"},
    }
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    event = github_triggers.handle_repository_event(
        ProviderTriggerRequest(
            headers=_signed_headers(raw, secret),
            query={},
            body=body,
            raw_body=raw,
        ),
        {"webhook_secret": secret},
    )

    assert event.response_status == 202
    assert event.dedupe_key == "github:delivery-1"
    assert event.payload is not None
    assert event.payload["event"] == "push"
    assert event.payload["repository"]["full_name"] == "octocat/hello-world"


def test_github_trigger_rejects_bad_signature() -> None:
    raw = b'{"ok":true}'
    event = github_triggers.handle_repository_event(
        ProviderTriggerRequest(
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": "sha256=bad",
            },
            query={},
            body={"ok": True},
            raw_body=raw,
        ),
        {"webhook_secret": "secret"},
    )

    assert event.payload is None
    assert event.response_status == 401


def test_github_trigger_ping_acknowledges_without_run() -> None:
    secret = "secret"
    raw = b'{"zen":"Keep it logically awesome."}'
    event = github_triggers.handle_repository_event(
        ProviderTriggerRequest(
            headers=_signed_headers(raw, secret, event="ping"),
            query={},
            body={"zen": "Keep it logically awesome."},
            raw_body=raw,
        ),
        {"webhook_secret": secret},
    )

    assert event.payload is None
    assert event.response_status == 200
