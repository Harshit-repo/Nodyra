"""HubSpot v2 operation specs and executors."""

from __future__ import annotations

import json as json_mod
from typing import Any
from urllib.parse import quote

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.errors import ProviderError
from nodyra_nodes.integrations_v2.registry import register_integration, register_operation
from nodyra_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from nodyra_nodes.integrations_v2.transport import ProviderTransport

HUBSPOT_API_BASE = "https://api.hubapi.com"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="hubspot",
            key="*",
            label="HubSpot access token",
            fields=["access_token"],
            multi=True,
            test_service="hubspot",
        ),
        description="HubSpot private app access token.",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    token = str(creds.get("access_token") or "")
    if not token:
        raise ValueError("hubspot: access_token is required")
    return ProviderTransport(
        provider="hubspot",
        base_url=HUBSPOT_API_BASE,
        default_headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"access_token": value}
    return {}


def _check_error(response: Any, operation: str) -> Any:
    if isinstance(response, dict):
        status = response.get("status")
        if status and status != "complete":
            msg = response.get("message", "") or response.get("error", "")
            raise ProviderError(
                provider="hubspot",
                operation=operation,
                status_code=response.get("statusCode", 400),
                code=str(status),
                message=str(msg),
                retryable=False,
                response_body_summary=str(response),
            )
    return response


def _properties_from_input(input_value: Any, extra_json: str = "") -> dict[str, Any]:
    props: dict[str, Any] = {}
    if isinstance(input_value, dict):
        for k, v in input_value.items():
            props[str(k)] = str(v) if not isinstance(v, (dict, list)) else json_mod.dumps(v)
    if extra_json and extra_json.strip():
        try:
            extra = json_mod.loads(extra_json)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    props[str(k)] = str(v) if not isinstance(v, (dict, list)) else json_mod.dumps(v)
        except json_mod.JSONDecodeError:
            pass
    return props


HUBSPOT_CREATE_CONTACT_SPEC = OperationSpec(
    node_id="hubspot_create_contact_v2",
    name="HubSpot Create Contact",
    provider="hubspot",
    resource="contact",
    operation="create",
    description="Create a contact in HubSpot.",
    icon="brand:hubspot",
    params=(
        _credentials_param(),
        OperationParamSpec(name="email", required=True, placeholder="user@example.com"),
        OperationParamSpec(name="firstname", group="Options"),
        OperationParamSpec(name="lastname", group="Options"),
        OperationParamSpec(name="phone", group="Options"),
        OperationParamSpec(
            name="properties_json",
            group="Options",
            multiline=True,
            placeholder='{"jobtitle": "Engineer"}',
        ),
    ),
)

HUBSPOT_GET_CONTACT_SPEC = OperationSpec(
    node_id="hubspot_get_contact_v2",
    name="HubSpot Get Contact",
    provider="hubspot",
    resource="contact",
    operation="get",
    description="Get a HubSpot contact by ID or email.",
    icon="brand:hubspot",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="contact_id",
            required=True,
            placeholder="contact_id or email",
        ),
    ),
)

HUBSPOT_UPDATE_CONTACT_SPEC = OperationSpec(
    node_id="hubspot_update_contact_v2",
    name="HubSpot Update Contact",
    provider="hubspot",
    resource="contact",
    operation="update",
    description="Update a HubSpot contact.",
    icon="brand:hubspot",
    params=(
        _credentials_param(),
        OperationParamSpec(name="contact_id", required=True),
        OperationParamSpec(name="firstname", group="Options"),
        OperationParamSpec(name="lastname", group="Options"),
        OperationParamSpec(name="phone", group="Options"),
        OperationParamSpec(
            name="properties_json",
            group="Options",
            multiline=True,
        ),
    ),
)

HUBSPOT_SEARCH_CONTACTS_SPEC = OperationSpec(
    node_id="hubspot_search_contacts_v2",
    name="HubSpot Search Contacts",
    provider="hubspot",
    resource="contact",
    operation="search",
    description="Search HubSpot contacts.",
    icon="brand:hubspot",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="query",
            required=True,
            placeholder="email: user@example.com",
        ),
        OperationParamSpec(name="limit", type="number", default=50, group="Options"),
    ),
)

