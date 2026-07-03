"""Communication nodes — send messages via popular channels.

Each integration is a thin HTTP wrapper using ``requests``. Icons use the
``brand:<slug>`` convention which the frontend resolves to a SimpleIcons CDN
URL, so palette tiles show the real brand mark instead of a generic glyph.

Credential metadata replaces inline ``api_key`` / ``token`` / ``username`` /
``password`` fields with a single "Credentials" picker per service. Picking
a credential decrypts to the right field(s) at run time.
"""

from __future__ import annotations

from typing import Any

import requests

from nodyra.sdk import node
from nodyra_nodes._creds import cred_multi

# Shared timeout — HTTP nodes should never wedge a worker forever.
_HTTP_TIMEOUT = 30


def _expect_ok(response: requests.Response, service: str) -> dict:
    """Return a small JSON envelope; raise with body snippet on HTTP error."""
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
# Pushover
# ============================================================================


@node(
    name="Pushover Notify",
    id="pushover_notify",
    category="Integrations",
    icon="brand:pushover",
    params={
        "credentials": {
            **cred_multi(
                "pushover",
                "Pushover credentials",
                ["app_token", "user_key"],
            ),
            "description": "Pushover app token + user/group key.",
        },
        "title": {
            "group": "Options",
            "placeholder": "Optional title",
            "description": "Notification title (optional).",
        },
        "message": {
            "description": (
                "Notification body. Falls back to the wired input as text "
                "if blank."
            ),
            "multiline": True,
        },
        "priority": {
            "group": "Options",
            "choices": ["-2", "-1", "0", "1", "2"],
            "description": (
                "-2 silent, -1 quiet, 0 normal (default), 1 high, "
                "2 emergency (requires acknowledgement)."
            ),
        },
    },
)
def pushover_notify(
    input: Any = None,
    credentials: dict | None = None,
    title: str = "",
    message: str = "",
    priority: str = "0",
) -> dict:
    """Send a Pushover notification."""
    creds = credentials or {}
    app_token = str(creds.get("app_token") or "")
    user_key = str(creds.get("user_key") or "")
    if not app_token or not user_key:
        raise ValueError(
            "pushover_notify: credentials (app_token + user_key) are required"
        )
    text = message or (str(input) if input is not None else "")
    payload: dict[str, Any] = {
        "token": app_token,
        "user": user_key,
        "message": text,
        "priority": int(priority or "0"),
    }
    if title:
        payload["title"] = title
    response = requests.post(
        "https://api.pushover.net/1/messages.json",
        data=payload,
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "pushover")
