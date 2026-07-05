"""One-click Docker workers: spawn long-lived nodyra-runner containers,
autoscale them per pool by queue depth, target a local or remote daemon.

Split into a PURE planner (plan_scaling — unit-testable, no I/O) and Docker
I/O (spawn/remove/reconcile/loop). Reuses the agent-runner trust model:
containers run the nodyra-runner agent, register via a signed token, and
connect outbound over WebSocket, so heartbeat/offline-requeue/drain all apply.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

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
