"""Official integration nodes.

These nodes intentionally use plain HTTP APIs where possible. Database and S3
nodes use their standard Python drivers if the workflow environment provides
them, and raise an actionable error if the driver is missing.
"""

import json
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

from noodle.sdk import node
from noodle_nodes.llm import AI_CATEGORY, _normalize_anthropic, _normalize_openai


def _json_or_text(response: Any) -> Any:
    content = getattr(response, "content", b"")
    if content in (b"", ""):
        return {"status_code": response.status_code}
    try:
        return response.json()
    except ValueError:
        return response.text


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    data: Any = None,
) -> Any:
    import requests

    response = requests.request(
        method,
        url,
        headers=headers or None,
        params=params or None,
        json=json_body,
        data=data,
        timeout=30,
    )
    if response.status_code >= 400:
        detail = getattr(response, "text", "")[:500]
        raise RuntimeError(f"HTTP {response.status_code} from {url}: {detail}")
    return _json_or_text(response)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _with_json(headers: dict[str, str] | None = None) -> dict[str, str]:
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    return merged


def _text_from_input(input: Any, text: str = "") -> str:
    if text:
        return text
    if input is None:
        return ""
    if isinstance(input, dict | list):
        return json.dumps(input, default=str)
    return str(input)


def _dict_from_input(input: Any, value: dict | None = None) -> dict:
    if value is not None:
        return value
    return input if isinstance(input, dict) else {}


def _missing_dependency(feature: str, package: str) -> RuntimeError:
    return RuntimeError(
        f"{feature} requires the Python package '{package}'. "
        "Add it to this workflow environment and rebuild the environment."
    )


def _query_parameters(parameters: Any) -> Any:
    if parameters in (None, ""):
        return None
    if isinstance(parameters, dict | list | tuple):
        return parameters
    return [parameters]


def _spreadsheet_values(input: Any, values: list | None = None) -> list[list[Any]]:
    source = values if values is not None else input
    if source is None:
        return []
    if isinstance(source, list):
        if not source:
            return []
        if all(isinstance(row, list) for row in source):
            return source
        if all(isinstance(row, dict) for row in source):
            return [list(row.values()) for row in source]
        return [source]
    if isinstance(source, dict):
        return [list(source.values())]
    return [[source]]


