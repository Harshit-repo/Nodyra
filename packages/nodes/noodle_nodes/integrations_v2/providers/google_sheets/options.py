"""Dynamic option loaders for Google Sheets v2 nodes."""

from __future__ import annotations

from typing import Any

from noodle_nodes.integrations_v2.dynamic_options import DynamicOption, register_loader
from noodle_nodes.integrations_v2.providers.google_sheets.operations import _transport


def _list_sheet_names(
    spreadsheet_id: str = "",
    credentials: Any = None,
    **_kwargs: Any,
) -> list[DynamicOption]:
    """Return the sheet (tab) names inside a spreadsheet as selectable options."""
    if not spreadsheet_id:
        return []
    transport = _transport(credentials)
    response = transport.request(
        "GET",
        f"/spreadsheets/{spreadsheet_id}",
        operation="list_sheet_names",
        params={"fields": "sheets.properties.title,sheets.properties.sheetId"},
    )
    sheets = response.get("sheets") if isinstance(response, dict) else []
    return [
        DynamicOption(
            value=s.get("properties", {}).get("title", ""),
            label=s.get("properties", {}).get("title", ""),
        )
        for s in (sheets or [])
        if isinstance(s, dict)
        and s.get("properties", {}).get("title")
    ]


def _list_header_columns(
    spreadsheet_id: str = "",
    sheet_name: str = "Sheet1",
    credentials: Any = None,
    **_kwargs: Any,
) -> list[DynamicOption]:
    """Return the header row column names from the first row of a sheet."""
    if not spreadsheet_id:
        return []
    from urllib.parse import quote

    range_name = f"{sheet_name}!1:1"
    encoded = quote(range_name, safe="!:'")
    transport = _transport(credentials)
    response = transport.request(
        "GET",
        f"/spreadsheets/{spreadsheet_id}/values/{encoded}",
        operation="list_header_columns",
    )
    rows = response.get("values") if isinstance(response, dict) else []
    header_row = rows[0] if rows and isinstance(rows, list) else []
    return [
        DynamicOption(value=str(col), label=str(col))
        for col in header_row
        if col
    ]


register_loader("google_sheets.list_sheet_names", _list_sheet_names)
register_loader("google_sheets.list_header_columns", _list_header_columns)
