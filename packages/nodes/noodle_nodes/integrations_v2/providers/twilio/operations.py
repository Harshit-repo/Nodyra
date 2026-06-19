"""Twilio v2 operation specs and executors."""

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

TWILIO_API_BASE = "https://api.twilio.com"
TWILIO_LOOKUP_BASE = "https://lookups.twilio.com"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="twilio_sms",
            key="*",
            label="Twilio credentials",
            fields=["account_sid", "auth_token"],
            multi=True,
            test_service="twilio_sms",
        ),
        description="Twilio Account SID and Auth Token.",
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _account_sid(credentials: Any) -> str:
    creds = _credentials_dict(credentials)
    return str(creds.get("account_sid") or "")


def _transport(credentials: Any, base_url: str = TWILIO_API_BASE) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    account_sid = str(creds.get("account_sid") or "")
    auth_token = str(creds.get("auth_token") or "")
    if not account_sid or not auth_token:
        raise ValueError("twilio: account_sid and auth_token are required")
    import base64

    encoded = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    return ProviderTransport(
        provider="twilio",
        base_url=base_url,
        default_headers={
            "Authorization": f"Basic {encoded}",
        },
    )


def _text_from_input(input_value: Any, text: str = "") -> str:
    if text:
        return text
    if input_value is None:
        return ""
    return str(input_value)


TWILIO_SEND_SMS_SPEC = OperationSpec(
    node_id="twilio_send_sms_v2",
    name="Twilio Send SMS",
    provider="twilio",
    resource="message",
    operation="send",
    description="Send an SMS message via Twilio.",
    icon="brand:twilio",
    params=(
        _credentials_param(),
        OperationParamSpec(name="to", required=True, placeholder="+1234567890"),
        OperationParamSpec(
            name="body",
            placeholder="Message body. Blank uses the input payload.",
        ),
        OperationParamSpec(name="from_phone", required=True, placeholder="+1234567890"),
    ),
)

TWILIO_LIST_MESSAGES_SPEC = OperationSpec(
    node_id="twilio_list_messages_v2",
    name="Twilio List Messages",
    provider="twilio",
    resource="message",
    operation="list",
    description="List SMS messages from your Twilio account.",
    icon="brand:twilio",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="to", group="Filters", placeholder="+1234567890"),
        OperationParamSpec(name="from_phone", group="Filters", placeholder="+1234567890"),
        OperationParamSpec(name="limit", type="number", default=20, group="Options"),
    ),
)

TWILIO_GET_MESSAGE_SPEC = OperationSpec(
    node_id="twilio_get_message_v2",
    name="Twilio Get Message",
    provider="twilio",
    resource="message",
    operation="get",
    description="Get a specific SMS message by SID.",
    icon="brand:twilio",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="sid", required=True, placeholder="SM..."),
    ),
)

TWILIO_LIST_PHONE_NUMBERS_SPEC = OperationSpec(
    node_id="twilio_list_phone_numbers_v2",
    name="Twilio List Phone Numbers",
    provider="twilio",
    resource="phone_number",
    operation="list",
    description="List phone numbers owned by your Twilio account.",
    icon="brand:twilio",
    tool_side_effecting=False,
    params=(_credentials_param(),),
)

TWILIO_LOOKUP_PHONE_NUMBER_SPEC = OperationSpec(
    node_id="twilio_lookup_phone_number_v2",
    name="Twilio Lookup Phone Number",
    provider="twilio",
    resource="lookup",
    operation="get",
    description="Look up information about a phone number using Twilio Lookup.",
    icon="brand:twilio",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="phone_number", required=True, placeholder="+1234567890"),
    ),
)


def send_sms(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    to: str = "",
    body: str = "",
    from_phone: str = "",
) -> Any:
    if not to:
        raise ValueError("twilio_send_sms_v2: to is required")
    if not from_phone:
        raise ValueError("twilio_send_sms_v2: from_phone is required")
    sid = _account_sid(credentials)
    text = _text_from_input(input, body)
    return _transport(credentials).request(
        "POST",
        f"/2010-04-01/Accounts/{sid}/Messages.json",
        operation="send_sms",
        json_body={"To": to, "From": from_phone, "Body": text},
    )


def list_messages(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    to: str = "",
    from_phone: str = "",
    limit: int = 20,
) -> Any:
    sid = _account_sid(credentials)
    params: dict[str, Any] = {}
    if to:
        params["To"] = to
    if from_phone:
        params["From"] = from_phone
    if limit:
        params["PageSize"] = str(max(1, min(1000, int(limit or 20))))
    return _transport(credentials).request(
        "GET",
        f"/2010-04-01/Accounts/{sid}/Messages.json",
        operation="list_messages",
        params=params,
    )


def get_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    sid: str = "",
) -> Any:
    if not sid:
        raise ValueError("twilio_get_message_v2: sid is required")
    account_sid = _account_sid(credentials)
    return _transport(credentials).request(
        "GET",
        f"/2010-04-01/Accounts/{account_sid}/Messages/{sid}.json",
        operation="get_message",
    )


def list_phone_numbers(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
) -> Any:
    sid = _account_sid(credentials)
    return _transport(credentials).request(
        "GET",
        f"/2010-04-01/Accounts/{sid}/IncomingPhoneNumbers.json",
        operation="list_phone_numbers",
    )


def lookup_phone_number(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    phone_number: str = "",
) -> Any:
    if not phone_number:
        raise ValueError("twilio_lookup_phone_number_v2: phone_number is required")
    return _transport(credentials, base_url=TWILIO_LOOKUP_BASE).request(
        "GET",
        f"/v1/PhoneNumbers/{phone_number}",
        operation="lookup_phone_number",
    )


register_operation(TWILIO_SEND_SMS_SPEC, send_sms, node_registry=None)
register_operation(TWILIO_LIST_MESSAGES_SPEC, list_messages, node_registry=None)
register_operation(TWILIO_GET_MESSAGE_SPEC, get_message, node_registry=None)
register_operation(TWILIO_LIST_PHONE_NUMBERS_SPEC, list_phone_numbers, node_registry=None)
register_operation(TWILIO_LOOKUP_PHONE_NUMBER_SPEC, lookup_phone_number, node_registry=None)

TWILIO_INTEGRATION = IntegrationSpec(
    id="twilio",
    name="Twilio",
    description="Send SMS, list messages, and manage phone numbers via Twilio.",
    icon="brand:twilio",
    credential_types=("twilio_sms",),
    resources=(
        ResourceSpec(
            id="message",
            name="Message",
            operations=(
                TWILIO_SEND_SMS_SPEC,
                TWILIO_LIST_MESSAGES_SPEC,
                TWILIO_GET_MESSAGE_SPEC,
            ),
        ),
        ResourceSpec(
            id="phone_number",
            name="Phone Number",
            operations=(TWILIO_LIST_PHONE_NUMBERS_SPEC,),
        ),
        ResourceSpec(
            id="lookup",
            name="Lookup",
            operations=(TWILIO_LOOKUP_PHONE_NUMBER_SPEC,),
        ),
    ),
)

register_integration(TWILIO_INTEGRATION)
