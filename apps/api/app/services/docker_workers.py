"""One-click Docker workers: spawn long-lived nodyra-runner containers,
autoscale them per pool by queue depth, target a local or remote daemon.

Split into a PURE planner (plan_scaling — unit-testable, no I/O) and Docker
I/O (spawn/remove/reconcile/loop). Reuses the agent-runner trust model:
containers run the nodyra-runner agent, register via a signed token, and
connect outbound over WebSocket, so heartbeat/offline-requeue/drain all apply.
"""

from __future__ import annotations

import asyncio
import io
import logging
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from app.services.wheel_index import ensure_wheels

logger = logging.getLogger(__name__)

_MAX_RUNNERS_CEILING = 32


@dataclass(frozen=True)
class RunnerState:
    runner_id: str
    current_runs: int = 0
    idle_seconds: float = 0.0
    draining: bool = False
    online: bool = True
    max_concurrent_runs: int = 2


@dataclass(frozen=True)
class PoolState:
    pool_id: str
    queued: int = 0
    runners: list[RunnerState] = field(default_factory=list)
    enabled: bool = True
    min_runners: int = 0
    max_runners: int = 4
    idle_seconds: float = 300.0


@dataclass(frozen=True)
class SpawnRunner:
    pool_id: str


@dataclass(frozen=True)
class RemoveRunner:
    runner_id: str


Action = SpawnRunner | RemoveRunner


def plan_scaling(pools: list[PoolState]) -> list[Action]:
    """Pure: at most one action per pool per tick. Scale up on backlog when
    all online runners are saturated and count < max; else scale down one
    idle, drained runner while count > min. Never touch a busy runner."""
    actions: list[Action] = []
    for pool in pools:
        if not pool.enabled:
            continue
        max_runners = min(pool.max_runners, _MAX_RUNNERS_CEILING)
        min_runners = min(pool.min_runners, max_runners)
        count = len(pool.runners)

        if count < min_runners:
            actions.append(SpawnRunner(pool.pool_id))
            continue

        online = [r for r in pool.runners if r.online]
        has_free = any(
            r.current_runs < r.max_concurrent_runs for r in online
        )
        if pool.queued > 0 and not has_free and count < max_runners:
            actions.append(SpawnRunner(pool.pool_id))
            continue

        if count > min_runners:
            idle = [
                r for r in pool.runners
                if r.current_runs == 0
                and r.idle_seconds >= pool.idle_seconds
                and r.draining
            ]
            if idle:
                actions.append(RemoveRunner(idle[0].runner_id))
    return actions


AGENT_IMAGE_SCHEMA = "v1"


def agent_image_tag() -> str:
    return f"nodyra-runner-agent:{AGENT_IMAGE_SCHEMA}"


_ENTRYPOINT = (
    "#!/bin/sh\nset -e\n"
    'nodyra-runner register --api-url "$NODYRA_API_URL" '
    '--token "$NODYRA_RUNNER_TOKEN" --name "$NODYRA_RUNNER_NAME"\n'
    "exec nodyra-runner start\n"
)


def _agent_dockerfile() -> str:
    return (
        "FROM python:3.12-slim\n"
        "RUN pip install --no-cache-dir uv\n"
        "COPY wheels /wheels\n"
        "RUN pip install --no-cache-dir nodyra-runner --find-links /wheels\n"
        "RUN useradd --create-home --uid 65533 --shell /usr/sbin/nologin runner\n"
        "COPY entrypoint.sh /entrypoint.sh\n"
        "RUN chmod +x /entrypoint.sh && chown -R runner /home/runner\n"
        "USER runner\n"
        'ENTRYPOINT ["/entrypoint.sh"]\n'
    )


