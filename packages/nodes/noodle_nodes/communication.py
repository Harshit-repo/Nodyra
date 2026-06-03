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

from noodle.sdk import node
from noodle_nodes._creds import cred_multi, cred_single

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
# Telegram
# ============================================================================


@node(
    name="Telegram Send Message",
    id="telegram_send_message",
    category="Integrations",
    icon="brand:telegram",
    params={
        "credentials": {
            **cred_single("telegram_bot", "bot_token", "Telegram Bot token"),
            "description": "Bot API token from @BotFather.",
        },
        "chat_id": {
            "placeholder": "@channel or numeric chat id",
            "description": "Target chat — channel @handle, group id, or user id.",
        },
        "text": {
            "description": "Message body. Falls back to the wired input as text if blank.",
            "multiline": True,
        },
        "parse_mode": {
            "group": "Options",
            "choices": ["", "Markdown", "MarkdownV2", "HTML"],
            "description": "Optional rich-text format. Leave blank for plain text.",
        },
        "disable_notification": {
            "group": "Options",
            "description": "Send silently — no sound or vibration for recipients.",
        },
    },
)
def telegram_send_message(
    input: Any = None,
    credentials: str = "",
    chat_id: str = "",
    text: str = "",
    parse_mode: str = "",
    disable_notification: bool = False,
) -> dict:
    """Post a message to a Telegram chat via the Bot API."""
    bot_token = credentials
    if not bot_token or not chat_id:
        raise ValueError(
            "telegram_send_message: credentials and chat_id are required"
        )
    body = text or (str(input) if input is not None else "")
    payload: dict[str, Any] = {"chat_id": chat_id, "text": body}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if disable_notification:
        payload["disable_notification"] = True
    response = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json=payload,
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "telegram")


# ============================================================================
# Microsoft Teams (incoming webhook)
# ============================================================================


@node(
    name="Teams Send Webhook",
    id="teams_send_webhook",
    category="Integrations",
    icon="brand:microsoftteams",
    params={
        "credentials": {
            **cred_single("teams_webhook", "webhook_url", "Teams Incoming Webhook URL"),
            "description": (
                "Incoming Webhook URL from the Teams channel connector."
            ),
        },
        "title": {
            "group": "Options",
            "placeholder": "Build succeeded",
            "description": "Optional card title.",
        },
        "text": {
            "description": (
                "Card body (supports limited markdown). Falls back to the "
                "wired input as text if blank."
            ),
            "multiline": True,
        },
        "theme_color": {
            "group": "Options",
            "placeholder": "0078d4",
            "description": "Optional hex colour (no #) for the card accent.",
        },
    },
)
def teams_send_webhook(
    input: Any = None,
    credentials: str = "",
    title: str = "",
    text: str = "",
    theme_color: str = "",
) -> dict:
    """Post a MessageCard to a Microsoft Teams channel via Incoming Webhook."""
    webhook_url = credentials
    if not webhook_url:
        raise ValueError("teams_send_webhook: credentials are required")
    body = text or (str(input) if input is not None else "")
    card: dict[str, Any] = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "text": body,
    }
    if title:
        card["title"] = title
    if theme_color:
        card["themeColor"] = theme_color.lstrip("#")
    response = requests.post(
        webhook_url, json=card, timeout=_HTTP_TIMEOUT
    )
    return _expect_ok(response, "teams")


# ============================================================================
# SendGrid
# ============================================================================


@node(
    name="SendGrid Send Email",
    id="sendgrid_send_email",
    category="Integrations",
    icon="brand:sendgrid",
    params={
        "credentials": {
            **cred_single("sendgrid", "api_key", "SendGrid API key"),
            "description": "SendGrid API key with mail.send permission.",
        },
        "from_email": {
            "placeholder": "alerts@example.com",
            "description": "Verified sender address.",
        },
        "to_email": {
            "placeholder": "user@example.com",
            "description": "Recipient. Use comma-separated for multiple.",
        },
        "subject": {
            "placeholder": "Subject line",
            "description": "Email subject.",
        },
        "body": {
            "description": "Email body (HTML or plain text per content_type).",
            "multiline": True,
        },
        "content_type": {
            "group": "Options",
            "choices": ["text/plain", "text/html"],
            "description": "MIME type of the body.",
        },
    },
)
def sendgrid_send_email(
    input: Any = None,
    credentials: str = "",
    from_email: str = "",
    to_email: str = "",
    subject: str = "",
    body: str = "",
    content_type: str = "text/plain",
) -> dict:
    """Send an email via the SendGrid v3 API."""
    api_key = credentials
    if not api_key or not from_email or not to_email:
        raise ValueError(
            "sendgrid_send_email: credentials, from_email, and to_email are required"
        )
    text = body or (str(input) if input is not None else "")
    recipients = [
        {"email": addr.strip()}
        for addr in to_email.split(",")
        if addr.strip()
    ]
    payload = {
        "personalizations": [{"to": recipients}],
        "from": {"email": from_email},
        "subject": subject or "(no subject)",
        "content": [{"type": content_type or "text/plain", "value": text}],
    }
    response = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=_HTTP_TIMEOUT,
    )
    # SendGrid returns 202 with empty body on success.
    if response.status_code == 202:
        return {"status_code": 202, "body": {}}
    return _expect_ok(response, "sendgrid")


# ============================================================================
# Twilio SMS
# ============================================================================


@node(
    name="Twilio Send SMS",
    id="twilio_send_sms",
    category="Integrations",
    icon="brand:twilio",
    params={
        "credentials": {
            **cred_multi(
                "twilio",
                "Twilio credentials",
                ["account_sid", "auth_token"],
            ),
            "description": "Twilio Account SID + Auth Token.",
        },
        "from_number": {
            "placeholder": "+15555550100",
            "description": "Twilio sending number (E.164).",
        },
        "to_number": {
            "placeholder": "+15555550111",
            "description": "Recipient number (E.164).",
        },
        "body": {
            "description": "SMS body. Falls back to the wired input as text if blank.",
            "multiline": True,
        },
    },
)
def twilio_send_sms(
    input: Any = None,
    credentials: dict | None = None,
    from_number: str = "",
    to_number: str = "",
    body: str = "",
) -> dict:
    """Send an SMS via Twilio's REST API."""
    creds = credentials or {}
    account_sid = str(creds.get("account_sid") or "")
    auth_token = str(creds.get("auth_token") or "")
    if not all((account_sid, auth_token, from_number, to_number)):
        raise ValueError(
            "twilio_send_sms: credentials (account_sid + auth_token), "
            "from_number, and to_number are required"
        )
    text = body or (str(input) if input is not None else "")
    response = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        auth=(account_sid, auth_token),
        data={"From": from_number, "To": to_number, "Body": text},
        timeout=_HTTP_TIMEOUT,
    )
    return _expect_ok(response, "twilio")


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
