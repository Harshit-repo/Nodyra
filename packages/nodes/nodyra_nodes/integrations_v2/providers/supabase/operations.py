"""Supabase v2 operation specs and executors."""

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
            type="supabase",
            key="*",
            label="Supabase project URL and service role key",
            fields=["project_url", "service_role_key"],
            multi=True,
            test_service="supabase",
        ),
        description="Supabase project URL and service role key from Project Settings > API.",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    project_url = str(creds.get("project_url") or "").rstrip("/")
    service_role_key = str(creds.get("service_role_key") or creds.get("api_key") or "")
    if not project_url:
        raise ValueError("supabase: project_url is required")
    if not service_role_key:
        raise ValueError("supabase: service_role_key is required")
    return ProviderTransport(
        provider="supabase",
        base_url=project_url,
        default_headers={
            "Authorization": f"Bearer {service_role_key}",
            "api_key": service_role_key,
            "Content-Profile": "public",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"project_url": value}
    return {}


SUPABASE_QUERY_SPEC = OperationSpec(
    node_id="supabase_query_v2",
    name="Supabase Query",
    provider="supabase",
    resource="table",
    operation="query",
    description="Query rows from a Supabase table.",
    icon="brand:supabase",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="table", required=True, placeholder="users"),
        OperationParamSpec(
            name="select",
            default="*",
            group="Options",
            placeholder="*",
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=100,
            group="Options",
        ),
        OperationParamSpec(
            name="order",
            group="Options",
            placeholder="created_at.desc",
        ),
    ),
)

SUPABASE_INSERT_SPEC = OperationSpec(
    node_id="supabase_insert_v2",
    name="Supabase Insert",
    provider="supabase",
    resource="table",
    operation="insert",
    description="Insert a row into a Supabase table.",
    icon="brand:supabase",
    params=(
        _credentials_param(),
        OperationParamSpec(name="table", required=True, placeholder="users"),
        OperationParamSpec(
            name="data",
            type="json",
            required=True,
            multiline=True,
            placeholder='{"name": "Alice", "email": "alice@example.com"}',
        ),
    ),
)

SUPABASE_UPDATE_SPEC = OperationSpec(
    node_id="supabase_update_v2",
    name="Supabase Update",
    provider="supabase",
    resource="table",
    operation="update",
    description="Update rows in a Supabase table matching a condition.",
    icon="brand:supabase",
    params=(
        _credentials_param(),
        OperationParamSpec(name="table", required=True, placeholder="users"),
        OperationParamSpec(
            name="matching_column",
            required=True,
            placeholder="id",
        ),
        OperationParamSpec(
            name="matching_value",
            required=True,
            placeholder="42",
        ),
        OperationParamSpec(
            name="data",
            type="json",
            required=True,
            multiline=True,
            placeholder='{"name": "Bob"}',
        ),
    ),
)

SUPABASE_DELETE_SPEC = OperationSpec(
    node_id="supabase_delete_v2",
    name="Supabase Delete",
    provider="supabase",
    resource="table",
    operation="delete",
    description="Delete rows from a Supabase table matching a condition.",
    icon="brand:supabase",
    params=(
        _credentials_param(),
        OperationParamSpec(name="table", required=True, placeholder="users"),
        OperationParamSpec(
            name="matching_column",
            required=True,
            placeholder="id",
        ),
        OperationParamSpec(
            name="matching_value",
            required=True,
            placeholder="42",
        ),
    ),
)


def query(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    table: str = "",
    select: str = "*",
    limit: int = 100,
    order: str = "",
) -> Any:
    if not table:
        raise ValueError("supabase_query_v2: table is required")
    safe_limit = max(1, min(1000, int(limit or 100)))
    params: dict[str, Any] = {"select": select or "*", "limit": safe_limit}
    if order:
        params["order"] = order
    return _transport(credentials).request(
        "GET",
        f"/rest/v1/{table}",
        operation="query",
        params=params,
    )


def insert(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    table: str = "",
    data: Any = None,
) -> Any:
    if not table:
        raise ValueError("supabase_insert_v2: table is required")
    if data is None:
        raise ValueError("supabase_insert_v2: data is required")
    return _transport(credentials).request(
        "POST",
        f"/rest/v1/{table}",
        operation="insert",
        json_body=data,
    )


def update(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    table: str = "",
    matching_column: str = "",
    matching_value: str = "",
    data: Any = None,
) -> Any:
    if not table:
        raise ValueError("supabase_update_v2: table is required")
    if not matching_column:
        raise ValueError("supabase_update_v2: matching_column is required")
    if matching_value == "":
        raise ValueError("supabase_update_v2: matching_value is required")
    if data is None:
        raise ValueError("supabase_update_v2: data is required")
    params = {matching_column: f"eq.{matching_value}"}
    return _transport(credentials).request(
        "PATCH",
        f"/rest/v1/{table}",
        operation="update",
        params=params,
        json_body=data,
    )


def delete(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    table: str = "",
    matching_column: str = "",
    matching_value: str = "",
) -> Any:
    if not table:
        raise ValueError("supabase_delete_v2: table is required")
    if not matching_column:
        raise ValueError("supabase_delete_v2: matching_column is required")
    if matching_value == "":
        raise ValueError("supabase_delete_v2: matching_value is required")
    params = {matching_column: f"eq.{matching_value}"}
    return _transport(credentials).request(
        "DELETE",
        f"/rest/v1/{table}",
        operation="delete",
        params=params,
    )


register_operation(SUPABASE_QUERY_SPEC, query, node_registry=None)
register_operation(SUPABASE_INSERT_SPEC, insert, node_registry=None)
register_operation(SUPABASE_UPDATE_SPEC, update, node_registry=None)
register_operation(SUPABASE_DELETE_SPEC, delete, node_registry=None)


SUPABASE_INTEGRATION = IntegrationSpec(
    id="supabase",
    name="Supabase",
    description="Query, insert, update, and delete rows in Supabase tables.",
    icon="brand:supabase",
    credential_types=("supabase",),
    resources=(
        ResourceSpec(
            id="table",
            name="Table",
            operations=(
                SUPABASE_QUERY_SPEC,
                SUPABASE_INSERT_SPEC,
                SUPABASE_UPDATE_SPEC,
                SUPABASE_DELETE_SPEC,
            ),
        ),
    ),
)

register_integration(SUPABASE_INTEGRATION)
