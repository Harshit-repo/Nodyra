"""Pipedrive v2 operation specs and executors."""

from __future__ import annotations

from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_integration, register_operation
from nodyra_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from nodyra_nodes.integrations_v2.transport import ProviderTransport


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="pipedrive",
            key="*",
            label="Pipedrive credentials",
            fields=["company_domain", "api_token"],
            multi=True,
        ),
        description="Pipedrive company domain and API token.",
    )


def _transport_and_token(credentials: Any) -> tuple[ProviderTransport, str]:
    creds = _credentials_dict(credentials)
    domain = str(creds.get("company_domain") or "")
    token = str(creds.get("api_token") or "")
    if not domain or not token:
        raise ValueError("pipedrive: company_domain and api_token are required")
    transport = ProviderTransport(
        provider="pipedrive",
        base_url=f"https://{domain}.pipedrive.com/api/v1",
        default_headers={"Content-Type": "application/json"},
    )
    return transport, token


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"api_token": value}
    return {}


PIPEDRIVE_LIST_DEALS_SPEC = OperationSpec(
    node_id="pipedrive_list_deals_v2",
    name="Pipedrive List Deals",
    provider="pipedrive",
    resource="deal",
    operation="list",
    description="List all deals in Pipedrive.",
    icon="brand:pipedrive",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="limit", type="number", default=50, group="Options"),
        OperationParamSpec(
            name="status",
            group="Options",
            choices=("open", "won", "lost", "deleted", "all_not_deleted"),
        ),
    ),
)

PIPEDRIVE_CREATE_DEAL_SPEC = OperationSpec(
    node_id="pipedrive_create_deal_v2",
    name="Pipedrive Create Deal",
    provider="pipedrive",
    resource="deal",
    operation="create",
    description="Create a new deal in Pipedrive.",
    icon="brand:pipedrive",
    params=(
        _credentials_param(),
        OperationParamSpec(name="title", required=True, placeholder="Deal title"),
        OperationParamSpec(name="value", type="number", group="Options"),
        OperationParamSpec(name="currency", default="USD", group="Options"),
        OperationParamSpec(
            name="status",
            group="Options",
            choices=("open", "won", "lost", "deleted"),
        ),
        OperationParamSpec(name="person_id", type="number", group="Options"),
        OperationParamSpec(name="org_id", type="number", group="Options"),
        OperationParamSpec(name="stage_id", type="number", group="Options"),
    ),
)

PIPEDRIVE_GET_DEAL_SPEC = OperationSpec(
    node_id="pipedrive_get_deal_v2",
    name="Pipedrive Get Deal",
    provider="pipedrive",
    resource="deal",
    operation="get",
    description="Get details of a specific deal.",
    icon="brand:pipedrive",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="deal_id", type="number", required=True, placeholder="12345"
        ),
    ),
)

PIPEDRIVE_LIST_PERSONS_SPEC = OperationSpec(
    node_id="pipedrive_list_persons_v2",
    name="Pipedrive List Persons",
    provider="pipedrive",
    resource="person",
    operation="list",
    description="List all persons in Pipedrive.",
    icon="brand:pipedrive",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="limit", type="number", default=50, group="Options"),
    ),
)

PIPEDRIVE_CREATE_PERSON_SPEC = OperationSpec(
    node_id="pipedrive_create_person_v2",
    name="Pipedrive Create Person",
    provider="pipedrive",
    resource="person",
    operation="create",
    description="Create a new person in Pipedrive.",
    icon="brand:pipedrive",
    params=(
        _credentials_param(),
        OperationParamSpec(name="name", required=True, placeholder="Person name"),
        OperationParamSpec(name="email", group="Options"),
        OperationParamSpec(name="phone", group="Options"),
        OperationParamSpec(name="org_id", type="number", group="Options"),
    ),
)

PIPEDRIVE_LIST_ORGANIZATIONS_SPEC = OperationSpec(
    node_id="pipedrive_list_organizations_v2",
    name="Pipedrive List Organizations",
    provider="pipedrive",
    resource="organization",
    operation="list",
    description="List all organizations in Pipedrive.",
    icon="brand:pipedrive",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="limit", type="number", default=50, group="Options"),
    ),
)

PIPEDRIVE_CREATE_ORGANIZATION_SPEC = OperationSpec(
    node_id="pipedrive_create_organization_v2",
    name="Pipedrive Create Organization",
    provider="pipedrive",
    resource="organization",
    operation="create",
    description="Create a new organization in Pipedrive.",
    icon="brand:pipedrive",
    params=(
        _credentials_param(),
        OperationParamSpec(name="name", required=True, placeholder="Organization name"),
        OperationParamSpec(name="address", group="Options"),
        OperationParamSpec(name="phone", group="Options"),
        OperationParamSpec(name="email", group="Options"),
    ),
)


