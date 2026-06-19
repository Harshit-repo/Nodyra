"""Vapi.ai v2 operation specs and executors."""
from __future__ import annotations

import base64
import json
import requests
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_integration, register_operation, register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
    ResourceSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport

VAPI_API_BASE = "https://api.vapi.ai"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="vapi",
            key="*",
            label="Vapi API key",
            fields=["api_key"],
            multi=True,
            test_service="vapi",
        ),
        description="Vapi private API key.",
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"api_key": value}
    return {}


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    api_key = str(creds.get("api_key") or "")
    if not api_key:
        raise ValueError("vapi: api_key is required")
    return ProviderTransport(
        provider="vapi",
        base_url=VAPI_API_BASE,
        default_headers={"Authorization": f"Bearer {api_key}"},
    )


# --- Operation Specs ---

VAPI_START_CALL_SPEC = OperationSpec(
    node_id="vapi_start_call",
    name="Vapi Start Call",
    provider="vapi",
    resource="call",
    operation="create",
    description="Initiate an outbound voice call with a Vapi assistant.",
    icon="brand:vapi",
    params=(
        _credentials_param(),
        OperationParamSpec(name="assistant_id", required=True, description="Vapi assistant ID."),
        OperationParamSpec(name="phone_number_id", required=True, description="Vapi phone number ID to call from."),
        OperationParamSpec(name="customer_number", required=True, placeholder="+1234567890", description="Destination E.164 phone number."),
        OperationParamSpec(name="assistant_overrides", multiline=True, group="Options", description="JSON string of assistant overrides (optional)."),
        OperationParamSpec(name="metadata", multiline=True, group="Options", description="JSON string of metadata to attach to the call."),
    ),
)

VAPI_GET_CALL_SPEC = OperationSpec(
    node_id="vapi_get_call",
    name="Vapi Get Call",
    provider="vapi",
    resource="call",
    operation="get",
    description="Fetch details and transcript for a Vapi call.",
    icon="brand:vapi",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="call_id", required=True, placeholder="call_abc123", description="Vapi call ID."),
    ),
)

VAPI_LIST_CALLS_SPEC = OperationSpec(
    node_id="vapi_list_calls",
    name="Vapi List Calls",
    provider="vapi",
    resource="call",
    operation="list",
    description="List recent Vapi calls.",
    icon="brand:vapi",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="limit", type="number", default=20, group="Options", description="Max calls to return."),
        OperationParamSpec(name="created_at_gt", group="Filters", description="ISO 8601 datetime — only calls after this."),
        OperationParamSpec(name="assistant_id", group="Filters", description="Filter by assistant ID."),
    ),
)

VAPI_END_CALL_SPEC = OperationSpec(
    node_id="vapi_end_call",
    name="Vapi End Call",
    provider="vapi",
    resource="call",
    operation="delete",
    description="Terminate an active Vapi call.",
    icon="brand:vapi",
    params=(
        _credentials_param(),
        OperationParamSpec(name="call_id", required=True, description="Vapi call ID to terminate."),
    ),
)

VAPI_CREATE_ASSISTANT_SPEC = OperationSpec(
    node_id="vapi_create_assistant",
    name="Vapi Create Assistant",
    provider="vapi",
    resource="assistant",
    operation="create",
    description="Create or update a Vapi assistant configuration.",
    icon="brand:vapi",
    params=(
        _credentials_param(),
        OperationParamSpec(name="name", required=True, description="Assistant name."),
        OperationParamSpec(name="first_message", description="First thing the assistant says."),
        OperationParamSpec(name="system_prompt", multiline=True, description="Assistant system prompt."),
        OperationParamSpec(name="voice_provider", default="playht", choices=["playht", "eleven-labs", "openai", "deepgram"], group="Voice", description="Voice provider."),
        OperationParamSpec(name="voice_id", group="Voice", description="Voice ID for the chosen provider."),
        OperationParamSpec(name="model_provider", default="openai", choices=["openai", "anthropic", "together-ai"], group="Model", description="LLM provider."),
        OperationParamSpec(name="model_id", default="gpt-4o-mini", group="Model", description="Model ID."),
        OperationParamSpec(name="assistant_config", multiline=True, group="Advanced", description="Full assistant config as JSON (overrides all other fields if set)."),
    ),
)

