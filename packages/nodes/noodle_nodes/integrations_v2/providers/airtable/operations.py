"""Airtable v2 operation specs and executors."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_operation
from noodle_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec
from noodle_nodes.integrations_v2.transport import ProviderTransport

AIRTABLE_API_BASE = "https://api.airtable.com/v0"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="airtable",
            key="*",
            label="Airtable token",
            fields=["token"],
            multi=True,
            test_service="airtable",
        ),
        description="Airtable personal access token.",
    )


AIRTABLE_LIST_RECORDS_SPEC = OperationSpec(
    node_id="airtable_list_records_v2",
    name="Airtable List Records",
    provider="airtable",
    resource="records",
    operation="list",
    description="List Airtable records using the v2 provider transport.",
    icon="brand:airtable",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="base_id", required=True, placeholder="app..."),
        OperationParamSpec(name="table_name", required=True, placeholder="Tasks"),
        OperationParamSpec(name="view", group="Options", placeholder="Grid view"),
        OperationParamSpec(
            name="max_records",
            type="number",
            default=100,
            group="Options",
            description="Maximum records to fetch.",
        ),
        OperationParamSpec(
            name="filter_formula",
            group="Options",
            placeholder="{Status} = 'Open'",
        ),
    ),
)


AIRTABLE_CREATE_RECORD_SPEC = OperationSpec(
    node_id="airtable_create_record_v2",
    name="Airtable Create Record",
    provider="airtable",
    resource="record",
    operation="create",
    description="Create an Airtable record using the v2 provider transport.",
    icon="brand:airtable",
    params=(
        _credentials_param(),
        OperationParamSpec(name="base_id", required=True, placeholder="app..."),
        OperationParamSpec(name="table_name", required=True, placeholder="Tasks"),
        OperationParamSpec(
            name="fields",
            type="object",
            description="Record fields. Blank uses input when it is an object.",
        ),
        OperationParamSpec(
            name="typecast",
            type="boolean",
            default=False,
            group="Options",
            description="Let Airtable coerce select/date fields.",
        ),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items()}
    if isinstance(value, str):
        return {"token": value}
    return {}


def _token(credentials: Any) -> str:
    creds = _credentials_dict(credentials)
    return str(creds.get("token") or creds.get("api_key") or "")


def _transport(credentials: Any) -> ProviderTransport:
    token = _token(credentials)
    if not token:
        raise ValueError("airtable v2: credentials are required")
    return ProviderTransport(
        provider="airtable",
        base_url=AIRTABLE_API_BASE,
        default_headers={"Authorization": f"Bearer {token}"},
    )


def _table_path(base_id: str, table_name: str) -> str:
    clean_base = str(base_id or "").strip()
    clean_table = str(table_name or "").strip()
    if not clean_base or not clean_table:
        raise ValueError("airtable v2: base_id and table_name are required")
    return f"/{clean_base}/{quote(clean_table, safe='')}"


def _dict_from_input(input_value: Any, value: dict[str, Any] | None = None) -> dict[str, Any]:
    if value is not None:
        return value
    return input_value if isinstance(input_value, dict) else {}


def list_records(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    base_id: str = "",
    table_name: str = "",
    view: str = "",
    max_records: int = 100,
    filter_formula: str = "",
) -> Any:
    params: dict[str, Any] = {"maxRecords": int(max_records or 100)}
    if view:
        params["view"] = view
    if filter_formula:
        params["filterByFormula"] = filter_formula
    return _transport(credentials).request(
        "GET",
        _table_path(base_id, table_name),
        operation="list_records",
        params=params,
    )


def create_record(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    base_id: str = "",
    table_name: str = "",
    fields: dict[str, Any] | None = None,
    typecast: bool = False,
) -> Any:
    return _transport(credentials).request(
        "POST",
        _table_path(base_id, table_name),
        operation="create_record",
        json_body={
            "fields": _dict_from_input(input, fields),
            "typecast": bool(typecast),
        },
    )


register_operation(AIRTABLE_LIST_RECORDS_SPEC, list_records)
register_operation(AIRTABLE_CREATE_RECORD_SPEC, create_record)
