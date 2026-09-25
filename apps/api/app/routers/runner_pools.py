"""Runner pools: groups of remote execution agents.

Supports three provider types:
  agent      — outbound-WS daemon on a VM or EC2 instance
  docker     — API manages containers via the Docker SDK
  kubernetes — API creates K8s Jobs whose pods connect back as agents

Also hosts the batch-runs endpoint that dispatches a parameter matrix as
N parallel workflow runs.
"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, get_session
from app.models import (
    Artifact,
    Run,
    RunBatch,
    Runner,
    RunnerPool,
    RunQueueEntry,
    Workflow,
    WorkflowVersion,
)
from app.schemas import (
    DrainRequest,
    FleetSummary,
    RegistrationTokenRequest,
    RegistrationTokenResponse,
    RunBatchCreate,
    RunBatchInfo,
    RunHistoryBucket,
    RunnerFleetHealth,
    RunnerInfo,
    RunnerPoolCreate,
    RunnerPoolHealth,
    RunnerPoolInfo,
    RunnerPoolUpdate,
    RunnerUpdate,
    SSHOnboardRequest,
    SSHOnboardResponse,
)
from app.security import audit_recorder, require_permission
from app.services.artifacts import _artifact_path, atomic_write_bytes
from app.services.audit import AuditRecorder
from app.services.crypto import (
    decode_payload_token,
    encrypt_data,
)
from app.services.graph_utils import first_trigger_node
from app.services.licensing import Feature, require_feature
from app.services.remote_dispatch import dispatcher
from app.services.runner import cancel_run, start_run
from app.services.runner_tokens import mint_runner_registration
from app.services.ssh_onboard import onboard_machine

router = APIRouter(prefix="/runner-pools", tags=["runner-pools"])


# ---------------------------------------------------------------------------
# Wheel index (program A2) — lets a runner on a clean machine install the
# unpublished nodyra-* packages. Public: the wheels are the OSS nodyra packages
# (no secrets) and uv sends no auth header. Exempted in main._AUTH_EXEMPT_PREFIXES.
# ---------------------------------------------------------------------------


@router.get("/wheels", response_class=HTMLResponse, include_in_schema=False)
@router.get("/wheels/", response_class=HTMLResponse, include_in_schema=False)
async def runner_wheel_index() -> HTMLResponse:
    """A ``--find-links`` page the agent points uv at. Built on first hit."""
    from app.services.wheel_index import ensure_wheels

    wheels = await ensure_wheels()
    links = "\n".join(f'<a href="{w.name}">{w.name}</a><br>' for w in wheels)
    return HTMLResponse(f"<!doctype html><html><body>\n{links}\n</body></html>")


@router.get("/wheels/{filename}", include_in_schema=False)
async def runner_wheel_file(filename: str) -> FileResponse:
    from app.services.wheel_index import wheels_dir

    # Path-traversal guard: serve only a plain ``*.whl`` basename present in
    # the cache directory.
    if "/" in filename or "\\" in filename or not filename.endswith(".whl"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "wheel not found")
    target = wheels_dir() / filename
    if target.name != filename or not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "wheel not found")
    return FileResponse(target, media_type="application/octet-stream", filename=filename)


# ---------------------------------------------------------------------------
# Fleet health (program A6)
# ---------------------------------------------------------------------------


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@router.get("/health", response_model=RunnerFleetHealth)
async def runner_fleet_health(
    session: AsyncSession = Depends(get_session),
) -> RunnerFleetHealth:
    """Live fleet + per-pool health: capacity, queue depth, 24h success, and
    whether a dispatcher is actually leasing each pool's provider. Powers the
    Runner Pools health strip and the "no dispatcher reachable" banner."""
    from app.services.dispatcher_health import live_providers

    now = datetime.now(UTC)
    pools = (await session.scalars(select(RunnerPool))).all()
    runners = (await session.scalars(select(Runner))).all()
    runners_by_pool: dict[str, list[Runner]] = {}
    for runner in runners:
        runners_by_pool.setdefault(runner.pool_id, []).append(runner)

    # Queued depth + oldest available per pool (None key = local/in-process).
    queued_rows = (
        await session.execute(
            select(
                RunQueueEntry.runner_pool_id,
                func.count().label("n"),
                func.min(RunQueueEntry.available_at).label("oldest"),
            )
            .where(RunQueueEntry.status == "queued")
            .group_by(RunQueueEntry.runner_pool_id)
        )
    ).all()
    queued_by_pool = {pid: (n, oldest) for pid, n, oldest in queued_rows}

    # Label-mismatch: queued runs whose required_labels no online runner satisfies.
    # Fetch queued entries with their run's required_labels in one join.
    label_queued_rows = (
        await session.execute(
            select(RunQueueEntry.runner_pool_id, Run.required_labels)
            .join(Run, Run.id == RunQueueEntry.run_id)
            .where(
                RunQueueEntry.status == "queued",
                Run.required_labels.isnot(None),
            )
        )
    ).all()
    # Build online runner capabilities per pool for O(1) lookup.
    online_caps_by_pool: dict[str, list[dict]] = {}
    for r in runners:
        if r.status in ("online", "busy"):
            online_caps_by_pool.setdefault(r.pool_id, []).append(r.capabilities or {})
    label_mismatch_by_pool: dict[str, int] = {}
    for pid, req_labels in label_queued_rows:
        if not req_labels or pid is None:
            continue
        caps_list = online_caps_by_pool.get(pid, [])
        satisfiable = any(
            all(caps.get(k) == v for k, v in req_labels.items()) for caps in caps_list
        )
        if not satisfiable:
            label_mismatch_by_pool[pid] = label_mismatch_by_pool.get(pid, 0) + 1

    # 24h success rate per pool.
    cutoff = now - timedelta(hours=24)
    run_rows = (
        await session.execute(
            select(Run.runner_pool_id, Run.status, func.count().label("n"))
            .where(
                Run.finished_at >= cutoff,
                Run.status.in_(("success", "error", "timed_out")),
            )
            .group_by(Run.runner_pool_id, Run.status)
        )
    ).all()
    runs_by_pool: dict[str, dict[str, int]] = {}
    for pid, run_status, n in run_rows:
        status_key = "error" if run_status == "timed_out" else run_status
        pool_counts = runs_by_pool.setdefault(pid, {})
        pool_counts[status_key] = pool_counts.get(status_key, 0) + n

    in_flight = (
        await session.scalar(select(func.count()).select_from(Run).where(Run.status == "running"))
    ) or 0
    live = await live_providers()

    pool_healths: list[RunnerPoolHealth] = []
    runners_total = 0
    runners_online = 0
    for pool in pools:
        prunners = runners_by_pool.get(pool.id, [])
        runners_total += len(prunners)
        online = sum(1 for r in prunners if r.status in ("online", "busy"))
        runners_online += online
        cap_used = sum(r.current_runs for r in prunners)
        cap_total = sum(r.max_concurrent_runs for r in prunners) or (pool.max_concurrent_runs)
        qn, oldest = queued_by_pool.get(pool.id, (0, None))
        oldest_aware = _as_utc(oldest)
        oldest_secs = (now - oldest_aware).total_seconds() if oldest_aware else None
        stats = runs_by_pool.get(pool.id, {})
        succeeded = stats.get("success", 0)
        finished = succeeded + stats.get("error", 0)
        pool_healths.append(
            RunnerPoolHealth(
                pool_id=pool.id,
                provider=pool.provider,
                queue_depth=qn,
                oldest_queued_seconds=oldest_secs,
                capacity_used=cap_used,
                capacity_total=cap_total,
                online_count=online,
                runner_count=len(prunners),
                success_24h=(succeeded / finished) if finished else None,
                dispatcher_reachable=pool.provider in live,
                label_mismatch_queued=label_mismatch_by_pool.get(pool.id, 0),
            )
        )

    total_queue = sum(n for n, _ in queued_by_pool.values())
    stuck = sorted(
        {h.provider for h in pool_healths if h.queue_depth > 0 and not h.dispatcher_reachable}
    )
    # Local (no-pool) entries stuck when no dispatcher leases "local".
    if queued_by_pool.get(None, (0, None))[0] > 0 and "local" not in live:
        stuck = sorted({*stuck, "local"})

    return RunnerFleetHealth(
        fleet=FleetSummary(
            runners_online=runners_online,
            runners_total=runners_total,
            queue_depth=total_queue,
            in_flight=in_flight,
            providers_dispatchable=sorted(live),
            providers_stuck=stuck,
        ),
        pools=pool_healths,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def _extract_aws_secret(pool: RunnerPool, provider_config: dict) -> dict:
    """Pop aws_secret_access_key from provider_config and store it encrypted.

    Also re-encrypts the migration sentinel (plaintext prefixed with
    ``__migrated__``) written by migration 0068 when Fernet key wasn't
    available at migration time.
    """
    secret = provider_config.pop("aws_secret_access_key", None)
    if secret is not None:
        pool.aws_secret_key_enc = encrypt_data({"key": secret})
    elif (pool.aws_secret_key_enc or "").startswith("__migrated__"):
        plaintext = pool.aws_secret_key_enc[len("__migrated__") :]
        pool.aws_secret_key_enc = encrypt_data({"key": plaintext})
    return provider_config


_DOCKER_HOST_SCHEMES = ("tcp://", "ssh://", "unix://", "npipe://")


def _validate_docker_pool_config(cfg: dict) -> None:
    from app.config import settings as _s

    host = cfg.get("docker_host") or ""
    if host and not host.startswith(_DOCKER_HOST_SCHEMES):
        raise HTTPException(422, f"docker_host must start with one of {_DOCKER_HOST_SCHEMES}")
    rc = cfg.get("docker_runner") or {}
    if rc:
        cpu = float(rc.get("cpu", 1.0))
        if not (0 < cpu <= _s.sandbox_max_cpu):
            raise HTTPException(422, f"cpu must be in (0, {_s.sandbox_max_cpu}]")
        mem = int(rc.get("memory_mb", 1024))
        if not (128 <= mem <= _s.sandbox_max_memory_mb):
            raise HTTPException(422, f"memory_mb must be in [128, {_s.sandbox_max_memory_mb}]")
    auto = cfg.get("docker_autoscale") or {}
    if auto:
        mn = int(auto.get("min_runners", 0))
        mx = int(auto.get("max_runners", 4))
        if not (0 <= mn <= mx <= 32):
            raise HTTPException(422, "require 0 <= min_runners <= max_runners <= 32")
        if int(auto.get("idle_seconds", 300)) < 30:
            raise HTTPException(422, "idle_seconds must be >= 30")


def _pool_info(pool: RunnerPool, runners: list[Runner]) -> RunnerPoolInfo:
    online = sum(1 for r in runners if r.status in ("online", "busy"))
    ghost_count = sum(1 for r in runners if r.last_seen_at is None and r.status == "offline")
    return RunnerPoolInfo(
        id=pool.id,
        name=pool.name,
        provider=pool.provider,
        provider_config=pool.provider_config or {},
        max_concurrent_runs=pool.max_concurrent_runs,
        runner_count=len(runners),
        online_count=online,
        ghost_count=ghost_count,
        aws_secret_configured=pool.aws_secret_key_enc is not None,
        created_at=pool.created_at,
        updated_at=pool.updated_at,
    )


def _runner_info(runner: Runner) -> RunnerInfo:
    return RunnerInfo(
        id=runner.id,
        pool_id=runner.pool_id,
        name=runner.name,
        status=runner.status,
        capabilities=runner.capabilities or {},
        last_seen_at=runner.last_seen_at,
        current_runs=runner.current_runs,
        max_concurrent_runs=runner.max_concurrent_runs,
        cached_env_ids=runner.cached_env_ids or [],
        created_at=runner.created_at,
        updated_at=runner.updated_at,
        token_expires_at=runner.token_expires_at,
        ssh_host=runner.ssh_host,
    )


@router.get("", response_model=list[RunnerPoolInfo])
async def list_runner_pools(
    session: AsyncSession = Depends(get_session),
) -> list[RunnerPoolInfo]:
    pools = (await session.scalars(select(RunnerPool).order_by(RunnerPool.created_at))).all()
    if not pools:
        return []
    # Batch-load all runners in one query instead of N+1.
    pool_ids = [p.id for p in pools]
    runners = (await session.scalars(select(Runner).where(Runner.pool_id.in_(pool_ids)))).all()
    by_pool: dict[str, list[Runner]] = {}
    for r in runners:
        by_pool.setdefault(r.pool_id, []).append(r)
    return [_pool_info(p, by_pool.get(p.id, [])) for p in pools]


@router.post(
    "",
    response_model=RunnerPoolInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_permission("runner_pool:write")),
        Depends(require_feature(Feature.DEDICATED_POOLS)),
    ],
)
async def create_runner_pool(
    body: RunnerPoolCreate,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> RunnerPoolInfo:
    from app.services.licensing import enforce_resource_cap

    await enforce_resource_cap(session, "runners")

    cfg = dict(body.provider_config or {})
    # Same validation the PATCH path runs — otherwise a pool could be CREATED in
    # one call with an out-of-band docker_host or unbounded per-container
    # resource envelope that spawn_docker_runner would later apply verbatim.
    _validate_docker_pool_config(cfg)
    pool = RunnerPool(
        name=body.name,
        provider=body.provider,
        provider_config=cfg,
        max_concurrent_runs=body.max_concurrent_runs,
    )
    pool.provider_config = _extract_aws_secret(pool, cfg)
    session.add(pool)
    await session.commit()
    await session.refresh(pool)
    await audit(
        "create",
        "runner_pool",
        pool.id,
        f"name={pool.name} provider={pool.provider} max_concurrent_runs={pool.max_concurrent_runs}",
    )
    return _pool_info(pool, [])


@router.get("/{pool_id}", response_model=RunnerPoolInfo)
async def get_runner_pool(
    pool_id: str, session: AsyncSession = Depends(get_session)
) -> RunnerPoolInfo:
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    runners = (await session.scalars(select(Runner).where(Runner.pool_id == pool_id))).all()
    return _pool_info(pool, list(runners))


@router.patch(
    "/{pool_id}",
    response_model=RunnerPoolInfo,
    dependencies=[
        Depends(require_permission("runner_pool:write")),
        Depends(require_feature(Feature.DEDICATED_POOLS)),
    ],
)
async def update_runner_pool(
    pool_id: str,
    body: RunnerPoolUpdate,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> RunnerPoolInfo:
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    if body.name is not None:
        pool.name = body.name
    if body.provider_config is not None:
        _validate_docker_pool_config(body.provider_config)
        cfg = dict(body.provider_config)
        pool.provider_config = _extract_aws_secret(pool, cfg)
    if body.max_concurrent_runs is not None:
        pool.max_concurrent_runs = body.max_concurrent_runs
    pool.updated_at = datetime.now(UTC)
    await session.commit()
    await audit(
        "update",
        "runner_pool",
        pool.id,
        "fields=" + ",".join(sorted(body.model_dump(exclude_unset=True))),
    )
    runners = (await session.scalars(select(Runner).where(Runner.pool_id == pool_id))).all()
    return _pool_info(pool, list(runners))


@router.delete(
    "/{pool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(require_permission("runner_pool:write")),
        Depends(require_feature(Feature.DEDICATED_POOLS)),
    ],
)
async def delete_runner_pool(
    pool_id: str,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> None:
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    await audit("delete", "runner_pool", pool.id, f"name={pool.name} provider={pool.provider}")
    await session.delete(pool)
    await session.commit()


# ---------------------------------------------------------------------------
# Runners sub-resource
# ---------------------------------------------------------------------------


@router.get("/{pool_id}/runners", response_model=list[RunnerInfo])
async def list_runners(
    pool_id: str, session: AsyncSession = Depends(get_session)
) -> list[RunnerInfo]:
    if await session.get(RunnerPool, pool_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    runners = (
        await session.scalars(
            select(Runner).where(Runner.pool_id == pool_id).order_by(Runner.created_at)
        )
    ).all()
    return [_runner_info(r) for r in runners]


@router.delete(
    "/{pool_id}/runners/{runner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def delete_runner(
    pool_id: str,
    runner_id: str,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> None:
    runner = await session.get(Runner, runner_id)
    if runner is None or runner.pool_id != pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner not found")
    await audit("delete", "runner", runner.id, f"pool={pool_id} name={runner.name}")
    await session.delete(runner)
    await session.commit()


@router.patch(
    "/{pool_id}/runners/{runner_id}",
    response_model=RunnerInfo,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def update_runner(
    pool_id: str,
    runner_id: str,
    body: RunnerUpdate,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> RunnerInfo:
    """Update editable machine metadata for a registered runner.

    Useful after the agent has self-registered with the default placeholder
    name so the operator can rename it ("ci-worker-3"), tune its concurrency,
    or attach labels (``{"region": "eu", "gpu": "a100"}``) used by future
    label-aware dispatch policies.
    """
    runner = await session.get(Runner, runner_id)
    if runner is None or runner.pool_id != pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner not found")
    if body.name is not None:
        runner.name = body.name
    if body.max_concurrent_runs is not None:
        runner.max_concurrent_runs = body.max_concurrent_runs
    if body.capabilities is not None:
        runner.capabilities = body.capabilities
    runner.updated_at = datetime.now(UTC)
    await session.commit()
    await audit(
        "update",
        "runner",
        runner.id,
        "fields=" + ",".join(sorted(body.model_dump(exclude_unset=True))),
    )
    await session.refresh(runner)
    return _runner_info(runner)


# ---------------------------------------------------------------------------
# Registration token
# ---------------------------------------------------------------------------


@router.post(
    "/{pool_id}/registration-tokens",
    response_model=RegistrationTokenResponse,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def create_registration_token(
    pool_id: str,
    request: Request,
    body: RegistrationTokenRequest | None = None,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> RegistrationTokenResponse:
    """Generate a registration token for a new agent runner.

    The optional body lets the operator capture machine details (name, max
    concurrent runs, free-form capability labels) up-front so the placeholder
    row is already populated when the agent connects.

    The agent uses this token to connect to /ws/runners/{runner_id} and to
    upload artifacts. The token is a *reusable* runner credential valid for
    ``settings.runner_token_ttl_days`` (default 365 days) — not single-use —
    because an SSH-onboarded agent reuses the same token across restarts. It is
    bound to one runner (``sub`` = runner id) and is revocable: deleting the
    runner row invalidates the token everywhere (RP-1).
    """
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")

    body = body or RegistrationTokenRequest()
    runner, token, expires_at = await mint_runner_registration(
        session,
        pool_id,
        org_id=pool.org_id,
        name=(body.name or f"runner-{pool.name[:20]}"),
        max_concurrent_runs=body.max_concurrent_runs or 1,
        capabilities=body.capabilities or {},
    )
    # Prefer the operator-configured public URL; fall back to the URL this
    # request came in on (correct in single-host setups). The web origin is
    # never used — a runner must reach the API directly, not the SPA.
    api_url = (settings.public_api_url or "").rstrip("/") or str(request.base_url).rstrip("/")
    # A registration token lets a new host join the pool and execute workflow
    # code; minting one is privileged even though the token is short-lived.
    await audit(
        "mint_registration_token",
        "runner",
        runner.id,
        f"pool={pool_id} expires_at={expires_at}",
    )
    return RegistrationTokenResponse(
        token=token, runner_id=runner.id, expires_at=expires_at, api_url=api_url
    )


# ---------------------------------------------------------------------------
# Docker runners
# ---------------------------------------------------------------------------


@router.post(
    "/{pool_id}/docker-runners",
    response_model=RunnerInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def add_docker_runner(
    pool_id: str,
    body: dict | None = None,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> RunnerInfo:
    from app.services import docker_workers

    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    if pool.provider != "agent":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Docker runners attach to agent pools")
    try:
        runner = await docker_workers.spawn_docker_runner(
            session, pool, name=(body or {}).get("name")
        )
    except docker_workers.DaemonUnreachable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    await audit("create", "runner", runner.id, f"pool={pool_id} provider=docker")
    return _runner_info(runner)


@router.delete(
    "/{pool_id}/docker-runners/{runner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def remove_docker_runner_ep(
    pool_id: str,
    runner_id: str,
    force: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> None:
    from app.services import docker_workers

    runner = await session.get(Runner, runner_id)
    if runner is None or runner.pool_id != pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner not found")
    pool = await session.get(RunnerPool, pool_id)
    client = None
    try:
        client = docker_workers._docker_client((pool.provider_config or {}) if pool else {})
    except docker_workers.DaemonUnreachable:
        pass
    try:
        await docker_workers.remove_docker_runner(session, runner, client=client, force=force)
    except docker_workers.RunnerBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except docker_workers.DaemonUnreachable as exc:
        # Removing would orphan a live container (daemon down). Surface a
        # retryable 502 — matching the spawn endpoint — not an opaque 500.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    await audit("delete", "runner", runner_id, f"pool={pool_id} provider=docker force={force}")


# ---------------------------------------------------------------------------
# SSH onboarding
# ---------------------------------------------------------------------------


@router.post(
    "/{pool_id}/ssh-onboard",
    response_model=SSHOnboardResponse,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def ssh_onboard(
    pool_id: str,
    body: SSHOnboardRequest,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> SSHOnboardResponse:
    """SSH into a host, install + register + start ``nodyra-runner``, and add
    it to this (agent) pool. SSH credentials are stored encrypted on the runner
    row so the machine can be restarted later."""
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    if pool.provider != "agent":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "SSH onboarding only applies to agent pools",
        )

    api_url = body.api_url or getattr(settings, "public_api_url", None)
    if not api_url:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "api_url is required (the URL the runner connects back to)",
        )

    name = body.name or f"ssh-{body.host}"
    runner, token, expires_at = await mint_runner_registration(
        session,
        pool_id,
        org_id=pool.org_id,
        name=name,
        max_concurrent_runs=body.max_concurrent_runs or 1,
        capabilities=body.capabilities or {},
    )

    try:
        install_log = await onboard_machine(body, api_url, token, name)
    except Exception as exc:  # noqa: BLE001 - cleanup the placeholder runner
        await session.delete(runner)
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Persist the SSH credentials (encrypted) for later restart / re-provision.
    runner.ssh_host = f"{body.username}@{body.host}:{body.port}"
    runner.ssh_credentials = encrypt_data(
        {
            "host": body.host,
            "port": body.port,
            "username": body.username,
            "auth_method": body.auth_method,
            "password": body.password,
            "private_key": body.private_key,
            "passphrase": body.passphrase,
            "use_systemd": body.use_systemd,
        }
    )
    await session.commit()

    # Onboarding installs and starts the Nodyra agent on a remote host over
    # SSH. Credentials are never recorded — only who onboarded which host.
    await audit(
        "ssh_onboard",
        "runner",
        runner.id,
        f"pool={pool_id} host={body.host}:{body.port} "
        f"username={body.username} auth_method={body.auth_method}",
    )
    return SSHOnboardResponse(runner_id=runner.id, runner_name=name, install_log=install_log)


# ---------------------------------------------------------------------------
# WebSocket — agent runner connection
# ---------------------------------------------------------------------------


@router.websocket("/ws/runners/{runner_id}")
async def runner_ws(
    runner_id: str,
    ws: WebSocket,
    token: str = Query(...),
) -> None:
    """WebSocket endpoint for agent runners to connect and receive run assignments."""
    payload = decode_payload_token(token)
    if (
        payload is None
        or payload.get("sub") != runner_id
        or payload.get("kind") not in ("runner_registration", "k8s_run")
    ):
        await ws.close(code=1008)
        return

    # For K8s single-run agents, use the dedicated handler.
    if payload.get("kind") == "k8s_run":
        await ws.accept()
        from app.tenancy import run_as_org

        with run_as_org(str(payload.get("org_id") or "") or None):
            await dispatcher.handle_k8s_runner_connect(runner_id, ws)
        return

    # Runner-token requests have no user/org header. Resolve the globally unique
    # runner id without request scoping, then pin the long-lived connection to
    # the runner's verified org. Merely setting skip_org_filter is insufficient
    # on Postgres because RLS still applies underneath.
    from app.tenancy import run_as_org, run_as_system

    with run_as_system():
        async with SessionLocal() as session:
            runner = await session.get(Runner, runner_id)
    if runner is None:
        await ws.close(code=1008)
        return
    if payload.get("pool_id") != runner.pool_id:
        await ws.close(code=1008)
        return
    token_org = payload.get("org_id")
    if token_org is not None and str(token_org) != runner.org_id:
        await ws.close(code=1008)
        return

    await ws.accept()
    with run_as_org(runner.org_id):
        await dispatcher.handle_runner_connect(runner_id, ws)


# ---------------------------------------------------------------------------
# Artifact upload (runner-authenticated)
# ---------------------------------------------------------------------------


@router.get("/artifacts/input")
async def download_run_input(
    run_id: str = Query(...),
    artifact_id: str = Query(...),
    input_kind: Literal["upload", "artifact"] = Query(default="upload"),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Serve only files already authorized and staged for this assigned run."""
    from app.services.artifacts import make_artifact_store
    from app.tenancy import run_as_system

    token = (
        authorization.removeprefix("Bearer ")
        if authorization and authorization.startswith("Bearer ")
        else ""
    )
    payload = decode_payload_token(token) if token else None
    if payload is None or payload.get("kind") != "runner_registration":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid runner token")
    with run_as_system():
        runner = await session.get(Runner, payload.get("sub"))
        run = await session.get(Run, run_id)
    if runner is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Runner has been revoked")
    if payload.get("pool_id") not in (None, runner.pool_id) or payload.get("org_id") not in (
        None,
        runner.org_id,
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Runner token scope mismatch")
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if run.org_id != runner.org_id or run.runner_id != runner.id or run.status != "running":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Runner is not assigned to this active run")
    store = make_artifact_store(run_id, org_id=run.org_id)
    try:
        directory = (
            store.upload_path(artifact_id)
            if input_kind == "upload"
            else store.input_path({"artifact_id": artifact_id}).parent
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    files = (
        [
            p
            for p in directory.iterdir()
            if p.is_file() and not p.is_symlink() and not p.name.startswith(".")
        ]
        if directory.is_dir()
        else []
    )
    if len(files) != 1:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File was not staged for this run")
    path = files[0]

    def checksum():
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()

    return FileResponse(
        path,
        filename=path.name,
        headers={
            "X-Nodyra-Artifact-Name": path.name,
            "X-Nodyra-Checksum-SHA256": await asyncio.to_thread(checksum),
            "Cache-Control": "no-store",
        },
    )


@router.post("/artifact-upload", status_code=status.HTTP_201_CREATED)
async def upload_artifact(
    run_id: str = Query(...),
    node_id: str = Query(...),
    artifact_id: str = Query(...),
    name: str = Query(...),
    storage_key: str = Query(...),
    content_type: str = Query(default="application/octet-stream"),
    kind: str = Query(default="binary"),
    size_bytes: int = Query(default=0),
    data: UploadFile = File(...),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Upload an artifact from a remote runner.

    Authentication: ``Authorization: Bearer <runner_registration_token>``.
    Writes the bytes under the API's artifact dir and upserts the metadata
    row. Idempotent on ``artifact_id`` so the upload and the run_event that
    references the artifact can arrive in any order.
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Runner token required")
    payload = decode_payload_token(authorization.removeprefix("Bearer "))
    if payload is None or payload.get("kind") != "runner_registration":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid runner token")

    # RP-1: deleting a runner row revokes its registration token everywhere. The
    # WebSocket path already rejects unknown runners; mirror that here so a
    # leaked/rotated token can be revoked immediately (by deleting the runner)
    # instead of staying valid until its 24h expiry.
    # Runner auth uses runner-registration tokens, not X-Org-Id — query
    # org-blind so the lookup works regardless of the request's org context.
    from app.tenancy import run_as_system

    with run_as_system():
        runner = await session.get(Runner, payload.get("sub"))
        run = await session.get(Run, run_id)
        existing = await session.get(Artifact, artifact_id)
    if runner is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Runner has been revoked")
    if payload.get("pool_id") not in (None, runner.pool_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Runner token pool mismatch")
    token_org = payload.get("org_id")
    if token_org is not None and str(token_org) != runner.org_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Runner token organization mismatch")

    try:
        path = _artifact_path(storage_key)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # RP-2: bind the upload to the run's assigned runner. Once a run has been
    # dispatched to a specific agent (remote_dispatch sets ``run.runner_id``),
    # only that runner's token may write its artifacts — a different connected
    # runner with a valid registration token must not be able to inject
    # artifacts into someone else's run. Runs not yet assigned have
    # ``runner_id is None`` (assignment happens before execution, so artifacts
    # always arrive after) and are not bound here.
    # Runner auth is the gate here, not the request org context (runners send
    # no X-Org-Id) — look the run up org-blind or non-default orgs would 404.
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if runner.org_id != run.org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Runner and run organizations differ")
    if run.runner_id != payload.get("sub"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Runner is not assigned to this run")
    # Phase F: a runner may only write inside its run's org namespace — a
    # compromised runner token must not plant bytes under another tenant's
    # prefix. Legacy unprefixed keys are rejected too once MT is on; the
    # runner protocol ships artifact_key_prefix with every assignment.
    if settings.multi_tenancy_enabled and run.org_id:
        required_prefix = f"{run.org_id}/runs/{run_id}/"
        if not storage_key.startswith(required_prefix):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Artifact storage key must be namespaced under the assigned run.",
            )
    elif not storage_key.startswith((f"runs/{run_id}/", f"{run.org_id}/runs/{run_id}/")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Artifact storage key must be namespaced under the assigned run.",
        )
    if existing is not None and (
        existing.org_id != run.org_id
        or existing.run_id != run_id
        or existing.node_id != node_id
        or existing.storage_key != storage_key
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Artifact id belongs to another run")

    max_bytes = settings.max_artifact_bytes
    body = await data.read(max_bytes + 1 if max_bytes > 0 else -1)
    if max_bytes > 0 and len(body) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Artifact exceeds maximum size of {max_bytes} bytes",
        )
    if size_bytes not in (0, len(body)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Declared artifact size does not match the uploaded bytes",
        )
    # Write to a sibling temporary file then atomically replace the destination.
    # A process crash must not leave a truncated artifact that still has a
    # durable metadata row claiming the upload succeeded. Keep blocking disk IO
    # off the request event loop.
    import asyncio

    await asyncio.to_thread(atomic_write_bytes, path, body)

    if existing is None:
        session.add(
            Artifact(
                id=artifact_id,
                run_id=run_id,
                # Runner uploads authenticate with a runner token and send no
                # X-Org-Id, so the before_flush stamp hook would file this under
                # the default org. Stamp the run's org explicitly so the artifact
                # is visible to its owning tenant.
                org_id=run.org_id,
                node_id=node_id,
                name=name,
                kind=kind,
                content_type=content_type,
                size_bytes=len(body),
                storage_backend="local",
                storage_key=storage_key,
                artifact_metadata={},
                preview=None,
            )
        )
    else:
        existing.name = name
        existing.kind = kind
        existing.content_type = content_type
        existing.size_bytes = len(body)
    await session.commit()

    return {"artifact_id": artifact_id, "status": "accepted"}


# ---------------------------------------------------------------------------
# Parameter matrix batch runs
# ---------------------------------------------------------------------------


@router.post("/workflows/{workflow_id}/batch-runs", status_code=status.HTTP_201_CREATED)
async def create_batch_run(
    workflow_id: str,
    body: RunBatchCreate,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
    _: None = Depends(require_permission("workflow:run")),
) -> dict:
    """Dispatch a parameter matrix as N parallel workflow runs.

    Each entry in ``body.parameters`` spawns one Run with those parameters
    merged into the trigger node's ``main`` input. All runs are grouped under
    a ``RunBatch`` row for progress tracking.
    """
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")

    version = (
        await session.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version.desc())
            .limit(1)
        )
    ).first()
    if version is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Workflow has no published version")
    graph_dict = version.graph or {"nodes": [], "edges": []}

    # Pick the trigger node for seeding each run.
    trigger = first_trigger_node(graph_dict, prefer_manual=True)
    if trigger is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Workflow needs a trigger to run.")
    trigger_id = trigger.id if hasattr(trigger, "id") else trigger["id"]

    runner_pool_id = body.runner_pool_id or workflow.default_runner_pool_id
    # X4 write-time check: a dedicated_pool org cannot point batch runs at a
    # foreign or non-container pool (the dispatch gate would refuse anyway;
    # this fails the whole batch up front with a clear error).
    from app.services.isolation import validate_pool_assignment

    await validate_pool_assignment(session, workflow.org_id, runner_pool_id)

    batch = RunBatch(
        workflow_id=workflow_id,
        runner_pool_id=runner_pool_id,
        status="running",
        total_runs=len(body.parameters),
        parameters=body.parameters,
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)

    run_ids: list[str] = []
    try:
        for params in body.parameters:
            run_id = await start_run(
                workflow_id,
                graph_dict,
                version.version,
                workflow_version_id=version.id,
                mode="batch",
                trigger_type="batch",
                trigger_node_id=body.trigger_node_id or trigger_id,
                parameters=params,
                batch_id=batch.id,
                runner_pool_id=runner_pool_id,
                required_labels=body.required_labels,
            )
            run_ids.append(run_id)
    except Exception:
        # A batch is an all-or-cancel operation. If admission fails part-way
        # through, stop already-created children and retain an inspectable error
        # batch rather than leaving a permanently "running" partial batch.
        for created_run_id in run_ids:
            await cancel_run(created_run_id)
        async with SessionLocal() as failed_session:
            failed_batch = await failed_session.get(RunBatch, batch.id)
            if failed_batch is not None:
                failed_batch.status = "error"
                failed_batch.finished_at = datetime.now(UTC)
                from app.services.run_batches import reconcile_batch

                await reconcile_batch(failed_session, failed_batch.id)
                await failed_session.commit()
        raise

    from app.services.run_batches import reconcile_batch

    async with SessionLocal() as reconcile_session:
        await reconcile_batch(reconcile_session, batch.id)
        await reconcile_session.commit()

    await audit("create", "run_batch", batch.id, f"workflow={workflow_id} runs={len(run_ids)}")
    return {
        "batch_id": batch.id,
        "run_ids": run_ids,
        "total": len(run_ids),
    }


@router.get("/run-batches/{batch_id}", response_model=RunBatchInfo)
async def get_batch(batch_id: str, session: AsyncSession = Depends(get_session)) -> RunBatchInfo:
    from app.services.run_batches import reconcile_batch

    batch = await reconcile_batch(session, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    await session.commit()
    return RunBatchInfo(
        id=batch.id,
        workflow_id=batch.workflow_id,
        deployment_id=batch.deployment_id,
        runner_pool_id=batch.runner_pool_id,
        status=batch.status,
        total_runs=batch.total_runs,
        succeeded_runs=batch.succeeded_runs,
        failed_runs=batch.failed_runs,
        cancelled_runs=batch.cancelled_runs,
        created_at=batch.created_at,
        finished_at=batch.finished_at,
    )


@router.post("/run-batches/{batch_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_batch(
    batch_id: str,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
    _: None = Depends(require_permission("workflow:run")),
) -> dict:
    batch = await session.get(RunBatch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")

    from app.services.runner import cancel_run  # noqa: PLC0415

    queued_runs = (
        await session.scalars(
            select(Run).where(
                Run.batch_id == batch_id,
                Run.status.in_(["queued", "running"]),
            )
        )
    ).all()
    # Recorded before the cancellations start: the recorder commits this
    # request's session, and each cancel_run tears its run down through a
    # separate session, so auditing afterwards would put a write transaction in
    # the way of that teardown (see cancel_workflow_run). What an auditor needs
    # is that this operator stopped this batch, which is true either way.
    await audit("cancel", "run_batch", batch_id, f"cancelling {len(queued_runs)} run(s)")
    cancelled = 0
    for run in queued_runs:
        await cancel_run(run.id)
        cancelled += 1

    from app.services.run_batches import reconcile_batch

    await reconcile_batch(session, batch.id)
    batch.status = "cancelled"
    batch.finished_at = datetime.now(UTC)
    await session.commit()
    return {"batch_id": batch_id, "cancelled_runs": cancelled}


# ---------------------------------------------------------------------------
# Drain mode
# ---------------------------------------------------------------------------

_restart_attempts: dict[str, list[float]] = {}
_RESTART_WINDOW_SECS = 600  # 10 minutes
_RESTART_MAX = 3


def _check_restart_rate(runner_id: str) -> None:
    import time

    now = time.monotonic()
    attempts = [t for t in _restart_attempts.get(runner_id, []) if now - t < _RESTART_WINDOW_SECS]
    if len(attempts) >= _RESTART_MAX:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many restarts: max {_RESTART_MAX} per {_RESTART_WINDOW_SECS // 60} minutes",
        )
    attempts.append(now)
    _restart_attempts[runner_id] = attempts


@router.post(
    "/{pool_id}/runners/{runner_id}/drain",
    response_model=RunnerInfo,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def drain_runner(
    pool_id: str,
    runner_id: str,
    body: DrainRequest,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> RunnerInfo:
    """Toggle drain mode on a runner.

    A draining runner is skipped by the dispatcher for new run assignments.
    Once current_runs reaches 0, the runner transitions to ``offline``
    automatically. Set ``draining: false`` to bring it back online immediately.
    """
    runner = await session.get(Runner, runner_id)
    if runner is None or runner.pool_id != pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner not found")
    if body.draining:
        runner.status = "draining"
    else:
        # Bring back online only if the runner was previously reachable.
        runner.status = "online" if runner.last_seen_at is not None else "offline"
    runner.updated_at = datetime.now(UTC)
    await session.commit()
    await audit(
        "drain" if body.draining else "undrain",
        "runner",
        runner.id,
        f"pool={pool_id} status={runner.status}",
    )
    await session.refresh(runner)
    return _runner_info(runner)


# ---------------------------------------------------------------------------
# SSH restart
# ---------------------------------------------------------------------------


@router.post(
    "/{pool_id}/runners/{runner_id}/restart",
    response_model=dict,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def restart_runner(
    pool_id: str,
    runner_id: str,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> dict:
    """SSH into a previously onboarded runner and restart the nodyra-runner service.

    Requires that SSH credentials were persisted during onboarding.
    Rate-limited to 3 attempts per 10 minutes per runner.
    """
    from app.services.crypto import decrypt_data
    from app.services.ssh_onboard import onboard_restart

    runner = await session.get(Runner, runner_id)
    if runner is None or runner.pool_id != pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner not found")
    if not runner.ssh_credentials:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "No SSH credentials stored for this runner"
        )
    _check_restart_rate(runner_id)
    creds = decrypt_data(runner.ssh_credentials)
    try:
        log = await onboard_restart(creds)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await audit("restart", "runner", runner_id, f"pool={pool_id}")
    return {"runner_id": runner_id, "log": log}


# ---------------------------------------------------------------------------
# Ghost cleanup
# ---------------------------------------------------------------------------


@router.post(
    "/{pool_id}/cleanup-ghosts",
    response_model=dict,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def cleanup_pool_ghosts(
    pool_id: str,
    session: AsyncSession = Depends(get_session),
    audit: AuditRecorder = Depends(audit_recorder),
) -> dict:
    """Delete ghost runners: offline rows that were never registered.

    A ghost runner was created by a token mint but the agent never connected
    (``last_seen_at IS NULL``) and the row is older than ``runner_ghost_ttl_hours``.
    """
    from app.services.ghost_cleanup import cleanup_ghost_runners

    deleted = await cleanup_ghost_runners(session, pool_id=pool_id)
    await audit("cleanup_ghosts", "runner_pool", pool_id, f"deleted={deleted}")
    return {"pool_id": pool_id, "deleted": deleted}


# ---------------------------------------------------------------------------
# Run history / recent runs
# ---------------------------------------------------------------------------


@router.get("/{pool_id}/run-history", response_model=list[RunHistoryBucket])
async def runner_pool_run_history(
    pool_id: str,
    hours: int = Query(default=24, ge=1, le=168),
    buckets: int = Query(default=24, ge=1, le=168),
    session: AsyncSession = Depends(get_session),
) -> list[RunHistoryBucket]:
    """Time-bucketed run counts for a pool over the past N hours.

    Returns ``buckets`` equally-sized intervals. Uses Python-side bucketing
    for SQLite compatibility (no ``date_trunc`` required).
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=hours)
    bucket_secs = (hours * 3600) / buckets

    rows = (
        await session.execute(
            select(Run.status, Run.finished_at, Run.started_at).where(
                Run.runner_pool_id == pool_id,
                Run.finished_at >= cutoff,
                Run.status.in_(("success", "error", "timed_out")),
            )
        )
    ).all()

    result: list[dict] = [
        {
            "bucket_start": cutoff + timedelta(seconds=i * bucket_secs),
            "success": 0,
            "error": 0,
            "durations": [],
        }
        for i in range(buckets)
    ]

    for run_status, finished_at, started_at in rows:
        if finished_at is None:
            continue
        finished_aware = _as_utc(finished_at)
        if finished_aware is None:
            continue
        offset_secs = (finished_aware - cutoff).total_seconds()
        idx = min(int(offset_secs // bucket_secs), buckets - 1)
        status_key = "error" if run_status == "timed_out" else run_status
        result[idx][status_key] = result[idx].get(status_key, 0) + 1
        if started_at is not None:
            started_aware = _as_utc(started_at)
            if started_aware is not None:
                result[idx]["durations"].append((finished_aware - started_aware).total_seconds())

    return [
        RunHistoryBucket(
            bucket_start=b["bucket_start"],
            success=b["success"],
            error=b["error"],
            total=b["success"] + b["error"],
            avg_duration_seconds=(
                sum(b["durations"]) / len(b["durations"]) if b["durations"] else None
            ),
        )
        for b in result
    ]


@router.get("/{pool_id}/recent-runs", response_model=list[dict])
async def runner_pool_recent_runs(
    pool_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Most recent N runs for a pool, for the runs table on the pool detail page."""
    runs = (
        await session.scalars(
            select(Run)
            .where(Run.runner_pool_id == pool_id)
            .order_by(Run.started_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": r.id,
            "workflow_id": r.workflow_id,
            "status": r.status,
            "runner_id": r.runner_id,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        }
        for r in runs
    ]