VAPI_UPLOAD_FILE_SPEC = OperationSpec(
    node_id="vapi_upload_file",
    name="Vapi Upload File",
    provider="vapi",
    resource="file",
    operation="create",
    description="Upload a file (e.g., knowledge base) for use with a Vapi assistant.",
    icon="brand:vapi",
    params=(
        _credentials_param(),
        OperationParamSpec(name="file_content", multiline=True, required=True, description="File content as text or base64 bytes."),
        OperationParamSpec(name="filename", required=True, placeholder="knowledge.txt", description="Filename for the upload."),
        OperationParamSpec(name="content_type", default="text/plain", description="MIME type."),
    ),
)


# --- Executor functions ---

def start_call(
    *,
    input: Any = None,
    credentials: Any = None,
    assistant_id: str = "",
    phone_number_id: str = "",
    customer_number: str = "",
    assistant_overrides: str = "",
    metadata: str = "",
) -> Any:
    body: dict[str, Any] = {
        "type": "outboundPhoneCall",
        "assistantId": assistant_id,
        "phoneNumberId": phone_number_id,
        "customer": {"number": customer_number},
    }
    if assistant_overrides:
        body["assistantOverrides"] = json.loads(assistant_overrides)
    if metadata:
        body["metadata"] = json.loads(metadata)
    return _transport(credentials).request("POST", "/call", operation="start_call", json_body=body)


def get_call(
    *,
    input: Any = None,
    credentials: Any = None,
    call_id: str = "",
) -> Any:
    return _transport(credentials).request("GET", f"/call/{call_id}", operation="get_call")


def list_calls(
    *,
    input: Any = None,
    credentials: Any = None,
    limit: int = 20,
    created_at_gt: str = "",
    assistant_id: str = "",
) -> Any:
    params: dict[str, Any] = {"limit": limit}
    if created_at_gt:
        params["createdAtGt"] = created_at_gt
    if assistant_id:
        params["assistantId"] = assistant_id
    return _transport(credentials).request("GET", "/call", operation="list_calls", params=params)


def end_call(
    *,
    input: Any = None,
    credentials: Any = None,
    call_id: str = "",
) -> Any:
    return _transport(credentials).request("DELETE", f"/call/{call_id}", operation="end_call")


def create_assistant(
    *,
    input: Any = None,
    credentials: Any = None,
    name: str = "",
    first_message: str = "",
    system_prompt: str = "",
    voice_provider: str = "playht",
    voice_id: str = "",
    model_provider: str = "openai",
    model_id: str = "gpt-4o-mini",
    assistant_config: str = "",
) -> Any:
    if assistant_config:
        body = json.loads(assistant_config)
    else:
        body: dict[str, Any] = {"name": name}
        if first_message:
            body["firstMessage"] = first_message
        model_body: dict[str, Any] = {"provider": model_provider, "model": model_id}
        if system_prompt:
            model_body["messages"] = [{"role": "system", "content": system_prompt}]
        else:
            model_body["messages"] = []
        body["model"] = model_body
        voice_body: dict[str, Any] = {"provider": voice_provider}
        if voice_id:
            voice_body["voiceId"] = voice_id
        body["voice"] = voice_body
    return _transport(credentials).request("POST", "/assistant", operation="create_assistant", json_body=body)


def upload_file(
    *,
    input: Any = None,
    credentials: Any = None,
    file_content: str = "",
    filename: str = "",
    content_type: str = "text/plain",
) -> Any:
    try:
        content_bytes = base64.b64decode(file_content)
    except Exception:
        content_bytes = file_content.encode("utf-8")
    creds = _credentials_dict(credentials)
    api_key = str(creds.get("api_key") or "")
    response = requests.post(
        f"{VAPI_API_BASE}/file",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": (filename, content_bytes, content_type)},
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"vapi_upload_file: HTTP {response.status_code} — {response.text[:500]}")
    return response.json()


