"""SaaS integration nodes.

Thin HTTP wrappers using ``requests``. Icons use the ``brand:<slug>``
convention so palette tiles show the real brand mark. Each node has a
single "Credentials" picker for secrets — no raw inline API key fields.
"""

from __future__ import annotations

import json as json_mod
from typing import Any

import requests

from noodle.sdk import node
from noodle_nodes._creds import cred_multi, cred_single

_HTTP_TIMEOUT = 30


def _expect_ok(response: requests.Response, service: str) -> dict:
    if response.status_code >= 400:
        body = response.text[:500]
        raise RuntimeError(
            f"{service}: HTTP {response.status_code} — {body}"
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {"text": response.text}
    return {"status_code": response.status_code, "body": payload}


def _coerce_dict(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json_mod.loads(value)
        except json_mod.JSONDecodeError as exc:
            raise ValueError(f"expected JSON object: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("expected JSON object, got something else")
        return parsed
    raise ValueError("expected dict or JSON object string")


# ============================================================================
# Linear
# ============================================================================


@node(
    name="Linear Create Issue",
    id="linear_create_issue",
    category="Integrations",
    icon="brand:linear",
    params={
        "credentials": {
            **cred_single("linear", "api_key", "Linear API key"),
            "description": "Linear personal API key.",
        },
        "team_id": {
            "placeholder": "team UUID",
            "description": "Linear team UUID — the issue's home team.",
        },
        "title": {"description": "Issue title."},
        "description": {
            "description": "Issue description (markdown).",
            "multiline": True,
        },
        "priority": {
            "choices": ["0", "1", "2", "3", "4"],
            "description": "0=none, 1=urgent, 2=high, 3=medium, 4=low.",
        },
    },
)
def linear_create_issue(
    input: Any = None,
    credentials: str = "",
    team_id: str = "",
    title: str = "",
    description: str = "",
    priority: str = "0",
) -> dict:
    """Create an issue in Linear via the GraphQL API."""
    _ = input
    if not credentials or not team_id or not title:
        raise ValueError(
            "linear_create_issue: credentials, team_id, and title are required"
        )
    mutation = """
    mutation($input: IssueCreateInput!) {
        issueCreate(input: $input) {
            success
            issue { id identifier url title }
        }
    }
    """
    variables = {
        "input": {
            "teamId": team_id,
            "title": title,
            "description": description or "",
            "priority": int(priority or "0"),
        }
    }
    response = requests.post(
        "https://api.linear.app/graphql",
        headers={
            "Authorization": credentials,
            "Content-Type": "application/json",
        },
        json={"query": mutation, "variables": variables},
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "linear")


# ============================================================================
# Jira
# ============================================================================


@node(
    name="Jira Create Issue",
    id="jira_create_issue",
    category="Integrations",
    icon="brand:jira",
    params={
        "credentials": {
            **cred_multi("jira", "Jira credentials", ["email", "api_token"]),
            "description": "Atlassian email + API token.",
        },
        "site": {
            "placeholder": "your-domain.atlassian.net",
            "description": "Jira Cloud site host (no scheme).",
        },
        "project_key": {
            "placeholder": "ENG",
            "description": "Project key the issue lives in.",
        },
        "summary": {"description": "Issue summary."},
        "description": {
            "description": "Issue description (plain text).",
            "multiline": True,
        },
        "issue_type": {
            "placeholder": "Task",
            "description": "Issue type name (Task / Bug / Story).",
        },
    },
)
def jira_create_issue(
    input: Any = None,
    credentials: dict | None = None,
    site: str = "",
    project_key: str = "",
    summary: str = "",
    description: str = "",
    issue_type: str = "Task",
) -> dict:
    """Create an issue in Jira Cloud."""
    _ = input
    creds = credentials or {}
    email = str(creds.get("email") or "")
    api_token = str(creds.get("api_token") or "")
    if not all((email, api_token, site, project_key, summary)):
        raise ValueError(
            "jira_create_issue: credentials (email + api_token), site, "
            "project_key, and summary are required"
        )
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type or "Task"},
        }
    }
    if description:
        payload["fields"]["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }
            ],
        }
    response = requests.post(
        f"https://{site}/rest/api/3/issue",
        auth=(email, api_token),
        json=payload,
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "jira")


# ============================================================================
# Trello
# ============================================================================