def _flatten_stripe_data(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in data.items():
        form_key = f"{prefix}[{key}]" if prefix else key
        if isinstance(value, dict):
            flattened.update(_flatten_stripe_data(value, form_key))
        elif value is not None:
            flattened[form_key] = value
    return flattened


def _credential(
    credential_type: str,
    key: str,
    label: str,
    fields: list[str] | None = None,
    multi: bool = False,
) -> dict[str, Any]:
    return {
        "credential": {
            "type": credential_type,
            "key": key,
            "label": label,
            "fields": fields or [key],
            "multi": multi,
        }
    }


@node(
    name="Slack Send Message",
    id="slack_send_message",
    category="Integrations",
    icon="message",
    params={
        "bot_token": {
            **_credential("slack_bot", "bot_token", "Slack bot token"),
            "description": "Slack bot token.",
        },
        "channel": {"placeholder": "C0123456789 or #alerts"},
        "text": {"placeholder": "Message text. Blank uses the input payload."},
        "blocks": {"description": "Optional Slack Block Kit JSON array."},
        "thread_ts": {"placeholder": "Optional parent message timestamp."},
    },
)
def slack_send_message(
    input: Any = None,
    bot_token: str = "",
    channel: str = "",
    text: str = "",
    blocks: list | None = None,
    thread_ts: str = "",
) -> Any:
    """Send a message with Slack's chat.postMessage API."""
    payload: dict[str, Any] = {"channel": channel, "text": _text_from_input(input, text)}
    if blocks is not None:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return _request_json(
        "POST",
        "https://slack.com/api/chat.postMessage",
        headers=_with_json(_bearer(bot_token)),
        json_body=payload,
    )


@node(
    name="Discord Send Message",
    id="discord_send_message",
    category="Integrations",
    icon="message",
    params={
        "webhook_url": {
            **_credential("discord_webhook", "webhook_url", "Discord webhook URL"),
            "description": "Discord channel webhook URL.",
        },
        "content": {"placeholder": "Message text. Blank uses the input payload."},
        "username": {"placeholder": "Optional webhook display name."},
        "embeds": {"description": "Optional Discord embeds JSON array."},
    },
)
def discord_send_message(
    input: Any = None,
    webhook_url: str = "",
    content: str = "",
    username: str = "",
    embeds: list | None = None,
) -> Any:
    """Send a message to a Discord channel webhook."""
    payload: dict[str, Any] = {"content": _text_from_input(input, content)}
    if username:
        payload["username"] = username
    if embeds is not None:
        payload["embeds"] = embeds
    return _request_json("POST", webhook_url, headers=_with_json(), json_body=payload)


@node(
    name="SMTP Send Email",
    id="smtp_send_email",
    category="Integrations",
    icon="mail",
    params={
        "host": {"placeholder": "smtp.gmail.com"},
        "port": {"description": "SMTP port, usually 587 for STARTTLS."},
        "credentials": {
            **_credential(
                "smtp",
                "*",
                "SMTP username/password",
                ["username", "password"],
                multi=True,
            ),
            "description": "Stored SMTP username and password/app password.",
        },
        "use_tls": {"description": "Use STARTTLS before sending."},
        "from_email": {"placeholder": "sender@example.com"},
        "to_email": {"placeholder": "one@example.com, two@example.com"},
        "subject": {"placeholder": "Email subject"},
        "body_format": {
            "choices": ["text", "html"],
            "description": "Send the body as plain text or HTML.",
        },
        "body": {"multiline": True, "description": "Email body. Blank uses input."},
    },
)
def smtp_send_email(
    input: Any = None,
    host: str = "",
    port: int = 587,
    credentials: dict | None = None,
    use_tls: bool = True,
    from_email: str = "",
    to_email: str = "",
    subject: str = "",
    body_format: str = "text",
    body: str = "",
    **legacy: Any,
) -> dict:
    """Send a text or HTML email through any SMTP server, including Gmail SMTP."""
    import smtplib
    import ssl

    creds = credentials if isinstance(credentials, dict) else {}
    username = str(creds.get("username") or legacy.get("username") or "")
    password = str(
        creds.get("password")
        or creds.get("app_password")
        or legacy.get("password")
        or ""
    )
    recipients = [email.strip() for email in to_email.split(",") if email.strip()]
    if not host:
        raise ValueError("smtp_send_email: host is required")
    if not recipients:
        raise ValueError("smtp_send_email: to_email is required")

    body_value = _text_from_input(input, body)
    mode = str(body_format or "text").lower()
    if mode not in {"text", "html"}:
        raise ValueError("smtp_send_email: body_format must be 'text' or 'html'")

    message = EmailMessage()
    message["From"] = from_email or username
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    if mode == "html":
        message.set_content("This email contains HTML. View it in an HTML-capable client.")
        message.add_alternative(body_value, subtype="html")
    else:
        message.set_content(body_value)

    port_num = int(port)
    context = ssl.create_default_context()
    # Pass the host to the constructor (which connects) so ``smtp._host`` is
    # set. STARTTLS uses ``_host`` as the TLS ``server_hostname``, and a default
    # SSL context has ``check_hostname=True`` — so constructing without a host
    # and calling ``connect()`` separately leaves ``_host`` empty and makes
    # ``starttls()`` raise "check_hostname requires server_hostname", which the
    # context-manager exit then masks as ``SMTPResponseException(-1, ...)``.
    #
    # Port 465 is implicit TLS (encrypted from the first byte) so it uses
    # SMTP_SSL regardless of ``use_tls``; STARTTLS applies to plain ports (587).
    implicit_ssl = port_num == 465
    if implicit_ssl:
        smtp = smtplib.SMTP_SSL(host, port_num, timeout=30, context=context)
    else:
        smtp = smtplib.SMTP(host, port_num, timeout=30)
    with smtp:
        smtp.ehlo()
        if use_tls and not implicit_ssl:
            smtp.starttls(context=context)
            smtp.ehlo()
        if username or password:
            smtp.login(username, password)
        smtp.send_message(message)
    preview: dict[str, Any] = {
        "sent": True,
        "to": recipients,
        "from": message["From"],
        "subject": subject,
        "body_format": mode,
    }
    if mode == "html":
        preview["html_preview"] = body_value
    else:
        preview["text_preview"] = body_value
    return preview


@node(
    name="Google Sheets Read",
    id="google_sheets_read",
    category="Integrations",
    icon="sheet",
    params={
        "spreadsheet_id": {"placeholder": "Google Sheets spreadsheet ID"},
        "range_name": {"placeholder": "Sheet1!A1:D20"},
        "api_key": {
            **_credential(
                "google_sheets", "api_key", "Google Sheets credential", ["api_key", "access_token"]
            ),
            "description": "API key for public/readable sheets.",
        },
        "access_token": {
            **_credential(
                "google_sheets",
                "access_token",
                "Google Sheets credential",
                ["api_key", "access_token"],
            ),
            "description": "OAuth access token for private sheets.",
        },
    },
)
def google_sheets_read(
    input: Any = None,  # noqa: ARG001 - input ignored
    spreadsheet_id: str = "",
    range_name: str = "Sheet1!A1:Z100",
    api_key: str = "",
    access_token: str = "",
) -> Any:
    """Read values from a Google Sheet using the Sheets REST API."""
    encoded_range = quote(range_name, safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}"
    params = {"key": api_key} if api_key else None
    return _request_json("GET", url, headers=_bearer(access_token), params=params)


@node(
    name="Google Sheets Append",
    id="google_sheets_append",
    category="Integrations",
    icon="sheet",
    params={
        "spreadsheet_id": {"placeholder": "Google Sheets spreadsheet ID"},
        "range_name": {"placeholder": "Sheet1!A:D"},
        "values": {"description": "Rows to append. Blank derives rows from the input."},
        "value_input_option": {"choices": ["RAW", "USER_ENTERED"]},
        "access_token": {
            **_credential(
                "google_sheets",
                "access_token",
                "Google Sheets credential",
                ["api_key", "access_token"],
            ),
            "description": "OAuth access token with write access.",
        },
    },
)
def google_sheets_append(
    input: Any = None,
    spreadsheet_id: str = "",
    range_name: str = "Sheet1!A:Z",
    values: list | None = None,
    value_input_option: str = "USER_ENTERED",
    access_token: str = "",
) -> Any:
    """Append one or more rows to a Google Sheet."""
    encoded_range = quote(range_name, safe="!:'")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/"
        f"{encoded_range}:append"
    )
    return _request_json(
        "POST",
        url,
        headers=_with_json(_bearer(access_token)),
        params={"valueInputOption": value_input_option},
        json_body={"values": _spreadsheet_values(input, values)},
    )


