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
            await self._write_message(
                {
                    "type": "run",
                    "request_id": request_id,
                    "graph": graph,
                    "cache": cache,
                    "targets": targets,
                }
            )

            callbacks: set[asyncio.Task] = set()
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
                    return str(event.get("status", "success"))
                if kind == "error":
                    raise RuntimeError(event.get("error", "runtime error"))
                clean = {k: v for k, v in event.items() if k != "request_id"}
                await on_event(clean)

    async def close(self) -> None:
        if self.process.returncode is not None:
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


class RuntimePool:
    def __init__(self) -> None:
        self._processes: dict[str, _RuntimeProcess] = {}
        self._lock = asyncio.Lock()

    async def dispatch(
        self,
        env_id: str | None,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        on_event: EventCallback,
        sub_workflow_caller: SubWorkflowCaller | None = None,
    ) -> str:
        key = env_id or "_default"
        async with self._lock:
            proc = self._processes.get(key)
            if proc is None or proc.dead:
                proc = await _RuntimeProcess.spawn(env_id)
                self._processes[key] = proc
        return await proc.run(
            graph, cache, targets, on_event, sub_workflow_caller
        )

    async def shutdown(self) -> None:
        async with self._lock:
            processes = list(self._processes.values())
            self._processes.clear()
        for proc in processes:
            await proc.close()


pool = RuntimePool()
