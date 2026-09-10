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


async def test_the_worker_stream_limit_clears_the_output_cap() -> None:
    """The protocol must carry what the engine permits.

    Worker messages are newline-framed JSON, one node result per line, and
    asyncio's StreamReader defaults to a 64 KiB buffer — well under
    max_output_bytes (256 KiB). Past it readline() raises "Separator is not
    found, and chunk exceed the limit" and the run dies naming nothing.
    """
    from app.config import settings
    from app.services.runtime_pool import _STREAM_LIMIT_BYTES, _stream_limit_bytes

    assert _STREAM_LIMIT_BYTES > 64 * 1024, "still at asyncio's default"
    assert _STREAM_LIMIT_BYTES > settings.max_output_bytes, (
        "a node at the output cap must still fit on one line, with room for "
        "the JSON envelope and escaping"
    )
    assert _stream_limit_bytes() >= 8 * 1024 * 1024


async def test_the_stream_limit_follows_a_raised_output_cap(monkeypatch) -> None:
    from app.config import settings
    from app.services import runtime_pool

    monkeypatch.setattr(settings, "max_output_bytes", 32 * 1024 * 1024)

    assert runtime_pool._stream_limit_bytes() > 32 * 1024 * 1024


async def test_the_worker_is_spawned_with_that_limit(monkeypatch) -> None:
    """A limit computed but not passed would fix nothing."""
    import asyncio as _asyncio

    from app.services import runtime_pool

    seen: dict = {}
    real_exec = _asyncio.create_subprocess_exec

    async def capturing_exec(*args, **kwargs):
        seen.update(kwargs)
        return await real_exec(
            sys.executable,
            "-c",
            "pass",
            stdin=_asyncio.subprocess.PIPE,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
        )

    monkeypatch.setattr(_asyncio, "create_subprocess_exec", capturing_exec)
    monkeypatch.setattr(
        runtime_pool, "_python_for_env", lambda env_id: _immediate(sys.executable)
    )
    monkeypatch.setattr(
        runtime_pool, "_resolve_env_runtime_flags", lambda env_id: _immediate({})
    )

    with pytest.raises(RuntimeError):
        await runtime_pool._RuntimeProcess.spawn("env-limit")

    assert seen.get("limit") == runtime_pool._STREAM_LIMIT_BYTES
