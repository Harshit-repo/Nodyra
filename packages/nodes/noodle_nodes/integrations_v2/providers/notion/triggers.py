"""Notion polling trigger — detects new pages in a database."""
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

NOTION_API_BASE = "https://api.notion.com"
_MAX_SEEN_IDS = 500


def _transport(credentials: Any) -> ProviderTransport:
    creds = credentials if isinstance(credentials, dict) else {}
    token = str(creds.get("token") or creds.get("api_key") or "")
    if not token:
        raise ValueError(
            "notion_new_database_page_trigger_v2: credentials are required"
        )
    return ProviderTransport(
        provider="notion",
        base_url=NOTION_API_BASE,
        default_headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
        },
    )


def poll_new_pages(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    params = ctx.params
    database_id = str(params.get("database_id") or "").strip()
    if not database_id:
        raise ValueError(
            "notion_new_database_page_trigger_v2: database_id is required"
        )

    cursor = ctx.cursor
    last_created_time: str | None = cursor.get("last_created_time") or None
    seen_ids: list[str] = list(cursor.get("seen_ids") or [])
    is_first_run = "last_created_time" not in cursor

    transport = _transport(params.get("credentials"))

    query_body: dict[str, Any] = {
        "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
        "page_size": 100,
    }
    if last_created_time:
        query_body["filter"] = {
            "timestamp": "created_time",
            "created_time": {"after": last_created_time},
        }

    response = transport.request(
        "POST",
        f"/v1/databases/{database_id}/query",
        operation="query_database_for_new_pages",
        json_body=query_body,
    )
    results: list[dict[str, Any]] = response.get("results") or []

    if not results:
        return ProviderTriggerPollResult(events=[], cursor=cursor)

    latest_time = last_created_time or ""
    events: list[dict[str, Any]] = []
    new_ids = list(seen_ids)

    for page in results:
        page_id = str(page.get("id") or "")
        created = str(page.get("created_time") or "")
        if page_id in seen_ids:
            continue
        if created > latest_time:
            latest_time = created
        events.append(
            {
                "provider": "notion",
                "database_id": database_id,
                "page_id": page_id,
                "created_time": created,
                "properties": page.get("properties"),
                "page": page,
            }
        )
        new_ids.append(page_id)

    new_ids = new_ids[-_MAX_SEEN_IDS:]

    if is_first_run:
        return ProviderTriggerPollResult(
            events=[],
            cursor={"last_created_time": latest_time or "", "seen_ids": new_ids},
        )

    return ProviderTriggerPollResult(
        events=events,
        cursor={"last_created_time": latest_time, "seen_ids": new_ids},
    )


NOTION_NEW_PAGE_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="notion_new_database_page_trigger_v2",
    name="Notion New Database Page",
    provider="notion",
    resource="database",
    event="new_page",
    description="Start a workflow when a new page is created in a Notion database.",
    icon="brand:notion",
    params=(
        OperationParamSpec(
            name="credentials",
            type="credential",
            required=True,
            credential=CredentialSpec(
                type="notion",
                key="*",
                label="Notion integration token",
                fields=["token"],
                multi=True,
                test_service="notion",
            ),
            description="Notion integration token with access to the database.",
        ),
        OperationParamSpec(
            name="database_id",
            required=True,
            placeholder="8a7c2f0e1d3b4a5c9e6f7d2b",
            description="The Notion database ID to watch for new pages.",
        ),
    ),
    poll=poll_new_pages,
    poll_interval_seconds=300,
)


register_provider_trigger(NOTION_NEW_PAGE_TRIGGER_SPEC)