# --- Poll trigger ---

def poll_completed_calls(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    params = ctx.params
    cursor = ctx.cursor
    assistant_id = str(params.get("assistant_id") or "").strip()
    is_first_run = "last_created_at" not in cursor
    last_created_at: str = cursor.get("last_created_at") or ""
    seen_ids: list[str] = list(cursor.get("seen_ids") or [])

    transport = _transport(params.get("credentials"))
    query_params: dict[str, Any] = {"limit": 50, "status": "ended"}
    if last_created_at:
        query_params["createdAtGt"] = last_created_at
    if assistant_id:
        query_params["assistantId"] = assistant_id

    response = transport.request("GET", "/call", operation="poll_calls", params=query_params)
    calls: list[dict[str, Any]] = (
        response if isinstance(response, list)
        else response.get("results", []) if isinstance(response, dict)
        else []
    )

    if not calls:
        return ProviderTriggerPollResult(events=[], cursor=cursor)

    latest = last_created_at
    events: list[dict[str, Any]] = []
    new_ids = list(seen_ids)

    for call in calls:
        call_id = str(call.get("id") or "")
        created = str(call.get("createdAt") or "")
        if call_id in seen_ids:
            continue
        if created > latest:
            latest = created
        events.append({
            "id": call_id,
            "status": call.get("status"),
            "assistantId": call.get("assistantId"),
            "transcript": call.get("transcript"),
            "summary": call.get("analysis", {}).get("summary") if call.get("analysis") else None,
            "recordingUrl": call.get("recordingUrl"),
            "startedAt": call.get("startedAt"),
            "endedAt": call.get("endedAt"),
            "call": call,
        })
        new_ids.append(call_id)

    new_ids = new_ids[-500:]
    new_cursor = {"last_created_at": latest or last_created_at, "seen_ids": new_ids}

    if is_first_run:
        return ProviderTriggerPollResult(events=[], cursor=new_cursor)

    return ProviderTriggerPollResult(events=events, cursor=new_cursor)


# --- Trigger Spec (defined after poll_completed_calls) ---

VAPI_CALL_COMPLETED_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="vapi_call_completed_trigger",
    name="Vapi Call Completed",
    provider="vapi",
    resource="call",
    event="completed",
    description="Start a workflow when a Vapi call completes. Polls the Vapi call list.",
    icon="brand:vapi",
    params=(
        _credentials_param(),
        OperationParamSpec(name="assistant_id", group="Filters", description="Only trigger for calls with this assistant ID. Leave blank for all."),
    ),
    poll=poll_completed_calls,
    poll_interval_seconds=10,
)


# --- Registration ---

register_operation(VAPI_START_CALL_SPEC, start_call, node_registry=None)
register_operation(VAPI_GET_CALL_SPEC, get_call, node_registry=None)
register_operation(VAPI_LIST_CALLS_SPEC, list_calls, node_registry=None)
register_operation(VAPI_END_CALL_SPEC, end_call, node_registry=None)
register_operation(VAPI_CREATE_ASSISTANT_SPEC, create_assistant, node_registry=None)
register_operation(VAPI_UPLOAD_FILE_SPEC, upload_file, node_registry=None)

VAPI_INTEGRATION = IntegrationSpec(
    id="vapi",
    name="Vapi",
    description="Manage AI voice calls with Vapi.ai.",
    icon="brand:vapi",
    credential_types=("vapi",),
    resources=(
        ResourceSpec(id="call", name="Call", operations=(VAPI_START_CALL_SPEC, VAPI_GET_CALL_SPEC, VAPI_LIST_CALLS_SPEC, VAPI_END_CALL_SPEC)),
        ResourceSpec(id="assistant", name="Assistant", operations=(VAPI_CREATE_ASSISTANT_SPEC,)),
        ResourceSpec(id="file", name="File", operations=(VAPI_UPLOAD_FILE_SPEC,)),
    ),
)

register_integration(VAPI_INTEGRATION)
register_provider_trigger(VAPI_CALL_COMPLETED_TRIGGER_SPEC)
