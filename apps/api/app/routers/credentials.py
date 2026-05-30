from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Credential, Environment, Workflow
from app.schemas import (
    CredentialCreate,
    CredentialInfo,
    CredentialTestRequest,
    CredentialTestResponse,
    CredentialUpdate,
)
from app.security import require_permission
from app.services.audit import log_audit
from app.services.credential_tests import (
    available_test_services,
    test_credential_connection,
)
from app.services.crypto import decrypt_data, encrypt_data
from app.services.redaction import invalidate_secret_cache

router = APIRouter(prefix="/credentials", tags=["credentials"])

SCOPES = {"global", "environment", "workflow", "runner_pool"}


def _info(cred: Credential) -> CredentialInfo:
    data = decrypt_data(cred.encrypted_data)
    return CredentialInfo(
        id=cred.id,
        name=cred.name,
        type=cred.type,
        scope=cred.scope,
        workflow_id=cred.workflow_id,
        environment_id=cred.environment_id,
        runner_pool_id=cred.runner_pool_id,
        description=cred.description,
        keys=sorted(data.keys()),
        last_used_at=cred.last_used_at,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )


async def _load(session: AsyncSession, cred_id: str) -> Credential:
    cred = await session.get(Credential, cred_id)
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    return cred


async def _validate_scope(
    session: AsyncSession,
    scope: str,
    workflow_id: str | None,
    environment_id: str | None,
    runner_pool_id: str | None,
) -> None:
    if scope not in SCOPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported credential scope '{scope}'.",
        )
    if scope == "workflow":
        if not workflow_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "workflow_id is required for workflow-scoped credentials.",
            )
        if await session.get(Workflow, workflow_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    if scope == "environment":
        if not environment_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "environment_id is required for environment-scoped credentials.",
            )
        if await session.get(Environment, environment_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Environment not found")
    if scope == "runner_pool" and not runner_pool_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "runner_pool_id is required for runner-pool-scoped credentials.",
        )


def _scope_rank(
    cred: Credential,
    workflow_id: str | None,
    environment_id: str | None,
    runner_pool_id: str | None,
) -> int:
    if cred.scope == "workflow" and cred.workflow_id == workflow_id:
        return 40
    if cred.scope == "environment" and cred.environment_id == environment_id:
        return 30
    if cred.scope == "runner_pool" and cred.runner_pool_id == runner_pool_id:
        return 20
    if cred.scope == "global":
        return 10
    return -1