@node(
    name="Notion Create Page",
    id="notion_create_page",
    category="Integrations",
    icon="page",
    params={
        "token": {
            **_credential("notion", "token", "Notion integration token"),
            "description": "Notion integration token.",
        },
        "database_id": {"description": "Create inside this database when set."},
        "parent_page_id": {"description": "Create under this page when database is blank."},
        "title": {"placeholder": "New page title"},
        "title_property": {"placeholder": "Name"},
        "properties": {"description": "Additional Notion page properties."},
        "content": {"multiline": True, "description": "Optional first paragraph."},
    },
)
def notion_create_page(
    input: Any = None,
    token: str = "",
    database_id: str = "",
    parent_page_id: str = "",
    title: str = "",
    title_property: str = "Name",
    properties: dict | None = None,
    content: str = "",
) -> Any:
    """Create a page in Notion."""
    props = dict(properties or {})
    page_title = title or _text_from_input(input)
    if database_id:
        parent = {"database_id": database_id}
        props.setdefault(title_property or "Name", {"title": [{"text": {"content": page_title}}]})
    else:
        parent = {"page_id": parent_page_id}
        props = {"title": [{"text": {"content": page_title}}], **props}

    payload: dict[str, Any] = {"parent": parent, "properties": props}
    paragraph = content or (
        json.dumps(input, default=str) if isinstance(input, dict | list) else ""
    )
    if paragraph:
        payload["children"] = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": paragraph}}]},
            }
        ]
    return _request_json(
        "POST",
        "https://api.notion.com/v1/pages",
        headers=_with_json(
            {
                **_bearer(token),
                "Notion-Version": "2022-06-28",
            }
        ),
        json_body=payload,
    )


