"""Optional managed MCP execution boundary shared by HTTP and all workers.

Upstream results are observations, not proof of a downstream transaction.
No tool arguments, secrets, or provider result bodies are persisted here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jsonschema import Draft202012Validator, SchemaError
from jsonschema.validators import validator_for
from referencing import Registry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    CustomRole,
    MCPConnection,
    MCPGatewayInvocation,
    Membership,
    Run,
    User,
    WorkflowVersion,
)
from app.services import mcp_client, rate_limit
from app.services.execution_actor import validate_execution_attempt


class GatewayDenied(ValueError):
    def __init__(self, reason: str, invocation_id: str = "", *, http_status: int = 403):
        self.reason = reason
        self.invocation_id = invocation_id
        self.http_status = http_status
        super().__init__(
            f"MCP gateway: {reason}" + (f" (reference {invocation_id})" if invocation_id else "")
        )


@dataclass(frozen=True)
class Principal:
    org_id: str
    actor_id: str | None
    actor_kind: str = "user"
    run_id: str | None = None
    workflow_id: str | None = None
    workflow_version_id: str | None = None
    workflow_version: int | None = None
    graph_digest: str | None = None


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def private_digest(value: Any) -> str:
    # A plain hash of low-entropy arguments would disclose them by guessing.
    return hmac.new(
        settings.secret_key.encode(), canonical_bytes(value), hashlib.sha256
    ).hexdigest()


def connection_fingerprint(conn: MCPConnection) -> str:
    return private_digest(
        [conn.url, conn.transport, conn.auth_type, conn.auth_secret, conn.headers]
    )


def contract(tool: dict) -> dict:
    """Descriptions are presentation; schemas and annotations affect execution."""
    return mcp_client.tool_contract(tool)


def catalog_digest(conn: MCPConnection, catalog: dict) -> str:
    return digest(
        {
            "connection": connection_fingerprint(conn),
            "capabilities": catalog["capabilities"],
            "tools": sorted(
                (contract(tool) for tool in catalog["tools"]), key=lambda tool: tool["name"]
            ),
        }
    )


def _schema_validator(schema: dict):
    """Honor the reviewed dialect and prohibit reference-driven network I/O."""
    # Visit schema positions, not arbitrary JSON values within const/default.
    schema_maps = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas", "dependencies"}
    schema_values = {"items", "additionalItems", "contains", "additionalProperties", "unevaluatedProperties", "unevaluatedItems", "propertyNames", "not", "if", "then", "else"}
    schema_lists = {"allOf", "anyOf", "oneOf", "prefixItems"}

    def walk(value):
        if isinstance(value, dict):
            if "$schema" in value:
                dialect = value["$schema"]
                if not isinstance(dialect, str) or validator_for(value, default=None) is None:
                    raise ValueError("Unsupported JSON Schema dialect")
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise ValueError("Only local schema references are supported")
                if key in schema_maps and isinstance(item, dict):
                    for child in item.values():
                        walk(child)
                elif key in schema_values:
                    if isinstance(item, list):
                        for child in item:
                            walk(child)
                    else:
                        walk(item)
                elif key in schema_lists and isinstance(item, list):
                    for child in item:
                        walk(child)

    walk(schema)
    validator_class = validator_for(schema, default=Draft202012Validator)
    try:
        validator_class.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Invalid JSON Schema") from exc
    # An explicit registry removes jsonschema's legacy remote-fetch fallback.
    # Even a nested $id or dialect-specific reference cannot fetch a resource.
    return validator_class(schema, registry=Registry())


def validate_schema(schema: dict) -> None:
    _schema_validator(schema)


async def approve_policy(
    session: AsyncSession,
    conn: MCPConnection,
    *,
    actor_id: str,
    tools: dict[str, dict],
    workflow_version_ids: list[str],
    expected_catalog_digest: str,
) -> dict:
    """Explicit administrator action. Discovery alone never grants access."""
    if not tools or len(tools) > 100:
        raise ValueError("Select between one and 100 tools")
    loaded, secret = await mcp_client._load_conn_with_secret(conn.id, conn.org_id, session)
    catalog = await mcp_client.discover_catalog(loaded, decrypted_secret=secret)
    if not hmac.compare_digest(catalog_digest(conn, catalog), expected_catalog_digest):
        raise ValueError("Catalog changed since review; discover and review it again")
    manifests = {tool["name"]: tool for tool in catalog["tools"]}
    approved = {}
    for name, constraints in tools.items():
        mcp_client.ensure_tool_allowed(conn, name)
        if name not in manifests:
            raise ValueError("A selected tool was not discovered")
        snapshot = contract(manifests[name])
        validate_schema(snapshot["inputSchema"])
        if "outputSchema" in snapshot:
            validate_schema(snapshot["outputSchema"])
        validate_schema(constraints)
        approved[name] = {"manifest": snapshot, "arguments_schema": constraints}
    versions = {}
    for version_id in set(workflow_version_ids):
        version = await session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.id == version_id, WorkflowVersion.org_id == conn.org_id
            )
        )
        if version is None:
            raise ValueError("A workflow version does not exist in this organization")
        versions[version.id] = {
            "workflow_id": version.workflow_id,
            "graph_digest": digest(version.graph),
        }
    policy = {
        "revision": uuid.uuid4().hex,
        "approved_by": actor_id,
        "approved_at": datetime.now(UTC).isoformat(),
        "connection_fingerprint": connection_fingerprint(conn),
        "capabilities": catalog["capabilities"],
        "tools": approved,
        "workflow_versions": versions,
    }
    conn.gateway_policy = policy
    return policy


async def _worker_actor_allowed(session: AsyncSession, principal: Principal) -> bool:
    if principal.actor_kind == "system":
        return True  # Still requires an explicitly approved immutable version.
    if principal.actor_kind != "user" or not principal.actor_id:
        return False
    user = await session.get(User, principal.actor_id, populate_existing=True)
    if user is None:
        return False
    role = user.role
    if settings.multi_tenancy_enabled:
        membership = await session.scalar(
            select(Membership)
            .where(Membership.org_id == principal.org_id, Membership.user_id == user.id)
            .execution_options(populate_existing=True)
        )
        if membership is None:
            return False
        if membership.custom_role_id:
            custom = await session.get(
                CustomRole, membership.custom_role_id, populate_existing=True
            )
            return custom is not None and "workflow:run" in (custom.permissions or [])
        role = membership.role
    return role in {"editor", "admin", "owner"}


async def execute(
    session: AsyncSession,
    connection_id: str,
    tool_name: str,
    arguments: dict,
    *,
    principal: Principal,
    return_envelope: bool = False,
) -> tuple[Any, str]:
    """Persist the decision before dispatch; never retry ambiguous effects."""
    try:
        encoded = canonical_bytes(arguments)
        valid_args = (
            isinstance(arguments, dict) and len(encoded) <= settings.mcp_gateway_max_arguments_bytes
        )
    except (ValueError, TypeError, RecursionError):
        valid_args = False
    row = MCPGatewayInvocation(
        id=uuid.uuid4().hex,
        org_id=principal.org_id,
        connection_id=connection_id[:255],
        actor_id=principal.actor_id,
        actor_kind=principal.actor_kind,
        run_id=principal.run_id,
        workflow_id=principal.workflow_id,
        workflow_version_id=principal.workflow_version_id,
        workflow_version=principal.workflow_version,
        graph_digest=principal.graph_digest,
        tool_name=tool_name[:255],
        arguments_digest=private_digest(arguments) if valid_args else private_digest("invalid"),
        # Keys can themselves contain customer data; omit them from audit.
        argument_keys=[],
        decision="denied",
        reason="not_evaluated",
        outcome="not_dispatched",
    )
    session.add(row)

    async def deny(reason: str, http_status: int = 403):
        row.reason = reason
        row.finished_at = datetime.now(UTC)
        await session.commit()
        raise GatewayDenied(reason, row.id, http_status=http_status)

    if not settings.mcp_gateway_enabled:
        await deny("gateway_disabled", 404)
    if not valid_args or not tool_name or len(tool_name) > 255:
        await deny("invalid_arguments", 422)
    if principal.actor_kind not in {"user", "system"} or (
        principal.actor_kind == "user" and not principal.actor_id
    ):
        await deny("authenticated_actor_required", 401)
    conn = await session.scalar(
        select(MCPConnection).where(
            MCPConnection.id == connection_id, MCPConnection.org_id == principal.org_id
        )
    )
    if conn is None:
        await deny("connection_unavailable", 404)
    try:
        mcp_client.ensure_tool_allowed(conn, tool_name)
    except ValueError:
        await deny("connection_policy_denied")
    policy = conn.gateway_policy
    if not isinstance(policy, dict) or policy.get(
        "connection_fingerprint"
    ) != connection_fingerprint(conn):
        await deny("contract_approval_required")
    row.policy_revision = policy.get("revision")
    selected = policy.get("tools", {}).get(tool_name)
    if not isinstance(selected, dict):
        await deny("tool_not_approved")
    if principal.run_id:
        run = await session.get(Run, principal.run_id, populate_existing=True)
        if run is None or run.org_id != principal.org_id or run.status != "running":
            await deny("run_not_active")
        approved_version = policy.get("workflow_versions", {}).get(principal.workflow_version_id)
        if (
            not isinstance(approved_version, dict)
            or approved_version.get("workflow_id") != principal.workflow_id
            or approved_version.get("graph_digest") != principal.graph_digest
        ):
            await deny("workflow_version_not_approved")
        if not await _worker_actor_allowed(session, principal):
            await deny("run_actor_permission_revoked")
    elif principal.actor_kind != "user":
        await deny("interactive_actor_required")
    try:
        for schema in (selected["manifest"]["inputSchema"], selected["arguments_schema"]):
            _schema_validator(schema).validate(arguments)
    except Exception:
        # jsonschema error messages may interpolate secret argument values.
        await deny("arguments_do_not_match_approved_contract", 422)
    if not await rate_limit.allow(
        "mcp_gateway_org",
        principal.org_id,
        limit=settings.mcp_gateway_rate_limit_per_minute,
        fail_closed=True,
    ):
        await deny("rate_limit_or_counter_unavailable", 429)
    identifier = (
        f"{principal.org_id}:{principal.actor_kind}:{principal.actor_id or principal.workflow_id}"
    )
    if not await rate_limit.allow(
        "mcp_gateway_actor",
        identifier,
        limit=settings.mcp_gateway_rate_limit_per_minute,
        fail_closed=True,
    ):
        await deny("rate_limit_or_counter_unavailable", 429)
    try:
        conn, secret = await mcp_client._load_conn_with_secret(
            connection_id, principal.org_id, session
        )
    except Exception:
        await deny("connection_secret_unavailable", 503)
    row.decision = "allowed"
    row.reason = "approved_contract"
    row.outcome = "dispatch_pending"
    # If this fails, execution never starts. A crash after this point remains
    # explicitly uncertain; a retry requires operator reconciliation.
    await session.commit()
    revalidation_failure = None

    async def before_dispatch():
        # Discovery can take seconds. Recheck authority after discovery and as
        # close to the effect as possible, including worker lease ownership.
        nonlocal revalidation_failure
        fresh = await session.scalar(
            select(MCPConnection)
            .where(MCPConnection.id == connection_id, MCPConnection.org_id == principal.org_id)
            .execution_options(populate_existing=True)
        )
        if (
            fresh is None
            or not fresh.enabled
            or not fresh.gateway_policy
            or fresh.gateway_policy.get("revision") != policy["revision"]
            or connection_fingerprint(fresh) != policy["connection_fingerprint"]
            or (fresh.allowed_tools is not None and tool_name not in fresh.allowed_tools)
        ):
            revalidation_failure = "policy_revoked_before_dispatch"
        elif principal.run_id:
            active_run = await session.get(Run, principal.run_id, populate_existing=True)
            if active_run is None or active_run.status != "running":
                revalidation_failure = "run_not_active"
            elif not await _worker_actor_allowed(session, principal):
                revalidation_failure = "run_actor_permission_revoked"
            else:
                revalidation_failure = await validate_execution_attempt(
                    session, run_id=principal.run_id
                )
        if revalidation_failure:
            raise mcp_client.MCPPolicyError(revalidation_failure)

    try:
        result = await mcp_client.call_tool(
            conn,
            tool_name,
            arguments,
            decrypted_secret=secret,
            expected_tool=selected["manifest"],
            expected_capabilities=policy["capabilities"],
            return_envelope=return_envelope,
            before_dispatch=before_dispatch,
        )
    except Exception as exc:
        attempted = getattr(exc, "execution_attempted", True)
        row.outcome = (
            "not_dispatched"
            if not attempted
            else ("tool_error" if isinstance(exc, mcp_client.MCPToolError) else "outcome_unknown")
        )
        row.reason = (
            "upstream_contract_or_discovery_failed"
            if not attempted
            else (
                "upstream_tool_error"
                if isinstance(exc, mcp_client.MCPToolError)
                else "upstream_outcome_unknown"
            )
        )
        if not attempted and revalidation_failure:
            row.reason = revalidation_failure
        if not attempted:
            row.decision = "denied"
        row.finished_at = datetime.now(UTC)
        await session.commit()
        raise GatewayDenied(row.reason, row.id, http_status=502) from None
    row.outcome = "provider_reported_success"
    row.result_digest = private_digest(result)
    row.finished_at = datetime.now(UTC)
    await session.commit()
    return result, row.id


async def execute_for_run(
    session: AsyncSession,
    connection_id: str,
    tool_name: str,
    arguments: dict,
    *,
    run_id: str,
) -> Any:
    # This service is invoked by host-owned callbacks with a host-owned run ID.
    run = await session.get(Run, run_id)
    if run is None:
        raise GatewayDenied("run_unavailable")
    if not settings.mcp_gateway_enabled:
        conn, secret = await mcp_client._load_conn_with_secret(connection_id, run.org_id, session)
        mcp_client.ensure_tool_allowed(conn, tool_name)
        return await mcp_client.call_tool(
            conn,
            tool_name,
            arguments,
            decrypted_secret=secret,
            audit_session=session,
            run_id=run_id,
            actor_id=run.initiator_id,
        )
    principal = Principal(
        org_id=run.org_id,
        actor_id=run.initiator_id,
        actor_kind=run.initiator_kind,
        run_id=run.id,
        workflow_id=run.workflow_id,
        workflow_version_id=run.workflow_version_id,
        workflow_version=run.workflow_version,
        graph_digest=run.execution_graph_digest,
    )
    result, _ = await execute(session, connection_id, tool_name, arguments, principal=principal)
    return result


def invocation_info(row: MCPGatewayInvocation) -> dict:
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "org_id",
            "connection_id",
            "actor_id",
            "actor_kind",
            "run_id",
            "workflow_id",
            "workflow_version_id",
            "workflow_version",
            "graph_digest",
            "tool_name",
            "arguments_digest",
            "policy_revision",
            "decision",
            "reason",
            "outcome",
            "result_digest",
            "created_at",
            "finished_at",
        )
    }
