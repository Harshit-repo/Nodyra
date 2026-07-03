"""Warm per-(org, environment) sandbox container pool (MT Phase D slice 1).

SandboxWorker wraps one hardened container running ``nodyra_runtime`` plus
its attach socket. SandboxPool hands a worker to at most one run at a time
and returns clean workers to a bounded warm list keyed by
``(org_id, environment_id, spawn_overrides)`` — reuse is strictly within one
key, so cross-tenant container reuse is impossible by construction.

The wire protocol is the same newline-framed JSON ``nodyra_runtime`` speaks
to the subprocess pool (see packages/runtime/nodyra_runtime/server.py),
including the ``call_workflow`` host callback that runtime_pool brokers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from app.config import settings
from app.services.container_runtime import (
    DockerStreamDemuxer,
    attach_raw_socket,
    ensure_docker_image,
    hardening_kwargs,
    image_tag_for,
)
from nodyra.serialization import deserialize_value, serialize_value

logger = logging.getLogger(__name__)

# Events forwarded verbatim to the host's on_event (same set as the docker
# runner-pool provider).
_FORWARDED_EVENTS = frozenset(
    {
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
    }
)


def overrides_key(overrides: dict | None) -> str:
    """Stable fingerprint for sandbox spawn overrides in the warm-pool key."""
    if not overrides:
        return ""
    return json.dumps(overrides, sort_keys=True)


class SandboxWorker:
    """One container + attach socket; drives one run at a time."""

    def __init__(
        self,
        client: Any,
        container: Any,
        sock: Any,
        *,
        key: tuple[str | None, str | None, str],
        image_tag: str,
    ) -> None:
        self.client = client
        self.container = container
        self._raw = attach_raw_socket(sock)
        self.key = key
        self.image_tag = image_tag
        self.runs_completed = 0
        self.idle_since = time.monotonic()
        self.dead = False
        self._buf = b""
        self._demux = DockerStreamDemuxer()
        self._write_lock = asyncio.Lock()

    @classmethod
    async def spawn(
        cls,
        client: Any,
        *,
        key: tuple[str | None, str | None, str],
        env_payload: dict,
        runtime: str,
        network: str,
        overrides: dict | None = None,
    ) -> SandboxWorker:
        loop = asyncio.get_running_loop()
        tag = image_tag_for(env_payload)
        await loop.run_in_executor(None, ensure_docker_image, client, tag, env_payload)
        name = f"nodyra-sbx-{uuid.uuid4().hex[:12]}"
        spawn_kwargs = hardening_kwargs(
            runtime=runtime, network=network, overrides=overrides
        )
        container = await loop.run_in_executor(
            None,
            lambda: client.containers.run(
                tag,
                detach=True,
                stdin_open=True,
                remove=False,
                name=name,
                **spawn_kwargs,
            ),
        )
        worker: SandboxWorker | None = None
        try:
            sock = await loop.run_in_executor(
                None,
                lambda: container.attach_socket(
                    params={"stdin": True, "stdout": True, "stderr": False, "stream": True}
                ),
            )
            worker = cls(client, container, sock, key=key, image_tag=tag)
            await worker._await_ready(loop)
            return worker
        except BaseException:
            if worker is not None:
                await worker.close()
            else:
                try:
                    await loop.run_in_executor(None, lambda: container.remove(force=True))
                except Exception:  # noqa: BLE001
                    pass
            raise

    async def _await_ready(self, loop: asyncio.AbstractEventLoop) -> None:
        await loop.run_in_executor(
            None, self._raw.settimeout, settings.sandbox_ready_timeout_seconds
        )
        try:
            event = await self._read_event(loop)
        except RuntimeError as exc:
            raise RuntimeError(
                f"sandbox container {self.container.name} did not become "
                f"ready within {settings.sandbox_ready_timeout_seconds}s: {exc}"
            ) from exc
        if event.get("type") != "ready":
            raise RuntimeError(
                f"sandbox container {self.container.name} sent {event.get('type')!r} before ready"
            )

    async def _read_event(self, loop: asyncio.AbstractEventLoop) -> dict:
        """Next JSON event from the attach socket (skips undecodable lines)."""
        _dropped = 0
        while True:
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    # Non-JSON stdout noise mixed into the protocol stream (E-10).
                    # Accumulating many dropped lines usually means the container
                    # wrote to stdout instead of the protocol channel — warn so
                    # operators can spot this rather than silently hanging.
                    _dropped += 1
                    if _dropped == 1 or _dropped % 50 == 0:
                        logger.warning(
                            "sandbox %s: dropped %d malformed JSON line(s) from protocol stream "
                            "(container stdout mixed with protocol channel — E-10)",
                            getattr(self, "container", {}) and getattr(self.container, "name", "?"),
                            _dropped,
                        )
                    continue
            try:
                chunk = await loop.run_in_executor(None, self._raw.recv, 4096)
            except Exception as exc:  # noqa: BLE001 — socket.timeout et al.
                self.dead = True
                raise RuntimeError(f"sandbox socket read failed: {exc}") from exc
            if not chunk:
                self.dead = True
                raise RuntimeError("sandbox container closed its output stream")
            self._buf += self._demux.feed(chunk)

    async def _send(self, message: dict, loop: asyncio.AbstractEventLoop) -> None:
        payload = json.dumps(serialize_value(message)).encode() + b"\n"
        async with self._write_lock:
            try:
                await loop.run_in_executor(None, self._raw.sendall, payload)
            except Exception as exc:  # noqa: BLE001 — broken pipe = dead container
                self.dead = True
                raise RuntimeError(f"sandbox socket write failed: {exc}") from exc

    async def run(
        self,
        run_id: str,
        *,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        workflow_modules: list[dict],
        on_event,
        subworkflow_resolver=None,
        subworkflow_meta: dict | None = None,
        pause_on_approval: bool = False,
        agent_action_resume: dict | None = None,
        run_timeout: float | None = None,
    ) -> str:
        """Execute one run on this container. Raises on transport failure
        (caller surfaces run_error); a missing ``result`` event marks the
        worker dead so the pool never reuses it."""
        loop = asyncio.get_running_loop()
        timeout = (
            run_timeout
            if (run_timeout and run_timeout > 0)
            else (settings.workflow_run_timeout_seconds or 3600.0)
        )
        await loop.run_in_executor(None, self._raw.settimeout, timeout)
        await self._send(
            {
                "type": "run",
                "request_id": run_id,
                "graph": graph,
                "cache": cache or {},
                "targets": targets or [],
                "workflow_modules": workflow_modules,
                "pause_on_approval": pause_on_approval,
                "agent_action_resume": agent_action_resume or {},
                "subworkflow_meta": subworkflow_meta or {},
            },
            loop,
        )

        callbacks: set[asyncio.Task] = set()
        status = "error"
        clean = False
        try:
            while True:
                event = await self._read_event(loop)
                etype = event.get("type")
                if etype == "call_workflow":
                    task = asyncio.create_task(
                        self._handle_call_workflow(event, subworkflow_resolver, loop)
                    )
                    callbacks.add(task)
                    task.add_done_callback(callbacks.discard)
                elif etype in _FORWARDED_EVENTS:
                    await on_event(event)
                elif etype == "result":
                    status = str(event.get("status", "error"))
                    clean = True
                    break
                elif etype == "error":
                    await on_event(
                        {
                            "type": "run_error",
                            "error": str(event.get("error", "runtime failure")),
                        }
                    )
                    break
                # unknown event types are ignored (forward-compat)
        finally:
            self.runs_completed += 1
            if not clean:
                self.dead = True
            for task in callbacks:
                task.cancel()
        return status

    async def _handle_call_workflow(
        self, event: dict, subworkflow_resolver, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Mirror of runtime_pool._handle_call_workflow over the attach socket."""
        from nodyra.engine.subworkflows import (  # noqa: PLC0415
            InlineSubworkflow,
            SubworkflowCall,
        )

        callback_id = event.get("callback_id", "")
        try:
            if subworkflow_resolver is None:
                raise RuntimeError("sandbox runner has no host-side sub-workflow resolver")
            call = SubworkflowCall.from_payload(
                {**event, "input": deserialize_value(event.get("input"))}
            )
            outcome = await subworkflow_resolver(call, parent_env_id=self.key[1])
            if isinstance(outcome, InlineSubworkflow):
                await self._send(
                    {
                        "type": "call_workflow_response",
                        "callback_id": callback_id,
                        "inline_graph": outcome.graph,
                        "inline_cache": outcome.cache,
                        "inline_targets": outcome.targets,
                        "inline_sources": list(outcome.sources),
                    },
                    loop,
                )
            else:
                await self._send(
                    {
                        "type": "call_workflow_response",
                        "callback_id": callback_id,
                        "result": outcome,
                    },
                    loop,
                )
        except Exception as exc:  # noqa: BLE001 — surface back into the run
            try:
                await self._send(
                    {
                        "type": "call_workflow_error",
                        "callback_id": callback_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    loop,
                )
            except RuntimeError:
                pass  # container died; the read loop reports it

    async def close(self) -> None:
        """Force-remove the container. Idempotent; never raises."""
        self.dead = True
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: self.container.remove(force=True))
        except Exception:  # noqa: BLE001 — already gone
            pass


