"""Salesforce v2 operation specs and executors."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_operation
from nodyra_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec
from nodyra_nodes.integrations_v2.transport import ProviderTransport


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="salesforce_jwt",
            key="*",
            label="Salesforce",
            fields=["instance_url", "access_token"],
            multi=True,
            test_service="salesforce",
        ),
    )


SALESFORCE_QUERY_SPEC = OperationSpec(
    node_id="salesforce_query",
    name="Salesforce Query",
    provider="salesforce",
    resource="data",
    operation="query",
    description="Run a SOQL query against Salesforce.",
    icon="brand:salesforce",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="query",
            required=True,
            multiline=True,
            placeholder="SELECT Id, Name FROM Account LIMIT 10",
            description="SOQL query string.",
        ),
    ),
)

SALESFORCE_CREATE_RECORD_SPEC = OperationSpec(
    node_id="salesforce_create_record",
    name="Salesforce Create Record",
    provider="salesforce",
    resource="data",
    operation="create",
    description="Create a Salesforce record (e.g. Account, Contact, Lead).",
    icon="brand:salesforce",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="object_type",
            required=True,
            placeholder="Account",
            description="Salesforce object API name (e.g. Account, Contact).",
        ),
        OperationParamSpec(
            name="fields",
            type="object",
            description="Record field values. Blank uses wired input when it is an object.",
        ),
    ),
)

SALESFORCE_UPDATE_RECORD_SPEC = OperationSpec(
    node_id="salesforce_update_record",
    name="Salesforce Update Record",
    provider="salesforce",
    resource="data",
    operation="update",
    description="Update a Salesforce record by ID.",
    icon="brand:salesforce",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="object_type",
            required=True,
            placeholder="Account",
            description="Salesforce object API name.",
        ),
        OperationParamSpec(
            name="record_id",
            required=True,
            placeholder="Record ID",
            description="ID of the record to update.",
        ),
        OperationParamSpec(
            name="fields",
            type="object",
            description="Field values to update. Blank uses wired input.",
        ),
    ),
)

SALESFORCE_DELETE_RECORD_SPEC = OperationSpec(
    node_id="salesforce_delete_record",
    name="Salesforce Delete Record",
    provider="salesforce",
    resource="data",
    operation="delete",
    description="Delete a Salesforce record by ID.",
    icon="brand:salesforce",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="object_type",
            required=True,
            placeholder="Account",
            description="Salesforce object API name.",
        ),
        OperationParamSpec(
            name="record_id",
            required=True,
            placeholder="Record ID",
            description="ID of the record to delete.",
        ),
    ),
)

SALESFORCE_GET_RECORD_SPEC = OperationSpec(
    node_id="salesforce_get_record",
    name="Salesforce Get Record",
    provider="salesforce",
    resource="data",
    operation="get",
    description="Get a Salesforce record by ID.",
    icon="brand:salesforce",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="object_type",
            required=True,
            placeholder="Account",
            description="Salesforce object API name.",
        ),
        OperationParamSpec(
            name="record_id",
            required=True,
            placeholder="Record ID",
            description="ID of the record to retrieve.",
        ),
    ),
)

SALESFORCE_SEARCH_SPEC = OperationSpec(
    node_id="salesforce_search",
    name="Salesforce Search",
    provider="salesforce",
    resource="data",
    operation="search",
    description="Search Salesforce objects using SOSL (Salesforce Object Search Language).",
    icon="brand:salesforce",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="search_query",
            required=True,
            placeholder="FIND {Acme} IN ALL FIELDS",
            description="SOSL search query.",
        ),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items()}
    return {}


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    instance_url = str(creds.get("instance_url") or "").rstrip("/")
    access_token = str(creds.get("access_token") or creds.get("token") or "")
    if not instance_url or not access_token:
        raise ValueError(
            "salesforce: instance_url and access_token are required in credentials"
        )
    return ProviderTransport(
        provider="salesforce",
        base_url=instance_url,
        default_headers={
            "Authorization": f"Bearer {access_token}",
        },
    )


def _require(value: str, node_id: str, name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{node_id}: {name} is required")
    return clean


def _fields_from_input(input_value: Any, fields: Any) -> dict[str, Any]:
    if isinstance(fields, dict) and fields:
        return fields
    if isinstance(input_value, dict):
        return input_value
    return {}


def query(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    query: str = "",
) -> dict[str, Any]:
    soql = _require(query, "salesforce_query", "query")
    result = _transport(credentials).request(
        "GET",
        "/services/data/v58.0/query",
        operation="query",
        params={"q": soql},
    )
    if isinstance(result, dict):
        records = result.get("records", [])
        # Remove attributes wrapper from each record
        clean = [
            {k: v for k, v in rec.items() if k != "attributes"}
            for rec in records
        ]
        return {
            "records": clean,
            "count": len(clean),
            "total_size": result.get("totalSize", len(clean)),
            "done": result.get("done", True),
        }
    return {"records": [], "count": 0}


def create_record(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    object_type: str = "",
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    obj = _require(object_type, "salesforce_create_record", "object_type")
    body = _fields_from_input(input, fields)
    if not body:
        raise ValueError("salesforce_create_record: fields or input object is required")
    result = _transport(credentials).request(
        "POST",
        f"/services/data/v58.0/sobjects/{quote(obj)}",
        operation="create_record",
        json_body=body,
    )
    if isinstance(result, dict):
        return {
            "id": result.get("id", ""),
            "success": result.get("success", False),
            "object_type": obj,
        }
    return result


def update_record(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    object_type: str = "",
    record_id: str = "",
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    obj = _require(object_type, "salesforce_update_record", "object_type")
    rid = _require(record_id, "salesforce_update_record", "record_id")
    body = _fields_from_input(input, fields)
    if not body:
        raise ValueError("salesforce_update_record: fields or input object is required")
    _transport(credentials).request(
        "PATCH",
        f"/services/data/v58.0/sobjects/{quote(obj)}/{quote(rid)}",
        operation="update_record",
        json_body=body,
    )
    return {"id": rid, "object_type": obj, "success": True}


def delete_record(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    object_type: str = "",
    record_id: str = "",
) -> dict[str, Any]:
    obj = _require(object_type, "salesforce_delete_record", "object_type")
    rid = _require(record_id, "salesforce_delete_record", "record_id")
    _transport(credentials).request(
        "DELETE",
        f"/services/data/v58.0/sobjects/{quote(obj)}/{quote(rid)}",
        operation="delete_record",
    )
    return {"id": rid, "object_type": obj, "success": True}


def get_record(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    object_type: str = "",
    record_id: str = "",
) -> dict[str, Any]:
    obj = _require(object_type, "salesforce_get_record", "object_type")
    rid = _require(record_id, "salesforce_get_record", "record_id")
    result = _transport(credentials).request(
        "GET",
        f"/services/data/v58.0/sobjects/{quote(obj)}/{quote(rid)}",
        operation="get_record",
    )
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k != "attributes"}
    return result


def search(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    search_query: str = "",
) -> dict[str, Any]:
    sosl = _require(search_query, "salesforce_search", "search_query")
    result = _transport(credentials).request(
        "GET",
        "/services/data/v58.0/search",
        operation="search",
        params={"q": sosl},
    )
    if isinstance(result, dict):
        records = result.get("searchRecords", [])
        clean = [
            {k: v for k, v in rec.items() if k != "attributes"}
            for rec in records
        ]
        return {"records": clean, "count": len(clean)}
    return {"records": [], "count": 0}


register_operation(SALESFORCE_QUERY_SPEC, query)
register_operation(SALESFORCE_CREATE_RECORD_SPEC, create_record)
register_operation(SALESFORCE_UPDATE_RECORD_SPEC, update_record)
register_operation(SALESFORCE_DELETE_RECORD_SPEC, delete_record)
register_operation(SALESFORCE_GET_RECORD_SPEC, get_record)
register_operation(SALESFORCE_SEARCH_SPEC, search)