@node(
    name="GitHub Get Repository",
    id="github_get_repo",
    category="Integrations",
    icon="github",
    params={
        "repo": {"placeholder": "owner/name"},
        "token": {
            **_credential("github", "token", "GitHub token"),
            "description": "Optional GitHub token.",
        },
    },
)
def github_get_repo(input: Any = None, repo: str = "", token: str = "") -> Any:  # noqa: ARG001
    """Fetch GitHub repository metadata."""
    return _request_json("GET", f"https://api.github.com/repos/{repo}", headers=_bearer(token))


@node(
    name="GitHub Create Issue",
    id="github_create_issue",
    category="Integrations",
    icon="github",
    params={
        "repo": {"placeholder": "owner/name"},
        "token": {
            **_credential("github", "token", "GitHub token"),
            "description": "GitHub token with issue write access.",
        },
        "title": {"placeholder": "Issue title"},
        "body": {"multiline": True, "description": "Issue body. Blank uses input."},
        "labels": {"description": "Optional list of labels."},
    },
)
def github_create_issue(
    input: Any = None,
    repo: str = "",
    token: str = "",
    title: str = "",
    body: str = "",
    labels: list | None = None,
) -> Any:
    """Create a GitHub issue."""
    payload: dict[str, Any] = {"title": title, "body": _text_from_input(input, body)}
    if labels:
        payload["labels"] = labels
    return _request_json(
        "POST",
        f"https://api.github.com/repos/{repo}/issues",
        headers=_with_json(_bearer(token)),
        json_body=payload,
    )


@node(
    name="Postgres Query",
    id="postgres_query",
    category="Integrations",
    icon="database",
    params={
        "connection_url": {
            **_credential("postgres", "connection_url", "Postgres connection URL"),
            "description": "Postgres connection URL.",
        },
        "sql": {"multiline": True, "placeholder": "select * from users limit 10"},
        "parameters": {"description": "Optional positional list or named dict parameters."},
    },
)
def postgres_query(
    input: Any = None,  # noqa: ARG001 - input ignored
    connection_url: str = "",
    sql: str = "",
    parameters: Any = None,
) -> Any:
    """Run a SQL statement against PostgreSQL and return rows for queries."""
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - depends on user env
        raise _missing_dependency("Postgres Query", "psycopg[binary]") from exc

    with psycopg.connect(connection_url, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, _query_parameters(parameters))
            if cursor.description:
                return cursor.fetchall()
            conn.commit()
            return {"rowcount": cursor.rowcount}


