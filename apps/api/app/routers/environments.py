import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import nodyra_nodes  # noqa: F401 - importing registers the built-in nodes
from app.db import get_session
from app.models import Environment, EnvironmentBuildJob, RunnerPool, User, Workflow
from app.schemas import (
    SUPPORTED_INTERPRETERS,
    SUPPORTED_PYTHON_VERSIONS,
    SUPPORTED_RUNTIME_FLAGS,
    EnvironmentBuildJobInfo,
    EnvironmentCreate,
    EnvironmentInfo,
    EnvironmentUpdate,
    PackageListRequest,
    PackageRequest,
    PackageUsageEntry,
    PackageUsageInfo,
    PackageUsagePackage,
    PageResponse,
)
from app.security import audit_recorder, optional_current_user, require_permission
from app.services.audit import AuditRecorder, log_audit
from app.services.environment_builds import (
    enqueue_environment_build,
    notify_environment_build_workers,
)
from app.services.package_preflight import bundled_packages
from app.tenancy import active_org_id
from nodyra.packages import canonical_package_name
from nodyra.sdk import registry as node_registry

logger = logging.getLogger(__name__)

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


def _to_build_job_info(job: EnvironmentBuildJob) -> EnvironmentBuildJobInfo:
    return EnvironmentBuildJobInfo.model_validate(job)


