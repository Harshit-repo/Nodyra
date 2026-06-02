"""Safe credential connection checks for official integrations."""

from __future__ import annotations

import base64
import smtplib
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.schemas import CredentialTestResponse
from app.services.redaction import redact_value

TestFn = Callable[[dict[str, str], dict[str, Any]], Awaitable[dict[str, Any]]]


def _value(data: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return ""


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _missing(*keys: str) -> dict[str, Any]:
    return {
        "ok": False,
        "message": f"Missing required context: {', '.join(keys)}",
        "details": {"missing_context": list(keys)},
    }


async def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    auth: tuple[str, str] | None = None,
) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            params=params,
            auth=auth,
        )
    details: dict[str, Any] = {"status_code": response.status_code}
    try:
        payload = response.json()
        if isinstance(payload, dict):
            details.update(
                {
                    key: value
                    for key, value in payload.items()
                    if key
                    in {
                        "ok",
                        "team",
                        "url",
                        "id",
                        "name",
                        "email",
                        "object",
                        "type",
                        "message",
                    }
                }
            )
    except ValueError:
        payload = response.text[:300]
        details["body"] = payload
    ok = 200 <= response.status_code < 300
    message = "Connected" if ok else f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        if payload.get("ok") is False:
            ok = False
            message = str(payload.get("error") or payload.get("message") or message)
        elif payload.get("message") and not ok:
            message = str(payload["message"])
    return {"ok": ok, "message": message, "details": details}