@node(
    name="MySQL Query",
    id="mysql_query",
    category="Integrations",
    icon="database",
    params={
        "host": {"placeholder": "localhost"},
        "port": {"description": "MySQL port."},
        "credentials": {
            **_credential(
                "mysql",
                "*",
                "MySQL username/password",
                ["username", "password"],
                multi=True,
            ),
            "description": "Stored MySQL username and password.",
        },
        "database": {"placeholder": "app"},
        "sql": {"multiline": True, "placeholder": "select * from users limit 10"},
        "parameters": {"description": "Optional positional list or named dict parameters."},
    },
)
def mysql_query(
    input: Any = None,  # noqa: ARG001 - input ignored
    host: str = "localhost",
    port: int = 3306,
    credentials: dict | None = None,
    database: str = "",
    sql: str = "",
    parameters: Any = None,
    **legacy: Any,
) -> Any:
    """Run a SQL statement against MySQL and return rows for queries."""
    try:
        import pymysql
        import pymysql.cursors
    except ImportError as exc:  # pragma: no cover - depends on user env
        raise _missing_dependency("MySQL Query", "PyMySQL") from exc

    creds = credentials if isinstance(credentials, dict) else {}
    username = str(creds.get("username") or legacy.get("username") or "")
    password = str(creds.get("password") or legacy.get("password") or "")
    connection = pymysql.connect(
        host=host,
        port=int(port),
        user=username,
        password=password,
        database=database,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=30,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, _query_parameters(parameters))
            if cursor.description:
                return cursor.fetchall()
            connection.commit()
            return {"rowcount": cursor.rowcount}
    finally:
        connection.close()


def _boto3_s3_client(
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    region_name: str = "",
    endpoint_url: str = "",
) -> Any:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - depends on user env
        raise _missing_dependency("S3", "boto3") from exc

    kwargs = {
        "aws_access_key_id": aws_access_key_id or None,
        "aws_secret_access_key": aws_secret_access_key or None,
        "region_name": region_name or None,
        "endpoint_url": endpoint_url or None,
    }
    return boto3.client("s3", **{k: v for k, v in kwargs.items() if v is not None})


@node(
    name="S3 Put Object",
    id="s3_put_object",
    category="Integrations",
    icon="storage",
    params={
        "bucket": {"placeholder": "my-bucket"},
        "key": {"placeholder": "path/file.json"},
        "body": {"multiline": True, "description": "Object body. Blank uses input."},
        "content_type": {"placeholder": "application/json"},
        "aws_access_key_id": {
            **_credential(
                "aws",
                "aws_access_key_id",
                "AWS access keys",
                ["aws_access_key_id", "aws_secret_access_key"],
            ),
            "description": "Optional; falls back to AWS env/instance auth.",
        },
        "aws_secret_access_key": {
            **_credential(
                "aws",
                "aws_secret_access_key",
                "AWS access keys",
                ["aws_access_key_id", "aws_secret_access_key"],
            ),
            "description": "Optional; falls back to AWS env/instance auth.",
        },
        "region_name": {"placeholder": "us-east-1"},
        "endpoint_url": {"description": "Optional S3-compatible endpoint, e.g. MinIO."},
    },
)
def s3_put_object(
    input: Any = None,
    bucket: str = "",
    key: str = "",
    body: str = "",
    content_type: str = "",
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    region_name: str = "",
    endpoint_url: str = "",
) -> dict:
    """Write an object to S3 or an S3-compatible object store."""
    source = body if body else input
    if isinstance(source, bytes | bytearray):
        payload = bytes(source)
    elif isinstance(source, dict | list):
        payload = json.dumps(source, default=str).encode("utf-8")
        content_type = content_type or "application/json"
    else:
        payload = str(source if source is not None else "").encode("utf-8")
        content_type = content_type or "text/plain; charset=utf-8"

    client = _boto3_s3_client(
        aws_access_key_id,
        aws_secret_access_key,
        region_name,
        endpoint_url,
    )
    result = client.put_object(Bucket=bucket, Key=key, Body=payload, ContentType=content_type)
    return {
        "bucket": bucket,
        "key": key,
        "etag": str(result.get("ETag", "")).strip('"'),
        "version_id": result.get("VersionId"),
    }


