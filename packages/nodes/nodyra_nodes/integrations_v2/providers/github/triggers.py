"""GitHub provider trigger specs and lifecycle hooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_provider_trigger
from nodyra_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerActivationContext,
    ProviderTriggerDeactivationContext,
    ProviderTriggerEvent,
    ProviderTriggerRequest,
    ProviderTriggerSpec,
    ProviderTriggerSubscription,
)
from nodyra_nodes.integrations_v2.transport import ProviderTransport

GITHUB_API_BASE = "https://api.github.com"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="github",
            key="*",
            label="GitHub token",
            fields=["token"],
            multi=True,
            test_service="github",
        ),
        description="GitHub token with repository webhook permissions.",
        documentation_url=(
            "https://docs.github.com/rest/repos/webhooks#create-a-repository-webhook"
        ),
    )


def _secret_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="webhook_secret",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="generic",
            key="value",
            label="Webhook secret",
            fields=["value"],
            multi=False,
        ),
        group="Security",
        description="Shared secret used to verify GitHub webhook signatures.",
    )


GITHUB_REPOSITORY_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="github_repository_trigger_v2",
    name="GitHub Repository Trigger",
    provider="github",
    resource="repository",
    event="webhook",
    description="Start a workflow from GitHub repository webhook events.",
    icon="brand:github",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="owner",
            required=True,
            placeholder="octocat",
            description="Repository owner or organization.",
        ),
        OperationParamSpec(
            name="repo",
            required=True,
            placeholder="hello-world",
            description="Repository name.",
        ),
        OperationParamSpec(
            name="events",
            default="push",
            placeholder="push, pull_request",
            description="Comma/newline-separated GitHub webhook events.",
        ),
        _secret_param(),
    ),
    activate=lambda context: activate_repository_webhook(context),
    deactivate=lambda context: deactivate_repository_webhook(context),
    handle_event=lambda request, params: handle_repository_event(request, params),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items()}
    if isinstance(value, str):
        return {"token": value}
    return {}


def _token(credentials: Any) -> str:
    creds = _credentials_dict(credentials)
    return str(creds.get("token") or creds.get("access_token") or "")


def _transport(credentials: Any) -> ProviderTransport:
    token = _token(credentials)
    if not token:
        raise ValueError("github_repository_trigger_v2: credentials are required")
    return ProviderTransport(
        provider="github",
        base_url=GITHUB_API_BASE,
        default_headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _events(value: Any) -> list[str]:
    raw = str(value or "push")
    events = [part.strip() for part in re.split(r"[,\n]", raw) if part.strip()]
    return events or ["push"]


def _repo_path(owner: str, repo: str) -> str:
    clean_owner = str(owner or "").strip()
    clean_repo = str(repo or "").strip()
    if not clean_owner or not clean_repo:
        raise ValueError("github_repository_trigger_v2: owner and repo are required")
    return f"/repos/{clean_owner}/{clean_repo}/hooks"


def activate_repository_webhook(
    context: ProviderTriggerActivationContext,
) -> ProviderTriggerSubscription:
    params = context.params
    secret = str(params.get("webhook_secret") or "").strip()
    if not secret:
        raise ValueError("github_repository_trigger_v2: webhook_secret is required")
    events = _events(params.get("events"))
    response = _transport(params.get("credentials")).request(
        "POST",
        _repo_path(str(params.get("owner") or ""), str(params.get("repo") or "")),
        operation="create_repository_webhook",
        json_body={
            "name": "web",
            "active": True,
            "events": events,
            "config": {
                "url": context.callback_url,
                "content_type": "json",
                "insecure_ssl": "0",
                "secret": secret,
            },
        },
    )
    external_id = str(response.get("id") if isinstance(response, dict) else "")
    if not external_id:
        raise ValueError("github_repository_trigger_v2: provider did not return hook id")
    return ProviderTriggerSubscription(
        external_id=external_id,
        config={
            "owner": str(params.get("owner") or "").strip(),
            "repo": str(params.get("repo") or "").strip(),
            "events": events,
            "callback_url": context.callback_url,
        },
    )


def deactivate_repository_webhook(context: ProviderTriggerDeactivationContext) -> None:
    if not context.external_id:
        return
    params = context.params
    hooks_path = _repo_path(
        str(params.get("owner") or ""),
        str(params.get("repo") or ""),
    )
    _transport(params.get("credentials")).request(
        "DELETE",
        f"{hooks_path}/{context.external_id}",
        operation="delete_repository_webhook",
    )


def _lower_headers(headers: dict[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in (headers or {}).items()}


def _verify_signature(secret: str, signature: str, raw_body: bytes) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body or b"",
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature.strip(), expected)


def _body_dict(request: ProviderTriggerRequest) -> dict[str, Any]:
    if isinstance(request.body, dict):
        return request.body
    if request.raw_body:
        try:
            parsed = json.loads(request.raw_body.decode("utf-8"))
            if isinstance(parsed, dict):
                return parsed
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    return {"body": request.body}


def handle_repository_event(
    request: ProviderTriggerRequest,
    params: dict[str, Any],
) -> ProviderTriggerEvent:
    headers = _lower_headers(request.headers)
    secret = str(params.get("webhook_secret") or "").strip()
    if secret and not _verify_signature(
        secret,
        headers.get("x-hub-signature-256", ""),
        request.raw_body,
    ):
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "GitHub webhook signature verification failed"},
            response_status=401,
        )

    event_name = headers.get("x-github-event", "")
    delivery_id = headers.get("x-github-delivery", "")
    if event_name == "ping":
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "GitHub webhook ping received"},
            response_status=200,
        )

    body = _body_dict(request)
    repository = body.get("repository") if isinstance(body, dict) else None
    sender = body.get("sender") if isinstance(body, dict) else None
    return ProviderTriggerEvent(
        payload={
            "provider": "github",
            "event": event_name,
            "delivery_id": delivery_id,
            "repository": repository,
            "sender": sender,
            "body": body,
        },
        dedupe_key=f"github:{delivery_id}" if delivery_id else None,
        response_body={"message": "GitHub event accepted"},
        response_status=202,
    )


register_provider_trigger(GITHUB_REPOSITORY_TRIGGER_SPEC)