def _agent_build_context(wheels: list[Path]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        df = _agent_dockerfile().encode()
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(df)
        tar.addfile(info, io.BytesIO(df))
        ep = _ENTRYPOINT.encode()
        ep_info = tarfile.TarInfo("entrypoint.sh")
        ep_info.size = len(ep)
        tar.addfile(ep_info, io.BytesIO(ep))
        for wheel in wheels:
            tar.add(wheel, arcname=f"wheels/{wheel.name}")
    buf.seek(0)
    return buf.read()


async def ensure_agent_image(client) -> str:
    """Build the nodyra-runner-agent image from the server's wheel cache if
    absent. Sync Docker calls run in the default executor."""
    tag = agent_image_tag()
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, client.images.get, tag)
        return tag
    except Exception:  # noqa: BLE001 — NotFound; build below
        pass
    wheels = await ensure_wheels()
    context = _agent_build_context(list(wheels))
    await loop.run_in_executor(
        None,
        lambda: client.images.build(
            fileobj=io.BytesIO(context), custom_context=True, tag=tag, rm=True
        ),
    )
    logger.info("built agent image %s", tag)
    return tag


from app.config import settings
from app.models import Runner, RunnerPool
from app.services.runner_tokens import mint_runner_registration


class RunnerBusy(Exception):
    """Raised when removing a runner that still has in-flight runs."""


class DaemonUnreachable(Exception):
    """Raised when the configured Docker daemon can't be contacted."""


def _docker_client(cfg: dict):
    try:
        import docker  # noqa: PLC0415
    except ImportError as exc:
        raise DaemonUnreachable(
            "the 'docker' package is required for Docker workers"
        ) from exc
    host = (cfg or {}).get("docker_host") or ""
    try:
        return docker.DockerClient(base_url=host) if host else docker.from_env()
    except Exception as exc:  # noqa: BLE001
        raise DaemonUnreachable(str(exc)) from exc


def _resolve_api_url(cfg: dict) -> str:
    url = (cfg or {}).get("docker_api_url") or settings.public_api_url
    if not url and not (cfg or {}).get("docker_host"):
        url = "http://host.docker.internal:8000"
    if not url:
        raise DaemonUnreachable(
            "cannot resolve an API URL for the runner to dial back to; set "
            "provider_config.docker_api_url or PUBLIC_API_URL"
        )
    return url.rstrip("/")


async def spawn_docker_runner(session, pool: RunnerPool, *, client=None, name=None) -> Runner:
    cfg = pool.provider_config or {}
    rc = cfg.get("docker_runner") or {}
    sandbox = bool(rc.get("sandbox"))
    client = client or _docker_client(cfg)
    await ensure_agent_image(client)

    runner, token, _ = await mint_runner_registration(
        session, pool.id, org_id=pool.org_id,
        name=name or f"docker-{pool.name[:16]}",
        max_concurrent_runs=int(rc.get("max_concurrent_runs", 2)),
        capabilities={"docker_managed": True, "sandbox": sandbox},
    )
    container_name = f"nodyra-worker-{runner.id[:12]}"
    api_url = _resolve_api_url(cfg)

    volumes = {}
    if sandbox:
        volumes["/var/run/docker.sock"] = {
            "bind": "/var/run/docker.sock", "mode": "rw"
        }
    run_kwargs = dict(
        detach=True,
        name=container_name,
        environment={
            "NODYRA_API_URL": api_url,
            "NODYRA_RUNNER_TOKEN": token,
            "NODYRA_RUNNER_NAME": runner.name,
        },
        network=cfg.get("docker_network") or None,
        mem_limit=f"{int(rc.get('memory_mb', 1024))}m",
        nano_cpus=int(float(rc.get("cpu", 1.0)) * 1_000_000_000),
        pids_limit=int(rc.get("pids", 512)),
        restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
        security_opt=["no-new-privileges:true"],
        init=True,
        extra_hosts={"host.docker.internal": "host-gateway"},
        volumes=volumes or None,
        labels={
            "nodyra.managed": "true",
            "nodyra.pool": pool.id,
            "nodyra.runner": runner.id,
        },
    )
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, lambda: client.containers.run(agent_image_tag(), **run_kwargs)
        )
    except Exception as exc:  # noqa: BLE001 — roll back the placeholder row
        await session.delete(runner)
        await session.commit()
        raise DaemonUnreachable(f"failed to start runner container: {exc}") from exc

    caps = dict(runner.capabilities or {})
    caps["container_name"] = container_name
    runner.capabilities = caps
    await session.commit()
    await session.refresh(runner)
    return runner