def _to_info(
    env: Environment,
    pool_name: str | None = None,
    build_job: EnvironmentBuildJob | None = None,
) -> EnvironmentInfo:
    return EnvironmentInfo(
        id=env.id,
        name=env.name,
        is_global=env.is_global,
        python_version=env.python_version,
        packages=list(env.packages),
        bundled_packages=sorted(bundled_packages()),
        status=env.status,
        status_detail=env.status_detail,
        description=env.description,
        runner_pool_size=env.runner_pool_size,
        runner_pool_max=env.runner_pool_max,
        effective_pool_max=_effective_pool_max(env),
        runner_pool_id=env.runner_pool_id,
        runner_pool_name=pool_name,
        worker_rss_estimate_bytes=env.worker_rss_estimate_bytes,
        backend=env.backend,
        backend_config=dict(env.backend_config or {}),
        interpreter=env.interpreter,
        runtime_flags=dict(env.runtime_flags or {}),
        build_job_id=build_job.id if build_job else None,
        build_job_status=build_job.status if build_job else None,
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


@router.get("", response_model=PageResponse[EnvironmentInfo])
async def list_environments(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    _user: User | None = Depends(optional_current_user),
):
    total = await session.scalar(select(func.count()).select_from(Environment))
    result = await session.scalars(
        select(Environment)
        .order_by(Environment.is_global.desc(), Environment.name)
        .offset(offset)
        .limit(limit)
    )
    envs = result.all()
    pool_ids = {e.runner_pool_id for e in envs if e.runner_pool_id}
    names: dict[str, str] = {}
    if pool_ids:
        pools = await session.scalars(select(RunnerPool).where(RunnerPool.id.in_(pool_ids)))
        names = {p.id: p.name for p in pools.all()}
    items = [_to_info(env, names.get(env.runner_pool_id or "")) for env in envs]
    return PageResponse(items=items, total=total or 0, limit=limit, offset=offset)


@router.post(
    "",
    response_model=EnvironmentInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def create_environment(
    body: EnvironmentCreate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    if body.backend not in {"venv", "conda", "pixi"}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "backend must be one of: venv, conda, pixi",
        )
    _validate_pool(body.runner_pool_size, body.runner_pool_max)
    await _validate_pool_ref(session, body.runner_pool_id)
    from app.services.isolation import validate_pool_assignment

    await validate_pool_assignment(session, active_org_id(), body.runner_pool_id)

    from app.services.licensing import enforce_resource_cap

    await enforce_resource_cap(session, "environments")

    env = Environment(
        name=body.name,
        python_version=body.python_version,
        packages=body.packages,
        description=body.description,
        runner_pool_size=body.runner_pool_size,
        runner_pool_max=body.runner_pool_max,
        runner_pool_id=body.runner_pool_id,
        backend=body.backend,
        backend_config=body.backend_config,
        interpreter=body.interpreter,
        runtime_flags=dict(body.runtime_flags),
        status="pending",
    )
    session.add(env)
    await log_audit(
        session,
        "create",
        "environment",
        detail=body.name,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.flush()
    build_job = await enqueue_environment_build(
        session,
        env,
        reason="create_environment",
        requested_by=actor,
    )
    await session.commit()
    await session.refresh(env)
    await session.refresh(build_job)
    await notify_environment_build_workers()
    return _to_info(env, await _pool_name(session, env.runner_pool_id), build_job)


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
        from app.services.isolation import validate_pool_assignment

        await validate_pool_assignment(session, env.org_id, body.runner_pool_id)
        env.runner_pool_id = body.runner_pool_id
    needs_rebuild = False
    if body.backend_config is not None:
        from app.schemas import _validate_backend_config

        try:
            _validate_backend_config(body.backend_config, env.interpreter)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        env.backend_config = body.backend_config
        needs_rebuild = True
    if body.runtime_flags is not None:
        # Spawn-time only: no rebuild needed, flags take effect for the next
        # worker the pool spawns for this environment.
        env.runtime_flags = dict(body.runtime_flags)
    await log_audit(
        session,
        "update",
        "environment",
        env.id,
        env.name,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    build_job: EnvironmentBuildJob | None = None
    if needs_rebuild:
        build_job = await enqueue_environment_build(
            session,
            env,
            reason="update_environment",
            requested_by=actor,
        )
    await session.commit()
    await session.refresh(env)
    if build_job is not None:
        await session.refresh(build_job)
        await notify_environment_build_workers()
    return _to_info(env, await _pool_name(session, env.runner_pool_id), build_job)


@router.get("/backends")
async def list_backends(
    _user: User | None = Depends(optional_current_user),
) -> dict:
    """Return server platform and available backends.

    conda and pixi are always available — their binaries auto-download on first use.
    Only Docker is greyed out when the daemon is unreachable.
    The ``platform`` field is used by the frontend PEP 508 marker evaluator.
    """
    import shutil
    import sys

    from app.services.backends.tools import TOOLS_DIR

    def _tool_version(name: str) -> str | None:
        suffix = ".exe" if sys.platform == "win32" else ""
        binary = TOOLS_DIR / f"{name}{suffix}"
        if binary.exists():
            return f"{name} (managed)"
        system = shutil.which(name)
        if system:
            return f"{name} (system)"
        return None

    uv_path = shutil.which("uv")
    micromamba_version = _tool_version("micromamba") or (
        "mamba (system)"
        if shutil.which("mamba")
        else "conda (system)"
        if shutil.which("conda")
        else None
    )
    pixi_version = _tool_version("pixi")
    docker_available = shutil.which("docker") is not None

    return {
        "platform": sys.platform,
        "supported_python_versions": list(SUPPORTED_PYTHON_VERSIONS),
        "supported_interpreters": {k: list(v) for k, v in SUPPORTED_INTERPRETERS.items()},
        "supported_runtime_flags": list(SUPPORTED_RUNTIME_FLAGS),
        "venv": {
            "available": uv_path is not None,
            "version": None,
            "managed": False,
        },
        "conda": {
            "available": True,
            "version": micromamba_version,
            "managed": micromamba_version is not None and "managed" in (micromamba_version or ""),
        },
        "pixi": {
            "available": True,
            "version": pixi_version,
            "managed": pixi_version is not None and "managed" in (pixi_version or ""),
        },
        "docker": {
            "available": docker_available,
            "version": None,
            "managed": False,
        },
    }


@router.get("/{env_id}", response_model=EnvironmentInfo)
async def get_environment(
    env_id: str,
    session: AsyncSession = Depends(get_session),
    _user: User | None = Depends(optional_current_user),
):
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
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
    actor: User | None = Depends(optional_current_user),
):
    env = await _load(session, env_id)
    packages = list(env.packages)
    build_job: EnvironmentBuildJob | None = None
    if body.package not in packages:
        packages.append(body.package)
        env.packages = packages
        build_job = await enqueue_environment_build(
            session,
            env,
            reason="add_package",
            requested_by=actor,
        )
        await session.commit()
        await session.refresh(env)
        await session.refresh(build_job)
        await notify_environment_build_workers()
        await audit("add_package", "environment", env.id, f"package={body.package}")
    return _to_info(env, await _pool_name(session, env.runner_pool_id), build_job)


def _node_requirements_by_type() -> dict[str, list[str]]:
    return {m.id: m.requirements for m in node_registry.manifests() if m.requirements}


@router.get("/{env_id}/package-usage", response_model=PackageUsageInfo)
async def package_usage(
    env_id: str,
    session: AsyncSession = Depends(get_session),
    _user: User | None = Depends(optional_current_user),
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
        stmt = stmt.where((Workflow.environment_id == env_id) | (Workflow.environment_id.is_(None)))
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
        packages=[PackageUsagePackage(package=k, used_by=v) for k, v in sorted(usage.items())]
    )


@router.put(
    "/{env_id}/packages",
    response_model=EnvironmentInfo,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def set_packages(
    env_id: str,
    body: PackageListRequest,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
    actor: User | None = Depends(optional_current_user),
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
    build_job: EnvironmentBuildJob | None = None
    if packages != list(env.packages):
        env.packages = packages
        build_job = await enqueue_environment_build(
            session,
            env,
            reason="set_packages",
            requested_by=actor,
        )
        await session.commit()
        await session.refresh(env)
        await session.refresh(build_job)
        await notify_environment_build_workers()
        await audit("set_packages", "environment", env.id, f"packages={packages}")
    return _to_info(env, await _pool_name(session, env.runner_pool_id), build_job)


@router.delete(
    "/{env_id}/packages/{package}",
    response_model=EnvironmentInfo,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def remove_package(
    env_id: str,
    package: str,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
    actor: User | None = Depends(optional_current_user),
):
    env = await _load(session, env_id)
    remaining = [p for p in env.packages if p != package]
    build_job: EnvironmentBuildJob | None = None
    if remaining != list(env.packages):
        env.packages = remaining
        build_job = await enqueue_environment_build(
            session,
            env,
            reason="remove_package",
            requested_by=actor,
        )
        await session.commit()
        await session.refresh(env)
        await session.refresh(build_job)
        await notify_environment_build_workers()
        await audit("remove_package", "environment", env.id, f"package={package}")
    return _to_info(env, await _pool_name(session, env.runner_pool_id), build_job)


@router.post(
    "/{env_id}/rebuild",
    response_model=EnvironmentInfo,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def rebuild_environment(
    env_id: str,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
    actor: User | None = Depends(optional_current_user),
):
    env = await _load(session, env_id)
    build_job = await enqueue_environment_build(
        session,
        env,
        reason="manual_rebuild",
        requested_by=actor,
    )
    await session.commit()
    await session.refresh(env)
    await session.refresh(build_job)
    await notify_environment_build_workers()
    await audit("rebuild", "environment", env.id, f"build_job={build_job.id}")
    return _to_info(env, await _pool_name(session, env.runner_pool_id), build_job)


@router.get("/{env_id}/build-jobs", response_model=PageResponse[EnvironmentBuildJobInfo])
async def list_environment_build_jobs(
    env_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    _user: User | None = Depends(optional_current_user),
):
    await _load(session, env_id)
    total = await session.scalar(
        select(func.count())
        .select_from(EnvironmentBuildJob)
        .where(EnvironmentBuildJob.environment_id == env_id)
    )
    rows = (
        await session.scalars(
            select(EnvironmentBuildJob)
            .where(EnvironmentBuildJob.environment_id == env_id)
            .order_by(EnvironmentBuildJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return PageResponse(
        items=[_to_build_job_info(job) for job in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/{env_id}/build-jobs/{job_id}", response_model=EnvironmentBuildJobInfo)
async def get_environment_build_job(
    env_id: str,
    job_id: str,
    session: AsyncSession = Depends(get_session),
    _user: User | None = Depends(optional_current_user),
):
    await _load(session, env_id)
    job = await session.scalar(
        select(EnvironmentBuildJob).where(
            EnvironmentBuildJob.id == job_id,
            EnvironmentBuildJob.environment_id == env_id,
        )
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Environment build job not found")
    return _to_build_job_info(job)


@router.delete(
    "/{env_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("environment:write"))],
)
async def delete_environment(
    env_id: str,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
):
    env = await _load(session, env_id)
    if env.is_global:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The global environment cannot be deleted")
    await audit("delete", "environment", env.id, f"name={env.name}")
    # Warm runtime workers hold the env's code (and on Windows its .pyd
    # files); force-drain them (in-flight runs included — a deleted env
    # cannot finish them) so the directory can be removed and no worker ever
    # serves a deleted environment again.
    try:
        from app.services.runtime_pool import pool as _runtime_pool

        await _runtime_pool.drain_env(env.id, force=True)
    except Exception:  # noqa: BLE001 - deletion must proceed regardless
        logger.exception("delete_environment: could not drain runtime workers")
    # Remove the venv directory itself. Without this, every deleted
    # environment leaked a multi-hundred-MB venv on disk forever. Runs
    # before the DB delete so the env row is still available for the
    # backend dispatcher; failures are best-effort.
    try:
        from app.services.backends import get_backend

        await get_backend(env).destroy(env.id)
    except Exception:  # noqa: BLE001 - best-effort; the row must go regardless
        logger.exception("delete_environment: could not remove environment directory")
    await session.delete(env)
    await session.commit()
