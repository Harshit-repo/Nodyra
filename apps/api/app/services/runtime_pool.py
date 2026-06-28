"""Env-runner subprocess pool.

When ``settings.use_subprocess_runner`` is enabled, the runner dispatches
executions to a long-lived ``noodle_runtime`` subprocess per environment id.
This is the productionization path that gives a workflow real isolation
inside its assigned ``uv`` venv.

The pool also brokers sub-workflow calls back to the host: when an
``execute_workflow`` node runs inside a subprocess, it writes a
``call_workflow`` event to stdout (carrying the SubworkflowCall payload —
depth/chain travel explicitly, A3); the pool catches it, invokes the
host-side ``subworkflow_resolver`` passed by the runner
(``app.services.subworkflows.resolve_subworkflow``), and writes the result
— a leaf value or an inline directive — back to the subprocess's stdin.

Each subprocess is serialized via an ``asyncio.Lock`` — concurrent runs to
the same env queue rather than interleaving their stdio. Different envs run
in different subprocesses and can therefore run in parallel.
"""

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from noodle.engine.subworkflows import SubworkflowMeta

from app.config import settings
from app.db import SessionLocal
from app.models import Environment
from app.services.artifacts import artifact_base_dir
from app.services.venv import ensure_environment_ready
from noodle.serialization import deserialize_value, serialize_value

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict], Awaitable[None]]
# Host-side sub-workflow resolver: (SubworkflowCall, *, parent_env_id) →
# leaf value | InlineSubworkflow directive. Implemented by
# app.services.subworkflows.resolve_subworkflow. ``parent_env_id`` lets the
# resolver detect the same-env inline opportunity.
SubworkflowResolver = Callable[..., Awaitable[Any]]


async def _resolve_pool_sizes(env_id: str | None) -> tuple[int, int]:
    """Per-environment ``(min_size, max_size)`` from the DB.

    Falls back to the boot default for both bounds when the env row is
    missing. ``min_size==0`` means Spawn-per-run (release closes the worker
    immediately). ``runner_pool_max is None`` means Fixed pool
    (``max := runner_pool_size``).

    Looked up only when an env's pool is first created. Changing the value
    afterwards takes effect on next API restart — the UI surfaces that.
    """
    if env_id is None:
        size = max(1, settings.runner_pool_size)
        return size, size
    try:
        async with SessionLocal() as session:
            env = await session.get(Environment, env_id)
            if env is None or env.runner_pool_size is None:
                size = max(1, settings.runner_pool_size)
                return size, size
            min_size = max(0, int(env.runner_pool_size))
            if env.runner_pool_max is not None:
                max_size = max(1, int(env.runner_pool_max))
            else:
                max_size = max(1, min_size)
            if max_size < max(1, min_size):
                max_size = max(1, min_size)
            return min_size, max_size
    except Exception:  # noqa: BLE001 - degrade gracefully if the DB is unavailable
        size = max(1, settings.runner_pool_size)
        return size, size


async def _resolve_env_rss_estimate(env_id: str | None) -> int:
    """Per-environment measured worker RSS estimate (bytes), or 0 if unknown.

    Populated by ``venv._measure_worker_rss`` after a build. 0 (no estimate,
    or no env) means the RSS gate treats the run as un-costed and never
    blocks on it. Looked up once when the env's pool is first created.
    """
    if env_id is None:
        return 0
    try:
        async with SessionLocal() as session:
            env = await session.get(Environment, env_id)
            if env is None or env.worker_rss_estimate_bytes is None:
                return 0
            return max(0, int(env.worker_rss_estimate_bytes))
    except Exception:  # noqa: BLE001 - degrade gracefully if the DB is unavailable
        return 0


async def _rss_soft_budget_bytes() -> int:
    """Current soft RSS budget (bytes). 0 disables RSS gating.

    Reads the live workspace setting (cached, 5s TTL) so an operator can tune
    it from the admin UI without a restart; falls back to the boot default.
    """
    try:
        from app.services.live_settings import get_live_settings  # noqa: PLC0415

        live = await get_live_settings()
        return max(0, int(live.worker_rss_soft_budget_bytes))
    except Exception:  # noqa: BLE001 - never let settings load block a dispatch
        return max(0, int(settings.worker_rss_soft_budget_bytes))