async def remove_docker_runner(session, runner: Runner, *, client=None, force=False) -> None:
    if runner.current_runs > 0 and not force:
        raise RunnerBusy(f"runner {runner.id} has {runner.current_runs} in-flight run(s)")
    container_name = (runner.capabilities or {}).get("container_name")
    if client is not None and container_name:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, lambda: client.containers.get(container_name).remove(force=True)
            )
        except Exception:  # noqa: BLE001 — already gone
            pass
    await session.delete(runner)
    await session.commit()


from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import RunQueueEntry


def _idle_seconds(runner: Runner, now: datetime) -> float:
    last = runner.last_seen_at
    if last is None:
        return 0.0
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return max(0.0, (now - last).total_seconds())


async def _build_pool_states(session) -> list[PoolState]:
    now = datetime.now(UTC)
    pools = (await session.scalars(
        select(RunnerPool).where(RunnerPool.provider == "agent")
    )).all()
    queued_rows = (await session.execute(
        select(RunQueueEntry.runner_pool_id, func.count())
        .where(RunQueueEntry.status == "queued")
        .group_by(RunQueueEntry.runner_pool_id)
    )).all()
    queued_by_pool = {pid: n for pid, n in queued_rows}
    runners = (await session.scalars(select(Runner))).all()
    by_pool: dict[str, list[Runner]] = {}
    for r in runners:
        if (r.capabilities or {}).get("docker_managed"):
            by_pool.setdefault(r.pool_id, []).append(r)

    states: list[PoolState] = []
    for pool in pools:
        cfg = pool.provider_config or {}
        auto = cfg.get("docker_autoscale") or {}
        managed = by_pool.get(pool.id, [])
        # Only surface pools that either autoscale or already have managed runners.
        if not auto.get("enabled") and not managed:
            continue
        states.append(PoolState(
            pool_id=pool.id,
            queued=int(queued_by_pool.get(pool.id, 0)),
            runners=[
                RunnerState(
                    runner_id=r.id,
                    current_runs=r.current_runs,
                    idle_seconds=_idle_seconds(r, now),
                    draining=r.status in ("draining", "offline", "online"),
                    online=r.status in ("online", "busy"),
                    max_concurrent_runs=r.max_concurrent_runs or 2,
                )
                for r in managed
            ],
            enabled=bool(auto.get("enabled")),
            min_runners=int(auto.get("min_runners", 0)),
            max_runners=int(auto.get("max_runners", 4)),
            idle_seconds=float(auto.get("idle_seconds", 300)),
        ))
    return states


async def _apply_action(action: Action) -> None:
    async with SessionLocal() as session:
        if isinstance(action, SpawnRunner):
            pool = await session.get(RunnerPool, action.pool_id)
            if pool is None:
                return
            try:
                await spawn_docker_runner(session, pool)
            except DaemonUnreachable as exc:
                logger.warning("autoscale spawn skipped for %s: %s", action.pool_id, exc)
        elif isinstance(action, RemoveRunner):
            runner = await session.get(Runner, action.runner_id)
            if runner is not None:
                cfg_pool = await session.get(RunnerPool, runner.pool_id)
                client = None
                try:
                    client = _docker_client((cfg_pool.provider_config or {}) if cfg_pool else {})
                except DaemonUnreachable:
                    pass
                await remove_docker_runner(session, runner, client=client, force=False)


async def docker_workers_autoscale_loop() -> None:
    """Tick: snapshot → plan_scaling → apply. Never lets one tick kill the loop."""
    while True:
        try:
            async with SessionLocal() as session:
                states = await _build_pool_states(session)
            for action in plan_scaling(states):
                try:
                    await _apply_action(action)
                except RunnerBusy:
                    pass
                except Exception:  # noqa: BLE001
                    logger.exception("autoscale action failed: %s", action)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("docker autoscale loop tick failed")
        await asyncio.sleep(settings.docker_autoscale_tick_seconds)
