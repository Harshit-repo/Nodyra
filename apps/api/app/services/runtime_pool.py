"""Env-runner subprocess pool.

When ``settings.use_subprocess_runner`` is enabled, the runner dispatches
executions to a long-lived ``nodyra_runtime`` subprocess per environment id.
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
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nodyra.engine.subworkflows import SubworkflowMeta

from app.config import settings
from app.db import SessionLocal
from app.models import Environment
from app.services.artifacts import artifact_base_dir
from app.services.stream_limits import stream_limit_bytes
from app.services.venv import ensure_environment_ready
from nodyra.serialization import deserialize_value, serialize_value

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


async def _resolve_env_runtime_flags(env_id: str | None) -> dict[str, bool]:
    """Per-environment runtime flags ({"jit": bool, "lazy_imports": bool}).

    Read fresh on every worker spawn (spawns are rare and already pay a DB
    round-trip via ensure_environment_ready), so a PATCH takes effect for the
    next worker without an API restart. Any failure degrades to {} — flags are
    accelerators, never a reason to fail a dispatch.
    """
    if env_id is None:
        return {}
    try:
        async with SessionLocal() as session:
            env = await session.get(Environment, env_id)
            if env is None:
                return {}
            return {k: bool(v) for k, v in (env.runtime_flags or {}).items() if v}
    except Exception:  # noqa: BLE001 - degrade gracefully if the DB is unavailable
        return {}


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
# ``NODYRA_CODE_NODE_TIMEOUT_SECONDS`` (set explicitly below); the rest of the
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

    Passes through OS plumbing and ``NODYRA_*`` variables only; never the
    API's secrets. Name matching is case-insensitive (Windows semantics).
    Runtime timeout and heartbeat settings are set fresh on every call so a
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
            if key.upper() in _WORKER_ENV_ALLOWLIST
            or key.upper().startswith("NODYRA_")
            or key.upper() == "NOODLE_ALLOW_PRIVATE_EGRESS"
        }
        _WORKER_ENV_CACHE = dict(env)
        _WORKER_ENV_CACHE_AT = now
    env["NODYRA_CODE_NODE_TIMEOUT_SECONDS"] = str(settings.code_node_timeout_seconds)
    env["NODYRA_RUNTIME_HEARTBEAT_SECONDS"] = str(settings.runtime_heartbeat_interval_seconds)
    # SEC-3: egress policy for node HTTP/DB. If the operator pinned
    # NODYRA_ALLOW_PRIVATE_EGRESS it was copied through the allowlist above and
    # wins; otherwise the default follows the deployment model — hosted
    # multi-tenant blocks private targets (a tenant must never reach internal
    # services or 169.254.169.254), single-tenant self-hosted trusts its own
    # network so Ollama/localhost, VPC databases, and self-hosted integrations
    # work out of the box.
    if "NODYRA_ALLOW_PRIVATE_EGRESS" not in env and "NOODLE_ALLOW_PRIVATE_EGRESS" in env:
        env["NODYRA_ALLOW_PRIVATE_EGRESS"] = env["NOODLE_ALLOW_PRIVATE_EGRESS"]
    if "NODYRA_ALLOW_PRIVATE_EGRESS" not in env:
        env["NODYRA_ALLOW_PRIVATE_EGRESS"] = "0" if settings.multi_tenancy_enabled else "1"
    return env


async def _org_subworkflow_cap(org_id: str) -> int:
    """The org's max in-flight sub-workflow spawns (C4).

    Org override via org_settings; 0/unset falls back to the global
    ``max_concurrent_subworkflows`` (itself falling back to
    ``max_concurrent_runs``). Degrades to the global cap if the DB is
    unreachable — a throttle must never block dispatch outright.
    """
    fallback = max(1, settings.max_concurrent_subworkflows or settings.max_concurrent_runs)
    try:
        from app.services.org_limits import effective_limits
        from app.tenancy import run_as_system

        with run_as_system():
            async with SessionLocal() as session:
                limits = await effective_limits(session, org_id)
        return (
            max(1, limits.max_inflight_subworkflows)
            if (limits.max_inflight_subworkflows)
            else fallback
        )
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
            raise RuntimeError(f"environment '{env_id}' Python was not found at {candidate}")
        return str(candidate)
    return sys.executable


_STREAM_LIMIT_BYTES: int = stream_limit_bytes()


async def _drain_startup_stderr(
    process: asyncio.subprocess.Process, *, limit: int = 2000, timeout: float = 2.0
) -> str:
    """Return whatever a failed-to-start worker wrote to stderr, for the error.

    Best-effort and time-boxed: a worker that died has already closed the pipe,
    and one that merely hung must not make the caller wait a second time.
    Returns "" when there is nothing to add, so callers can append it directly.
    """
    if process.stderr is None:
        return ""
    try:
        raw = await asyncio.wait_for(process.stderr.read(limit), timeout=timeout)
    except Exception:  # noqa: BLE001 - diagnosing a failure must not raise
        return ""
    text = raw.decode("utf-8", errors="replace").strip()
    return f": {text}" if text else ""


class _PoolPaused(Exception):
    """The environment is being rebuilt; runs park in the durable queue.

    Raised by ``_EnvPool.acquire`` while the pool is paused so a rebuild can
    replace the environment directory on Windows (loaded ``.pyd`` files lock
    it). The runner catches this and requeues the run with backoff.
    """


class _RuntimeProcess:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        env_id: str | None,
    ) -> None:
        self.process = process
        self.env_id = env_id
        self.dead = False
        # Set by _EnvPool.acquire when the worker is admitted; workers with a
        # generation older than the pool's are closed on release (see
        # _EnvPool.drain).
        self.generation = 0
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
        flags = await _resolve_env_runtime_flags(env_id)
        # PYTHON_JIT / PYTHON_LAZY_IMPORTS are read by CPython at startup; unknown
        # or unsupported vars are ignored by the interpreter, so passing them to an
        # interpreter without the feature is harmless by design.
        if flags.get("jit"):
            env["PYTHON_JIT"] = "1"
        if flags.get("lazy_imports"):
            env["PYTHON_LAZY_IMPORTS"] = "1"
        process = await asyncio.create_subprocess_exec(
            python,
            "-u",
            "-m",
            "nodyra_runtime",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=_STREAM_LIMIT_BYTES,
        )
        if process.stdout is None or process.stdin is None:
            raise RuntimeError("runtime subprocess pipes were not opened")
        _startup_timeout = 30.0
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=_startup_timeout)
        except TimeoutError:
            detail = await _drain_startup_stderr(process)
            process.kill()
            await process.wait()
            raise RuntimeError(
                f"runtime for env {env_id!r} timed out waiting for ready event "
                f"({_startup_timeout}s){detail}"
            ) from None
        if not line:
            # The worker died before saying hello. Its stderr holds the reason —
            # a SyntaxError in a node module, a missing dependency, an OOM kill —
            # and nothing else will ever report it: the stderr consumer below
            # only starts once a worker is ready. Reporting "did not emit a ready
            # event" on its own sends the reader looking in the wrong place.
            detail = await _drain_startup_stderr(process)
            raise RuntimeError(f"runtime for env {env_id!r} exited before it was ready{detail}")
        ready = json.loads(line)
        if ready.get("type") != "ready":
            raise RuntimeError(f"unexpected first event: {ready}")
        # .get() — old workers without the field must keep working (rolling deploys).
        logger.info("runtime worker ready env=%s startup_ms=%s", env_id, ready.get("startup_ms"))
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
        *,
        run_id: str | None = None,
    ) -> None:
        from nodyra.engine.subworkflows import InlineSubworkflow, SubworkflowCall

        callback_id = event.get("callback_id", "")
        try:
            if subworkflow_resolver is None:
                raise RuntimeError("subprocess runner has no host-side sub-workflow resolver")
            from app.models import Run
            from app.tenancy import DEFAULT_ORG_ID, run_as_system

            org_id = DEFAULT_ORG_ID
            if run_id is not None:
                with run_as_system():
                    async with SessionLocal() as session:
                        parent = await session.get(Run, run_id)
                        if parent is None:
                            raise ValueError("Sub-workflow parent run no longer exists")
                        org_id = parent.org_id
            call = SubworkflowCall.from_payload({
                **event,
                "input": deserialize_value(event.get("input")),
                "parent_run_id": run_id,
                "org_id": org_id,
            })
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

    async def _handle_call_mcp_tool(self, event: dict, run_id: str) -> None:
        """Mirror of the in-process MCP hook: resolve the connection (org
        scoping, secret decryption, allowlist, audit) and answer the runtime."""
        from app.services.mcp_gateway import execute_for_run

        callback_id = event.get("callback_id", "")
        try:
            connection_id = str(event.get("connection_id") or "")
            tool_name = str(event.get("tool_name") or "")
            arguments = event.get("arguments", {})
            async with SessionLocal() as session:
                result = await execute_for_run(
                    session,
                    connection_id,
                    tool_name,
                    arguments,
                    run_id=run_id,
                )
            await self._write_message(
                {
                    "type": "call_mcp_tool_response",
                    "callback_id": callback_id,
                    "result": result,
                }
            )
        except Exception as exc:  # noqa: BLE001 - surface back to the runtime
            try:
                await self._write_message(
                    {
                        "type": "call_mcp_tool_error",
                        "callback_id": callback_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            except RuntimeError:
                pass  # worker died; the read loop reports it

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
            last_liveness_at = time.monotonic()
            last_progress_at = last_liveness_at
            heartbeat_timeout = settings.runtime_heartbeat_timeout_seconds
            no_progress_timeout = settings.runtime_no_progress_timeout_seconds
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
                    heartbeat_remaining = heartbeat_timeout - (time.monotonic() - last_liveness_at)
                    if heartbeat_remaining <= 0:
                        await self.close()
                        raise RuntimeError(
                            f"runtime heartbeat lost for run {run_id!r} after "
                            f"{heartbeat_timeout:g}s"
                        )
                    try:
                        line = await asyncio.wait_for(
                            self.process.stdout.readline(),
                            timeout=heartbeat_remaining,
                        )
                    except TimeoutError:
                        await self.close()
                        raise RuntimeError(
                            f"runtime heartbeat lost for run {run_id!r} after "
                            f"{heartbeat_timeout:g}s"
                        ) from None
                    if not line:
                        self.dead = True
                        raise RuntimeError("runtime subprocess closed stdout")
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Rolling-upgrade compatibility: older runtimes did not put
                    # request_id on call_workflow callbacks. Reject an explicit
                    # different id, but temporarily accept a missing one.
                    event_request_id = event.get("request_id")
                    if event_request_id not in (None, request_id):
                        continue

                    now = time.monotonic()
                    last_liveness_at = now
                    kind = event.get("type")
                    if kind == "heartbeat":
                        if no_progress_timeout > 0 and (
                            now - last_progress_at >= no_progress_timeout
                        ):
                            await self.close()
                            raise RuntimeError(
                                f"runtime run {run_id!r} made no protocol progress for "
                                f"{no_progress_timeout:g}s while heartbeats continued"
                            )
                        continue

                    # Sub-workflow callback from the subprocess — handle it on a
                    # task so the read loop keeps draining the pipe.
                    if kind == "call_workflow":
                        last_progress_at = now
                        task = asyncio.create_task(
                            self._handle_call_workflow(event, subworkflow_resolver, run_id=run_id)
                        )
                        callbacks.add(task)
                        task.add_done_callback(callbacks.discard)
                        continue

                    # MCP tool call from the subprocess — same task pattern.
                    if kind == "call_mcp_tool":
                        last_progress_at = now
                        task = asyncio.create_task(self._handle_call_mcp_tool(event, run_id))
                        callbacks.add(task)
                        task.add_done_callback(callbacks.discard)
                        continue

                    if event_request_id != request_id:
                        continue
                    last_progress_at = now
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
                self._kill_tree()
                await self.process.wait()
        except ProcessLookupError:
            pass
        finally:
            self.dead = True
            await self._cancel_stderr_consumer()

    def _kill_tree(self) -> None:
        """Kill the worker and its whole descendant tree.

        On Windows ``subprocess.kill`` only kills the direct child: the
        runtime's process-isolator workers (multiprocessing spawn
        grandchildren) would be orphaned and keep the env venv's ``.pyd``
        files loaded, locking the environment directory against rebuilds
        (PermissionError). ``taskkill /T`` removes the full tree.
        """
        if os.name != "nt" or self.process.pid is None:
            self.process.kill()
            return
        try:
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()

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
        self,
        env_id: str | None,
        min_size: int,
        max_size: int,
        rss_estimate: int = 0,
        max_runs_per_subprocess: int = 0,
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
        # Bumped whenever the environment is rebuilt/replaced. Workers stamped
        # with an older generation are closed on release instead of returning
        # to the idle list — a warm worker running the previous environment's
        # code must never serve runs after a rebuild, and on Windows a live
        # worker's loaded DLLs would otherwise block the rebuild itself.
        self._generation = 0
        # Set while the environment is being rebuilt: acquire() parks runs in
        # the durable queue instead of spawning workers against a half-built
        # environment directory.
        self._paused = False

    @staticmethod
    def _alive(proc: _RuntimeProcess) -> bool:
        return not proc.dead and proc.process.returncode is None

    def _should_recycle(self, proc: _RuntimeProcess) -> bool:
        if self.max_runs_per_subprocess <= 0:
            return False
        return proc.run_count >= self.max_runs_per_subprocess

    async def acquire(self) -> _RuntimeProcess:
        await self._sem.acquire()
        if self._paused:
            self._sem.release()
            raise _PoolPaused(self.env_id)
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
            proc.generation = self._generation
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
        if self._alive(proc) and proc.generation == self._generation:
            proc.idle_since = time.time()
            self._idle.append(proc)
        else:
            # Worker died, or its environment has since been drained/rebuilt —
            # never keep a stale worker warm.
            self._all.discard(proc)
            asyncio.create_task(proc.close())
        self._sem.release()

    async def drain(self, *, force: bool = False) -> None:
        """Close workers so a rebuild can replace the environment directory.

        Idle workers are always closed. With ``force=True`` (the rebuild
        path), in-flight workers are terminated too — on Windows their loaded
        ``.pyd`` files lock the environment directory, and waiting for a
        wedged or long-running run would block the rebuild indefinitely.
        Their runs surface as errors when the worker's stdout closes. The
        pool is paused until ``unpause()`` so no new workers spawn against
        the half-built environment directory.
        """
        async with self._lock:
            self._generation += 1
            to_close = list(self._idle)
            self._idle.clear()
            for proc in to_close:
                self._all.discard(proc)
            if force:
                to_close.extend(proc for proc in self._all if self._alive(proc))
                self._all.clear()
                self._paused = True
        for proc in to_close:
            await proc.close()

    async def unpause(self) -> None:
        """Allow runs to acquire workers again (build finished or failed)."""
        async with self._lock:
            self._paused = False

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


class _ResizableAdmission:
    """Loop-local, dynamically resizable concurrency gate.

    Unlike replacing an ``asyncio.Semaphore`` during a resize, this preserves
    existing waiters and accounts for every in-flight holder. Shrinking below
    current usage simply withholds new admissions until enough holders exit.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = max(1, capacity)
        self._in_use = 0
        self._condition = asyncio.Condition()

    @property
    def available(self) -> int:
        return max(0, self._capacity - self._in_use)

    def locked(self) -> bool:
        return self.available == 0

    async def resize(self, capacity: int) -> int:
        capacity = max(1, capacity)
        async with self._condition:
            self._capacity = capacity
            self._condition.notify_all()
        return capacity

    async def __aenter__(self) -> "_ResizableAdmission":
        async with self._condition:
            await self._condition.wait_for(lambda: self._in_use < self._capacity)
            self._in_use += 1
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        async with self._condition:
            self._in_use = max(0, self._in_use - 1)
            self._condition.notify_all()