@node(
    name="Trello Create Card",
    id="trello_create_card",
    category="Integrations",
    icon="brand:trello",
    params={
        "credentials": {
            **cred_multi("trello", "Trello credentials", ["api_key", "token"]),
            "description": "Trello developer API key + OAuth token.",
        },
        "list_id": {
            "placeholder": "list id",
            "description": "Target list id (find it in the list URL).",
        },
        "name": {"description": "Card title."},
        "desc": {
            "description": "Card description.",
            "multiline": True,
        },
    },
)
def trello_create_card(
    input: Any = None,
    credentials: dict | None = None,
    list_id: str = "",
    name: str = "",
    desc: str = "",
) -> dict:
    """Create a card in a Trello list."""
    _ = input
    creds = credentials or {}
    api_key = str(creds.get("api_key") or "")
    token = str(creds.get("token") or "")
    if not all((api_key, token, list_id, name)):
        raise ValueError(
            "trello_create_card: credentials (api_key + token), list_id, "
            "and name are required"
        )
    response = requests.post(
        "https://api.trello.com/1/cards",
        params={
            "key": api_key,
            "token": token,
            "idList": list_id,
            "name": name,
            "desc": desc,
        },
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "trello")


# ============================================================================
# HubSpot
# ============================================================================


@node(
    name="HubSpot Create Contact",
    id="hubspot_create_contact",
    category="Integrations",
    icon="brand:hubspot",
    params={
        "credentials": {
            **cred_single("hubspot", "access_token", "HubSpot access token"),
            "description": "HubSpot private app access token.",
        },
        "email": {
            "placeholder": "user@example.com",
            "description": "Contact email (the natural identifier).",
        },
        "firstname": {"description": "First name."},
        "lastname": {"description": "Last name."},
        "properties_json": {
            "placeholder": '{"phone": "+1-555-0100"}',
            "description": "Additional contact properties as JSON.",
            "multiline": True,
        },
    },
)
def hubspot_create_contact(
    input: Any = None,
    credentials: str = "",
    email: str = "",
    firstname: str = "",
    lastname: str = "",
    properties_json: str = "",
) -> dict:
    """Create a contact in HubSpot via the CRM v3 API."""
    _ = input
    if not credentials or not email:
        raise ValueError(
            "hubspot_create_contact: credentials and email are required"
        )
    properties = {"email": email}
    if firstname:
        properties["firstname"] = firstname
    if lastname:
        properties["lastname"] = lastname
    properties.update(_coerce_dict(properties_json))
    response = requests.post(
        "https://api.hubapi.com/crm/v3/objects/contacts",
        headers={
            "Authorization": f"Bearer {credentials}",
            "Content-Type": "application/json",
        },
        json={"properties": properties},
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "hubspot")


# ============================================================================
# Asana
# ============================================================================


@node(
    name="Asana Create Task",
    id="asana_create_task",
    category="Integrations",
    icon="brand:asana",
    params={
        "credentials": {
            **cred_single("asana", "access_token", "Asana personal access token"),
            "description": "Asana personal access token.",
        },
        "workspace_id": {
            "placeholder": "workspace gid",
            "description": "Workspace gid (required if project is empty).",
        },
        "project_id": {
            "placeholder": "project gid",
            "description": "Project gid (the task's home project).",
        },
        "name": {"description": "Task name."},
        "notes": {
            "description": "Task description / notes.",
            "multiline": True,
        },
    },
)
def asana_create_task(
    input: Any = None,
    credentials: str = "",
    workspace_id: str = "",
    project_id: str = "",
    name: str = "",
    notes: str = "",
) -> dict:
    """Create a task in Asana."""
    _ = input
    if not credentials or not name:
        raise ValueError("asana_create_task: credentials and name are required")
    if not workspace_id and not project_id:
        raise ValueError(
            "asana_create_task: either workspace_id or project_id is required"
        )
    data: dict[str, Any] = {"name": name, "notes": notes}
    if workspace_id:
        data["workspace"] = workspace_id
    if project_id:
        data["projects"] = [project_id]
    response = requests.post(
        "https://app.asana.com/api/1.0/tasks",
        headers={
            "Authorization": f"Bearer {credentials}",
            "Content-Type": "application/json",
        },
        json={"data": data},
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "asana")


# ============================================================================
# Calendly
# ============================================================================


@node(
    name="Calendly Get Event",
    id="calendly_get_event",
    category="Integrations",
    icon="brand:calendly",
    params={
        "credentials": {
            **cred_single("calendly", "access_token", "Calendly token"),
            "description": "Calendly personal access token.",
        },
        "event_uri": {
            "placeholder": "https://api.calendly.com/scheduled_events/UUID",
            "description": "Full scheduled event URI from a webhook payload.",
        },
    },
)
def calendly_get_event(
    input: Any = None,
    credentials: str = "",
    event_uri: str = "",
) -> dict:
    """Fetch details of a Calendly scheduled event."""
    _ = input
    if not credentials or not event_uri:
        raise ValueError(
            "calendly_get_event: credentials and event_uri are required"
        )
    response = requests.get(
        event_uri,
        headers={"Authorization": f"Bearer {credentials}"},
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "calendly")


# ============================================================================
# Zoom
# ============================================================================