async def _test_slack(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    token = _value(data, "bot_token", "token", "api_key")
    if not token:
        return {"ok": False, "message": "Missing bot_token", "details": {}}
    return await _request(
        "GET",
        "https://slack.com/api/auth.test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _test_discord(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    webhook_url = _value(data, "webhook_url", "url")
    if not webhook_url:
        return {"ok": False, "message": "Missing webhook_url", "details": {}}
    return await _request("GET", webhook_url)


async def _test_github(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    token = _value(data, "token", "api_key")
    if not token:
        return {"ok": False, "message": "Missing token", "details": {}}
    return await _request(
        "GET",
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )


async def _test_openai(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    api_key = _value(data, "api_key", "token")
    if not api_key:
        return {"ok": False, "message": "Missing api_key", "details": {}}
    return await _request(
        "GET",
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )


async def _test_anthropic(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    api_key = _value(data, "api_key", "token")
    if not api_key:
        return {"ok": False, "message": "Missing api_key", "details": {}}
    result = await _request(
        "GET",
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    if not result["ok"] and result["details"].get("status_code") == 404:
        result["message"] = "Anthropic auth endpoint unavailable"
    return result


async def _test_llm_provider(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    provider = _value(data, "provider") or str(context.get("provider") or "openai")
    provider = provider.strip().lower()
    api_key = _value(data, "api_key", "token")
    base_url = _value(data, "base_url")
    if provider == "anthropic":
        return await _test_anthropic(data, context)
    if provider == "ollama":
        url = (base_url or "http://localhost:11434").rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        return await _request("GET", f"{url}/api/tags")
    if provider == "azure_openai":
        endpoint = _value(data, "azure_endpoint", "base_url").rstrip("/")
        api_version = _value(data, "azure_api_version") or "2024-02-15-preview"
        if not endpoint or not api_key:
            return {
                "ok": False,
                "message": "Missing api_key or azure_endpoint",
                "details": {},
            }
        return await _request(
            "GET",
            f"{endpoint}/openai/deployments",
            headers={"api-key": api_key},
            params={"api-version": api_version},
        )
    if not api_key and provider != "ollama":
        return {"ok": False, "message": "Missing api_key", "details": {}}
    url = f"{(base_url or 'https://api.openai.com/v1').rstrip('/')}/models"
    return await _request("GET", url, headers={"Authorization": f"Bearer {api_key}"})


async def _test_cohere(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    api_key = _value(data, "api_key", "token")
    if not api_key:
        return {"ok": False, "message": "Missing api_key", "details": {}}
    return await _request(
        "GET",
        "https://api.cohere.ai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )


async def _test_deepl(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    api_key = _value(data, "api_key", "auth_key")
    if not api_key:
        return {"ok": False, "message": "Missing api_key", "details": {}}
    base = "https://api-free.deepl.com" if api_key.endswith(":fx") else "https://api.deepl.com"
    return await _request(
        "GET",
        f"{base}/v2/usage",
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
    )


async def _test_pinecone(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    api_key = _value(data, "api_key")
    index_host = _value(data, "index_host")
    if not api_key or not index_host:
        return {
            "ok": False,
            "message": "Missing api_key or index_host",
            "details": {},
        }
    return await _request(
        "POST",
        f"https://{index_host}/describe_index_stats",
        headers={"Api-Key": api_key, "Content-Type": "application/json"},
    )


async def _test_notion(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    token = _value(data, "token", "api_key")
    if not token:
        return {"ok": False, "message": "Missing token", "details": {}}
    return await _request(
        "GET",
        "https://api.notion.com/v1/users/me",
        headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"},
    )


async def _test_stripe(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    api_key = _value(data, "api_key", "token")
    if not api_key:
        return {"ok": False, "message": "Missing api_key", "details": {}}
    return await _request("GET", "https://api.stripe.com/v1/account", auth=(api_key, ""))


async def _test_airtable(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    token = _value(data, "token", "api_key")
    if not token:
        return {"ok": False, "message": "Missing token", "details": {}}
    return await _request(
        "GET",
        "https://api.airtable.com/v0/meta/bases",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _test_google_sheets(
    data: dict[str, str], context: dict[str, Any]
) -> dict[str, Any]:
    spreadsheet_id = str(context.get("spreadsheet_id") or "").strip()
    if not spreadsheet_id:
        return _missing("spreadsheet_id")
    headers: dict[str, str] = {}
    params: dict[str, str] = {"fields": "spreadsheetId,properties.title"}
    api_key = _value(data, "api_key")
    access_token = _value(data, "access_token", "token")
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    elif api_key:
        params["key"] = api_key
    else:
        return {"ok": False, "message": "Missing api_key or access_token", "details": {}}
    return await _request(
        "GET",
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
        headers=headers or None,
        params=params,
    )


async def _test_smtp(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    host = str(context.get("host") or data.get("host") or "").strip()
    if not host:
        return _missing("host")
    port = int(context.get("port") or data.get("port") or 587)
    username = _value(data, "username", "user")
    password = _value(data, "password", "app_password")
    use_tls = _truthy(context.get("use_tls", data.get("use_tls")), default=True)

    def connect() -> dict[str, Any]:
        import ssl

        smtp_cls = smtplib.SMTP_SSL if use_tls and port == 465 else smtplib.SMTP
        with smtp_cls(timeout=10) as smtp:
            smtp.connect(host, port)
            smtp.ehlo()
            if use_tls and smtp_cls is smtplib.SMTP:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if username or password:
                smtp.login(username, password)
        return {"ok": True, "message": "Connected", "details": {"host": host, "port": port}}

    import asyncio

    return await asyncio.to_thread(connect)


async def _test_postgres(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    connection_url = _value(data, "connection_url", "url")
    if not connection_url:
        return {"ok": False, "message": "Missing connection_url", "details": {}}
    try:
        import psycopg
    except ImportError:
        return {"ok": False, "message": "Install psycopg[binary] to test Postgres", "details": {}}

    def connect() -> dict[str, Any]:
        with psycopg.connect(connection_url, connect_timeout=10) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        return {"ok": True, "message": "Connected", "details": {}}

    import asyncio

    return await asyncio.to_thread(connect)


async def _test_mysql(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    host = str(context.get("host") or data.get("host") or "").strip()
    database = str(context.get("database") or data.get("database") or "").strip()
    if not host:
        return _missing("host")
    if not database:
        return _missing("database")
    try:
        import pymysql
    except ImportError:
        return {"ok": False, "message": "Install PyMySQL to test MySQL", "details": {}}

    username = _value(data, "username", "user")
    password = _value(data, "password")
    port = int(context.get("port") or data.get("port") or 3306)

    def connect() -> dict[str, Any]:
        connection = pymysql.connect(
            host=host,
            port=port,
            user=username,
            password=password,
            database=database,
            connect_timeout=10,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        finally:
            connection.close()
        return {"ok": True, "message": "Connected", "details": {"host": host, "database": database}}

    import asyncio

    return await asyncio.to_thread(connect)


async def _test_aws(data: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    try:
        import boto3
    except ImportError:
        return {"ok": False, "message": "Install boto3 to test S3/AWS", "details": {}}
    access_key = _value(data, "aws_access_key_id")
    secret_key = _value(data, "aws_secret_access_key")
    # Refuse empty keys: boto3 would otherwise fall back to the API server's
    # instance/role auth, letting any Editor probe what *the server itself*
    # can reach in AWS. The credential must carry its own keys.
    if not access_key or not secret_key:
        return {
            "ok": False,
            "message": "aws_access_key_id and aws_secret_access_key are required for testing.",
            "details": {},
        }
    bucket = str(context.get("bucket") or "").strip()
    kwargs = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": str(context.get("region_name") or data.get("region_name") or "") or None,
        "endpoint_url": str(context.get("endpoint_url") or data.get("endpoint_url") or "") or None,
    }
    kwargs = {key: value for key, value in kwargs.items() if value is not None}

    def connect() -> dict[str, Any]:
        client = boto3.client("s3", **kwargs)
        if bucket:
            client.head_bucket(Bucket=bucket)
            return {"ok": True, "message": "Connected", "details": {"bucket": bucket}}
        client.list_buckets()
        return {"ok": True, "message": "Connected", "details": {}}

    import asyncio

    return await asyncio.to_thread(connect)


_TESTERS: dict[str, TestFn] = {
    "slack_bot": _test_slack,
    "discord_webhook": _test_discord,
    "github": _test_github,
    "openai": _test_openai,
    "anthropic": _test_anthropic,
    "llm_provider": _test_llm_provider,
    "cohere": _test_cohere,
    "deepl": _test_deepl,
    "pinecone": _test_pinecone,
    "notion": _test_notion,
    "stripe": _test_stripe,
    "airtable": _test_airtable,
    "google_sheets": _test_google_sheets,
    "smtp": _test_smtp,
    "postgres": _test_postgres,
    "mysql": _test_mysql,
    "aws": _test_aws,
}


def available_test_services() -> list[str]:
    """Return the credential service ids that have a registered test handler.

    Sorted for stable UI rendering. Consumed by ``GET /credentials/test-handlers``
    and used by the editor to decide whether to surface a "Test on save" action
    next to a credential's manifest entry.
    """
    return sorted(_TESTERS.keys())


def has_test_handler(service: str) -> bool:
    return service in _TESTERS


async def test_credential_connection(
    service: str,
    data: dict[str, str],
    context: dict[str, Any],
) -> CredentialTestResponse:
    started = time.perf_counter()
    tester = _TESTERS.get(service)
    try:
        if tester is None:
            raw = {
                "ok": False,
                "message": f"No connection test is available for credential type '{service}'.",
                "details": {},
            }
        else:
            raw = await tester(data, context)
    except Exception as exc:  # noqa: BLE001 - surface connection diagnostics
        raw = {"ok": False, "message": f"{type(exc).__name__}: {exc}", "details": {}}

    elapsed = int((time.perf_counter() - started) * 1000)
    secret_values = [value for value in data.values() if isinstance(value, str)]
    # Include common derived basic-auth forms in exact redaction.
    if data.get("username") and data.get("password"):
        token = base64.b64encode(
            f"{data['username']}:{data['password']}".encode()
        ).decode()
        secret_values.append(token)
    clean = redact_value(raw, secret_values)
    return CredentialTestResponse(
        ok=bool(clean.get("ok")),
        status="success" if clean.get("ok") else "error",
        service=service,
        message=str(clean.get("message") or ("Connected" if clean.get("ok") else "Failed")),
        latency_ms=elapsed,
        checked_at=datetime.now(UTC),
        details=clean.get("details") if isinstance(clean.get("details"), dict) else {},
    )
