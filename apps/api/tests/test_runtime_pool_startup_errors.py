"""A worker that dies before it is ready must say why.

Found when a syntax error in a node module made every run hang: the runner
subprocess died on import, and the only report was "did not emit a ready
event". The traceback naming the file and line was sitting unread in the
dead process's stderr — the consumer that drains stderr only starts once a
worker is already ready.
"""

import asyncio
import sys

import pytest

from app.services.runtime_pool import _drain_startup_stderr, _RuntimeProcess


async def _spawn(code: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def test_stderr_from_a_dead_worker_is_recovered() -> None:
    process = await _spawn("import sys; sys.stderr.write('SyntaxError: bad node\\n')")
    await process.wait()

    detail = await _drain_startup_stderr(process)

    assert "SyntaxError: bad node" in detail
    assert detail.startswith(": ")


async def test_a_silent_worker_adds_nothing() -> None:
    """No stderr means no suffix, so the caller's message reads normally."""
    process = await _spawn("pass")
    await process.wait()

    assert await _drain_startup_stderr(process) == ""


async def test_draining_never_raises_or_hangs() -> None:
    """A worker still holding its pipe open must not stall the error path."""
    process = await _spawn("import time; time.sleep(30)")
    try:
        detail = await asyncio.wait_for(
            _drain_startup_stderr(process, timeout=0.2), timeout=5
        )
        assert detail == ""
    finally:
        process.kill()
        await process.wait()


async def test_start_reports_why_the_worker_died(monkeypatch) -> None:
    """The end-to-end message must carry the real reason, not just 'not ready'."""

    # Capture the real spawner first, or fake_exec recurses into its own patch.
    real_exec = asyncio.create_subprocess_exec
    dying_worker = (
        "import sys; sys.stderr.write("
        "'  File \"geospatial.py\", line 532\\n"
        "SyntaxError: keyword argument repeated: requirements\\n')"
    )

    async def fake_exec(*args, **kwargs):
        return await real_exec(
            sys.executable,
            "-c",
            dying_worker,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(
        "app.services.runtime_pool._python_for_env",
        lambda env_id: _immediate(sys.executable),
    )
    monkeypatch.setattr(
        "app.services.runtime_pool._resolve_env_runtime_flags",
        lambda env_id: _immediate({}),
    )

    with pytest.raises(RuntimeError) as excinfo:
        await _RuntimeProcess.spawn("env-1")

    message = str(excinfo.value)
    assert "exited before it was ready" in message
    assert "SyntaxError: keyword argument repeated" in message
    assert "geospatial.py" in message


async def _immediate(value):
    return value