@node(
    name="S3 Get Object",
    id="s3_get_object",
    category="Integrations",
    icon="storage",
    params={
        "bucket": {"placeholder": "my-bucket"},
        "key": {"placeholder": "path/file.json"},
        "aws_access_key_id": {
            **_credential(
                "aws",
                "aws_access_key_id",
                "AWS access keys",
                ["aws_access_key_id", "aws_secret_access_key"],
            ),
            "description": "Optional; falls back to AWS env/instance auth.",
        },
        "aws_secret_access_key": {
            **_credential(
                "aws",
                "aws_secret_access_key",
                "AWS access keys",
                ["aws_access_key_id", "aws_secret_access_key"],
            ),
            "description": "Optional; falls back to AWS env/instance auth.",
        },
        "region_name": {"placeholder": "us-east-1"},
        "endpoint_url": {"description": "Optional S3-compatible endpoint, e.g. MinIO."},
    },
)
def s3_get_object(
    input: Any = None,  # noqa: ARG001 - input ignored
    bucket: str = "",
    key: str = "",
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    region_name: str = "",
    endpoint_url: str = "",
) -> dict:
    """Read an object from S3 or an S3-compatible object store."""
    client = _boto3_s3_client(
        aws_access_key_id,
        aws_secret_access_key,
        region_name,
        endpoint_url,
    )
    result = client.get_object(Bucket=bucket, Key=key)
    raw = result["Body"].read()
    content_type = result.get("ContentType", "")
    text = raw.decode("utf-8", "replace")
    body: Any = text
    if "json" in content_type:
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = text
    return {
        "body": body,
        "content_type": content_type,
        "content_length": result.get("ContentLength"),
        "etag": str(result.get("ETag", "")).strip('"'),
    }


@node(
    name="OpenAI Chat",
    id="openai_chat",
    category=AI_CATEGORY,
    icon="ai",
    params={
        "api_key": {
            **_credential("openai", "api_key", "OpenAI API key"),
            "description": "OpenAI API key.",
        },
        "model": {"placeholder": "gpt-4.1-mini"},
        "system": {"multiline": True},
        "prompt": {"multiline": True, "description": "User prompt. Blank uses input."},
        "temperature": {"description": "Sampling temperature."},
        "max_tokens": {"description": "Optional response token limit."},
        "include_raw": {"description": "Include the raw provider response."},
    },
)
def openai_chat(
    input: Any = None,
    api_key: str = "",
    model: str = "gpt-4.1-mini",
    system: str = "",
    prompt: str = "",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    include_raw: bool = False,
) -> Any:
    """Call OpenAI's chat completions API."""
    if not api_key:
        raise ValueError("openai_chat: api_key is required")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": _text_from_input(input, prompt)})
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
    }
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    raw = _request_json(
        "POST",
        "https://api.openai.com/v1/chat/completions",
        headers=_with_json(_bearer(api_key)),
        json_body=payload,
    )
    if not isinstance(raw, dict):
        raise RuntimeError("openai_chat: expected JSON object response")
    out = _normalize_openai(raw)
    out["provider"] = "openai"
    if include_raw:
        out["raw"] = raw
    return out


@node(
    name="Anthropic Message",
    id="anthropic_message",
    category=AI_CATEGORY,
    icon="ai",
    params={
        "api_key": {
            **_credential("anthropic", "api_key", "Anthropic API key"),
            "description": "Anthropic API key.",
        },
        "model": {"placeholder": "claude-3-5-haiku-latest"},
        "system": {"multiline": True},
        "prompt": {"multiline": True, "description": "User prompt. Blank uses input."},
        "max_tokens": {"description": "Maximum output tokens."},
        "temperature": {"description": "Sampling temperature."},
        "include_raw": {"description": "Include the raw provider response."},
    },
)
def anthropic_message(
    input: Any = None,
    api_key: str = "",
    model: str = "claude-3-5-haiku-latest",
    system: str = "",
    prompt: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.2,
    include_raw: bool = False,
) -> Any:
    """Call Anthropic's Messages API."""
    if not api_key:
        raise ValueError("anthropic_message: api_key is required")
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens),
        "messages": [{"role": "user", "content": _text_from_input(input, prompt)}],
        "temperature": float(temperature),
    }
    if system:
        payload["system"] = system
    raw = _request_json(
        "POST",
        "https://api.anthropic.com/v1/messages",
        headers=_with_json(
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
        ),
        json_body=payload,
    )
    if not isinstance(raw, dict):
        raise RuntimeError("anthropic_message: expected JSON object response")
    out = _normalize_anthropic(raw)
    out["provider"] = "anthropic"
    if include_raw:
        out["raw"] = raw
    return out


