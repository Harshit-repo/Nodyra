from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Environment
from app.schemas import (
    EnvironmentCreate,
    EnvironmentInfo,
    EnvironmentUpdate,
    PackageRequest,
)
from app.security import require_permission
from app.services.audit import log_audit
from app.services.venv import build_environment

router = APIRouter(prefix="/environments", tags=["environments"])


async def _load(session: AsyncSession, env_id: str) -> Environment:
    env = await session.get(Environment, env_id)
    if env is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Environment not found")
    return env


def _effective_pool_max(env: Environment) -> int:
    if env.runner_pool_max is not None:
        return env.runner_pool_max
    if env.runner_pool_size and env.runner_pool_size > 0:
        return env.runner_pool_size
    return 1


def _to_info(env: Environment) -> EnvironmentInfo:
    return EnvironmentInfo(
        id=env.id,
        name=env.name,
        is_global=env.is_global,
        python_version=env.python_version,
        packages=list(env.packages),
        status=env.status,
        status_detail=env.status_detail,
        description=env.description,
        runner_pool_size=env.runner_pool_size,
        runner_pool_max=env.runner_pool_max,
        effective_pool_max=_effective_pool_max(env),
        worker_rss_estimate_bytes=env.worker_rss_estimate_bytes,
        created_at=env.created_at,
        updated_at=env.updated_at,
    )


def _validate_pool(size: int, pool_max: int | None) -> None:
    """Reject invalid (size, max) pairs.

    - size==0 means Spawn-per-run, which requires max>=1.
    - max must be at least max(1, size) when provided.
    """
    if size == 0 and pool_max is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "runner_pool_max must be set when runner_pool_size is 0 (Spawn-per-run mode).",
        )
    if pool_max is not None and pool_max < max(1, size):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "runner_pool_max must be >= max(1, runner_pool_size).",
        )


@router.get("", response_model=list[EnvironmentInfo])
async def list_environments(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(
        select(Environment).order_by(Environment.is_global.desc(), Environment.name)
    )
    return [_to_info(env) for env in result.all()]


@router.post(
    "",
    response_model=EnvironmentInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def create_environment(
    body: EnvironmentCreate,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    _validate_pool(body.runner_pool_size, body.runner_pool_max)
    env = Environment(
        name=body.name,
        python_version=body.python_version,
        packages=body.packages,
        description=body.description,
        runner_pool_size=body.runner_pool_size,
        runner_pool_max=body.runner_pool_max,
        status="pending",
    )
    session.add(env)
    await log_audit(session, "create", "environment", detail=body.name)
    await session.commit()
    await session.refresh(env)
    background.add_task(build_environment, env.id)
    return _to_info(env)


@router.patch(
    "/{env_id}",
    response_model=EnvironmentInfo,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def update_environment(
    env_id: str,
    body: EnvironmentUpdate,
    session: AsyncSession = Depends(get_session),
):
    env = await _load(session, env_id)
    sent = body.model_fields_set
    new_size = (
        body.runner_pool_size
        if "runner_pool_size" in sent and body.runner_pool_size is not None
        else env.runner_pool_size
    )
    new_max = body.runner_pool_max if "runner_pool_max" in sent else env.runner_pool_max
    _validate_pool(new_size, new_max)
    if body.name is not None:
        env.name = body.name
    if body.description is not None:
        env.description = body.description
    if "runner_pool_size" in sent and body.runner_pool_size is not None:
        env.runner_pool_size = body.runner_pool_size
    if "runner_pool_max" in sent:
        env.runner_pool_max = body.runner_pool_max
    await log_audit(session, "update", "environment", env.id, env.name)
    await session.commit()
    await session.refresh(env)
    return _to_info(env)


@router.get("/{env_id}", response_model=EnvironmentInfo)
async def get_environment(env_id: str, session: AsyncSession = Depends(get_session)):
    return _to_info(await _load(session, env_id))


@router.post(
    "/{env_id}/packages",
    response_model=EnvironmentInfo,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def add_package(
    env_id: str,
    body: PackageRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    env = await _load(session, env_id)
    packages = list(env.packages)
    if body.package not in packages:
        packages.append(body.package)
        env.packages = packages
        env.status = "pending"
        await session.commit()
        await session.refresh(env)
        background.add_task(build_environment, env.id)
    return _to_info(env)


@router.delete(
    "/{env_id}/packages/{package}",
    response_model=EnvironmentInfo,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def remove_package(
    env_id: str,
    package: str,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    env = await _load(session, env_id)
    remaining = [p for p in env.packages if p != package]
    if remaining != list(env.packages):
        env.packages = remaining
        env.status = "pending"
        await session.commit()
        await session.refresh(env)
        background.add_task(build_environment, env.id)
    return _to_info(env)


@router.post(
    "/{env_id}/rebuild",
    response_model=EnvironmentInfo,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def rebuild_environment(
    env_id: str,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    env = await _load(session, env_id)
    env.status = "pending"
    await session.commit()
    await session.refresh(env)
    background.add_task(build_environment, env.id)
    return _to_info(env)


@router.delete(
    "/{env_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def delete_environment(
    env_id: str, session: AsyncSession = Depends(get_session)
):
    env = await _load(session, env_id)
    if env.is_global:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "The global environment cannot be deleted"
        )
    await session.delete(env)
    await session.commit()
