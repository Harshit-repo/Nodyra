"""Credential reference resolution for workflow execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Credential, Workflow
from app.services.crypto import decrypt_credential

CREDENTIAL_REF_MARKER = "__noodle_credential__"


def is_credential_ref(value: Any) -> bool:
    return isinstance(value, dict) and value.get(CREDENTIAL_REF_MARKER) is True


def credential_ref(credential_id: str, key: str) -> dict[str, str | bool]:
    return {CREDENTIAL_REF_MARKER: True, "id": credential_id, "key": key}


def _scope_visible(
    cred: Credential,
    *,
    workflow_id: str | None,
    environment_id: str | None,
    runner_pool_id: str | None,
) -> bool:
    if cred.scope == "global":
        return True
    if cred.scope == "workflow":
        return bool(workflow_id and cred.workflow_id == workflow_id)
    if cred.scope == "environment":
        return bool(environment_id and cred.environment_id == environment_id)
    if cred.scope == "runner_pool":
        return bool(runner_pool_id and cred.runner_pool_id == runner_pool_id)
    return False


async def _workflow_environment_id(
    session: AsyncSession, workflow_id: str | None
) -> str | None:
    if not workflow_id:
        return None
    workflow = await session.get(Workflow, workflow_id)
    return workflow.environment_id if workflow is not None else None


async def _resolve_ref(
    session: AsyncSession,
    ref: dict[str, Any],
    *,
    workflow_id: str | None,
    environment_id: str | None,
    runner_pool_id: str | None,
) -> Any:
    cred_id = str(ref.get("id") or "")
    key = str(ref.get("key") or "")
    if not cred_id:
        raise RuntimeError("credential reference is missing an id")
    cred = await session.get(Credential, cred_id)
    if cred is None:
        raise RuntimeError(f"credential '{cred_id}' was not found")
    if not _scope_visible(
        cred,
        workflow_id=workflow_id,
        environment_id=environment_id,
        runner_pool_id=runner_pool_id,
    ):
        raise RuntimeError(f"credential '{cred.name}' is not visible to this run")

    data = decrypt_credential(cred.encrypted_data, cred.encrypted_dek)
    cred.last_used_at = datetime.now(UTC)
    # ``*`` means "the whole credential dict" — used by multi-field params
    # so a node receives e.g. ``{"username": ..., "password": ...}`` in a
    # single parameter (n8n-style single credentials picker).
    if key == "*":
        return dict(data)
    if not key:
        if len(data) != 1:
            raise RuntimeError(
                f"credential '{cred.name}' needs an explicit field key"
            )
        key = next(iter(data))
    if key not in data:
        raise RuntimeError(f"credential '{cred.name}' has no field '{key}'")
    return data[key]


async def resolve_credential_refs(
    session: AsyncSession,
    value: Any,
    *,
    workflow_id: str | None = None,
    environment_id: str | None = None,
    runner_pool_id: str | None = None,
) -> Any:
    """Return value with stored credential refs replaced by decrypted fields.

    This is intentionally an API/runtime boundary operation. Stored workflow
    graphs keep references only; raw secrets are injected into a throwaway graph
    immediately before engine dispatch.
    """
    if environment_id is None:
        environment_id = await _workflow_environment_id(session, workflow_id)

    async def walk(item: Any) -> Any:
        if is_credential_ref(item):
            return await _resolve_ref(
                session,
                item,
                workflow_id=workflow_id,
                environment_id=environment_id,
                runner_pool_id=runner_pool_id,
            )
        if isinstance(item, list):
            return [await walk(child) for child in item]
        if isinstance(item, dict):
            return {key: await walk(child) for key, child in item.items()}
        return item

    resolved = await walk(value)
    await session.flush()
    return resolved