HUBSPOT_CREATE_COMPANY_SPEC = OperationSpec(
    node_id="hubspot_create_company_v2",
    name="HubSpot Create Company",
    provider="hubspot",
    resource="company",
    operation="create",
    description="Create a company in HubSpot.",
    icon="brand:hubspot",
    params=(
        _credentials_param(),
        OperationParamSpec(name="name", required=True),
        OperationParamSpec(name="domain", group="Options"),
        OperationParamSpec(
            name="properties_json",
            group="Options",
            multiline=True,
        ),
    ),
)

HUBSPOT_CREATE_DEAL_SPEC = OperationSpec(
    node_id="hubspot_create_deal_v2",
    name="HubSpot Create Deal",
    provider="hubspot",
    resource="deal",
    operation="create",
    description="Create a deal in HubSpot.",
    icon="brand:hubspot",
    params=(
        _credentials_param(),
        OperationParamSpec(name="deal_name", required=True),
        OperationParamSpec(name="amount", type="number", group="Options"),
        OperationParamSpec(name="deal_stage", group="Options", placeholder="appointmentscheduled"),
        OperationParamSpec(
            name="properties_json",
            group="Options",
            multiline=True,
        ),
    ),
)

HUBSPOT_CREATE_TICKET_SPEC = OperationSpec(
    node_id="hubspot_create_ticket_v2",
    name="HubSpot Create Ticket",
    provider="hubspot",
    resource="ticket",
    operation="create",
    description="Create a support ticket in HubSpot.",
    icon="brand:hubspot",
    params=(
        _credentials_param(),
        OperationParamSpec(name="subject", required=True),
        OperationParamSpec(name="description", group="Options", multiline=True),
        OperationParamSpec(name="category", group="Options"),
        OperationParamSpec(
            name="properties_json",
            group="Options",
            multiline=True,
        ),
    ),
)


def create_contact(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    email: str = "",
    firstname: str = "",
    lastname: str = "",
    phone: str = "",
    properties_json: str = "",
) -> Any:
    if not email:
        raise ValueError("hubspot_create_contact_v2: email is required")
    properties = _properties_from_input(input, properties_json)
    properties["email"] = email
    if firstname:
        properties["firstname"] = firstname
    if lastname:
        properties["lastname"] = lastname
    if phone:
        properties["phone"] = phone
    result = _transport(credentials).request(
        "POST",
        "/crm/v3/objects/contacts",
        operation="create_contact",
        json_body={"properties": properties},
    )
    return _check_error(result, "create_contact")


def get_contact(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    contact_id: str = "",
) -> Any:
    if not contact_id:
        raise ValueError("hubspot_get_contact_v2: contact_id is required")
    cid = str(contact_id).strip()
    path = f"/crm/v3/objects/contacts/{cid}"
    if "@" in cid:
        path = f"/crm/v3/objects/contacts/{quote(cid, safe='')}"
    result = _transport(credentials).request("GET", path, operation="get_contact")
    return _check_error(result, "get_contact")


def update_contact(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    contact_id: str = "",
    firstname: str = "",
    lastname: str = "",
    phone: str = "",
    properties_json: str = "",
) -> Any:
    if not contact_id:
        raise ValueError("hubspot_update_contact_v2: contact_id is required")
    properties = _properties_from_input(input, properties_json)
    if firstname:
        properties["firstname"] = firstname
    if lastname:
        properties["lastname"] = lastname
    if phone:
        properties["phone"] = phone
    result = _transport(credentials).request(
        "PATCH",
        f"/crm/v3/objects/contacts/{contact_id}",
        operation="update_contact",
        json_body={"properties": properties},
    )
    return _check_error(result, "update_contact")


