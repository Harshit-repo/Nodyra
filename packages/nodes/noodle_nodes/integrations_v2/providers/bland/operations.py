"""Bland AI v2 operation specs and executors."""

from __future__ import annotations

from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_integration, register_operation
from noodle_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport

BLAND_API_BASE = "https://api.bland.ai"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="bland",
            key="*",
            label="Bland API key",
            fields=["api_key"],
            multi=True,
            test_service="bland",
        ),
        description="Bland AI API key.",
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
        raise ValueError("bland: api_key is required")
    return ProviderTransport(
        provider="bland",
        base_url=BLAND_API_BASE,
        default_headers={"authorization": api_key},
    )


# ---------------------------------------------------------------------------
# Operation specs
# ---------------------------------------------------------------------------

BLAND_SEND_CALL_SPEC = OperationSpec(
    node_id="bland_send_call",
    name="Bland Send Call",
    provider="bland",
    resource="call",
    operation="create",
    description="Initiate an outbound AI voice call with Bland AI.",
    icon="brand:bland",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="phone_number",
            required=True,
            placeholder="+1234567890",
            description="Destination phone number (E.164).",
        ),
        OperationParamSpec(
            name="task",
            required=True,
            multiline=True,
            description="What the AI should do on the call (the task prompt).",
        ),
        OperationParamSpec(
            name="voice",
            default="maya",
            group="Options",
            description="Voice preset name (e.g. maya, ryan).",
        ),
        OperationParamSpec(
            name="max_duration",
            type="number",
            default=30,
            group="Options",
            description="Max call duration in minutes.",
        ),
        OperationParamSpec(
            name="from_number",
            group="Options",
            placeholder="+1987654321",
            description="Caller phone number (must be owned or verified with Bland).",
        ),
        OperationParamSpec(
            name="wait_for_greeting",
            type="boolean",
            default=True,
            group="Options",
            description="Wait for the person to say hello before speaking.",
        ),
        OperationParamSpec(
            name="record",
            type="boolean",
            default=False,
            group="Options",
            description="Record the call.",
        ),
        OperationParamSpec(
            name="model",
            default="enhanced",
            choices=["base", "turbo", "enhanced"],
            group="Options",
            description="Bland AI model tier.",
        ),
        OperationParamSpec(
            name="language",
            default="en-US",
            group="Options",
            description="Language code for the call.",
        ),
        OperationParamSpec(
            name="webhook",
            group="Options",
            description="URL to POST call results to when the call ends.",
        ),
    ),
)

BLAND_GET_CALL_SPEC = OperationSpec(
    node_id="bland_get_call",
    name="Bland Get Call",
    provider="bland",
    resource="call",
    operation="get",
    description="Fetch details and transcript for a Bland AI call.",
    icon="brand:bland",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="call_id",
            required=True,
            placeholder="call_abc123",
            description="Bland AI call ID.",
        ),
    ),
)

BLAND_LIST_CALLS_SPEC = OperationSpec(
    node_id="bland_list_calls",
    name="Bland List Calls",
    provider="bland",
    resource="call",
    operation="list",
    description="List recent Bland AI calls.",
    icon="brand:bland",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="limit",
            type="number",
            default=20,
            group="Options",
            description="Max calls to return.",
        ),
        OperationParamSpec(
            name="from_number",
            group="Filters",
            description="Filter by the caller phone number.",
        ),
    ),
)

BLAND_STOP_CALL_SPEC = OperationSpec(
    node_id="bland_stop_call",
    name="Bland Stop Call",
    provider="bland",
    resource="call",
    operation="stop",
    description="Terminate an active Bland AI call.",
    icon="brand:bland",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="call_id",
            required=True,
            description="Bland AI call ID to stop.",
        ),
    ),
)

BLAND_ANALYZE_CALL_SPEC = OperationSpec(
    node_id="bland_analyze_call",
    name="Bland Analyze Call",
    provider="bland",
    resource="call",
    operation="analyze",
    description="Get AI analysis of call outcomes using Bland's analysis endpoint.",
    icon="brand:bland",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="call_id",
            required=True,
            description="Bland AI call ID to analyze.",
        ),
        OperationParamSpec(
            name="goal",
            multiline=True,
            description="What the call was trying to accomplish.",
        ),
        OperationParamSpec(
            name="questions",
            multiline=True,
            description='JSON array of questions for the analysis, e.g. [["Did they agree?", "boolean"]].',
        ),
    ),
)