@node(
    name="Zoom Create Meeting",
    id="zoom_create_meeting",
    category="Integrations",
    icon="brand:zoom",
    params={
        "credentials": {
            **cred_single("zoom", "access_token", "Zoom OAuth access token"),
            "description": "OAuth access token (server-to-server app credentials).",
        },
        "user_id": {
            "placeholder": "me",
            "description": "Zoom user id or 'me' for the token's owner.",
        },
        "topic": {"description": "Meeting topic / title."},
        "start_time": {
            "placeholder": "2026-06-01T14:00:00Z",
            "description": "Start time in ISO 8601 (UTC).",
        },
        "duration": {
            "description": "Length in minutes.",
        },
    },
)
def zoom_create_meeting(
    input: Any = None,
    credentials: str = "",
    user_id: str = "me",
    topic: str = "",
    start_time: str = "",
    duration: int = 30,
) -> dict:
    """Schedule a Zoom meeting (type 2 = scheduled)."""
    _ = input
    if not credentials or not topic:
        raise ValueError("zoom_create_meeting: credentials and topic are required")
    payload: dict[str, Any] = {
        "topic": topic,
        "type": 2,
        "duration": max(1, int(duration or 30)),
    }
    if start_time:
        payload["start_time"] = start_time
    response = requests.post(
        f"https://api.zoom.us/v2/users/{user_id or 'me'}/meetings",
        headers={
            "Authorization": f"Bearer {credentials}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "zoom")


# ============================================================================
# Mailchimp
# ============================================================================


@node(
    name="Mailchimp Add Subscriber",
    id="mailchimp_add_subscriber",
    category="Integrations",
    icon="brand:mailchimp",
    params={
        "credentials": {
            **cred_single("mailchimp", "api_key", "Mailchimp API key"),
            "description": (
                "API key (must include the dc suffix, e.g. xxx-us21)."
            ),
        },
        "list_id": {
            "placeholder": "abc123",
            "description": "Audience (list) id.",
        },
        "email": {"description": "Subscriber email."},
        "status": {
            "choices": ["subscribed", "pending", "unsubscribed", "cleaned"],
            "description": "Subscription status to set.",
        },
        "merge_fields_json": {
            "placeholder": '{"FNAME": "Alice", "LNAME": "Lee"}',
            "description": "Optional merge fields (FNAME/LNAME/etc) as JSON.",
            "multiline": True,
        },
    },
)
def mailchimp_add_subscriber(
    input: Any = None,
    credentials: str = "",
    list_id: str = "",
    email: str = "",
    status: str = "subscribed",
    merge_fields_json: str = "",
) -> dict:
    """Add or update a subscriber on a Mailchimp audience."""
    _ = input
    api_key = credentials
    if not api_key or not list_id or not email:
        raise ValueError(
            "mailchimp_add_subscriber: credentials, list_id, and email are required"
        )
    if "-" not in api_key:
        raise ValueError(
            "mailchimp_add_subscriber: api_key must include the dc suffix (e.g. xxx-us21)"
        )
    dc = api_key.rsplit("-", 1)[1]
    payload: dict[str, Any] = {
        "email_address": email,
        "status": status or "subscribed",
    }
    merge = _coerce_dict(merge_fields_json)
    if merge:
        payload["merge_fields"] = merge
    response = requests.post(
        f"https://{dc}.api.mailchimp.com/3.0/lists/{list_id}/members",
        auth=("anystring", api_key),
        json=payload,
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "mailchimp")


# ============================================================================
# Shopify
# ============================================================================


@node(
    name="Shopify List Orders",
    id="shopify_list_orders",
    category="Integrations",
    icon="brand:shopify",
    params={
        "credentials": {
            **cred_multi(
                "shopify",
                "Shopify store credentials",
                ["shop_domain", "access_token"],
            ),
            "description": "Store *.myshopify.com domain + Admin API access token.",
        },
        "status": {
            "choices": ["any", "open", "closed", "cancelled"],
            "description": "Order status filter.",
        },
        "limit": {
            "description": "Max orders to return (1-250).",
        },
    },
)
def shopify_list_orders(
    input: Any = None,
    credentials: dict | None = None,
    status: str = "any",
    limit: int = 50,
) -> dict:
    """List orders from a Shopify store via the Admin REST API."""
    _ = input
    creds = credentials or {}
    shop_domain = str(creds.get("shop_domain") or "")
    access_token = str(creds.get("access_token") or "")
    if not shop_domain or not access_token:
        raise ValueError(
            "shopify_list_orders: credentials (shop_domain + access_token) are required"
        )
    response = requests.get(
        f"https://{shop_domain}/admin/api/2024-04/orders.json",
        headers={"X-Shopify-Access-Token": access_token},
        params={"status": status or "any", "limit": max(1, min(250, int(limit or 50)))},
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "shopify")
