"""PostgreSQL database nodes."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from noodle.sdk import node
from noodle_nodes.http_security import assert_public_host


def _assert_connection_allowed(connection_url: str) -> None:
    """SEC-4: block connections to private/loopback/link-local DB hosts unless
    the operator opted in via NOODLE_ALLOW_PRIVATE_EGRESS. A user-supplied
    connection URL is otherwise protocol-level SSRF (reach any internal host).
    """
    try:
        parts = urlsplit(connection_url)
    except ValueError as exc:
        raise ValueError("postgres: invalid connection URL") from exc
    host = parts.hostname
    # A URL with no host (e.g. a local unix-socket DSN) isn't a network target.
    if host:
        assert_public_host(host, parts.port, context="postgres")


def _psycopg():
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Postgres requires the `psycopg[binary]` package. "
            "Install with: uv pip install psycopg[binary]"
        ) from exc
    return psycopg


def _dict_row():
    try:
        from psycopg.rows import dict_row  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Postgres requires the `psycopg[binary]` package. "
            "Install with: uv pip install psycopg[binary]"
        ) from exc
    return dict_row


def _params(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return None


@node(
    name="Postgres Query",
    id="postgres_query_v2",
    category="Integrations",
    icon="brand:postgresql",
    params={
        "connection_url": {
            "type": "credential",
            "credential_type": "postgres",
            "description": "Postgres connection URL.",
        },
        "sql": {
            "multiline": True,
            "placeholder": "select * from users limit 10",
        },
        "parameters": {
            "group": "Options",
            "description": "Optional positional list or named dict parameters.",
        },
    },
)
def postgres_query(
    input: Any = None,
    connection_url: str = "",
    sql: str = "",
    parameters: Any = None,
) -> Any:
    """Run a SQL statement against PostgreSQL and return rows for queries."""
    _assert_connection_allowed(connection_url)
    psycopg = _psycopg()
    dict_row = _dict_row()
    with psycopg.connect(connection_url, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, _params(parameters))
            if cursor.description:
                return cursor.fetchall()
            conn.commit()
            return {"rowcount": cursor.rowcount}


@node(
    name="Postgres Get Tables",
    id="postgres_get_tables_v2",
    category="Integrations",
    icon="brand:postgresql",
    tool_side_effecting=False,
    params={
        "connection_url": {
            "type": "credential",
            "credential_type": "postgres",
            "description": "Postgres connection URL.",
        },
        "schema": {
            "placeholder": "public",
            "description": "Schema name (default: public).",
        },
    },
)
def postgres_get_tables(
    input: Any = None,
    connection_url: str = "",
    schema: str = "public",
) -> Any:
    """List tables and views in a PostgreSQL schema."""
    _assert_connection_allowed(connection_url)
    psycopg = _psycopg()
    dict_row = _dict_row()
    with psycopg.connect(connection_url, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    table_schema,
                    table_name,
                    table_type
                FROM information_schema.tables
                WHERE table_schema = %s
                ORDER BY table_name
                """,
                (schema or "public",),
            )
            return cursor.fetchall()


@node(
    name="Postgres List Schemas",
    id="postgres_list_schemas_v2",
    category="Integrations",
    icon="brand:postgresql",
    tool_side_effecting=False,
    params={
        "connection_url": {
            "type": "credential",
            "credential_type": "postgres",
            "description": "Postgres connection URL.",
        },
    },
)
def postgres_list_schemas(
    input: Any = None,
    connection_url: str = "",
) -> Any:
    """List all schemas in a PostgreSQL database."""
    _assert_connection_allowed(connection_url)
    psycopg = _psycopg()
    dict_row = _dict_row()
    with psycopg.connect(connection_url, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT schema_name
                FROM information_schema.schemata
                ORDER BY schema_name
                """
            )
            return cursor.fetchall()