# ---------------------------------------------------------------------------
# Executor functions
# ---------------------------------------------------------------------------

def send_call(
    *,
    input: Any = None,
    credentials: Any = None,
    phone_number: str = "",
    task: str = "",
    voice: str = "maya",
    max_duration: int = 30,
    from_number: str = "",
    wait_for_greeting: bool = True,
    record: bool = False,
    model: str = "enhanced",
    language: str = "en-US",
    webhook: str = "",
) -> Any:
    if not phone_number:
        raise ValueError("bland_send_call: phone_number is required")
    if not task:
        raise ValueError("bland_send_call: task is required")
    body: dict[str, Any] = {
        "phone_number": phone_number,
        "task": task,
        "voice": voice or "maya",
        "max_duration": int(max_duration or 30),
        "wait_for_greeting": wait_for_greeting,
        "record": record,
        "model": model or "enhanced",
        "language": language or "en-US",
    }
    if from_number:
        body["from"] = from_number
    if webhook:
        body["webhook"] = webhook
    return _transport(credentials).request(
        "POST", "/v1/calls", operation="send_call", json_body=body
    )


def get_call(
    *,
    input: Any = None,
    credentials: Any = None,
    call_id: str = "",
) -> Any:
    if not call_id:
        raise ValueError("bland_get_call: call_id is required")
    return _transport(credentials).request(
        "GET", f"/v1/calls/{call_id}", operation="get_call"
    )


def list_calls(
    *,
    input: Any = None,
    credentials: Any = None,
    limit: int = 20,
    from_number: str = "",
) -> Any:
    query: dict[str, Any] = {"limit": int(limit or 20)}
    if from_number:
        query["from"] = from_number
    return _transport(credentials).request(
        "GET", "/v1/calls", operation="list_calls", params=query
    )


def stop_call(
    *,
    input: Any = None,
    credentials: Any = None,
    call_id: str = "",
) -> Any:
    if not call_id:
        raise ValueError("bland_stop_call: call_id is required")
    return _transport(credentials).request(
        "POST", f"/v1/calls/{call_id}/stop", operation="stop_call"
    )


def analyze_call(
    *,
    input: Any = None,
    credentials: Any = None,
    call_id: str = "",
    goal: str = "",
    questions: str = "",
) -> Any:
    if not call_id:
        raise ValueError("bland_analyze_call: call_id is required")
    import json as _json
    body: dict[str, Any] = {}
    if goal:
        body["goal"] = goal
    if questions:
        body["questions"] = _json.loads(questions)
    return _transport(credentials).request(
        "POST", f"/v1/calls/{call_id}/analyze", operation="analyze_call", json_body=body
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

register_operation(BLAND_SEND_CALL_SPEC, send_call, node_registry=None)
register_operation(BLAND_GET_CALL_SPEC, get_call, node_registry=None)
register_operation(BLAND_LIST_CALLS_SPEC, list_calls, node_registry=None)
register_operation(BLAND_STOP_CALL_SPEC, stop_call, node_registry=None)
register_operation(BLAND_ANALYZE_CALL_SPEC, analyze_call, node_registry=None)

BLAND_INTEGRATION = IntegrationSpec(
    id="bland",
    name="Bland AI",
    description="Make AI-powered outbound voice calls with Bland AI.",
    icon="brand:bland",
    credential_types=("bland",),
    resources=(
        ResourceSpec(
            id="call",
            name="Call",
            operations=(
                BLAND_SEND_CALL_SPEC,
                BLAND_GET_CALL_SPEC,
                BLAND_LIST_CALLS_SPEC,
                BLAND_STOP_CALL_SPEC,
                BLAND_ANALYZE_CALL_SPEC,
            ),
        ),
    ),
)

register_integration(BLAND_INTEGRATION)
