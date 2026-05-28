"""Runner pools: groups of remote execution agents.

Supports three provider types:
  agent      — outbound-WS daemon on a VM or EC2 instance
  docker     — API manages containers via the Docker SDK
  kubernetes — API creates K8s Jobs whose pods connect back as agents

Also hosts the batch-runs endpoint that dispatches a parameter matrix as
N parallel workflow runs.
"""

from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, get_session
from app.models import Artifact, Run, RunBatch, Runner, RunnerPool, Workflow, WorkflowVersion
from app.schemas import (
    RegistrationTokenResponse,
    RunBatchCreate,
    RunBatchInfo,
    RunnerInfo,
    RunnerPoolCreate,
    RunnerPoolInfo,
    RunnerPoolUpdate,
    SSHOnboardRequest,
    SSHOnboardResponse,
)
from app.security import require_permission
from app.services.artifacts import _artifact_path
from app.services.crypto import (
    create_payload_token,
    decode_payload_token,
    encrypt_data,
)
from app.services.graph_utils import first_trigger_node
from app.services.remote_dispatch import dispatcher
from app.services.runner import start_run
from app.services.ssh_onboard import onboard_machine

router = APIRouter(prefix="/runner-pools", tags=["runner-pools"])


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def _pool_info(pool: RunnerPool, runners: list[Runner]) -> RunnerPoolInfo:
    online = sum(1 for r in runners if r.status in ("online", "busy"))
    return RunnerPoolInfo(
        id=pool.id,
        name=pool.name,
        provider=pool.provider,
        provider_config=pool.provider_config or {},
        max_concurrent_runs=pool.max_concurrent_runs,
        runner_count=len(runners),
        online_count=online,
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
    )


@router.get("", response_model=list[RunnerPoolInfo])
async def list_runner_pools(
    session: AsyncSession = Depends(get_session),
) -> list[RunnerPoolInfo]:
    pools = (await session.scalars(select(RunnerPool).order_by(RunnerPool.created_at))).all()
    result = []
    for pool in pools:
        runners = (
            await session.scalars(select(Runner).where(Runner.pool_id == pool.id))
        ).all()
        result.append(_pool_info(pool, list(runners)))
    return result