# Environment variables a runtime worker legitimately needs. Everything else
# — SECRET_KEY (the master KEK), DATABASE_URL, OAuth client secrets, cloud
# credentials — must NOT reach user code, which can trivially read
# ``os.environ`` from a Code node. The runtime itself reads only
# ``NOODLE_CODE_NODE_TIMEOUT_SECONDS`` (set explicitly below); the rest of the
# allowlist is OS plumbing the interpreter needs to boot and make TLS/temp-file
# syscalls work, cross-platform.
#
# Deliberately EXCLUDED: HTTP_PROXY, HTTPS_PROXY, NO_PROXY.  User code in Code
# / HTTP Request nodes that needs a corporate proxy must configure it at the
# node level (HTTP Request node params) rather than relying on environment
# variables — this prevents untrusted code from reaching internal networks
# through a proxy that the host implicitly trusts.
_WORKER_ENV_ALLOWLIST = frozenset(
    name.upper()
    for name in (
        "PATH",
        "HOME",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        # Windows essentials
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "COMSPEC",
        "WINDIR",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        # TLS trust stores commonly pointed at by env (certifi overrides)
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    )
)


_WORKER_ENV_CACHE: dict[str, str] | None = None
_WORKER_ENV_CACHE_AT: float = 0.0
_WORKER_ENV_CACHE_TTL = 60.0


def _worker_env() -> dict[str, str]:
    """Allowlisted environment for runtime worker subprocesses.

    Passes through OS plumbing and ``NOODLE_*`` variables only; never the
    API's secrets. Name matching is case-insensitive (Windows semantics).
    ``NOODLE_CODE_NODE_TIMEOUT_SECONDS`` is set fresh on every call so a
    live-settings change takes effect for the next spawned worker without
    waiting for the allowlist cache to expire.
    """
    global _WORKER_ENV_CACHE, _WORKER_ENV_CACHE_AT
    now = time.monotonic()
    if _WORKER_ENV_CACHE is not None and (now - _WORKER_ENV_CACHE_AT) < _WORKER_ENV_CACHE_TTL:
        env = dict(_WORKER_ENV_CACHE)
    else:
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in _WORKER_ENV_ALLOWLIST or key.upper().startswith("NOODLE_")
        }
        _WORKER_ENV_CACHE = dict(env)
        _WORKER_ENV_CACHE_AT = now
    env["NOODLE_CODE_NODE_TIMEOUT_SECONDS"] = str(
        settings.code_node_timeout_seconds
    )
    # SEC-3: egress policy for node HTTP/DB. If the operator pinned
    # NOODLE_ALLOW_PRIVATE_EGRESS it was copied through the allowlist above and
    # wins; otherwise the default follows the deployment model — hosted
    # multi-tenant blocks private targets (a tenant must never reach internal
    # services or 169.254.169.254), single-tenant self-hosted trusts its own
    # network so Ollama/localhost, VPC databases, and self-hosted integrations
    # work out of the box.
    if "NOODLE_ALLOW_PRIVATE_EGRESS" not in env:
        env["NOODLE_ALLOW_PRIVATE_EGRESS"] = (
            "0" if settings.multi_tenancy_enabled else "1"
        )
    return env


async def _org_subworkflow_cap(org_id: str) -> int:
    """The org's max in-flight sub-workflow spawns (C4).

    Org override via org_settings; 0/unset falls back to the global
    ``max_concurrent_subworkflows`` (itself falling back to
    ``max_concurrent_runs``). Degrades to the global cap if the DB is
    unreachable — a throttle must never block dispatch outright.
    """
    fallback = max(
        1, settings.max_concurrent_subworkflows or settings.max_concurrent_runs
    )
    try:
        from app.services.org_limits import effective_limits
        from app.tenancy import run_as_system

        with run_as_system():
            async with SessionLocal() as session:
                limits = await effective_limits(session, org_id)
        return max(1, limits.max_inflight_subworkflows) if (
            limits.max_inflight_subworkflows
        ) else fallback
    except Exception:  # noqa: BLE001
        return fallback


async def _org_run_limits_for(org_id: str) -> dict:
    """Amplification caps shipped to the engine in the run request (C5).

    The engine runs in a subprocess with no DB access, so limits resolve
    host-side at dispatch. Empty dict = uncapped (single-tenant, or a limits
    lookup failure — caps must never block dispatch outright)."""
    if not settings.multi_tenancy_enabled:
        return {}
    try:
        from app.services.org_limits import effective_limits
        from app.tenancy import run_as_system

        with run_as_system():
            async with SessionLocal() as session:
                limits = await effective_limits(session, org_id)
        return {
            "max_map_width": limits.max_map_width,
            "max_loop_iterations": limits.max_loop_iterations,
        }
    except Exception:  # noqa: BLE001
        return {}


