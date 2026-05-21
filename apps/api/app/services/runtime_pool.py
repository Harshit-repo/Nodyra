"""Env-runner subprocess pool.

When ``settings.use_subprocess_runner`` is enabled, the runner dispatches
executions to a long-lived ``noodle_runtime`` subprocess per environment id.
This is the productionization path that gives a workflow real isolation
inside its assigned ``uv`` venv.

The pool also brokers sub-workflow calls back to the host: when an
``execute_workflow`` node runs inside a subprocess, it writes a
``call_workflow`` event to stdout; the pool catches it, invokes the host-side
``sub_workflow_caller`` passed by the runner, and writes the result back to
the subprocess's stdin. The host-side caller still runs the engine
in-process, so the call-chain contextvar enforces cycle detection across
sub-workflow boundaries.

Each subprocess is serialized via an ``asyncio.Lock`` — concurrent runs to
the same env queue rather than interleaving their stdio. Different envs run
in different subprocesses and can therefore run in parallel.
"""

import asyncio
import json
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import settings
from app.services.venv import venv_python

EventCallback = Callable[[dict], Awaitable[None]]
SubWorkflowCaller = Callable[[str, Any], Awaitable[Any]]


def _python_for_env(env_id: str | None) -> str:
    if env_id:
        candidate = venv_python(env_id)
        if candidate.exists():
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
        self._run_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    @classmethod
    async def spawn(cls, env_id: str | None) -> "_RuntimeProcess":
        python = _python_for_env(env_id)
        process = await asyncio.create_subprocess_exec(
            python,
            "-u",
            "-m",
            "noodle_runtime",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None or process.stdin is None:
            raise RuntimeError("runtime subprocess pipes were not opened")
        line = await process.stdout.readline()
        if not line:
            raise RuntimeError(
                f"runtime for env {env_id!r} did not emit a ready event"
            )
        ready = json.loads(line)
        if ready.get("type") != "ready":
            raise RuntimeError(f"unexpected first event: {ready}")
        return cls(process, env_id)

    async def _write_message(self, message: dict) -> None:
        if self.process.stdin is None:
            raise RuntimeError("runtime subprocess stdin is closed")
        payload = json.dumps(message, default=str).encode() + b"\n"
        async with self._write_lock:
            self.process.stdin.write(payload)
            await self.process.stdin.drain()

    async def _handle_call_workflow(
        self,
        event: dict,
        sub_workflow_caller: SubWorkflowCaller | None,
    ) -> None:
        callback_id = event.get("callback_id", "")
        try:
            if sub_workflow_caller is None:
                raise RuntimeError(
                    "subprocess runner has no host-side sub-workflow caller"
                )
            result = await sub_workflow_caller(
                event.get("workflow_id", ""), event.get("input")
            )
            await self._write_message(
                {
                    "type": "call_workflow_response",
                    "callback_id": callback_id,
                    "result": result,
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
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        on_event: EventCallback,
        sub_workflow_caller: SubWorkflowCaller | None = None,
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
                        "graph": graph,
                        "cache": cache,
                        "targets": targets,
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
                            self._handle_call_workflow(event, sub_workflow_caller)
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
                for task in callbacks:
                    task.cancel()
                await self.close()
                raise

    async def close(self) -> None:
        if self.process.returncode is not None:
            self.dead = True
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


class _EnvPool:
    """A pool of up to ``size`` warm runner processes for one environment.

    The semaphore bounds concurrent checkouts to ``size``, so the total number
    of live processes for the env never exceeds ``size``. Idle processes are
    reused; dead ones are dropped.
    """

    def __init__(self, env_id: str | None, size: int) -> None:
        self.env_id = env_id
        self.size = max(1, size)
        self._sem = asyncio.Semaphore(self.size)
        self._idle: list[_RuntimeProcess] = []
        self._all: set[_RuntimeProcess] = set()
        self._lock = asyncio.Lock()

    @staticmethod
    def _alive(proc: _RuntimeProcess) -> bool:
        return not proc.dead and proc.process.returncode is None

    async def acquire(self) -> _RuntimeProcess:
        await self._sem.acquire()
        async with self._lock:
            while self._idle:
                cand = self._idle.pop()
                if self._alive(cand):
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
        if self._alive(proc):
            self._idle.append(proc)
        else:
            self._all.discard(proc)
        self._sem.release()

    async def close(self) -> None:
        async with self._lock:
            procs = list(self._all)
            self._all.clear()
            self._idle.clear()
        for proc in procs:
            await proc.close()


class RuntimePool:
    def __init__(self) -> None:
        self._envs: dict[str, _EnvPool] = {}
        self._lock = asyncio.Lock()
        # Global ceiling on simultaneously executing top-level runs. Acquired
        # only here in ``dispatch`` — never around the in-process engine — so
        # sub-workflows (which call the engine directly, not the pool) consume
        # no slot and cannot deadlock a parent that is waiting on them.
        self._global_sem = asyncio.Semaphore(max(1, settings.max_concurrent_runs))

    async def _env_pool(self, env_id: str | None) -> _EnvPool:
        key = env_id or "_default"
        async with self._lock:
            envpool = self._envs.get(key)
            if envpool is None:
                envpool = _EnvPool(env_id, settings.runner_pool_size)
                self._envs[key] = envpool
            return envpool

    async def dispatch(
        self,
        env_id: str | None,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        on_event: EventCallback,
        sub_workflow_caller: SubWorkflowCaller | None = None,
    ) -> str:
        envpool = await self._env_pool(env_id)
        async with self._global_sem:
            proc = await envpool.acquire()
            try:
                run = proc.run(
                    graph, cache, targets, on_event, sub_workflow_caller
                )
                timeout = settings.workflow_run_timeout_seconds
                if timeout and timeout > 0:
                    return await asyncio.wait_for(run, timeout=timeout)
                return await run
            except TimeoutError as exc:
                await proc.close()
                raise RuntimeError(
                    f"workflow run timed out after "
                    f"{settings.workflow_run_timeout_seconds}s"
                ) from exc
            finally:
                envpool.release(proc)

    async def shutdown(self) -> None:
        async with self._lock:
            envs = list(self._envs.values())
            self._envs.clear()
        for envpool in envs:
            await envpool.close()


pool = RuntimePool()