class SandboxPool:
    """Bounded warm pool keyed by (org_id, environment_id, spawn overrides)."""

    def __init__(self) -> None:
        self._client: Any | None = None
        self._runtime: str = "runc"
        self._network: str = ""
        self._idle: dict[tuple[str | None, str | None, str], list[SandboxWorker]] = {}
        self._active: dict[str, SandboxWorker] = {}  # run_id -> worker
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    def configure(self, client: Any, *, runtime: str, network: str) -> None:
        self._client = client
        self._runtime = runtime
        self._network = network

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def describe(self) -> str:
        if not self.enabled:
            return "inactive"
        idle = sum(len(v) for v in self._idle.values())
        return f"runtime={self._runtime} idle={idle} active={len(self._active)}"

    def status(self) -> dict:
        """Structured status for the ops endpoint and settings card."""
        return {
            "active": self.enabled,
            "runtime": self._runtime if self.enabled else None,
            "network": self._network if self.enabled else None,
            "idle": sum(len(v) for v in self._idle.values()),
            "active_runs": len(self._active),
        }

    async def dispatch(
        self,
        run_id: str,
        *,
        org_id: str | None,
        env_id: str | None,
        env_payload: dict,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        workflow_modules: list[dict],
        on_event,
        subworkflow_resolver=None,
        subworkflow_meta: dict | None = None,
        run_timeout: float | None = None,
        pause_on_approval: bool = False,
        agent_action_resume: dict | None = None,
        spawn_overrides: dict | None = None,
    ) -> str:
        if self._client is None:
            raise RuntimeError("sandbox pool is not configured")
        self._ensure_reaper()
        key = (org_id, env_id, overrides_key(spawn_overrides))
        worker = await self._acquire(key, env_payload, overrides=spawn_overrides)
        self._active[run_id] = worker
        try:
            status = await worker.run(
                run_id,
                graph=graph,
                cache=cache,
                targets=targets,
                workflow_modules=workflow_modules,
                on_event=on_event,
                subworkflow_resolver=subworkflow_resolver,
                subworkflow_meta=subworkflow_meta,
                pause_on_approval=pause_on_approval,
                agent_action_resume=agent_action_resume,
                run_timeout=run_timeout,
            )
        except asyncio.CancelledError:
            await worker.close()  # cancelled task ⇒ hard-kill the container
            raise
        except Exception as exc:  # noqa: BLE001 — transport/spawn failure
            logger.exception("sandbox run failed run_id=%s: %s", run_id, exc)
            await on_event({"type": "run_error", "error": str(exc)})
            status = "error"
        finally:
            self._active.pop(run_id, None)
            await self._release(worker)
        return status

    async def cancel(self, run_id: str) -> bool:
        worker = self._active.get(run_id)
        if worker is None:
            return False
        await worker.close()  # read loop sees EOF; dispatch's finally cleans up
        return True

    async def flush(self) -> None:
        async with self._lock:
            workers = [w for lst in self._idle.values() for w in lst]
            self._idle.clear()
        for w in workers:
            await w.close()

    async def _acquire(
        self,
        key: tuple[str | None, str | None, str],
        env_payload: dict,
        *,
        overrides: dict | None = None,
    ) -> SandboxWorker:
        wanted_tag = image_tag_for(env_payload)
        stale: list[SandboxWorker] = []
        worker: SandboxWorker | None = None
        async with self._lock:
            bucket = self._idle.get(key) or []
            while bucket:
                candidate = bucket.pop()
                if candidate.dead or candidate.image_tag != wanted_tag:
                    stale.append(candidate)
                else:
                    worker = candidate
                    break
            if not bucket:
                self._idle.pop(key, None)
        for s in stale:
            await s.close()
        if worker is not None:
            return worker
        return await SandboxWorker.spawn(
            self._client,
            key=key,
            env_payload=env_payload,
            runtime=self._runtime,
            network=self._network,
            overrides=overrides,
        )

    async def _release(self, worker: SandboxWorker) -> None:
        if worker.dead or worker.runs_completed >= settings.sandbox_max_runs_per_container:
            await worker.close()
            return
        evicted: list[SandboxWorker] = []
        async with self._lock:
            bucket = self._idle.setdefault(worker.key, [])
            if len(bucket) >= settings.sandbox_warm_per_key:
                evicted.append(bucket.pop(0))
            worker.idle_since = time.monotonic()
            bucket.append(worker)
            total = sum(len(v) for v in self._idle.values())
            while total > settings.sandbox_warm_total:
                lru_key = min(
                    (k for k, v in self._idle.items() if v),
                    key=lambda k: self._idle[k][0].idle_since,
                )
                evicted.append(self._idle[lru_key].pop(0))
                if not self._idle[lru_key]:
                    self._idle.pop(lru_key)
                total -= 1
        for w in evicted:
            await w.close()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_idle())

    async def _reap_idle(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                cutoff = time.monotonic() - settings.sandbox_warm_ttl_seconds
                expired: list[SandboxWorker] = []
                async with self._lock:
                    for key in list(self._idle):
                        keep = [w for w in self._idle[key] if w.idle_since >= cutoff]
                        expired.extend(w for w in self._idle[key] if w.idle_since < cutoff)
                        if keep:
                            self._idle[key] = keep
                        else:
                            self._idle.pop(key)
                for w in expired:
                    await w.close()
        except asyncio.CancelledError:
            return


# Module-level singleton, mirroring runtime_pool's pattern.
pool = SandboxPool()


def _make_docker_client() -> Any:
    """Construct the SDK client (separated for test monkeypatching)."""
    import docker  # noqa: PLC0415 — optional dependency, sandbox-mode only

    if settings.sandbox_docker_host:
        return docker.DockerClient(base_url=settings.sandbox_docker_host)
    return docker.from_env()


async def init_sandbox() -> str | None:
    """Probe the daemon per execution_sandbox mode; configure the pool.

    Returns the active isolation runtime, or None when the sandbox is off /
    unavailable-in-auto. Call once from worker_main and the API lifespan
    (after enforce_sandbox_policy)."""
    from app.services.container_runtime import (  # noqa: PLC0415
        detect_runtime,
        ensure_sandbox_network,
    )

    mode = settings.execution_sandbox
    if mode == "off":
        return None
    loop = asyncio.get_running_loop()
    try:
        client = await loop.run_in_executor(None, _make_docker_client)
        await loop.run_in_executor(None, client.ping)
        runtime = await loop.run_in_executor(None, detect_runtime, client, settings.sandbox_runtime)
        network = await loop.run_in_executor(None, ensure_sandbox_network, client)
    except Exception as exc:
        if mode == "required":
            raise RuntimeError(
                f"execution_sandbox=required but no usable Docker daemon/runtime: {exc}"
            ) from exc
        logger.warning(
            "execution_sandbox=auto: no usable Docker daemon (%s); "
            "falling back to the subprocess runner",
            exc,
        )
        return None
    pool.configure(client, runtime=runtime, network=network)
    logger.info("sandbox execution active: runtime=%s network=%s", runtime, network)
    return runtime