async def _resolve_run_org(run_id: str) -> str:
    """The org a run belongs to, for artifact key namespacing (Phase F).

    Looked up under ``run_as_system`` because dispatch often happens from
    background loops with no request org context — the ORM filter would
    otherwise hide a non-default org's run row and misfile its artifacts.
    Falls back to the default org so single-tenant behaviour is unchanged.
    """
    from app.models import Run
    from app.tenancy import DEFAULT_ORG_ID, run_as_system

    try:
        with run_as_system():
            async with SessionLocal() as session:
                run = await session.get(Run, run_id)
                if run is not None and run.org_id:
                    return run.org_id
    except Exception:  # noqa: BLE001 - never let namespacing block a dispatch
        pass
    return DEFAULT_ORG_ID


async def _python_for_env(env_id: str | None) -> str:
    if env_id:
        candidate = await ensure_environment_ready(env_id)
        if not candidate.exists():
            raise RuntimeError(
                f"environment '{env_id}' Python was not found at {candidate}"
            )
        return str(candidate)
    return sys.executable


class _RuntimeProcess:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        env_id: str | None,
    ) -> None:
        self.process = process
        self.env_id = env_id
        self.dead = False
        self.idle_since = time.time()
        # Number of runs this process has serviced.  Used by the pool's
        # ``max_runs_per_subprocess`` cap to recycle processes before
        # accumulated global state from different workflows causes cross-
        # contamination (e.g. leaked module-level variables).
        self.run_count = 0
        self._run_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        # Background task that drains stderr so the OS pipe buffer never fills
        # and blocks the subprocess. Stderr lines are logged at debug level so
        # diagnostic output from the runtime is visible when needed.
        self._stderr_task: asyncio.Task[None] | None = None

    @classmethod
    async def spawn(cls, env_id: str | None) -> "_RuntimeProcess":
        python = await _python_for_env(env_id)
        env = _worker_env()
        process = await asyncio.create_subprocess_exec(
            python,
            "-u",
            "-m",
            "noodle_runtime",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        if process.stdout is None or process.stdin is None:
            raise RuntimeError("runtime subprocess pipes were not opened")
        _startup_timeout = 30.0
        try:
            line = await asyncio.wait_for(
                process.stdout.readline(), timeout=_startup_timeout
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(
                f"runtime for env {env_id!r} timed out waiting for ready event "
                f"({_startup_timeout}s)"
            ) from None
        if not line:
            raise RuntimeError(
                f"runtime for env {env_id!r} did not emit a ready event"
            )
        ready = json.loads(line)
        if ready.get("type") != "ready":
            raise RuntimeError(f"unexpected first event: {ready}")
        wp = cls(process, env_id)
        wp._start_stderr_consumer()
        return wp

    def _start_stderr_consumer(self) -> None:
        """Drain stderr in a background task so the OS pipe buffer never blocks
        the subprocess. Stale warnings/debug output is logged when non-empty."""
        if self.process.stderr is None:
            return

        async def _drain() -> None:
            try:
                while not self.dead:
                    line = await self.process.stderr.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if text:
                        logger.debug("runtime stderr (env=%s): %s", self.env_id, text)
            except Exception:
                pass

        self._stderr_task = asyncio.create_task(_drain())

    async def _write_message(self, message: dict) -> None:
        if self.process.stdin is None:
            raise RuntimeError("runtime subprocess stdin is closed")
        payload = json.dumps(serialize_value(message)).encode() + b"\n"
        async with self._write_lock:
            self.process.stdin.write(payload)
            await self.process.stdin.drain()

    async def _handle_call_workflow(
        self,
        event: dict,
        subworkflow_resolver: SubworkflowResolver | None,
    ) -> None:
        from noodle.engine.subworkflows import InlineSubworkflow, SubworkflowCall

        callback_id = event.get("callback_id", "")
        try:
            if subworkflow_resolver is None:
                raise RuntimeError(
                    "subprocess runner has no host-side sub-workflow resolver"
                )
            call = SubworkflowCall.from_payload(
                {**event, "input": deserialize_value(event.get("input"))}
            )
            outcome = await subworkflow_resolver(call, parent_env_id=self.env_id)
            if isinstance(outcome, InlineSubworkflow):
                await self._write_message(
                    {
                        "type": "call_workflow_response",
                        "callback_id": callback_id,
                        "inline_graph": outcome.graph,
                        "inline_cache": outcome.cache,
                        "inline_targets": outcome.targets,
                        "inline_sources": list(outcome.sources),
                    }
                )
            else:
                await self._write_message(
                    {
                        "type": "call_workflow_response",
                        "callback_id": callback_id,
                        "result": outcome,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - surface back to subprocess
            await self._write_message(
                {
                    "type": "call_workflow_error",
                    "callback_id": callback_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    async def run(
        self,
        run_id: str,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        on_event: EventCallback,
        subworkflow_resolver: SubworkflowResolver | None = None,
        subworkflow_meta: dict | None = None,
        workflow_modules: list[dict] | None = None,
        pause_on_approval: bool = False,
        agent_action_resume: dict | None = None,
        artifact_key_prefix: str = "",
        org_limits: dict | None = None,
    ) -> str:
        async with self._run_lock:
            if self.dead or self.process.returncode is not None:
                self.dead = True
                raise RuntimeError("runtime subprocess has exited")
            if self.process.stdin is None or self.process.stdout is None:
                raise RuntimeError("runtime subprocess has closed its pipes")

            request_id = uuid.uuid4().hex
            callbacks: set[asyncio.Task] = set()
            recycle_after_run = False
            try:
                await self._write_message(
                    {
                        "type": "run",
                        "request_id": request_id,
                        "run_id": run_id,
                        "graph": graph,
                        "cache": cache,
                        "targets": targets,
                        "workflow_modules": workflow_modules or [],
                        "pause_on_approval": pause_on_approval,
                        "agent_action_resume": agent_action_resume or {},
                        "subworkflow_meta": subworkflow_meta or {},
                        # Subprocess writes artifact bytes to the SAME path the
                        # API reads from — only safe because the runner is
                        # co-located on the host today. Remote runners (Slice 8)
                        # will need an upload/finalize path instead.
                        "artifacts_dir": str(artifact_base_dir()),
                        "artifact_key_prefix": artifact_key_prefix,
                        "org_limits": org_limits or {},
                        "max_artifact_bytes": settings.max_artifact_bytes,
                        "max_artifacts_per_run": settings.max_artifacts_per_run,
                    }
                )
                while True:
                    line = await self.process.stdout.readline()
                    if not line:
                        self.dead = True
                        raise RuntimeError("runtime subprocess closed stdout")
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Sub-workflow callback from the subprocess — handle it on a
                    # task so the read loop keeps draining the pipe.
                    if event.get("type") == "call_workflow":
                        task = asyncio.create_task(
                            self._handle_call_workflow(event, subworkflow_resolver)
                        )
                        callbacks.add(task)
                        task.add_done_callback(callbacks.discard)
                        continue

                    if event.get("request_id") != request_id:
                        continue
                    kind = event.get("type")
                    if kind == "result":
                        if callbacks:
                            await asyncio.gather(*callbacks, return_exceptions=True)
                        status = str(event.get("status", "success"))
                        if recycle_after_run:
                            await self.close()
                        return status
                    if kind == "error":
                        raise RuntimeError(event.get("error", "runtime error"))
                    clean = {k: v for k, v in event.items() if k != "request_id"}
                    if (
                        clean.get("type") == "node_finished"
                        and clean.get("status") == "error"
                        and "timed out" in str(clean.get("error") or "")
                    ):
                        recycle_after_run = True
                    await on_event(clean)
            except asyncio.CancelledError:
                await self.close()
                raise
            finally:
                # Never leave sub-workflow callback tasks running detached. On a
                # clean result they were already awaited above; on any error
                # (e.g. the runtime emitted an "error" event, or stdout closed)
                # they would otherwise keep writing to this worker's stdin while
                # it gets recycled into the idle pool — corrupting the next run's
                # stdio. Cancel and drain whatever remains.
                pending = [t for t in callbacks if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

    async def close(self) -> None:
        if self.process.returncode is not None:
            self.dead = True
            await self._cancel_stderr_consumer()
            return
        try:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        except ProcessLookupError:
            pass
        finally:
            self.dead = True
            await self._cancel_stderr_consumer()

    async def _cancel_stderr_consumer(self) -> None:
        if self._stderr_task is not None and not self._stderr_task.done():
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
            self._stderr_task = None


class _EnvPool:
    """Elastic pool of warm runner processes for one environment.

    Three user-facing presets, one mechanism:

    - **Fixed** (``min_size == max_size``): exactly N workers, always alive.
    - **Elastic** (``min_size >= 1`` and ``max_size > min_size``): keep
      ``min_size`` warm; burst up to ``max_size``; surplus dies via the
      idle reaper.
    - **Spawn-per-run** (``min_size == 0``, ``max_size >= 1``): no warm
      workers; every release closes the worker immediately.

    The semaphore is sized to ``max_size`` so concurrent runs are bounded
    by that ceiling. Idle processes are reused; dead ones are dropped.
    """

    def __init__(
        self, env_id: str | None, min_size: int, max_size: int,
        rss_estimate: int = 0, max_runs_per_subprocess: int = 0,
    ) -> None:
        self.env_id = env_id
        self.min_size = max(0, min_size)
        self.max_size = max(1, max_size, self.min_size)
        # Measured per-worker RSS for this env (bytes), used by the pool's
        # soft RSS budget. 0 → un-costed (never blocks the budget gate).
        self.rss_estimate = max(0, rss_estimate)
        # Maximum runs a single subprocess services before being recycled.
        # 0 (default) → unlimited.  A positive value prevents gradual state
        # accumulation from different workflows sharing the same warm process.
        self.max_runs_per_subprocess = max(0, max_runs_per_subprocess)
        self._sem = asyncio.Semaphore(self.max_size)
        self._idle: list[_RuntimeProcess] = []
        self._all: set[_RuntimeProcess] = set()
        self._lock = asyncio.Lock()
    @staticmethod
    def _alive(proc: _RuntimeProcess) -> bool:
        return not proc.dead and proc.process.returncode is None

    def _should_recycle(self, proc: _RuntimeProcess) -> bool:
        if self.max_runs_per_subprocess <= 0:
            return False
        return proc.run_count >= self.max_runs_per_subprocess

    async def acquire(self) -> _RuntimeProcess:
        await self._sem.acquire()
        async with self._lock:
            while self._idle:
                cand = self._idle.pop()
                if self._alive(cand):
                    if self._should_recycle(cand):
                        # Process has hit its run cap — close it
                        # asynchronously and spawn a fresh one.
                        self._all.discard(cand)
                        asyncio.create_task(cand.close())
                        break
                    return cand
                self._all.discard(cand)
        try:
            proc = await _RuntimeProcess.spawn(self.env_id)
        except BaseException:
            self._sem.release()
            raise
        async with self._lock:
            self._all.add(proc)
        return proc

    def release(self, proc: _RuntimeProcess) -> None:
        # Sync (no await) so it cannot interleave mid-statement with acquire.
        # Spawn-per-run preset: never keep workers warm.
        if self.min_size == 0:
            self._all.discard(proc)
            # Fire-and-forget close; don't block the dispatch caller.
            asyncio.create_task(proc.close())
            self._sem.release()
            return
        if self._alive(proc):
            proc.idle_since = time.time()
            self._idle.append(proc)
        else:
            self._all.discard(proc)
        self._sem.release()

    async def reap_idle(self, threshold_seconds: float) -> int:
        """Close warm processes idle past the threshold, respecting ``min_size``.

        Returns the number of processes closed. Closes oldest-idle first so
        the floor is filled by the freshest workers. Safe to call
        concurrently with acquire/release because we mutate ``_idle`` under
        the lock, and idle processes hold no semaphore slot.
        """
        if threshold_seconds <= 0:
            return 0
        now = time.time()
        to_close: list[_RuntimeProcess] = []
        async with self._lock:
            live_count = sum(1 for proc in self._all if self._alive(proc))
            surplus = max(0, live_count - self.min_size)
            if surplus == 0:
                return 0
            # Idle list is append-order (newest on the right). Oldest-idle is
            # the leftmost. Close those first, up to ``surplus``.
            ordered = sorted(
                self._idle,
                key=lambda p: getattr(p, "idle_since", now),
            )
            kept: list[_RuntimeProcess] = []
            for proc in ordered:
                idle_since = getattr(proc, "idle_since", now)
                if surplus > 0 and now - idle_since > threshold_seconds:
                    to_close.append(proc)
                    self._all.discard(proc)
                    surplus -= 1
                else:
                    kept.append(proc)
            self._idle = kept
        for proc in to_close:
            await proc.close()
        return len(to_close)

    async def close(self) -> None:
        async with self._lock:
            procs = list(self._all)
            self._all.clear()
            self._idle.clear()
        for proc in procs:
            await proc.close()


class _RssBudget:
    """Soft admission gate bounding the *sum* of estimated worker RSS across
    concurrently executing top-level runs.

    Complements the count-based ``_global_sem``: N concurrent runs of a
    1.2 GB env and N of an 80 MB env have wildly different memory cost, and a
    pure count cap can't tell them apart. Before a run acquires a worker it
    reserves its env's measured ``worker_rss_estimate_bytes`` here; the
    reservation is released when the run finishes, so the pool admits "as many
    runs as fit the memory budget" rather than a flat count.

    Soft by construction:

    * ``budget <= 0`` (unset) or ``estimate <= 0`` (env never measured) → no
      gating; the context manager is a no-op.
    * A run is ALWAYS admitted when nothing else is reserved, even if its
      estimate alone exceeds the budget — otherwise an env bigger than the
      whole budget could never run. This also guarantees forward progress
      (when all in-flight runs drain, ``_committed`` hits 0 and the next
      waiter is admitted), so the gate can never deadlock.

    Only ever consulted from top-level ``dispatch`` — never from
    sub-workflows — so a parent holding a reservation while awaiting a child
    can't deadlock (mirrors the ``_global_sem`` / ``dispatch_subworkflow``
    split).
    """

    def __init__(self) -> None:
        self._committed = 0
        self._cond = asyncio.Condition()

    @property
    def committed_bytes(self) -> int:
        return self._committed

    @contextlib.asynccontextmanager
    async def reserve(self, estimate: int, budget: int) -> AsyncIterator[None]:
        amount = estimate if (budget > 0 and estimate > 0) else 0
        if amount <= 0:
            yield
            return
        async with self._cond:
            # Admit immediately when nothing else is reserved (forward-progress
            # guarantee), otherwise wait until the new total fits the budget.
            while self._committed > 0 and self._committed + amount > budget:
                await self._cond.wait()
            self._committed += amount
        try:
            yield
        finally:
            async with self._cond:
                self._committed = max(0, self._committed - amount)
                self._cond.notify_all()


class RuntimePool:
    def __init__(self) -> None:
        self._envs: dict[str, _EnvPool] = {}
        self._lock = asyncio.Lock()
        # Global ceiling on simultaneously executing top-level runs. Acquired
        # only here in ``dispatch`` — never around the in-process engine — so
        # sub-workflows (which call the engine directly, not the pool) consume
        # no slot and cannot deadlock a parent that is waiting on them.
        self._global_sem = asyncio.Semaphore(max(1, settings.max_concurrent_runs))
        self._max_concurrent_runs = max(1, settings.max_concurrent_runs)
        self._scale_lock = asyncio.Lock()
        # Soft fan-out throttle for sub-workflow subprocess spawns. Separate
        # from ``_global_sem``/pool sems on purpose (the parent already holds
        # those). See ``subworkflow_slot`` for why it's a soft cap.
        sub_cap = settings.max_concurrent_subworkflows or settings.max_concurrent_runs
        self._subworkflow_sem = asyncio.Semaphore(max(1, sub_cap))
        # C4: with multi-tenancy on, each org throttles its own sub-workflow
        # fan-out (a wide map in one org must not exhaust the shared spawn
        # budget). Lazily populated per org; cleared by the test reset hook.
        self._org_subworkflow_sems: dict[str, asyncio.Semaphore] = {}
        # Soft memory ceiling across concurrent top-level runs. See _RssBudget.
        self._rss_budget = _RssBudget()

    def has_immediate_capacity(self) -> bool:
        """Best-effort, non-blocking probe used by the local durable queue to
        decide whether a fresh local run can be dispatched now or should wait
        as a queued entry.

        Only checks the global concurrency slot (a free slot ⇒ ``True``). The
        RSS budget is intentionally NOT probed here: it's enforced inside
        ``dispatch`` and a small over-admission just means a run briefly waits
        on the budget instead of in the queue — harmless and self-correcting.
        Subject to a benign TOCTOU race with the real acquire in ``dispatch``;
        the semaphore remains the actual enforcement.
        """
        return not self._global_sem.locked()

    async def resize(self, new_max: int) -> int:
        """Adjust the global concurrency ceiling at runtime.

        Increasing capacity creates a new semaphore with the extra permits
        already available.  Decreasing capacity lets existing permits drain
        naturally — in-flight runs are never cancelled.

        Returns the new effective max.
        """
        new_max = max(1, new_max)
        async with self._scale_lock:
            if new_max == self._max_concurrent_runs:
                return new_max
            old_value = self._max_concurrent_runs
            self._max_concurrent_runs = new_max
            if new_max > old_value:
                # Growing: create a new semaphore with extra permits.
                # Existing waiters remain on the old semaphore until they
                # acquire; new callers use the new (larger) one.
                delta = new_max - old_value
                new_sem = asyncio.Semaphore(new_max)
                # Transfer: release delta permits to match the new capacity.
                for _ in range(delta):
                    new_sem._value += 1  # noqa: SLF001 — internal field is documented in CPython
                self._global_sem = new_sem
            return new_max

    def current_max_slots(self) -> int:
        """Return the current max concurrency ceiling."""
        return self._max_concurrent_runs

    def available_global_slots(self) -> int:
        """Best-effort count of free global concurrency slots.

        Used by the local durable-queue dispatch loop to bound how many local
        runs it leases per tick (leasing more than there are slots would just
        pile up blocked coroutines). Reads the semaphore's internal permit
        counter — best-effort and may briefly over/under-count under races;
        the semaphore itself remains the real enforcement.
        """
        return max(0, getattr(self._global_sem, "_value", 0))

    def global_slot(self) -> asyncio.Semaphore:
        """The global ``max_concurrent_runs`` ceiling as an async context
        manager. Used by the in-process top-level path (runner) so it honours
        the same admission cap as subprocess ``dispatch``. Never acquire this
        around a sub-workflow call — the parent already holds a slot, so doing
        so would deadlock (mirrors the ``dispatch_subworkflow`` bypass).
        """
        return self._global_sem

    @contextlib.asynccontextmanager
    async def subworkflow_slot(self) -> AsyncIterator[None]:
        """Best-effort throttle on concurrently-spawned sub-workflow processes.

        Bounds fan-out — e.g. a parent that calls 100 sub-workflows at once —
        so the host can't be swamped by a burst of fresh subprocesses.

        Deliberately a SOFT cap. Sub-workflows nest (A→B→C) and every ancestor
        holds its slot while awaiting the child, so a hard cap would deadlock
        any chain deeper than the cap. Instead we wait up to
        ``subworkflow_spawn_timeout_seconds`` for a slot and then proceed
        anyway: wide fan-out gets throttled (siblings queue briefly) while
        legitimate nesting never blocks indefinitely.
        """
        sem = self._subworkflow_sem
        if settings.multi_tenancy_enabled:
            from app.tenancy import active_org_id

            org_key = active_org_id() or "default"
            sem = self._org_subworkflow_sems.get(org_key)
            if sem is None:
                sem = asyncio.Semaphore(await _org_subworkflow_cap(org_key))
                self._org_subworkflow_sems[org_key] = sem
        acquired = False
        try:
            await asyncio.wait_for(
                sem.acquire(),
                timeout=settings.subworkflow_spawn_timeout_seconds,
            )
            acquired = True
        except TimeoutError:
            logger.warning(
                "sub-workflow spawn throttle exhausted after %ss — proceeding "
                "without a slot (soft cap)",
                settings.subworkflow_spawn_timeout_seconds,
            )
        try:
            yield
        finally:
            if acquired:
                sem.release()

    async def _env_pool(self, env_id: str | None) -> _EnvPool:
        key = env_id or "_default"
        async with self._lock:
            envpool = self._envs.get(key)
            if envpool is None:
                min_size, max_size = await _resolve_pool_sizes(env_id)
                rss_estimate = await _resolve_env_rss_estimate(env_id)
                envpool = _EnvPool(
                    env_id, min_size, max_size, rss_estimate,
                    max_runs_per_subprocess=settings.runner_max_runs_per_subprocess,
                )
                self._envs[key] = envpool
            return envpool

    async def dispatch(
        self,
        run_id: str,
        env_id: str | None,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        on_event: EventCallback,
        subworkflow_resolver: SubworkflowResolver | None = None,
        subworkflow_meta: dict | None = None,
        workflow_modules: list[dict] | None = None,
        run_timeout: float | None = None,
        pause_on_approval: bool = False,
        agent_action_resume: dict | None = None,
    ) -> str:
        envpool = await self._env_pool(env_id)
        budget = await _rss_soft_budget_bytes()
        # Reserve memory budget BEFORE taking a concurrency slot so a
        # memory-blocked run doesn't sit on a precious ``_global_sem`` slot
        # while it waits. Consistent acquire order (rss → sem → worker) across
        # all callers keeps the two gates deadlock-free.
        async with self._rss_budget.reserve(envpool.rss_estimate, budget):
            async with self._global_sem:
                proc = await envpool.acquire()
                # Per-workflow override wins; None falls back to the global setting.
                timeout = (
                    run_timeout
                    if run_timeout is not None
                    else settings.workflow_run_timeout_seconds
                )
                try:
                    run_org = await _resolve_run_org(run_id)
                    run = proc.run(
                        run_id,
                        graph,
                        cache,
                        targets,
                        on_event,
                        subworkflow_resolver,
                        subworkflow_meta=subworkflow_meta,
                        workflow_modules=workflow_modules,
                        pause_on_approval=pause_on_approval,
                        agent_action_resume=agent_action_resume,
                        artifact_key_prefix=run_org,
                        org_limits=await _org_run_limits_for(run_org),
                    )
                    if timeout and timeout > 0:
                        result = await asyncio.wait_for(run, timeout=timeout)
                    else:
                        result = await run
                    proc.run_count += 1
                    return result
                except TimeoutError as exc:
                    await proc.close()
                    raise RuntimeError(
                        f"workflow run timed out after {timeout}s"
                    ) from exc
                finally:
                    envpool.release(proc)

    async def dispatch_subworkflow(
        self,
        run_id: str,
        env_id: str | None,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        on_event: EventCallback,
        subworkflow_resolver: SubworkflowResolver | None = None,
        subworkflow_meta: "SubworkflowMeta | None" = None,
        workflow_modules: list[dict] | None = None,
    ) -> str:
        """Run a sub-workflow in its env's subprocess, bypassing the pool caps.

        Both the global ``max_concurrent_runs`` semaphore and the per-env
        pool semaphore are skipped on purpose: the parent run that
        emitted the ``call_workflow`` event already holds both slots, and
        making the sub-call wait for them would deadlock whenever a
        parent + sub share an env at the cap.

        A fresh short-lived ``_RuntimeProcess`` is spawned and closed
        after the sub run finishes — it isn't pooled because pooling
        them would re-introduce the cap math we're trying to bypass.
        Memory cost: one extra subprocess per in-flight sub-workflow
        call, which goes away the moment the parent's
        ``execute_workflow`` node returns.

        To stop unbounded fan-out (a parent calling many subs at once, or
        deep nesting) from OOMing the host, the spawn is wrapped in the
        SOFT ``subworkflow_slot`` throttle — see that method for why it
        can't be a hard cap without re-introducing deadlock.
        """
        async with self.subworkflow_slot():
            proc = await _RuntimeProcess.spawn(env_id)
            try:
                run_org = await _resolve_run_org(run_id)
                run = proc.run(
                    run_id,
                    graph,
                    cache,
                    targets,
                    on_event,
                    subworkflow_resolver,
                    subworkflow_meta=(
                        subworkflow_meta.to_payload() if subworkflow_meta else {}
                    ),
                    workflow_modules=workflow_modules,
                    artifact_key_prefix=run_org,
                    org_limits=await _org_run_limits_for(run_org),
                )
                timeout = settings.workflow_run_timeout_seconds
                if timeout and timeout > 0:
                    return await asyncio.wait_for(run, timeout=timeout)
                return await run
            except TimeoutError as exc:
                raise RuntimeError(
                    f"sub-workflow run timed out after "
                    f"{settings.workflow_run_timeout_seconds}s"
                ) from exc
            finally:
                await proc.close()

    async def reap_idle(self, threshold_seconds: float) -> int:
        """Sweep every env pool, closing warm processes idle past the threshold."""
        if threshold_seconds <= 0:
            return 0
        async with self._lock:
            envs = list(self._envs.values())
        closed = 0
        for envpool in envs:
            closed += await envpool.reap_idle(threshold_seconds)
        return closed

    async def shutdown(self) -> None:
        async with self._lock:
            envs = list(self._envs.values())
            self._envs.clear()
        for envpool in envs:
            await envpool.close()


pool = RuntimePool()


async def idle_reaper_loop() -> None:
    """Background loop that reaps warm processes idle past the threshold.

    Cancellation-friendly (graceful shutdown sends a CancelledError that
    breaks the sleep). Errors per tick are swallowed so a transient
    subprocess hiccup doesn't kill the loop.
    """
    from app.services.live_settings import get_live_settings

    while True:
        try:
            live = await get_live_settings()
            threshold = live.runner_idle_seconds
            if threshold > 0:
                await pool.reap_idle(threshold)
        except Exception:  # noqa: BLE001 - a bad sweep must not kill the loop
            pass
        await asyncio.sleep(max(15, settings.runner_idle_tick_seconds))