class RuntimePool:
    def __init__(self) -> None:
        self._envs: dict[str, _EnvPool] = {}
        self._lock = asyncio.Lock()
        # Global ceiling on simultaneously executing top-level runs. Shared by
        # warm subprocesses, sandbox containers, and the in-process fallback;
        # sub-workflows bypass it because their parent already owns a slot.
        self._global_sem = _ResizableAdmission(settings.max_concurrent_runs)
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
            await self._global_sem.resize(new_max)
            self._max_concurrent_runs = new_max
            return new_max

    def current_max_slots(self) -> int:
        """Return the current max concurrency ceiling."""
        return self._max_concurrent_runs

    def available_global_slots(self) -> int:
        """Best-effort count of free global concurrency slots.

        Used by the local durable-queue dispatch loop to bound how many local
        runs it leases per tick (leasing more than there are slots would just
        pile up blocked coroutines). The resizable admission gate remains the
        authoritative enforcement if this point-in-time value races a caller.
        """
        return self._global_sem.available

    def global_slot(self) -> _ResizableAdmission:
        """The global ``max_concurrent_runs`` ceiling as an async context
        manager. Used by sandbox and in-process top-level paths so every local
        executor honours the same admission cap as subprocess ``dispatch``.
        Never acquire this around a sub-workflow call — the parent already
        holds a slot, so doing so would deadlock.
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
                    env_id,
                    min_size,
                    max_size,
                    rss_estimate,
                    max_runs_per_subprocess=settings.runner_max_runs_per_subprocess,
                )
                self._envs[key] = envpool
            return envpool

    async def drain_env(self, env_id: str | None, *, force: bool = False) -> None:
        """Close warm workers for one environment before a rebuild/delete.

        A warm worker keeps the previous environment's code in memory, and on
        Windows its loaded ``.pyd`` files lock the environment directory so the
        rebuild cannot replace them (PermissionError). With ``force=True``
        in-flight workers are terminated and the env's pool is paused until
        ``unpause_env`` runs.
        """
        key = env_id or "_default"
        async with self._lock:
            envpool = self._envs.get(key)
        if envpool is not None:
            await envpool.drain(force=force)

    async def unpause_env(self, env_id: str | None) -> None:
        """Resume dispatching runs for an env whose rebuild finished/failed."""
        key = env_id or "_default"
        async with self._lock:
            envpool = self._envs.get(key)
        if envpool is not None:
            await envpool.unpause()

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
                except TimeoutError:
                    await proc.close()
                    await on_event(
                        {
                            "type": "run_error",
                            "error": f"workflow run timed out after {timeout}s",
                        }
                    )
                    return "timed_out"
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
                    subworkflow_meta=(subworkflow_meta.to_payload() if subworkflow_meta else {}),
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
                    f"sub-workflow run timed out after {settings.workflow_run_timeout_seconds}s"
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


async def pool_autoscaler_loop() -> None:
    """Background loop that scales the global pool capacity based on queue depth.

    When the durable queue is backing up (queued > threshold), the pool grows
    up to ``pool_autoscale_max``.  When the queue drains, it shrinks back to
    the base ``max_concurrent_runs``.  This prevents the backlog we saw in
    burst tests — 2,847 queued with only 8 slots — from persisting.
    """
    import logging

    _log = logging.getLogger(__name__)

    base = max(1, settings.max_concurrent_runs)
    scale_max = max(base, getattr(settings, "pool_autoscale_max", base * 4))
    scale_threshold = max(1, getattr(settings, "pool_autoscale_threshold", base))
    scale_cooldown = max(30, getattr(settings, "pool_autoscale_cooldown_seconds", 60))
    last_scale_up: float = 0.0

    while True:
        try:
            from app.db import SessionLocal
            from app.services.queue import stats as _queue_stats

            async with SessionLocal() as session:
                qs = await _queue_stats(session)
            queued = qs.get("queued", 0)

            current = pool.current_max_slots()
            if queued > scale_threshold and current < scale_max:
                # Scale up: add capacity proportional to backlog
                target = min(scale_max, base + (queued // (scale_threshold // 2 + 1)))
                target = max(current + 1, target)  # at least +1
                await pool.resize(target)
                _log.info(
                    "autoscaler: scaled up to %d slots (queued=%d)",
                    target,
                    queued,
                )
                last_scale_up = time.monotonic()
            elif queued < scale_threshold and current > base:
                # Scale down: return to base once the queue has cleared below
                # threshold.  Checking queued == 0 would keep the pool inflated
                # as long as any single job remains queued.
                if time.monotonic() - last_scale_up > scale_cooldown:
                    await pool.resize(base)
                    _log.info(
                        "autoscaler: scaled down to %d slots (queued=%d, below threshold)",
                        base,
                        queued,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _log.debug("autoscaler: tick failed", exc_info=True)
        await asyncio.sleep(15)
