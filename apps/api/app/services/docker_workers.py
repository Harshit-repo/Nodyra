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