@node(
    name="Stripe Create Customer",
    id="stripe_create_customer",
    category="Integrations",
    icon="card",
    params={
        "api_key": {
            **_credential("stripe", "api_key", "Stripe API key"),
            "description": "Stripe API key.",
        },
        "email": {"placeholder": "customer@example.com"},
        "name": {"placeholder": "Customer name"},
        "description": {"placeholder": "Optional description"},
        "metadata": {"description": "Optional Stripe metadata object.", "key_value": True},
    },
)
def stripe_create_customer(
    input: Any = None,
    api_key: str = "",
    email: str = "",
    name: str = "",
    description: str = "",
    metadata: dict | None = None,
) -> Any:
    """Create a Stripe customer."""
    source = _dict_from_input(input)
    data = {
        "email": email or source.get("email"),
        "name": name or source.get("name"),
        "description": description,
        "metadata": metadata or {},
    }
    return _request_json(
        "POST",
        "https://api.stripe.com/v1/customers",
        headers=_bearer(api_key),
        data=_flatten_stripe_data(data),
    )


@node(
    name="Airtable List Records",
    id="airtable_list_records",
    category="Integrations",
    icon="table",
    params={
        "token": {
            **_credential("airtable", "token", "Airtable token"),
            "description": "Airtable personal access token.",
        },
        "base_id": {"placeholder": "app..."},
        "table_name": {"placeholder": "Tasks"},
        "view": {"placeholder": "Grid view"},
        "max_records": {"description": "Maximum records to fetch."},
        "filter_formula": {"placeholder": "{Status} = 'Open'"},
    },
)
def airtable_list_records(
    input: Any = None,  # noqa: ARG001 - input ignored
    token: str = "",
    base_id: str = "",
    table_name: str = "",
    view: str = "",
    max_records: int = 100,
    filter_formula: str = "",
) -> Any:
    """List records from an Airtable table."""
    params: dict[str, Any] = {"maxRecords": int(max_records)}
    if view:
        params["view"] = view
    if filter_formula:
        params["filterByFormula"] = filter_formula
    url = f"https://api.airtable.com/v0/{base_id}/{quote(table_name, safe='')}"
    return _request_json("GET", url, headers=_bearer(token), params=params)


@node(
    name="Airtable Create Record",
    id="airtable_create_record",
    category="Integrations",
    icon="table",
    params={
        "token": {
            **_credential("airtable", "token", "Airtable token"),
            "description": "Airtable personal access token.",
        },
        "base_id": {"placeholder": "app..."},
        "table_name": {"placeholder": "Tasks"},
        "fields": {"description": "Record fields. Blank uses input when it is an object."},
        "typecast": {"description": "Let Airtable coerce select/date fields."},
    },
)
def airtable_create_record(
    input: Any = None,
    token: str = "",
    base_id: str = "",
    table_name: str = "",
    fields: dict | None = None,
    typecast: bool = False,
) -> Any:
    """Create one Airtable record."""
    url = f"https://api.airtable.com/v0/{base_id}/{quote(table_name, safe='')}"
    return _request_json(
        "POST",
        url,
        headers=_with_json(_bearer(token)),
        json_body={"fields": _dict_from_input(input, fields), "typecast": typecast},
    )
