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

AIRTABLE_GET_RECORD_SPEC = OperationSpec(
    node_id="airtable_get_record_v2",
    name="Airtable Get Record",
    provider="airtable",
    resource="record",
    operation="get",
    description="Get an Airtable record by ID.",
    icon="brand:airtable",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="base_id", required=True, placeholder="app..."),
        OperationParamSpec(name="table_name", required=True, placeholder="Tasks"),
        OperationParamSpec(name="record_id", required=True, placeholder="rec..."),
    ),
)

AIRTABLE_UPDATE_RECORD_SPEC = OperationSpec(
    node_id="airtable_update_record_v2",
    name="Airtable Update Record",
    provider="airtable",
    resource="record",
    operation="update",
    description="Update fields on an Airtable record.",
    icon="brand:airtable",
    params=(
        _credentials_param(),
        OperationParamSpec(name="base_id", required=True, placeholder="app..."),
        OperationParamSpec(name="table_name", required=True, placeholder="Tasks"),
        OperationParamSpec(name="record_id", required=True, placeholder="rec..."),
        OperationParamSpec(
            name="fields",
            type="object",
            description="Record fields. Blank uses input when it is an object.",
        ),
        OperationParamSpec(name="typecast", type="boolean", default=False, group="Options"),
    ),
)

AIRTABLE_DELETE_RECORD_SPEC = OperationSpec(
    node_id="airtable_delete_record_v2",
    name="Airtable Delete Record",
    provider="airtable",
    resource="record",
    operation="delete",
    description="Delete an Airtable record.",
    icon="brand:airtable",
    params=(
        _credentials_param(),
        OperationParamSpec(name="base_id", required=True, placeholder="app..."),
        OperationParamSpec(name="table_name", required=True, placeholder="Tasks"),
        OperationParamSpec(name="record_id", required=True, placeholder="rec..."),
    ),
)

AIRTABLE_BATCH_CREATE_RECORDS_SPEC = OperationSpec(
    node_id="airtable_batch_create_records_v2",
    name="Airtable Batch Create Records",
    provider="airtable",
    resource="record",
    operation="batch_create",
    description="Create up to 10 Airtable records in one request.",
    icon="brand:airtable",
    params=(
        _credentials_param(),
        OperationParamSpec(name="base_id", required=True, placeholder="app..."),
        OperationParamSpec(name="table_name", required=True, placeholder="Tasks"),
        OperationParamSpec(
            name="records",
            type="array",
            description="Array of field objects or {fields} records. Blank uses input.",
        ),
        OperationParamSpec(name="typecast", type="boolean", default=False, group="Options"),
    ),
)

AIRTABLE_BATCH_UPDATE_RECORDS_SPEC = OperationSpec(
    node_id="airtable_batch_update_records_v2",
    name="Airtable Batch Update Records",
    provider="airtable",
    resource="record",
    operation="batch_update",
    description="Update up to 10 Airtable records in one request.",
    icon="brand:airtable",
    params=(
        _credentials_param(),
        OperationParamSpec(name="base_id", required=True, placeholder="app..."),
        OperationParamSpec(name="table_name", required=True, placeholder="Tasks"),
        OperationParamSpec(
            name="records",
            type="array",
            description="Array of {id, fields} records. Blank uses input.",
        ),
        OperationParamSpec(name="typecast", type="boolean", default=False, group="Options"),
    ),
)