def search_contacts(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    query: str = "",
    limit: int = 50,
) -> Any:
    if not query:
        raise ValueError("hubspot_search_contacts_v2: query is required")
    result = _transport(credentials).request(
        "POST",
        "/crm/v3/objects/contacts/search",
        operation="search_contacts",
        json_body={
            "query": query,
            "limit": max(1, min(200, int(limit or 50))),
        },
    )
    return _check_error(result, "search_contacts")


def create_company(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    name: str = "",
    domain: str = "",
    properties_json: str = "",
) -> Any:
    if not name:
        raise ValueError("hubspot_create_company_v2: name is required")
    properties: dict[str, Any] = {"name": name}
    if domain:
        properties["domain"] = domain
    extra = _properties_from_input(input, properties_json)
    properties.update(extra)
    result = _transport(credentials).request(
        "POST",
        "/crm/v3/objects/companies",
        operation="create_company",
        json_body={"properties": properties},
    )
    return _check_error(result, "create_company")


def create_deal(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    deal_name: str = "",
    amount: float | None = None,
    deal_stage: str = "",
    properties_json: str = "",
) -> Any:
    if not deal_name:
        raise ValueError("hubspot_create_deal_v2: deal_name is required")
    properties: dict[str, Any] = {"dealname": deal_name}
    if amount is not None:
        properties["amount"] = str(amount)
    if deal_stage:
        properties["dealstage"] = deal_stage
    extra = _properties_from_input(input, properties_json)
    properties.update(extra)
    result = _transport(credentials).request(
        "POST",
        "/crm/v3/objects/deals",
        operation="create_deal",
        json_body={"properties": properties},
    )
    return _check_error(result, "create_deal")


def create_ticket(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    subject: str = "",
    description: str = "",
    category: str = "",
    properties_json: str = "",
) -> Any:
    if not subject:
        raise ValueError("hubspot_create_ticket_v2: subject is required")
    properties: dict[str, Any] = {"subject": subject}
    if description:
        properties["description"] = description
    if category:
        properties["category"] = category
    extra = _properties_from_input(input, properties_json)
    properties.update(extra)
    result = _transport(credentials).request(
        "POST",
        "/crm/v3/objects/tickets",
        operation="create_ticket",
        json_body={"properties": properties},
    )
    return _check_error(result, "create_ticket")


register_operation(HUBSPOT_CREATE_CONTACT_SPEC, create_contact, node_registry=None)
register_operation(HUBSPOT_GET_CONTACT_SPEC, get_contact, node_registry=None)
register_operation(HUBSPOT_UPDATE_CONTACT_SPEC, update_contact, node_registry=None)
register_operation(HUBSPOT_SEARCH_CONTACTS_SPEC, search_contacts, node_registry=None)
register_operation(HUBSPOT_CREATE_COMPANY_SPEC, create_company, node_registry=None)
register_operation(HUBSPOT_CREATE_DEAL_SPEC, create_deal, node_registry=None)
register_operation(HUBSPOT_CREATE_TICKET_SPEC, create_ticket, node_registry=None)


HUBSPOT_INTEGRATION = IntegrationSpec(
    id="hubspot",
    name="HubSpot",
    description="Create and manage HubSpot contacts, companies, deals, and tickets.",
    icon="brand:hubspot",
    credential_types=("hubspot",),
    resources=(
        ResourceSpec(
            id="contact",
            name="Contact",
            operations=(
                HUBSPOT_CREATE_CONTACT_SPEC,
                HUBSPOT_GET_CONTACT_SPEC,
                HUBSPOT_UPDATE_CONTACT_SPEC,
                HUBSPOT_SEARCH_CONTACTS_SPEC,
            ),
        ),
        ResourceSpec(
            id="company",
            name="Company",
            operations=(HUBSPOT_CREATE_COMPANY_SPEC,),
        ),
        ResourceSpec(
            id="deal",
            name="Deal",
            operations=(HUBSPOT_CREATE_DEAL_SPEC,),
        ),
        ResourceSpec(
            id="ticket",
            name="Ticket",
            operations=(HUBSPOT_CREATE_TICKET_SPEC,),
        ),
    ),
)

register_integration(HUBSPOT_INTEGRATION)