async def _resolve(
    session: AsyncSession,
    *,
    name: str,
    type: str | None,
    workflow_id: str | None,
    environment_id: str | None,
    runner_pool_id: str | None,
) -> Credential | None:
    stmt = select(Credential).where(Credential.name == name)
    if type:
        stmt = stmt.where(Credential.type == type)
    rows = (await session.scalars(stmt)).all()
    candidates = [
        (rank, cred)
        for cred in rows
        if (rank := _scope_rank(cred, workflow_id, environment_id, runner_pool_id)) >= 0
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


@router.get(
    "/test-handlers",
    response_model=list[str],
    dependencies=[Depends(require_permission("credential:read"))],
)
async def list_credential_test_handlers() -> list[str]:
    """Service ids whose credentials can be validated via ``POST /credentials/{id}/test``.

    The editor reads this to decide whether to surface a "Test connection" action
    next to a credential whose ``CredentialSpec.test_service`` (or ``type`` fallback)
    appears in the list. Stable alphabetical order.
    """
    return available_test_services()


@router.get(
    "",
    response_model=list[CredentialInfo],
    dependencies=[Depends(require_permission("credential:read"))],
)
async def list_credentials(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(select(Credential).order_by(Credential.name))
    return [_info(c) for c in result.all()]


@router.get(
    "/resolve",
    response_model=CredentialInfo,
    dependencies=[Depends(require_permission("credential:read"))],
)
async def resolve_credential(
    name: str = Query(min_length=1),
    type: str | None = None,
    workflow_id: str | None = None,
    environment_id: str | None = None,
    runner_pool_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    cred = await _resolve(
        session,
        name=name,
        type=type,
        workflow_id=workflow_id,
        environment_id=environment_id,
        runner_pool_id=runner_pool_id,
    )
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    # Resolve is a lookup, not a use. ``resolve_credential_refs`` updates
    # ``last_used_at`` at workflow dispatch time, which is the real "used"
    # event. Keeping GET /credentials/resolve side-effect-free avoids
    # surprising last_used_at writes from caches / retries / prefetchers.
    return _info(cred)


@router.post(
    "",
    response_model=CredentialInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("credential:write"))],
)
async def create_credential(
    body: CredentialCreate, session: AsyncSession = Depends(get_session)
):
    await _validate_scope(
        session,
        body.scope,
        body.workflow_id,
        body.environment_id,
        body.runner_pool_id,
    )
    cred = Credential(
        name=body.name,
        type=body.type,
        scope=body.scope,
        workflow_id=body.workflow_id if body.scope == "workflow" else None,
        environment_id=body.environment_id if body.scope == "environment" else None,
        runner_pool_id=body.runner_pool_id if body.scope == "runner_pool" else None,
        description=body.description,
        encrypted_data=encrypt_data(body.data),
    )
    session.add(cred)
    await log_audit(session, "create", "credential", detail=body.name)
    await session.commit()
    await session.refresh(cred)
    invalidate_secret_cache()
    return _info(cred)


@router.put(
    "/{cred_id}",
    response_model=CredentialInfo,
    dependencies=[Depends(require_permission("credential:write"))],
)
async def update_credential(
    cred_id: str,
    body: CredentialUpdate,
    session: AsyncSession = Depends(get_session),
):
    cred = await _load(session, cred_id)
    if body.name is not None:
        cred.name = body.name
    scope = body.scope or cred.scope
    workflow_id = body.workflow_id if body.workflow_id is not None else cred.workflow_id
    environment_id = (
        body.environment_id if body.environment_id is not None else cred.environment_id
    )
    runner_pool_id = (
        body.runner_pool_id if body.runner_pool_id is not None else cred.runner_pool_id
    )
    if body.scope is not None or any(
        value is not None
        for value in (body.workflow_id, body.environment_id, body.runner_pool_id)
    ):
        await _validate_scope(
            session,
            scope,
            workflow_id,
            environment_id,
            runner_pool_id,
        )
        cred.scope = scope
        cred.workflow_id = workflow_id if scope == "workflow" else None
        cred.environment_id = environment_id if scope == "environment" else None
        cred.runner_pool_id = runner_pool_id if scope == "runner_pool" else None
    if body.description is not None:
        cred.description = body.description
    if body.data is not None:
        cred.encrypted_data = encrypt_data(body.data)
    await log_audit(session, "update", "credential", cred.id, cred.name)
    await session.commit()
    await session.refresh(cred)
    if body.data is not None:
        invalidate_secret_cache()
    return _info(cred)


@router.post(
    "/{cred_id}/test",
    response_model=CredentialTestResponse,
    dependencies=[Depends(require_permission("credential:test"))],
)
async def test_credential(
    cred_id: str,
    body: CredentialTestRequest,
    session: AsyncSession = Depends(get_session),
):
    cred = await _load(session, cred_id)
    if (
        _scope_rank(
            cred,
            body.workflow_id,
            body.environment_id,
            body.runner_pool_id,
        )
        < 0
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Credential is not visible for the supplied workflow/environment scope.",
        )
    data = decrypt_data(cred.encrypted_data)
    result = await test_credential_connection(cred.type, data, body.context)
    cred.last_used_at = datetime.now(UTC)
    await session.commit()
    return result


@router.delete(
    "/{cred_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("credential:write"))],
)
async def delete_credential(
    cred_id: str,
    session: AsyncSession = Depends(get_session),
):
    cred = await _load(session, cred_id)
    await log_audit(session, "delete", "credential", cred.id, cred.name)
    await session.delete(cred)
    await session.commit()
    invalidate_secret_cache()
