"""IMAP email polling trigger — fires a run for each new email."""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_provider_trigger
from nodyra_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)

_MAX_SEEN_IDS = 500


def _creds_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _connect(creds: dict[str, str]) -> imaplib.IMAP4:
    host = str(creds.get("host") or "").strip()
    if not host:
        raise ValueError("imap_email_trigger: IMAP host is required")
    username = str(creds.get("username") or "").strip()
    password = str(creds.get("password") or "")
    if not username:
        raise ValueError("imap_email_trigger: IMAP username is required")

    raw_port = str(creds.get("port") or "").strip()
    port = int(raw_port) if raw_port.isdigit() else None
    tls_flag = str(creds.get("tls", "")).lower()

    use_ssl = tls_flag not in ("false", "0", "no", "starttls")
    if port is None:
        port = 993 if use_ssl else 143

    if use_ssl:
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port)
    else:
        conn = imaplib.IMAP4(host, port)
        if tls_flag == "starttls":
            conn.starttls()

    conn.login(username, password)
    return conn


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded_parts = []
    for fragment, charset in parts:
        if isinstance(fragment, bytes):
            try:
                decoded_parts.append(fragment.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded_parts.append(fragment.decode("utf-8", errors="replace"))
        else:
            decoded_parts.append(str(fragment))
    return "".join(decoded_parts)


def _get_header(msg: email.message.Message, name: str) -> str:
    return _decode_header_value(msg.get(name, "")).strip()


def _extract_message_id(fetch_data: list[Any]) -> str:
    """Pull Message-ID from a BODY.PEEK[HEADER.FIELDS] fetch response."""
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
            try:
                msg = email.message_from_bytes(item[1])
                mid = msg.get("Message-ID", "").strip()
                if mid:
                    return mid
            except Exception:  # noqa: BLE001
                pass
    return ""


def _parse_email(
    msg: email.message.Message,
    include_attachments: bool,
    attachment_max_size: int,
) -> dict[str, Any]:
    subject = _get_header(msg, "Subject")
    from_ = _get_header(msg, "From")
    to_raw = _get_header(msg, "To")
    cc_raw = _get_header(msg, "Cc")
    date_raw = _get_header(msg, "Date")
    message_id = _get_header(msg, "Message-ID")

    to_list = [a.strip() for a in to_raw.split(",") if a.strip()]
    cc_list = [a.strip() for a in cc_raw.split(",") if a.strip()] if cc_raw else []

    body_text = ""
    body_html = ""
    attachments: list[dict[str, Any]] = []

    for part in msg.walk():
        ctype = part.get_content_type()
        disposition = str(part.get("Content-Disposition") or "").lower()

        if "attachment" in disposition or "inline" in disposition and part.get_filename():
            if include_attachments:
                filename = _decode_header_value(part.get_filename() or "attachment")
                size = len(part.get_payload(decode=True) or b"")
                if size <= attachment_max_size:
                    attachments.append(
                        {
                            "filename": filename,
                            "content_type": ctype,
                            "size": size,
                        }
                    )
            continue

        if ctype == "text/plain" and not body_text:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                body_text = payload.decode(charset, errors="replace")
        elif ctype == "text/html" and not body_html:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                body_html = payload.decode(charset, errors="replace")

    return {
        "message_id": message_id,
        "from": from_,
        "to": to_list,
        "cc": cc_list,
        "subject": subject,
        "date": date_raw,
        "body_text": body_text,
        "body_html": body_html,
        "attachments": attachments,
    }


def poll_imap(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    params = ctx.params
    creds = _creds_dict(params.get("credentials"))
    mailbox = str(params.get("mailbox") or "INBOX")
    search_criteria = str(params.get("search_criteria") or "UNSEEN")
    mark_as_read = str(params.get("mark_as_read", "true")).lower() not in (
        "false", "0", "no"
    )
    include_attachments = str(params.get("include_attachments", "false")).lower() in (
        "true", "1", "yes"
    )
    try:
        attachment_max_size = int(params.get("attachment_max_size") or 10_485_760)
    except (ValueError, TypeError):
        attachment_max_size = 10_485_760
    try:
        max_emails = int(params.get("max_emails_per_poll") or 10)
    except (ValueError, TypeError):
        max_emails = 10

    cursor = ctx.cursor
    seen_ids: list[str] | None = cursor.get("seen_message_ids")

    conn = _connect(creds)
    try:
        readonly = not mark_as_read
        conn.select(f'"{mailbox}"', readonly=readonly)

        typ, data = conn.search(None, search_criteria)
        if typ != "OK" or not data or not data[0]:
            return ProviderTriggerPollResult(
                events=[],
                cursor={"seen_message_ids": seen_ids or []},
            )

        uid_list = data[0].split()

        # First run: seed the seen set without firing events
        if seen_ids is None:
            seed_ids: list[str] = []
            for uid in uid_list[-_MAX_SEEN_IDS:]:
                typ2, hdr = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                if typ2 == "OK":
                    mid = _extract_message_id(hdr)
                    if mid:
                        seed_ids.append(mid)
            return ProviderTriggerPollResult(
                events=[],
                cursor={"seen_message_ids": seed_ids},
            )

        seen_set = set(seen_ids)
        events: list[dict[str, Any]] = []
        new_ids = list(seen_ids)

        for uid in uid_list[:max_emails]:
            typ2, msg_data = conn.fetch(uid, "(RFC822)")
            if typ2 != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            raw_bytes = msg_data[0][1]
            if not isinstance(raw_bytes, bytes):
                continue

            msg = email.message_from_bytes(raw_bytes)
            message_id = _get_header(msg, "Message-ID") or uid.decode()

            if message_id in seen_set:
                continue

            event = _parse_email(msg, include_attachments, attachment_max_size)
            events.append(event)
            seen_set.add(message_id)
            new_ids.append(message_id)

            if mark_as_read:
                conn.store(uid, "+FLAGS", "\\Seen")

        return ProviderTriggerPollResult(
            events=events,
            cursor={"seen_message_ids": new_ids[-_MAX_SEEN_IDS:]},
        )
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


_CREDENTIALS_PARAM = OperationParamSpec(
    name="credentials",
    type="credential",
    required=True,
    credential=CredentialSpec(
        type="imap",
        key="*",
        label="IMAP credentials",
        fields=["host", "port", "username", "password", "tls"],
        multi=True,
        test_service="imap",
    ),
    description="IMAP server credentials (host, port, username, password, TLS mode).",
)

IMAP_EMAIL_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="imap_email_trigger",
    name="IMAP Email",
    provider="imap",
    resource="inbox",
    event="new_email",
    description="Start a workflow when a new email arrives in an IMAP mailbox.",
    icon="mail",
    params=(
        _CREDENTIALS_PARAM,
        OperationParamSpec(
            name="mailbox",
            default="INBOX",
            description="IMAP mailbox (folder) to monitor.",
        ),
        OperationParamSpec(
            name="search_criteria",
            default="UNSEEN",
            description="IMAP SEARCH criteria. Default 'UNSEEN' fetches unread messages.",
            advanced=True,
        ),
        OperationParamSpec(
            name="mark_as_read",
            type="boolean",
            default=True,
            description="Mark fetched emails as read (\\Seen flag).",
        ),
        OperationParamSpec(
            name="include_attachments",
            type="boolean",
            default=False,
            description="Download and include attachment contents in the payload.",
        ),
        OperationParamSpec(
            name="attachment_max_size",
            type="number",
            default=10_485_760,
            group="Advanced",
            description="Skip attachments larger than this size in bytes (default 10 MB).",
            advanced=True,
        ),
        OperationParamSpec(
            name="max_emails_per_poll",
            type="number",
            default=10,
            group="Advanced",
            description="Maximum emails to process per poll cycle.",
            advanced=True,
        ),
    ),
    poll=poll_imap,
    poll_interval_seconds=60,
)

register_provider_trigger(IMAP_EMAIL_TRIGGER_SPEC)