def list_deals(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 50,
    status: str = "",
) -> Any:
    transport, token = _transport_and_token(credentials)
    params: dict[str, Any] = {"api_token": token, "limit": max(1, int(limit or 50))}
    if status:
        params["status"] = status
    return transport.request(
        "GET",
        "/deals",
        operation="list_deals",
        params=params,
    )


def create_deal(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    title: str = "",
    value: float | None = None,
    currency: str = "USD",
    status: str = "",
    person_id: int | None = None,
    org_id: int | None = None,
    stage_id: int | None = None,
) -> Any:
    if not title:
        raise ValueError("pipedrive_create_deal_v2: title is required")
    transport, token = _transport_and_token(credentials)
    body: dict[str, Any] = {"title": title}
    if value is not None:
        body["value"] = float(value)
    if currency:
        body["currency"] = currency
    if status:
        body["status"] = status
    if person_id is not None:
        body["person_id"] = int(person_id)
    if org_id is not None:
        body["org_id"] = int(org_id)
    if stage_id is not None:
        body["stage_id"] = int(stage_id)
    return transport.request(
        "POST",
        "/deals",
        operation="create_deal",
        params={"api_token": token},
        json_body=body,
    )


def get_deal(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    deal_id: int = 0,
) -> Any:
    if not deal_id:
        raise ValueError("pipedrive_get_deal_v2: deal_id is required")
    transport, token = _transport_and_token(credentials)
    return transport.request(
        "GET",
        f"/deals/{deal_id}",
        operation="get_deal",
        params={"api_token": token},
    )


def list_persons(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 50,
) -> Any:
    transport, token = _transport_and_token(credentials)
    return transport.request(
        "GET",
        "/persons",
        operation="list_persons",
        params={"api_token": token, "limit": max(1, int(limit or 50))},
    )


def create_person(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    name: str = "",
    email: str = "",
    phone: str = "",
    org_id: int | None = None,
) -> Any:
    if not name:
        raise ValueError("pipedrive_create_person_v2: name is required")
    transport, token = _transport_and_token(credentials)
    body: dict[str, Any] = {"name": name}
    if email:
        body["email"] = email
    if phone:
        body["phone"] = phone
    if org_id is not None:
        body["org_id"] = int(org_id)
    return transport.request(
        "POST",
        "/persons",
        operation="create_person",
        params={"api_token": token},
        json_body=body,
    )


def list_organizations(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 50,
) -> Any:
    transport, token = _transport_and_token(credentials)
    return transport.request(
        "GET",
        "/organizations",
        operation="list_organizations",
        params={"api_token": token, "limit": max(1, int(limit or 50))},
    )


def create_organization(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    name: str = "",
    address: str = "",
    phone: str = "",
    email: str = "",
) -> Any:
    if not name:
        raise ValueError("pipedrive_create_organization_v2: name is required")
    transport, token = _transport_and_token(credentials)
    body: dict[str, Any] = {"name": name}
    if address:
        body["address"] = address
    if phone:
        body["phone"] = phone
    if email:
        body["email"] = email
    return transport.request(
        "POST",
        "/organizations",
        operation="create_organization",
        params={"api_token": token},
        json_body=body,
    )


register_operation(PIPEDRIVE_LIST_DEALS_SPEC, list_deals, node_registry=None)
register_operation(PIPEDRIVE_CREATE_DEAL_SPEC, create_deal, node_registry=None)
register_operation(PIPEDRIVE_GET_DEAL_SPEC, get_deal, node_registry=None)
register_operation(PIPEDRIVE_LIST_PERSONS_SPEC, list_persons, node_registry=None)
register_operation(PIPEDRIVE_CREATE_PERSON_SPEC, create_person, node_registry=None)
register_operation(PIPEDRIVE_LIST_ORGANIZATIONS_SPEC, list_organizations, node_registry=None)
register_operation(PIPEDRIVE_CREATE_ORGANIZATION_SPEC, create_organization, node_registry=None)

PIPEDRIVE_INTEGRATION = IntegrationSpec(
    id="pipedrive",
    name="Pipedrive",
    description="Manage deals, persons, and organizations in Pipedrive CRM.",
    icon="brand:pipedrive",
    credential_types=("pipedrive",),
    resources=(
        ResourceSpec(
            id="deal",
            name="Deal",
            operations=(
                PIPEDRIVE_LIST_DEALS_SPEC,
                PIPEDRIVE_CREATE_DEAL_SPEC,
                PIPEDRIVE_GET_DEAL_SPEC,
            ),
        ),
        ResourceSpec(
            id="person",
            name="Person",
            operations=(
                PIPEDRIVE_LIST_PERSONS_SPEC,
                PIPEDRIVE_CREATE_PERSON_SPEC,
            ),
        ),
        ResourceSpec(
            id="organization",
            name="Organization",
            operations=(
                PIPEDRIVE_LIST_ORGANIZATIONS_SPEC,
                PIPEDRIVE_CREATE_ORGANIZATION_SPEC,
            ),
        ),
    ),
)

register_integration(PIPEDRIVE_INTEGRATION)
