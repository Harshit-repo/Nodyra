"""Google Sheets polling trigger — detects new rows appended to a sheet."""
from __future__ import annotations

from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport

SHEETS_API_BASE = "https://sheets.googleapis.com"


def _transport(credentials: Any) -> ProviderTransport:
    creds = credentials if isinstance(credentials, dict) else {}
    token = str(creds.get("access_token") or creds.get("api_key") or "")
    if not token:
        raise ValueError(
            "google_sheets_new_row_trigger_v2: credentials are required"
        )
    return ProviderTransport(
        provider="google_sheets",
        base_url=SHEETS_API_BASE,
        default_headers={"Authorization": f"Bearer {token}"},
    )


def _sheet_range(sheet_name: str) -> str:
    safe = sheet_name.replace("'", "''") if sheet_name else "Sheet1"
    return f"'{safe}'"


def poll_new_rows(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    params = ctx.params
    spreadsheet_id = str(params.get("spreadsheet_id") or "").strip()
    if not spreadsheet_id:
        raise ValueError(
            "google_sheets_new_row_trigger_v2: spreadsheet_id is required"
        )
    sheet_name = str(params.get("sheet_name") or "Sheet1").strip()
    try:
        header_row = max(1, int(params.get("header_row") or 1))
    except (ValueError, TypeError):
        header_row = 1

    transport = _transport(params.get("credentials"))
    response = transport.request(
        "GET",
        f"/v4/spreadsheets/{spreadsheet_id}/values/{_sheet_range(sheet_name)}",
        operation="get_sheet_values",
    )
    all_rows: list[list[str]] = response.get("values") or []

    if not all_rows:
        current_count = 0
        headers: list[str] = []
        data_rows: list[list[str]] = []
    else:
        headers = (
            [str(h) for h in all_rows[header_row - 1]]
            if len(all_rows) >= header_row
            else []
        )
        data_rows = all_rows[header_row:]
        current_count = len(data_rows)

    last_index = ctx.cursor.get("last_row_index")

    if last_index is None:
        return ProviderTriggerPollResult(
            events=[],
            cursor={"last_row_index": current_count},
        )

    new_rows = data_rows[last_index:]
    events: list[dict[str, Any]] = []
    for i, raw_row in enumerate(new_rows):
        row_dict: dict[str, Any] = {}
        for col_idx, header in enumerate(headers):
            row_dict[header] = raw_row[col_idx] if col_idx < len(raw_row) else ""
        events.append(
            {
                "provider": "google_sheets",
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
                "row_index": last_index + i + 1,
                "row": row_dict,
            }
        )

    return ProviderTriggerPollResult(
        events=events,
        cursor={"last_row_index": current_count},
    )


GOOGLE_SHEETS_NEW_ROW_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="google_sheets_new_row_trigger_v2",
    name="Google Sheets New Row",
    provider="google_sheets",
    resource="sheet",
    event="new_row",
    description="Start a workflow when a new row is appended to a Google Sheet.",
    icon="brand:google-sheets",
    params=(
        OperationParamSpec(
            name="credentials",
            type="credential",
            required=True,
            credential=CredentialSpec(
                type="google_sheets_oauth2",
                key="*",
                label="Google Sheets account",
                fields=["access_token"],
                multi=True,
                test_service="google_sheets",
            ),
            description="Google Sheets OAuth credential.",
        ),
        OperationParamSpec(
            name="spreadsheet_id",
            required=True,
            placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
            description="The spreadsheet ID from the Google Sheets URL.",
        ),
        OperationParamSpec(
            name="sheet_name",
            default="Sheet1",
            description="Name of the tab/sheet to watch.",
        ),
        OperationParamSpec(
            name="header_row",
            type="number",
            default=1,
            description="Row number containing column headers (1-indexed).",
        ),
    ),
    poll=poll_new_rows,
    poll_interval_seconds=300,
)


register_provider_trigger(GOOGLE_SHEETS_NEW_ROW_TRIGGER_SPEC)
