"""Google Sheets v2 operation specs and executors."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.providers.google import GoogleTransport
from noodle_nodes.integrations_v2.registry import register_operation
from noodle_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec

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


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------

GOOGLE_SHEETS_READ_SPEC = OperationSpec(
    node_id="google_sheets_read_v2",
    name="Google Sheets Read V2",
    provider="google_sheets",
    resource="values",
    operation="read",
    description="Read values from a Google Sheet using the v2 provider transport.",
    icon="sheet",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="Spreadsheet ID",
        ),
        OperationParamSpec(
            name="range_name",
            default="Sheet1!A1:Z100",
            placeholder="Sheet1!A1:D20",
        ),
    ),
)


GOOGLE_SHEETS_APPEND_SPEC = OperationSpec(
    node_id="google_sheets_append_v2",
    name="Google Sheets Append V2",
    provider="google_sheets",
    resource="values",
    operation="append",
    description="Append rows to a Google Sheet using the v2 provider transport.",
    icon="sheet",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="Spreadsheet ID",
        ),
        OperationParamSpec(
            name="range_name",
            default="Sheet1!A:Z",
            placeholder="Sheet1!A:D",
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
    name="Google Sheets Update V2",
    provider="google_sheets",
    resource="values",
    operation="update",
    description="Update a range of cells in a Google Sheet.",
    icon="sheet",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="Spreadsheet ID",
        ),
        OperationParamSpec(
            name="range_name",
            required=True,
            placeholder="Sheet1!A1:D5",
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
    name="Google Sheets Clear V2",
    provider="google_sheets",
    resource="values",
    operation="clear",
    description="Clear all values from a range in a Google Sheet.",
    icon="sheet",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="Spreadsheet ID",
        ),
        OperationParamSpec(
            name="range_name",
            required=True,
            placeholder="Sheet1!A1:Z100",
        ),
    ),
)

GOOGLE_SHEETS_GET_METADATA_SPEC = OperationSpec(
    node_id="google_sheets_get_metadata_v2",
    name="Google Sheets Get Metadata V2",
    provider="google_sheets",
    resource="spreadsheet",
    operation="get_metadata",
    description="Get spreadsheet metadata including sheet names and properties.",
    icon="sheet",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="Spreadsheet ID",
        ),
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


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def read_values(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    spreadsheet_id: str = "",
    range_name: str = "Sheet1!A1:Z100",
) -> Any:
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
    range_name: str = "Sheet1!A:Z",
    values: list | None = None,
    value_input_option: str = "USER_ENTERED",
) -> Any:
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
    range_name: str = "",
    values: list | None = None,
    value_input_option: str = "USER_ENTERED",
) -> Any:
    if not range_name:
        raise ValueError("google_sheets_update_v2: range_name is required")
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
    range_name: str = "",
) -> Any:
    if not range_name:
        raise ValueError("google_sheets_clear_v2: range_name is required")
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


register_operation(GOOGLE_SHEETS_READ_SPEC, read_values)
register_operation(GOOGLE_SHEETS_APPEND_SPEC, append_values)
register_operation(GOOGLE_SHEETS_UPDATE_SPEC, update_values)
register_operation(GOOGLE_SHEETS_CLEAR_SPEC, clear_values)
register_operation(GOOGLE_SHEETS_GET_METADATA_SPEC, get_spreadsheet_metadata)
