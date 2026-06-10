"""Remote runner dispatch orchestrator.

Provider implementations live in :mod:`app.services.providers`:

* ``agent``      — outbound-WS daemon on a VM/EC2 instance (plus the cloud
  auto-provision / idle-terminate machinery for those instances).
* ``docker``     — host drives containers via the Docker SDK.
* ``kubernetes`` — host creates a K8s Job whose pod connects back via the
  same WS agent protocol.

This module keeps the connection state (``_agents`` / ``_run_callbacks`` /
k8s futures) and the public dispatcher surface; providers are stateless
function modules that receive the dispatcher instance and this module's
(test-swappable) ``SessionLocal`` at call time.

Run queueing: when no runner has capacity, the run row is set to
``status="queued"`` and the durable-queue dispatch loop retries with backoff
(see ``app.services.queue``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Run, Runner, RunnerPool, RunQueueEntry
from app.services.executors.base import EventCallback  # noqa: F401 - re-export
from app.services.providers import agent as agent_provider
from app.services.providers import docker as docker_provider
from app.services.providers import k8s as k8s_provider

# Re-exports: these names are this module's established import surface
# (runner.py, routers, tests) — their implementations moved into providers.
from app.services.providers.agent import (  # noqa: F401
    QUEUE_TTL_SECONDS as _QUEUE_TTL_SECONDS,
)
from app.services.providers.agent import (  # noqa: F401
    _AgentConnection,
    _QueuedError,
)
from app.services.providers.docker import (  # noqa: F401
    _validate_packages,
    _validate_python_version,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

class RemoteDispatcher:
    def __init__(self) -> None:
        self._agents: dict[str, _AgentConnection] = {}
        # run_id → on_event callback; set during assign_run, cleared on finish
        self._run_callbacks: dict[str, EventCallback] = {}
        self._queue_loop_task: asyncio.Task | None = None
        # Lazily initialized lock — created on first async access so it binds
        # to the running event loop, not the module-import-time loop.
        self._lock_: asyncio.Lock | None = None
        # K8s single-run agents: run_id → future (resolved on run_finished)
        self._k8s_futures: dict[str, asyncio.Future] = {}
        # K8s single-run agents: run_id → run_assigned payload to deliver on connect
        self._k8s_payloads: dict[str, dict] = {}

    @property
    def _lock(self) -> asyncio.Lock:
        if self._lock_ is None:
            self._lock_ = asyncio.Lock()
        return self._lock_

    def _reset_loop_state(self) -> None:
        """Drop any asyncio objects bound to a now-gone event loop.

        Called at the start of ``cloud_idle_terminate_loop`` so each lifespan
        (including the test-client lifespan) gets fresh primitives on the
        correct loop.
        """
        self._lock_ = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def assign_run(
        self,
        run_id: str,
        pool_id: str,
        env_payload: dict,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        workflow_modules: list[dict],
        on_event: EventCallback,
        pause_on_approval: bool = False,
        agent_action_resume: dict | None = None,
    ) -> str:
        """Dispatch a run to the pool. Returns the final run status string."""
        async with SessionLocal() as session:
            pool = await session.get(RunnerPool, pool_id)
            if pool is None:
                raise ValueError(f"runner pool '{pool_id}' not found")
            provider = pool.provider

        if provider == "agent":
            return await self._assign_agent_run(
                run_id,
                pool_id,
                env_payload,
                graph,
                cache,
                targets,
                workflow_modules,
                on_event,
                pause_on_approval=pause_on_approval,
                agent_action_resume=agent_action_resume,
            )
        if provider == "docker":
            return await self._assign_docker_run(
                run_id,
                pool_id,
                env_payload,
                graph,
                cache,
                targets,
                workflow_modules,
                on_event,
                pause_on_approval=pause_on_approval,
                agent_action_resume=agent_action_resume,
            )
        if provider == "kubernetes":
            return await self._assign_k8s_run(
                run_id,
                pool_id,
                env_payload,
                graph,
                cache,
                targets,
                workflow_modules,
                on_event,
                pause_on_approval=pause_on_approval,
                agent_action_resume=agent_action_resume,
            )
        raise ValueError(f"unknown runner pool provider '{provider}'")

    async def handle_runner_connect(self, runner_id: str, ws: Any) -> None:
        """Called by the WS route when an agent runner connects.

        Runs the receive loop until the WebSocket closes.
        """
        conn = _AgentConnection(runner_id=runner_id, ws=ws)
        async with self._lock:
            self._agents[runner_id] = conn

        logger.info("runner connected runner_id=%s", runner_id)
        async with SessionLocal() as session:
            runner = await session.get(Runner, runner_id)
            if runner is not None:
                runner.status = "online"
                runner.last_seen_at = datetime.now(UTC)
                await session.commit()

        try:
            async for raw in ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_agent_message(conn, msg)
        except Exception:  # noqa: BLE001 - any disconnect / protocol error
            pass
        finally:
            await self.handle_runner_disconnect(runner_id)

    async def handle_runner_disconnect(self, runner_id: str) -> None:
        async with self._lock:
            conn = self._agents.pop(runner_id, None)

        logger.info("runner disconnected runner_id=%s", runner_id)
        async with SessionLocal() as session:
            runner = await session.get(Runner, runner_id)
            if runner is not None:
                runner.status = "offline"
                await session.commit()

        if conn is None:
            return

        # Fail all in-flight runs on this connection.
        for run_id, future in list(conn.active_runs.items()):
            if not future.done():
                future.set_exception(
                    RuntimeError("runner disconnected mid-run")
                )
            cb = self._run_callbacks.pop(run_id, None)
            if cb is not None:
                try:
                    await cb({
                        "type": "run_error",
                        "run_id": run_id,
                        "error": "runner disconnected",
                    })
                except Exception:  # noqa: BLE001
                    pass

    async def cancel_remote_run(self, run_id: str, runner_id: str) -> None:
        async with self._lock:
            conn = self._agents.get(runner_id)
        if conn is not None:
            await conn.send({"type": "run_cancel", "run_id": run_id})

    async def queue_run(self, run_id: str) -> None:
        """Mark a run as queued in the DB."""
        async with SessionLocal() as session:
            run = await session.get(Run, run_id)
            if run is not None:
                run.status = "queued"
                await session.commit()

    def signal_capacity(self) -> None:
        """No-op placeholder; the queue loop polls on a fixed interval."""

    async def shutdown(self) -> None:
        for conn in list(self._agents.values()):
            try:
                await conn.ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._agents.clear()

    # ------------------------------------------------------------------
    # Heartbeat + offline reaper
    # ------------------------------------------------------------------

    async def ping_connected_agents(self) -> int:
        """Send a ``ping`` to every connected agent. Returns the count sent.

        Agents reply ``pong`` which the message handler turns into a
        ``last_seen_at`` write. Failures are tolerated — the ws will surface
        the disconnect on its own iteration.
        """
        async with self._lock:
            conns = list(self._agents.values())
        sent = 0
        for conn in conns:
            try:
                await conn.send({"type": "ping"})
                sent += 1
            except Exception:  # noqa: BLE001
                pass
        return sent

    async def mark_stale_runners_offline(
        self, *, offline_after_seconds: int
    ) -> list[str]:
        """Find runners whose ``last_seen_at`` is older than the threshold,
        mark them offline, and requeue their in-flight runs.

        Returns the runner ids that were marked offline. Idempotent — runners
        already offline are skipped, and runs whose queue entry is already in
        a terminal state are left alone.
        """
        threshold = datetime.now(UTC) - timedelta(seconds=offline_after_seconds)
        marked: list[str] = []
        async with SessionLocal() as session:
            stale = (
                await session.scalars(
                    select(Runner).where(
                        Runner.status == "online",
                        Runner.last_seen_at.is_not(None),
                        Runner.last_seen_at < threshold,
                    )
                )
            ).all()
            if not stale:
                # Also handle the boundary case: status=online but last_seen_at
                # is NULL (never sent a hello). Treat them as offline so a
                # zombie row never permanently blocks dispatch.
                never_seen = (
                    await session.scalars(
                        select(Runner).where(
                            Runner.status == "online",
                            Runner.last_seen_at.is_(None),
                        )
                    )
                ).all()
                stale = never_seen
            for runner in stale:
                runner.status = "offline"
                runner.current_runs = 0
                marked.append(runner.id)
                # Drop the in-memory connection (if any) so the next assign
                # call doesn't try to route to a dead socket.
                async with self._lock:
                    self._agents.pop(runner.id, None)
                # Requeue any non-terminal runs assigned to this runner so
                # another runner in the pool can pick them up. The queue
                # service is the source of truth — clear the lease and reset
                # the entry to ``queued``.
                runs = (
                    await session.scalars(
                        select(Run).where(
                            Run.runner_id == runner.id,
                            Run.status.in_(("running", "queued", "pending")),
                        )
                    )
                ).all()
                for run in runs:
                    entry = await session.scalar(
                        select(RunQueueEntry).where(RunQueueEntry.run_id == run.id)
                    )
                    if entry is None:
                        continue
                    if entry.status in ("completed", "failed", "dead_lettered", "cancelled"):
                        continue
                    entry.status = "queued"
                    entry.leased_by = None
                    entry.lease_expires_at = None
                    entry.available_at = datetime.now(UTC)
                    entry.queue_reason = "runner_offline"
                    log = list(entry.attempts_log or [])
                    log.append({
                        "attempt": entry.attempts,
                        "event": "runner_offline",
                        "error": f"runner {runner.id} went offline",
                        "ts": datetime.now(UTC).isoformat(),
                    })
                    entry.attempts_log = log
                    run.status = "pending"
                    run.runner_id = None
            await session.commit()
        if marked:
            logger.warning(
                "marked %d runner(s) offline (no heartbeat in %ss): %s",
                len(marked), offline_after_seconds, marked,
            )
        return marked

    # ------------------------------------------------------------------
    # Provider delegations — implementations live in app.services.providers.
    # ``SessionLocal`` is passed from THIS module's globals at call time so
    # the test suite's session swap applies to every provider.
    # ------------------------------------------------------------------

    async def _assign_agent_run(self, *args, **kwargs) -> str:
        return await agent_provider.assign_agent_run(self, SessionLocal, *args, **kwargs)

    async def _pick_agent(self, pool_id: str) -> _AgentConnection | None:
        return await agent_provider.pick_agent(self, SessionLocal, pool_id)

    async def _handle_agent_message(self, conn: _AgentConnection, msg: dict) -> None:
        await agent_provider.handle_agent_message(self, SessionLocal, conn, msg)

    async def _maybe_provision(self, pool_id: str) -> None:
        await agent_provider.maybe_provision(SessionLocal, pool_id)

    async def idle_terminate_cloud_runners(self) -> None:
        await agent_provider.idle_terminate_cloud_runners(SessionLocal)

    async def _assign_docker_run(self, *args, **kwargs) -> str:
        return await docker_provider.assign_docker_run(SessionLocal, *args, **kwargs)

    async def _assign_k8s_run(self, *args, **kwargs) -> str:
        return await k8s_provider.assign_k8s_run(self, SessionLocal, *args, **kwargs)

    async def handle_k8s_runner_connect(self, run_id: str, ws: Any) -> None:
        await k8s_provider.handle_k8s_runner_connect(self, run_id, ws)


def build_env_payload(
    env_id: str, python_version: str, packages: list[str], noodle_version: str = "0.0.1"
) -> dict:
    packages_hash = hashlib.sha256(
        json.dumps(sorted(packages)).encode()
    ).hexdigest()[:16]
    return {
        "id": env_id,
        "python_version": python_version,
        "packages": packages,
        "packages_hash": packages_hash,
        "noodle_version": noodle_version,
    }


# ---------------------------------------------------------------------------
# Cloud runner idle-terminate background loop
# ---------------------------------------------------------------------------

async def cloud_idle_terminate_loop() -> None:
    """Periodically terminate idle cloud runners.

    Run-queue dispatch lives in :mod:`app.services.queue.run_queue_dispatch_loop`
    now; this loop is the residual non-queue work the old
    ``queue_dispatch_loop`` did (cleaning up cloud runner instances that have
    been idle long enough to release).
    """
    dispatcher._reset_loop_state()
    while True:
        await asyncio.sleep(30)
        try:
            await dispatcher.idle_terminate_cloud_runners()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let one tick kill the loop
            logger.exception("cloud_idle_terminate_loop tick failed")


async def runner_heartbeat_loop() -> None:
    """Send pings to connected agents and reap unresponsive runners.

    Single loop so the cadence stays predictable: ping every
    ``runner_heartbeat_interval_seconds``; after ``runner_offline_after_seconds``
    of silence a runner is marked offline and its in-flight runs are requeued.

    Disabled when either knob is set to 0 — useful for local dev where agents
    aren't part of the picture.
    """
    interval = max(1, settings.runner_heartbeat_interval_seconds)
    offline_after = settings.runner_offline_after_seconds
    if offline_after <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            await dispatcher.ping_connected_agents()
            await dispatcher.mark_stale_runners_offline(
                offline_after_seconds=offline_after
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("runner_heartbeat_loop tick failed")


# Singleton
dispatcher = RemoteDispatcher()
