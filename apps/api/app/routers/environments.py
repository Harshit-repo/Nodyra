from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Environment
from app.schemas import EnvironmentCreate, EnvironmentInfo, PackageRequest
from app.services.audit import log_audit
from app.services.venv import build_environment

router = APIRouter(prefix="/environments", tags=["environments"])


async def _load(session: AsyncSession, env_id: str) -> Environment:
    env = await session.get(Environment, env_id)
    if env is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Environment not found")
    return env


@router.get("", response_model=list[EnvironmentInfo])
async def list_environments(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(
        select(Environment).order_by(Environment.is_global.desc(), Environment.name)
    )
    return list(result.all())


@router.post("", response_model=EnvironmentInfo, status_code=status.HTTP_201_CREATED)
async def create_environment(
    body: EnvironmentCreate,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    env = Environment(
        name=body.name,
        python_version=body.python_version,
        packages=body.packages,
        status="pending",
    )
    session.add(env)
    await log_audit(session, "create", "environment", detail=body.name)
    await session.commit()
    await session.refresh(env)
    background.add_task(build_environment, env.id)
    return env


@router.get("/{env_id}", response_model=EnvironmentInfo)
async def get_environment(env_id: str, session: AsyncSession = Depends(get_session)):
    return await _load(session, env_id)


@router.post("/{env_id}/packages", response_model=EnvironmentInfo)
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
    return env


@router.delete("/{env_id}/packages/{package}", response_model=EnvironmentInfo)
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
    return env


@router.post("/{env_id}/rebuild", response_model=EnvironmentInfo)
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
    return env


@router.delete("/{env_id}", status_code=status.HTTP_204_NO_CONTENT)
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