@router.post(
    "",
    response_model=RunnerPoolInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def create_runner_pool(
    body: RunnerPoolCreate,
    session: AsyncSession = Depends(get_session),
) -> RunnerPoolInfo:
    pool = RunnerPool(
        name=body.name,
        provider=body.provider,
        provider_config=body.provider_config,
        max_concurrent_runs=body.max_concurrent_runs,
    )
    session.add(pool)
    await session.commit()
    await session.refresh(pool)
    return _pool_info(pool, [])


@router.get("/{pool_id}", response_model=RunnerPoolInfo)
async def get_runner_pool(
    pool_id: str, session: AsyncSession = Depends(get_session)
) -> RunnerPoolInfo:
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    runners = (
        await session.scalars(select(Runner).where(Runner.pool_id == pool_id))
    ).all()
    return _pool_info(pool, list(runners))


@router.patch(
    "/{pool_id}",
    response_model=RunnerPoolInfo,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def update_runner_pool(
    pool_id: str,
    body: RunnerPoolUpdate,
    session: AsyncSession = Depends(get_session),
) -> RunnerPoolInfo:
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    if body.name is not None:
        pool.name = body.name
    if body.provider_config is not None:
        pool.provider_config = body.provider_config
    if body.max_concurrent_runs is not None:
        pool.max_concurrent_runs = body.max_concurrent_runs
    pool.updated_at = datetime.now(UTC)
    await session.commit()
    runners = (
        await session.scalars(select(Runner).where(Runner.pool_id == pool_id))
    ).all()
    return _pool_info(pool, list(runners))


@router.delete(
    "/{pool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def delete_runner_pool(
    pool_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
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
) -> None:
    runner = await session.get(Runner, runner_id)
    if runner is None or runner.pool_id != pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner not found")
    await session.delete(runner)
    await session.commit()


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
    session: AsyncSession = Depends(get_session),
) -> RegistrationTokenResponse:
    """Generate a one-time registration token for a new agent runner.

    The agent uses this token to connect to /ws/runners/{runner_id} and
    authenticate. On first connect, the API hashes and stores the token.
    """
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")

    runner = Runner(
        pool_id=pool_id,
        name=f"runner-{pool.name[:20]}",
        status="offline",
    )
    session.add(runner)
    await session.commit()
    await session.refresh(runner)

    ttl = 86_400  # 24 hours
    token = create_payload_token(
        {"sub": runner.id, "pool_id": pool_id, "kind": "runner_registration"},
        ttl_seconds=ttl,
    )
    expires_at = datetime.fromtimestamp(
        datetime.now(UTC).timestamp() + ttl, tz=UTC
    )
    return RegistrationTokenResponse(
        token=token, runner_id=runner.id, expires_at=expires_at
    )


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
) -> SSHOnboardResponse:
    """SSH into a host, install + register + start ``noodle-runner``, and add
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
    runner = Runner(pool_id=pool_id, name=name, status="offline")
    session.add(runner)
    await session.commit()
    await session.refresh(runner)

    token = create_payload_token(
        {"sub": runner.id, "pool_id": pool_id, "kind": "runner_registration"},
        ttl_seconds=86_400,
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

    return SSHOnboardResponse(
        runner_id=runner.id, runner_name=name, install_log=install_log
    )


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
    if payload is None or payload.get("sub") != runner_id or payload.get("kind") not in (
        "runner_registration", "k8s_run"
    ):
        await ws.close(code=1008)
        return

    await ws.accept()

    # For K8s single-run agents, use the dedicated handler.
    if payload.get("kind") == "k8s_run":
        await dispatcher.handle_k8s_runner_connect(runner_id, ws)
        return

    # Verify runner row exists.
    async with SessionLocal() as session:
        runner = await session.get(Runner, runner_id)
        if runner is None:
            await ws.close(code=1008)
            return

    await dispatcher.handle_runner_connect(runner_id, ws)


# ---------------------------------------------------------------------------
# Artifact upload (runner-authenticated)
# ---------------------------------------------------------------------------


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

    try:
        path = _artifact_path(storage_key)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    body = await data.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)

    existing = await session.get(Artifact, artifact_id)
    if existing is None:
        session.add(
            Artifact(
                id=artifact_id,
                run_id=run_id,
                node_id=node_id,
                name=name,
                kind=kind,
                content_type=content_type,
                size_bytes=size_bytes or len(body),
                storage_backend="local",
                storage_key=storage_key,
                artifact_metadata={},
                preview=None,
            )
        )
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
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Workflow needs a trigger to run."
        )
    trigger_id = trigger.id if hasattr(trigger, "id") else trigger["id"]

    runner_pool_id = body.runner_pool_id or workflow.default_runner_pool_id

    batch = RunBatch(
        workflow_id=workflow_id,
        runner_pool_id=runner_pool_id,
        status="running",
        total_runs=len(body.parameters),
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)

    run_ids: list[str] = []
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
        )
        # Tag the run with the batch id.
        async with SessionLocal() as s:
            run = await s.get(Run, run_id)
            if run is not None:
                run.batch_id = batch.id
                run.runner_pool_id = runner_pool_id
                await s.commit()
        run_ids.append(run_id)

    return {
        "batch_id": batch.id,
        "run_ids": run_ids,
        "total": len(run_ids),
    }


@router.get("/run-batches/{batch_id}", response_model=RunBatchInfo)
async def get_batch(
    batch_id: str, session: AsyncSession = Depends(get_session)
) -> RunBatchInfo:
    batch = await session.get(RunBatch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
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
    cancelled = 0
    for run in queued_runs:
        await cancel_run(run.id)
        cancelled += 1

    batch.status = "cancelled"
    batch.finished_at = datetime.now(UTC)
    await session.commit()
    return {"batch_id": batch_id, "cancelled_runs": cancelled}
