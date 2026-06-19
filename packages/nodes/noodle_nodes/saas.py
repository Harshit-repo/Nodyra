"""SaaS integration nodes.

Thin HTTP wrappers using ``requests``. Icons use the ``brand:<slug>``
convention so palette tiles show the real brand mark. Each node has a
single "Credentials" picker for secrets — no raw inline API key fields.
"""

from __future__ import annotations

from typing import Any

import requests

from noodle.sdk import node
from noodle_nodes._creds import cred_multi, cred_single
from noodle_nodes.http_security import safe_request

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
            "group": "Options",
            "description": "Issue description (markdown).",
            "multiline": True,
        },
        "priority": {
            "group": "Options",
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
            "group": "Options",
            "description": "Issue description (plain text).",
            "multiline": True,
        },
        "issue_type": {
            "group": "Options",
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
    # SEC-2: ``site`` is user-controlled, so route through the SSRF guard.
    response = safe_request(
        "POST",
        f"https://{site}/rest/api/3/issue",
        auth=(email, api_token),
        json=payload,
        timeout=_HTTP_TIMEOUT,
        context="jira create_issue",
    )
    return _expect_ok(response, "jira")

