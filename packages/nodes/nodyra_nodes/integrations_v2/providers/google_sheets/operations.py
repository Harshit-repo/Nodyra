"""Google Sheets v2 operation specs and executors."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.providers.google import GoogleTransport
from nodyra_nodes.integrations_v2.registry import register_integration, register_operation
from nodyra_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="google_sheets_oauth2",
            key="*",
            label="Google Sheets OAuth2",
            fields=["access_token", "refresh_token"],
            multi=True,
            test_service="google_sheets",
        ),
        required_scopes=(SHEETS_SCOPE,),
    )


def _sheet_name_param() -> OperationParamSpec:
    """The sheet/tab picker: a dynamic dropdown listing the chosen
    spreadsheet's tabs. Combined with ``range_name`` at runtime so the cell
    range stays sheet-agnostic (e.g. ``A1:Z100``)."""
    return OperationParamSpec(
        name="sheet_name",
        default="Sheet1",
        placeholder="Sheet1",
        description="Sheet/tab to use. Pick from the list once a spreadsheet is set.",
        load_options="google_sheets.list_sheet_names",
        depends_on=("credentials", "spreadsheet_id"),
    )


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------

GOOGLE_SHEETS_READ_SPEC = OperationSpec(
    node_id="google_sheets_read_v2",
    name="Google Sheets Read",
    provider="google_sheets",
    resource="values",
    operation="read",
    description="Read values from a Google Sheet using the v2 provider transport.",
    icon="brand:googlesheets",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="Spreadsheet ID",
        ),
        _sheet_name_param(),
        OperationParamSpec(
            name="range_name",
            default="A1:Z100",
            placeholder="A1:D20",
            description="Cell range within the sheet. Blank reads the whole sheet.",
        ),
    ),
)


GOOGLE_SHEETS_APPEND_SPEC = OperationSpec(
    node_id="google_sheets_append_v2",
    name="Google Sheets Append",
    provider="google_sheets",
    resource="values",
    operation="append",
    description="Append rows to a Google Sheet using the v2 provider transport.",
    icon="brand:googlesheets",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="Spreadsheet ID",
        ),
        _sheet_name_param(),
        OperationParamSpec(
            name="range_name",
            default="A:Z",
            placeholder="A:D",
            description="Cell range within the sheet to append after.",
        ),
        OperationParamSpec(
            name="values",
            type="array",
            default=None,
            description="Rows to append. Blank derives rows from the input.",
        ),
        OperationParamSpec(
            name="value_input_option",
            default="USER_ENTERED",
            choices=("RAW", "USER_ENTERED"),
            group="Options",
        ),
    ),
)

GOOGLE_SHEETS_UPDATE_SPEC = OperationSpec(
    node_id="google_sheets_update_v2",
    name="Google Sheets Update",
    provider="google_sheets",
    resource="values",
    operation="update",
    description="Update a range of cells in a Google Sheet.",
    icon="brand:googlesheets",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="Spreadsheet ID",
        ),
        _sheet_name_param(),
        OperationParamSpec(
            name="range_name",
            required=True,
            placeholder="A1:D5",
            description="Cell range within the sheet to overwrite.",
        ),
        OperationParamSpec(
            name="values",
            type="array",
            default=None,
            description="2-D list of cell values. Blank derives rows from the input.",
        ),
        OperationParamSpec(
            name="value_input_option",
            default="USER_ENTERED",
            choices=("RAW", "USER_ENTERED"),
            group="Options",
        ),
    ),
)

GOOGLE_SHEETS_CLEAR_SPEC = OperationSpec(
    node_id="google_sheets_clear_v2",
    name="Google Sheets Clear",
    provider="google_sheets",
    resource="values",
    operation="clear",
    description="Clear all values from a range in a Google Sheet.",
    icon="brand:googlesheets",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="Spreadsheet ID",
        ),
        _sheet_name_param(),
        OperationParamSpec(
            name="range_name",
            required=True,
            placeholder="A1:Z100",
            description="Cell range within the sheet to clear.",
        ),
    ),
)

GOOGLE_SHEETS_GET_METADATA_SPEC = OperationSpec(
    node_id="google_sheets_get_metadata_v2",
    name="Google Sheets Get Metadata",
    provider="google_sheets",
    resource="spreadsheet",
    operation="get_metadata",
    description="Get spreadsheet metadata including sheet names and properties.",
    icon="brand:googlesheets",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="Spreadsheet ID",
        ),
    ),
)

GOOGLE_SHEETS_CREATE_SPREADSHEET_SPEC = OperationSpec(
    node_id="google_sheets_create_spreadsheet_v2",
    name="Google Sheets Create Spreadsheet",
    provider="google_sheets",
    resource="spreadsheet",
    operation="create",
    description="Create a new Google spreadsheet.",
    icon="brand:googlesheets",
    params=(
        _credentials_param(),
        OperationParamSpec(name="title", required=True, placeholder="Pipeline report"),
        OperationParamSpec(name="locale", group="Options", placeholder="en_US"),
        OperationParamSpec(name="time_zone", group="Options", placeholder="Australia/Sydney"),
    ),
)

GOOGLE_SHEETS_BATCH_UPDATE_VALUES_SPEC = OperationSpec(
    node_id="google_sheets_batch_update_values_v2",
    name="Google Sheets Batch Update Values",
    provider="google_sheets",
    resource="values",
    operation="batch_update",
    description="Update multiple value ranges in a single request.",
    icon="brand:googlesheets",
    params=(
        _credentials_param(),
        OperationParamSpec(name="spreadsheet_id", required=True, placeholder="Spreadsheet ID"),
        OperationParamSpec(
            name="data",
            type="array",
            description="Array of {range, values} objects. Blank uses input.",
        ),
        OperationParamSpec(
            name="value_input_option",
            default="USER_ENTERED",
            choices=("RAW", "USER_ENTERED"),
            group="Options",
        ),
    ),
)

GOOGLE_SHEETS_ADD_SHEET_SPEC = OperationSpec(
    node_id="google_sheets_add_sheet_v2",
    name="Google Sheets Add Sheet",
    provider="google_sheets",
    resource="sheet",
    operation="add",
    description="Add a tab to a spreadsheet.",
    icon="brand:googlesheets",
    params=(
        _credentials_param(),
        OperationParamSpec(name="spreadsheet_id", required=True, placeholder="Spreadsheet ID"),
        OperationParamSpec(name="title", required=True, placeholder="New Sheet"),
        OperationParamSpec(name="row_count", type="number", default=1000, group="Options"),
        OperationParamSpec(name="column_count", type="number", default=26, group="Options"),
        OperationParamSpec(name="index", type="number", default=None, group="Options"),
    ),
)

GOOGLE_SHEETS_RENAME_SHEET_SPEC = OperationSpec(
    node_id="google_sheets_rename_sheet_v2",
    name="Google Sheets Rename Sheet",
    provider="google_sheets",
    resource="sheet",
    operation="rename",
    description="Rename a spreadsheet tab by sheet ID.",
    icon="brand:googlesheets",
    params=(
        _credentials_param(),
        OperationParamSpec(name="spreadsheet_id", required=True, placeholder="Spreadsheet ID"),
        OperationParamSpec(name="sheet_id", type="number", required=True),
        OperationParamSpec(name="title", required=True, placeholder="Renamed Sheet"),
    ),
)

GOOGLE_SHEETS_DELETE_SHEET_SPEC = OperationSpec(
    node_id="google_sheets_delete_sheet_v2",
    name="Google Sheets Delete Sheet",
    provider="google_sheets",
    resource="sheet",
    operation="delete",
    description="Delete a spreadsheet tab by sheet ID.",
    icon="brand:googlesheets",
    params=(
        _credentials_param(),
        OperationParamSpec(name="spreadsheet_id", required=True, placeholder="Spreadsheet ID"),
        OperationParamSpec(name="sheet_id", type="number", required=True),
    ),
)

GOOGLE_SHEETS_LOOKUP_ROWS_SPEC = OperationSpec(
    node_id="google_sheets_lookup_rows_v2",
    name="Google Sheets Lookup Rows",
    provider="google_sheets",
    resource="row",
    operation="lookup",
    description="Read a range and return rows matching a key column value.",
    icon="brand:googlesheets",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="spreadsheet_id", required=True, placeholder="Spreadsheet ID"),
        _sheet_name_param(),
        OperationParamSpec(name="range_name", default="A1:Z100", placeholder="A1:Z100"),
        OperationParamSpec(name="key_column", required=True, placeholder="Email"),
        OperationParamSpec(name="key_value", required=True, placeholder="ada@example.com"),
        OperationParamSpec(name="header_row_index", type="number", default=0, group="Options"),
        OperationParamSpec(name="max_matches", type="number", default=10, group="Options"),
    ),
)

GOOGLE_SHEETS_UPSERT_ROW_SPEC = OperationSpec(
    node_id="google_sheets_upsert_row_v2",
    name="Google Sheets Upsert Row",
    provider="google_sheets",
    resource="row",
    operation="upsert",
    description="Update the first matching row by key, or append a new row.",
    icon="brand:googlesheets",
    params=(
        _credentials_param(),
        OperationParamSpec(name="spreadsheet_id", required=True, placeholder="Spreadsheet ID"),
        _sheet_name_param(),
        OperationParamSpec(name="range_name", default="A1:Z", placeholder="A1:Z"),
        OperationParamSpec(name="key_column", required=True, placeholder="Email"),
        OperationParamSpec(name="key_value", required=True, placeholder="ada@example.com"),
        OperationParamSpec(
            name="row_values",
            type="object",
            description="Object keyed by header name, or array of row values. Blank uses input.",
        ),
        OperationParamSpec(
            name="value_input_option",
            default="USER_ENTERED",
            choices=("RAW", "USER_ENTERED"),
            group="Options",
        ),
        OperationParamSpec(name="header_row_index", type="number", default=0, group="Options"),
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _credentials_dict(value: Any) -> dict[str, str]:
    return value if isinstance(value, dict) else {}


def _transport(credentials: Any) -> GoogleTransport:
    creds = _credentials_dict(credentials)
    return GoogleTransport(
        access_token=str(creds.get("access_token") or ""),
        api_key=str(creds.get("api_key") or ""),
    )


def _spreadsheet_values(input_value: Any, values: Any) -> list[list[Any]]:
    if values:
        return values
    if input_value is None:
        return []
    if isinstance(input_value, list):
        if input_value and all(isinstance(row, list) for row in input_value):
            return input_value
        return [[item] for item in input_value]
    if isinstance(input_value, dict):
        return [list(input_value.values())]
    return [[input_value]]


def _require(value: str, node_id: str, name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{node_id}: {name} is required")
    return clean


def _batch_value_data(input_value: Any, data: Any) -> list[dict[str, Any]]:
    raw = data if data is not None else input_value
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        raw = raw["data"]
    if not isinstance(raw, list):
        raise ValueError("google_sheets_batch_update_values_v2: data must be a list")
    return [
        item
        for item in raw
        if isinstance(item, dict) and item.get("range") and "values" in item
    ]


def _batch_update(
    credentials: Any,
    spreadsheet_id: str,
    requests: list[dict[str, Any]],
    *,
    operation: str,
) -> Any:
    return _transport(credentials).request(
        "POST",
        f"/spreadsheets/{spreadsheet_id}:batchUpdate",
        operation=operation,
        json_body={"requests": requests},
    )


def _sheet_id(value: Any, node_id: str) -> int:
    sheet = int(value or 0)
    if sheet < 0:
        raise ValueError(f"{node_id}: sheet_id is required")
    return sheet


def _values_response_values(response: Any) -> list[list[Any]]:
    if isinstance(response, dict) and isinstance(response.get("values"), list):
        return response["values"]
    return []


def _column_label_to_index(label: str) -> int | None:
    clean = label.strip().upper()
    if not re.fullmatch(r"[A-Z]+", clean):
        return None
    index = 0
    for char in clean:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _key_column_index(headers: list[Any], key_column: Any) -> int:
    if isinstance(key_column, int) or str(key_column).strip().isdigit():
        index = int(key_column) - 1
        if index >= 0:
            return index
    for index, header in enumerate(headers):
        if str(header).strip() == str(key_column).strip():
            return index
    label_index = _column_label_to_index(str(key_column))
    if label_index is not None:
        return label_index
    raise ValueError("google_sheets row operation: key_column was not found")


def _record_from_row(headers: list[Any], row: list[Any]) -> dict[str, Any]:
    return {
        str(header): row[index] if index < len(row) else ""
        for index, header in enumerate(headers)
        if str(header)
    }


def _range_parts(range_name: str) -> tuple[str, int, int]:
    sheet = "Sheet1"
    cell_range = range_name
    if "!" in range_name:
        sheet, cell_range = range_name.split("!", 1)
        sheet = sheet.strip("'") or "Sheet1"
    match = re.match(r"\$?([A-Za-z]+)?\$?(\d+)?", cell_range)
    start_col = _column_label_to_index(match.group(1) or "A") if match else 0
    start_row = int(match.group(2) or 1) if match else 1
    return sheet, start_col or 0, start_row


def _a1_column(index: int) -> str:
    if index < 0:
        raise ValueError("google_sheets row operation: invalid column index")
    label = ""
    current = index + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def _quote_sheet_name(sheet: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_]+", sheet):
        return sheet
    escaped = sheet.replace("'", "''")
    return f"'{escaped}'"


def _combine_range(sheet_name: str, range_name: str) -> str:
    """Qualify a sheet-agnostic A1 range with the chosen sheet/tab.

    Back-compatible: if ``range_name`` already names a sheet (contains ``!``) or
    no sheet is chosen, it is returned unchanged — so older free-text ranges
    like ``Sheet1!A1:Z100`` keep working exactly as before.
    """
    sheet = (sheet_name or "").strip()
    cell_range = (range_name or "").strip()
    if not sheet or "!" in cell_range:
        return range_name
    prefix = _quote_sheet_name(sheet)
    return f"{prefix}!{cell_range}" if cell_range else prefix


def _row_range(range_name: str, response_row_index: int, width: int) -> str:
    sheet, start_col, start_row = _range_parts(range_name)
    row_number = start_row + response_row_index
    end_col = start_col + max(1, width) - 1
    return (
        f"{_quote_sheet_name(sheet)}!"
        f"{_a1_column(start_col)}{row_number}:{_a1_column(end_col)}{row_number}"
    )


def _row_values(headers: list[Any], key_column: Any, key_value: Any, raw: Any) -> list[Any]:
    if isinstance(raw, list):
        values = list(raw)
    elif isinstance(raw, dict):
        values = [raw.get(str(header), "") for header in headers] if headers else list(raw.values())
    else:
        values = [raw]
    key_index = _key_column_index(headers, key_column) if headers else 0
    while len(values) <= key_index:
        values.append("")
    values[key_index] = key_value
    return values


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def read_values(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    sheet_name: str = "",
    range_name: str = "A1:Z100",
) -> Any:
    range_name = _combine_range(sheet_name, range_name)
    encoded_range = quote(range_name, safe="!:'")
    return _transport(credentials).request(
        "GET",
        f"/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
        operation="read_values",
    )


def append_values(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    sheet_name: str = "",
    range_name: str = "A:Z",
    values: list | None = None,
    value_input_option: str = "USER_ENTERED",
) -> Any:
    range_name = _combine_range(sheet_name, range_name)
    encoded_range = quote(range_name, safe="!:'")
    return _transport(credentials).request(
        "POST",
        f"/spreadsheets/{spreadsheet_id}/values/{encoded_range}:append",
        operation="append_values",
        params={"valueInputOption": value_input_option},
        json_body={"values": _spreadsheet_values(input, values)},
    )


def update_values(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    sheet_name: str = "",
    range_name: str = "",
    values: list | None = None,
    value_input_option: str = "USER_ENTERED",
) -> Any:
    if not range_name:
        raise ValueError("google_sheets_update_v2: range_name is required")
    range_name = _combine_range(sheet_name, range_name)
    encoded_range = quote(range_name, safe="!:'")
    return _transport(credentials).request(
        "PUT",
        f"/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
        operation="update_values",
        params={"valueInputOption": value_input_option},
        json_body={"values": _spreadsheet_values(input, values), "range": range_name},
    )


def clear_values(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    sheet_name: str = "",
    range_name: str = "",
) -> Any:
    if not range_name:
        raise ValueError("google_sheets_clear_v2: range_name is required")
    range_name = _combine_range(sheet_name, range_name)
    encoded_range = quote(range_name, safe="!:'")
    return _transport(credentials).request(
        "POST",
        f"/spreadsheets/{spreadsheet_id}/values/{encoded_range}:clear",
        operation="clear_values",
        json_body={},
    )


def get_spreadsheet_metadata(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
) -> Any:
    """Return spreadsheet metadata including the list of sheet (tab) names."""
    response = _transport(credentials).request(
        "GET",
        f"/spreadsheets/{spreadsheet_id}",
        operation="get_spreadsheet_metadata",
        params={"fields": "spreadsheetId,properties,sheets.properties"},
    )
    # Flatten sheet names for easy consumption downstream
    sheets = response.get("sheets") if isinstance(response, dict) else []
    response["sheet_names"] = [
        s.get("properties", {}).get("title", "")
        for s in (sheets or [])
        if isinstance(s, dict)
    ]
    return response


def create_spreadsheet(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    title: str = "",
    locale: str = "",
    time_zone: str = "",
) -> Any:
    source = input if isinstance(input, dict) else {}
    spreadsheet_title = title or str(source.get("title") or "")
    if not spreadsheet_title:
        raise ValueError("google_sheets_create_spreadsheet_v2: title is required")
    properties = {"title": spreadsheet_title}
    if locale:
        properties["locale"] = locale
    if time_zone:
        properties["timeZone"] = time_zone
    return _transport(credentials).request(
        "POST",
        "/spreadsheets",
        operation="create_spreadsheet",
        json_body={"properties": properties},
    )


def batch_update_values(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    data: list[dict[str, Any]] | None = None,
    value_input_option: str = "USER_ENTERED",
) -> Any:
    ranges = _batch_value_data(input, data)
    if not ranges:
        raise ValueError("google_sheets_batch_update_values_v2: at least one range is required")
    return _transport(credentials).request(
        "POST",
        f"/spreadsheets/{spreadsheet_id}/values:batchUpdate",
        operation="batch_update_values",
        json_body={
            "valueInputOption": value_input_option,
            "data": ranges,
        },
    )


def add_sheet(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    title: str = "",
    row_count: int = 1000,
    column_count: int = 26,
    index: int | None = None,
) -> Any:
    sheet_title = _require(title, "google_sheets_add_sheet_v2", "title")
    properties: dict[str, Any] = {
        "title": sheet_title,
        "gridProperties": {
            "rowCount": max(1, int(row_count or 1000)),
            "columnCount": max(1, int(column_count or 26)),
        },
    }
    if index is not None:
        properties["index"] = int(index)
    return _batch_update(
        credentials,
        spreadsheet_id,
        [{"addSheet": {"properties": properties}}],
        operation="add_sheet",
    )


def rename_sheet(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    sheet_id: int = 0,
    title: str = "",
) -> Any:
    sheet_title = _require(title, "google_sheets_rename_sheet_v2", "title")
    return _batch_update(
        credentials,
        spreadsheet_id,
        [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": _sheet_id(sheet_id, "google_sheets_rename_sheet_v2"),
                        "title": sheet_title,
                    },
                    "fields": "title",
                }
            }
        ],
        operation="rename_sheet",
    )


def delete_sheet(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    sheet_id: int = 0,
) -> Any:
    return _batch_update(
        credentials,
        spreadsheet_id,
        [{"deleteSheet": {"sheetId": _sheet_id(sheet_id, "google_sheets_delete_sheet_v2")}}],
        operation="delete_sheet",
    )


def lookup_rows(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    sheet_name: str = "",
    range_name: str = "A1:Z100",
    key_column: str = "",
    key_value: str = "",
    header_row_index: int = 0,
    max_matches: int = 10,
) -> Any:
    range_name = _combine_range(sheet_name, range_name)
    encoded_range = quote(range_name, safe="!:'")
    response = _transport(credentials).request(
        "GET",
        f"/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
        operation="lookup_rows",
    )
    values = _values_response_values(response)
    header_index = max(0, int(header_row_index or 0))
    headers = values[header_index] if len(values) > header_index else []
    key_index = _key_column_index(headers, key_column)
    matches = []
    for row_index, row in enumerate(values[header_index + 1 :], start=header_index + 1):
        cell = row[key_index] if key_index < len(row) else ""
        if str(cell) == str(key_value):
            matches.append(
                {
                    "row_number": _range_parts(range_name)[2] + row_index,
                    "values": row,
                    "record": _record_from_row(headers, row),
                }
            )
            if len(matches) >= max(1, int(max_matches or 10)):
                break
    return {"matches": matches, "match_count": len(matches), "headers": headers}


def upsert_row(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    sheet_name: str = "",
    range_name: str = "A1:Z",
    key_column: str = "",
    key_value: str = "",
    row_values: dict[str, Any] | list[Any] | None = None,
    value_input_option: str = "USER_ENTERED",
    header_row_index: int = 0,
) -> Any:
    range_name = _combine_range(sheet_name, range_name)
    transport = _transport(credentials)
    encoded_range = quote(range_name, safe="!:'")
    response = transport.request(
        "GET",
        f"/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
        operation="upsert_row_read",
    )
    values = _values_response_values(response)
    header_index = max(0, int(header_row_index or 0))
    headers = values[header_index] if len(values) > header_index else []
    key_index = _key_column_index(headers, key_column)
    raw_values = row_values if row_values is not None else input
    next_values = _row_values(headers, key_column, key_value, raw_values)
    for row_index, row in enumerate(values[header_index + 1 :], start=header_index + 1):
        cell = row[key_index] if key_index < len(row) else ""
        if str(cell) == str(key_value):
            update_range = _row_range(range_name, row_index, len(next_values))
            encoded_update_range = quote(update_range, safe="!:'")
            result = transport.request(
                "PUT",
                f"/spreadsheets/{spreadsheet_id}/values/{encoded_update_range}",
                operation="upsert_row_update",
                params={"valueInputOption": value_input_option},
                json_body={"values": [next_values], "range": update_range},
            )
            return {
                "action": "updated",
                "row_number": _range_parts(range_name)[2] + row_index,
                "range": update_range,
                "result": result,
            }
    result = transport.request(
        "POST",
        f"/spreadsheets/{spreadsheet_id}/values/{encoded_range}:append",
        operation="upsert_row_append",
        params={"valueInputOption": value_input_option},
        json_body={"values": [next_values]},
    )
    return {"action": "appended", "result": result}


# Register executors only (node_registry=None): the per-operation nodes are no
# longer exposed in the palette — the consolidated GOOGLE_SHEETS_INTEGRATION node
# below dispatches to them by (resource, operation).
register_operation(GOOGLE_SHEETS_READ_SPEC, read_values, node_registry=None)
register_operation(GOOGLE_SHEETS_APPEND_SPEC, append_values, node_registry=None)
register_operation(GOOGLE_SHEETS_UPDATE_SPEC, update_values, node_registry=None)
register_operation(GOOGLE_SHEETS_CLEAR_SPEC, clear_values, node_registry=None)
register_operation(
    GOOGLE_SHEETS_GET_METADATA_SPEC, get_spreadsheet_metadata, node_registry=None
)
register_operation(
    GOOGLE_SHEETS_CREATE_SPREADSHEET_SPEC, create_spreadsheet, node_registry=None
)
register_operation(
    GOOGLE_SHEETS_BATCH_UPDATE_VALUES_SPEC, batch_update_values, node_registry=None
)
register_operation(GOOGLE_SHEETS_ADD_SHEET_SPEC, add_sheet, node_registry=None)
register_operation(GOOGLE_SHEETS_RENAME_SHEET_SPEC, rename_sheet, node_registry=None)
register_operation(GOOGLE_SHEETS_DELETE_SHEET_SPEC, delete_sheet, node_registry=None)
register_operation(GOOGLE_SHEETS_LOOKUP_ROWS_SPEC, lookup_rows, node_registry=None)
register_operation(GOOGLE_SHEETS_UPSERT_ROW_SPEC, upsert_row, node_registry=None)


GOOGLE_SHEETS_INTEGRATION = IntegrationSpec(
    id="google_sheets",
    name="Google Sheets",
    description="Read, write, and manage Google Sheets spreadsheets and tabs.",
    icon="brand:googlesheets",
    credential_types=("google_sheets_oauth2",),
    resources=(
        ResourceSpec(
            id="values",
            name="Values",
            operations=(
                GOOGLE_SHEETS_READ_SPEC,
                GOOGLE_SHEETS_APPEND_SPEC,
                GOOGLE_SHEETS_UPDATE_SPEC,
                GOOGLE_SHEETS_CLEAR_SPEC,
                GOOGLE_SHEETS_BATCH_UPDATE_VALUES_SPEC,
            ),
        ),
        ResourceSpec(
            id="row",
            name="Row",
            operations=(
                GOOGLE_SHEETS_LOOKUP_ROWS_SPEC,
                GOOGLE_SHEETS_UPSERT_ROW_SPEC,
            ),
        ),
        ResourceSpec(
            id="spreadsheet",
            name="Spreadsheet",
            operations=(
                GOOGLE_SHEETS_GET_METADATA_SPEC,
                GOOGLE_SHEETS_CREATE_SPREADSHEET_SPEC,
            ),
        ),
        ResourceSpec(
            id="sheet",
            name="Sheet",
            operations=(
                GOOGLE_SHEETS_ADD_SHEET_SPEC,
                GOOGLE_SHEETS_RENAME_SHEET_SPEC,
                GOOGLE_SHEETS_DELETE_SHEET_SPEC,
            ),
        ),
    ),
)

register_integration(GOOGLE_SHEETS_INTEGRATION)
