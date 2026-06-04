import noodle_nodes  # noqa: F401 - importing registers the built-in nodes
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import Environment, RunnerPool, User, Workflow
from app.schemas import (
    EnvironmentCreate,
    EnvironmentInfo,
    EnvironmentUpdate,
    PackageListRequest,
    PackageRequest,
    PackageUsageEntry,
    PackageUsageInfo,
    PackageUsagePackage,
)
from noodle.packages import canonical_package_name
from noodle.sdk import registry as node_registry
from app.security import optional_current_user, require_permission
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


def _to_info(env: Environment, pool_name: str | None = None) -> EnvironmentInfo:
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
        runner_pool_id=env.runner_pool_id,
        runner_pool_name=pool_name,
        worker_rss_estimate_bytes=env.worker_rss_estimate_bytes,
        created_at=env.created_at,
        updated_at=env.updated_at,
    )


async def _pool_name(session: AsyncSession, pool_id: str | None) -> str | None:
    if not pool_id:
        return None
    pool = await session.get(RunnerPool, pool_id)
    return pool.name if pool else None


async def _validate_pool_ref(session: AsyncSession, pool_id: str | None) -> None:
    """Ensure a referenced runner pool exists before binding to it."""
    if pool_id and await session.get(RunnerPool, pool_id) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Runner pool {pool_id} not found.",
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
    envs = result.all()
    pool_ids = {e.runner_pool_id for e in envs if e.runner_pool_id}
    names: dict[str, str] = {}
    if pool_ids:
        pools = await session.scalars(
            select(RunnerPool).where(RunnerPool.id.in_(pool_ids))
        )
        names = {p.id: p.name for p in pools.all()}
    return [_to_info(env, names.get(env.runner_pool_id or "")) for env in envs]


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
    actor: User | None = Depends(optional_current_user),
):
    _validate_pool(body.runner_pool_size, body.runner_pool_max)
    await _validate_pool_ref(session, body.runner_pool_id)
    env = Environment(
        name=body.name,
        python_version=body.python_version,
        packages=body.packages,
        description=body.description,
        runner_pool_size=body.runner_pool_size,
        runner_pool_max=body.runner_pool_max,
        runner_pool_id=body.runner_pool_id,
        status="pending",
    )
    session.add(env)
    await log_audit(session, "create", "environment", detail=body.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.commit()
    await session.refresh(env)
    background.add_task(build_environment, env.id)
    return _to_info(env, await _pool_name(session, env.runner_pool_id))


@router.patch(
    "/{env_id}",
    response_model=EnvironmentInfo,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def update_environment(
    env_id: str,
    body: EnvironmentUpdate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
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
    if body.runner_pool_set or "runner_pool_id" in sent:
        await _validate_pool_ref(session, body.runner_pool_id)
        env.runner_pool_id = body.runner_pool_id
    await log_audit(session, "update", "environment", env.id, env.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.commit()
    await session.refresh(env)
    return _to_info(env, await _pool_name(session, env.runner_pool_id))


@router.get("/{env_id}", response_model=EnvironmentInfo)
async def get_environment(env_id: str, session: AsyncSession = Depends(get_session)):
    env = await _load(session, env_id)
    return _to_info(env, await _pool_name(session, env.runner_pool_id))


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
    return _to_info(env, await _pool_name(session, env.runner_pool_id))


def _node_requirements_by_type() -> dict[str, list[str]]:
    return {m.id: m.requirements for m in node_registry.manifests() if m.requirements}


@router.get("/{env_id}/package-usage", response_model=PackageUsageInfo)
async def package_usage(
    env_id: str, session: AsyncSession = Depends(get_session)
):
    """Map each required package (canonical name) to the workflow nodes needing it.

    Scans every workflow bound to this env (plus null-env workflows when this is
    the global env), reading each workflow's draft graph (falling back to its
    latest published version's graph).
    """
    env = await _load(session, env_id)
    reqs_by_type = _node_requirements_by_type()

    stmt = select(Workflow).options(selectinload(Workflow.versions))
    if env.is_global:
        stmt = stmt.where(
            (Workflow.environment_id == env_id) | (Workflow.environment_id.is_(None))
        )
    else:
        stmt = stmt.where(Workflow.environment_id == env_id)
    workflows = (await session.scalars(stmt)).all()

    usage: dict[str, list[PackageUsageEntry]] = {}
    for wf in workflows:
        graph = wf.draft_graph
        if graph is None and wf.versions:
            graph = wf.versions[-1].graph
        nodes = (graph or {}).get("nodes") or []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            reqs = reqs_by_type.get(n.get("type"))
            if not reqs:
                continue
            label = n.get("label") or n.get("type") or n.get("id") or ""
            for req in reqs:
                key = canonical_package_name(req)
                usage.setdefault(key, []).append(
                    PackageUsageEntry(
                        workflow_id=wf.id,
                        workflow_name=wf.name,
                        node_id=str(n.get("id") or ""),
                        node_label=str(label),
                    )
                )
    return PackageUsageInfo(
        packages=[
            PackageUsagePackage(package=k, used_by=v) for k, v in sorted(usage.items())
        ]
    )


@router.put(
    "/{env_id}/packages",
    response_model=EnvironmentInfo,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def set_packages(
    env_id: str,
    body: PackageListRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Replace the env's full package list (dedup by canonical name, last wins).

    Backs both the comma-separated add and the requirements.txt import on the
    env page; the client computes the desired final list.
    """
    env = await _load(session, env_id)
    deduped: dict[str, str] = {}
    for raw in body.packages:
        spec = raw.strip()
        if spec:
            deduped[canonical_package_name(spec)] = spec
    packages = list(deduped.values())
    if packages != list(env.packages):
        env.packages = packages
        env.status = "pending"
        await session.commit()
        await session.refresh(env)
        background.add_task(build_environment, env.id)
    return _to_info(env, await _pool_name(session, env.runner_pool_id))


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
    return _to_info(env, await _pool_name(session, env.runner_pool_id))


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
    return _to_info(env, await _pool_name(session, env.runner_pool_id))


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
