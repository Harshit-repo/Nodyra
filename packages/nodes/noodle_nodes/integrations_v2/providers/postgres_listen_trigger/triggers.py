"""Postgres LISTEN/NOTIFY polling trigger."""

from __future__ import annotations

import json
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)


def _creds_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"dsn": value}
    return {}


def poll_postgres_listen(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    try:
        import psycopg
        from psycopg import sql as pg_sql
    except ImportError:
        raise ImportError(
            "postgres_listen_trigger requires psycopg[binary]>=3.0. "
            "Install with: pip install 'psycopg[binary]>=3.0'"
        )

    params = ctx.params
    creds = _creds_dict(params.get("credentials"))
    channel = str(params.get("channel") or "noodle_events").strip()
    payload_format = str(params.get("payload_format") or "json").lower()

    dsn = str(creds.get("dsn") or "").strip()
    if not dsn:
        host = str(creds.get("host") or "localhost")
        port = str(creds.get("port") or "5432")
        dbname = str(creds.get("dbname") or "postgres")
        user = str(creds.get("user") or "")
        password = str(creds.get("password") or "")
        dsn = f"host={host} port={port} dbname={dbname}"
        if user:
            dsn += f" user={user}"
        if password:
            dsn += f" password={password}"

    events: list[dict[str, Any]] = []

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(pg_sql.SQL("LISTEN {}").format(pg_sql.Identifier(channel)))
        # Poll for notifications for up to 3 seconds
        gen = conn.notifies(timeout=3.0)
        try:
            for notification in gen:
                raw_payload = notification.payload or ""
                if payload_format == "json" and raw_payload:
                    try:
                        payload = json.loads(raw_payload)
                    except json.JSONDecodeError:
                        payload = {"raw": raw_payload}
                else:
                    payload = {"raw": raw_payload}

                events.append(
                    {
                        "channel": notification.channel,
                        "pid": notification.pid,
                        **payload,
                    }
                )
        except StopIteration:
            pass

    return ProviderTriggerPollResult(events=events, cursor=ctx.cursor)


_CREDENTIALS_PARAM = OperationParamSpec(
    name="credentials",
    type="credential",
    required=True,
    credential=CredentialSpec(
        type="postgres",
        key="*",
        label="Postgres credentials",
        fields=["dsn"],
        multi=True,
        test_service="postgres",
    ),
    description="Postgres connection credentials (DSN or host/port/dbname/user/password).",
)

POSTGRES_LISTEN_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="postgres_listen_trigger",
    name="Postgres LISTEN",
    provider="postgres",
    resource="channel",
    event="notify",
    description=(
        "Start a workflow when a Postgres NOTIFY event is received on a channel. "
        "Applications NOTIFY the channel with a JSON payload."
    ),
    icon="brand:postgresql",
    params=(
        _CREDENTIALS_PARAM,
        OperationParamSpec(
            name="channel",
            default="noodle_events",
            description="Postgres LISTEN channel name.",
        ),
        OperationParamSpec(
            name="payload_format",
            choices=["json", "text"],
            default="json",
            description="Expected NOTIFY payload format.",
        ),
    ),
    requirements=("psycopg[binary]>=3.0",),
    poll=poll_postgres_listen,
    poll_interval_seconds=5,
)

register_provider_trigger(POSTGRES_LISTEN_TRIGGER_SPEC)