AIRTABLE_UPSERT_RECORDS_SPEC = OperationSpec(
    node_id="airtable_upsert_records_v2",
    name="Airtable Upsert Records",
    provider="airtable",
    resource="record",
    operation="upsert",
    description="Create or update Airtable records using fieldsToMergeOn.",
    icon="brand:airtable",
    params=(
        _credentials_param(),
        OperationParamSpec(name="base_id", required=True, placeholder="app..."),
        OperationParamSpec(name="table_name", required=True, placeholder="Tasks"),
        OperationParamSpec(
            name="fields_to_merge_on",
            type="array",
            required=True,
            description="Field names Airtable should match on.",
        ),
        OperationParamSpec(
            name="records",
            type="array",
            description="Array of field objects or {fields} records. Blank uses input.",
        ),
        OperationParamSpec(name="typecast", type="boolean", default=False, group="Options"),
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


def _record_id(value: str, node_id: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{node_id}: record_id is required")
    return clean


def _string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _records_from_input(input_value: Any, records: Any) -> list[dict[str, Any]]:
    raw = records if records is not None else input_value
    if isinstance(raw, dict) and isinstance(raw.get("records"), list):
        raw = raw["records"]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("airtable batch operation: records must be a list")
    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("airtable batch operation: each record must be an object")
        if "fields" in item and isinstance(item["fields"], dict):
            normalized.append({"fields": item["fields"]})
        else:
            normalized.append({"fields": item})
    if not normalized:
        raise ValueError("airtable batch operation: at least one record is required")
    if len(normalized) > 10:
        raise ValueError("airtable batch operation: Airtable accepts at most 10 records")
    return normalized


def _update_records_from_input(input_value: Any, records: Any) -> list[dict[str, Any]]:
    raw = records if records is not None else input_value
    if isinstance(raw, dict) and isinstance(raw.get("records"), list):
        raw = raw["records"]
    if not isinstance(raw, list):
        raise ValueError("airtable batch update: records must be a list")
    normalized = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError("airtable batch update: each record needs id and fields")
        fields = item.get("fields")
        if not isinstance(fields, dict):
            fields = {key: value for key, value in item.items() if key != "id"}
        normalized.append({"id": str(item["id"]), "fields": fields})
    if not normalized:
        raise ValueError("airtable batch update: at least one record is required")
    if len(normalized) > 10:
        raise ValueError("airtable batch update: Airtable accepts at most 10 records")
    return normalized


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
    requested = max(1, int(max_records or 100))
    params: dict[str, Any] = {"maxRecords": requested}
    if view:
        params["view"] = view
    if filter_formula:
        params["filterByFormula"] = filter_formula
    transport = _transport(credentials)
    response = transport.request(
        "GET",
        _table_path(base_id, table_name),
        operation="list_records",
        params=params,
    )
    if not isinstance(response, dict) or not response.get("offset") or requested <= 100:
        return response
    records = list(response.get("records") or [])
    offset = response.get("offset")
    while offset and len(records) < requested:
        page = transport.request(
            "GET",
            _table_path(base_id, table_name),
            operation="list_records",
            params={**params, "offset": offset, "maxRecords": requested - len(records)},
        )
        if not isinstance(page, dict):
            break
        records.extend(page.get("records") or [])
        offset = page.get("offset")
    return {"records": records[:requested]}


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


def get_record(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    base_id: str = "",
    table_name: str = "",
    record_id: str = "",
) -> Any:
    record = _record_id(record_id, "airtable_get_record_v2")
    return _transport(credentials).request(
        "GET",
        f"{_table_path(base_id, table_name)}/{quote(record, safe='')}",
        operation="get_record",
    )


def update_record(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    base_id: str = "",
    table_name: str = "",
    record_id: str = "",
    fields: dict[str, Any] | None = None,
    typecast: bool = False,
) -> Any:
    record = _record_id(record_id, "airtable_update_record_v2")
    return _transport(credentials).request(
        "PATCH",
        f"{_table_path(base_id, table_name)}/{quote(record, safe='')}",
        operation="update_record",
        json_body={
            "fields": _dict_from_input(input, fields),
            "typecast": bool(typecast),
        },
    )


def delete_record(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    base_id: str = "",
    table_name: str = "",
    record_id: str = "",
) -> Any:
    record = _record_id(record_id, "airtable_delete_record_v2")
    return _transport(credentials).request(
        "DELETE",
        f"{_table_path(base_id, table_name)}/{quote(record, safe='')}",
        operation="delete_record",
    )


def batch_create_records(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    base_id: str = "",
    table_name: str = "",
    records: list[dict[str, Any]] | None = None,
    typecast: bool = False,
) -> Any:
    return _transport(credentials).request(
        "POST",
        _table_path(base_id, table_name),
        operation="batch_create_records",
        json_body={
            "records": _records_from_input(input, records),
            "typecast": bool(typecast),
        },
    )


def batch_update_records(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    base_id: str = "",
    table_name: str = "",
    records: list[dict[str, Any]] | None = None,
    typecast: bool = False,
) -> Any:
    return _transport(credentials).request(
        "PATCH",
        _table_path(base_id, table_name),
        operation="batch_update_records",
        json_body={
            "records": _update_records_from_input(input, records),
            "typecast": bool(typecast),
        },
    )


def upsert_records(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    base_id: str = "",
    table_name: str = "",
    fields_to_merge_on: list[str] | str | None = None,
    records: list[dict[str, Any]] | None = None,
    typecast: bool = False,
) -> Any:
    merge_fields = _string_list(fields_to_merge_on)
    if not merge_fields:
        raise ValueError("airtable_upsert_records_v2: fields_to_merge_on is required")
    return _transport(credentials).request(
        "PATCH",
        _table_path(base_id, table_name),
        operation="upsert_records",
        json_body={
            "performUpsert": {"fieldsToMergeOn": merge_fields},
            "records": _records_from_input(input, records),
            "typecast": bool(typecast),
        },
    )


register_operation(AIRTABLE_LIST_RECORDS_SPEC, list_records)
register_operation(AIRTABLE_CREATE_RECORD_SPEC, create_record)
register_operation(AIRTABLE_GET_RECORD_SPEC, get_record)
register_operation(AIRTABLE_UPDATE_RECORD_SPEC, update_record)
register_operation(AIRTABLE_DELETE_RECORD_SPEC, delete_record)
register_operation(AIRTABLE_BATCH_CREATE_RECORDS_SPEC, batch_create_records)
register_operation(AIRTABLE_BATCH_UPDATE_RECORDS_SPEC, batch_update_records)
register_operation(AIRTABLE_UPSERT_RECORDS_SPEC, upsert_records)
