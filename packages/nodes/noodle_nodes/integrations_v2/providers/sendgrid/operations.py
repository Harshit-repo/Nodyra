"""SendGrid v2 operation specs and executors."""

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

SENDGRID_API_BASE = "https://api.sendgrid.com"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="sendgrid",
            key="*",
            label="SendGrid API key",
            fields=["api_key"],
            multi=True,
            test_service="sendgrid",
        ),
        description="SendGrid API key.",
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    api_key = str(creds.get("api_key") or "")
    if not api_key:
        raise ValueError("sendgrid: api_key is required")
    return ProviderTransport(
        provider="sendgrid",
        base_url=SENDGRID_API_BASE,
        default_headers={
            "Authorization": f"Bearer {api_key}",
        },
    )


def _text_from_input(input_value: Any, text: str = "") -> str:
    if text:
        return text
    if input_value is None:
        return ""
    return str(input_value)


SENDGRID_SEND_EMAIL_SPEC = OperationSpec(
    node_id="sendgrid_send_email_v2",
    name="SendGrid Send Email",
    provider="sendgrid",
    resource="email",
    operation="send",
    description="Send an email via SendGrid.",
    icon="brand:sendgrid",
    params=(
        _credentials_param(),
        OperationParamSpec(name="to_email", required=True, placeholder="recipient@example.com"),
        OperationParamSpec(
            name="subject",
            required=True,
            placeholder="Email subject. Blank uses the input payload.",
        ),
        OperationParamSpec(name="body", multiline=True),
        OperationParamSpec(name="from_email", required=True, placeholder="sender@example.com"),
        OperationParamSpec(name="is_html", type="boolean", default=False),
        OperationParamSpec(name="cc", group="Options", placeholder="cc@example.com"),
        OperationParamSpec(name="bcc", group="Options", placeholder="bcc@example.com"),
    ),
)

SENDGRID_LIST_TEMPLATES_SPEC = OperationSpec(
    node_id="sendgrid_list_templates_v2",
    name="SendGrid List Templates",
    provider="sendgrid",
    resource="template",
    operation="list",
    description="List dynamic email templates.",
    icon="brand:sendgrid",
    tool_side_effecting=False,
    params=(_credentials_param(),),
)

SENDGRID_ADD_CONTACT_SPEC = OperationSpec(
    node_id="sendgrid_add_contact_v2",
    name="SendGrid Add Contact",
    provider="sendgrid",
    resource="contact",
    operation="upsert",
    description="Add or update a contact in your SendGrid marketing list.",
    icon="brand:sendgrid",
    params=(
        _credentials_param(),
        OperationParamSpec(name="email", required=True, placeholder="user@example.com"),
        OperationParamSpec(name="first_name", group="Options"),
        OperationParamSpec(name="last_name", group="Options"),
        OperationParamSpec(name="list_ids", type="array", group="Options"),
    ),
)

SENDGRID_LIST_LISTS_SPEC = OperationSpec(
    node_id="sendgrid_list_lists_v2",
    name="SendGrid List Lists",
    provider="sendgrid",
    resource="list",
    operation="list",
    description="List your contact lists.",
    icon="brand:sendgrid",
    tool_side_effecting=False,
    params=(_credentials_param(),),
)


def send_email(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    to_email: str = "",
    subject: str = "",
    body: str = "",
    from_email: str = "",
    is_html: bool = False,
    cc: str = "",
    bcc: str = "",
) -> Any:
    if not to_email:
        raise ValueError("sendgrid_send_email_v2: to_email is required")
    if not from_email:
        raise ValueError("sendgrid_send_email_v2: from_email is required")
    subject_text = _text_from_input(input, subject)
    if not subject_text:
        raise ValueError("sendgrid_send_email_v2: subject is required")
    personalization: dict[str, Any] = {"to": [{"email": to_email}]}
    if cc:
        personalization["cc"] = [{"email": cc}]
    if bcc:
        personalization["bcc"] = [{"email": bcc}]
    content_type = "text/html" if is_html else "text/plain"
    payload: dict[str, Any] = {
        "personalizations": [personalization],
        "from": {"email": from_email},
        "subject": subject_text,
        "content": [{"type": content_type, "value": body}],
    }
    return _transport(credentials).request(
        "POST",
        "/v3/mail/send",
        operation="send_email",
        json_body=payload,
    )


def list_templates(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/v3/templates",
        operation="list_templates",
        params={"generations": "dynamic"},
    )


def add_contact(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    email: str = "",
    first_name: str = "",
    last_name: str = "",
    list_ids: Any = None,
) -> Any:
    if not email:
        raise ValueError("sendgrid_add_contact_v2: email is required")
    contact: dict[str, Any] = {"email": email}
    if first_name:
        contact["first_name"] = first_name
    if last_name:
        contact["last_name"] = last_name
    payload: dict[str, Any] = {"contacts": [contact]}
    if list_ids:
        if isinstance(list_ids, (list, tuple)):
            payload["list_ids"] = list(list_ids)
        else:
            payload["list_ids"] = [str(list_ids)]
    return _transport(credentials).request(
        "PUT",
        "/v3/marketing/contacts",
        operation="add_contact",
        json_body=payload,
    )


def list_lists(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/v3/marketing/lists",
        operation="list_lists",
    )


register_operation(SENDGRID_SEND_EMAIL_SPEC, send_email, node_registry=None)
register_operation(SENDGRID_LIST_TEMPLATES_SPEC, list_templates, node_registry=None)
register_operation(SENDGRID_ADD_CONTACT_SPEC, add_contact, node_registry=None)
register_operation(SENDGRID_LIST_LISTS_SPEC, list_lists, node_registry=None)

SENDGRID_INTEGRATION = IntegrationSpec(
    id="sendgrid",
    name="SendGrid",
    description="Send emails, manage templates, and handle contacts via SendGrid.",
    icon="brand:sendgrid",
    credential_types=("sendgrid",),
    resources=(
        ResourceSpec(
            id="email",
            name="Email",
            operations=(SENDGRID_SEND_EMAIL_SPEC,),
        ),
        ResourceSpec(
            id="template",
            name="Template",
            operations=(SENDGRID_LIST_TEMPLATES_SPEC,),
        ),
        ResourceSpec(
            id="contact",
            name="Contact",
            operations=(SENDGRID_ADD_CONTACT_SPEC,),
        ),
        ResourceSpec(
            id="list",
            name="List",
            operations=(SENDGRID_LIST_LISTS_SPEC,),
        ),
    ),
)

register_integration(SENDGRID_INTEGRATION)
