"""Agent-side hardened sandbox execution."""

import pytest

from nodyra_runner_agent import sandbox_exec


def test_hardening_floor_present():
    kw = sandbox_exec.sandbox_run_kwargs(cpu=1.0, memory_mb=512, pids=128)
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges:true"]
    assert kw["read_only"] is True
    assert kw["network_mode"] == "none"
    assert kw["mem_limit"] == "512m"
    assert kw["nano_cpus"] == 1_000_000_000
    assert kw["pids_limit"] == 128
    assert "/tmp" in kw["tmpfs"]


async def test_run_sandboxed_refuses_without_daemon(monkeypatch):
    monkeypatch.setattr(sandbox_exec, "_client", lambda: (_ for _ in ()).throw(
        RuntimeError("no daemon")))
    events = []
    status = await sandbox_exec.run_workflow_sandboxed(
        run_id="r", graph={}, cache=None, targets=None, workflow_modules=[],
        on_event=lambda e: events.append(e) or _async_none(),
        env_payload={},
    )
    assert status == "error"
    assert any(e.get("type") == "run_error" for e in events)


def _async_none():
    import asyncio
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return fut
