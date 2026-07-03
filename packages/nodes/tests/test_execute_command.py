"""Tests for the Execute Command system node.

These exercise the node implementation directly (not through the engine) so
the cross-platform shell-resolution behaviour can be asserted without
spinning up the workflow runtime.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

import nodyra_nodes  # noqa: F401 - importing registers the node
from nodyra.sdk import registry
from nodyra_nodes.system import _coerce_env_vars, _resolve_shell_argv, execute_command

ON_WINDOWS = sys.platform == "win32"


def test_execute_command_is_registered() -> None:
    ids = {m.id for m in registry.manifests()}
    assert "execute_command" in ids


def test_resolve_shell_argv_auto_per_platform() -> None:
    argv, name = _resolve_shell_argv("auto", "echo hi")
    if ON_WINDOWS:
        assert name == "powershell"
        assert argv[0].lower().endswith("powershell") or argv[0].lower().endswith("pwsh")
    else:
        assert name in {"bash", "sh"}


def test_resolve_shell_argv_rejects_cmd_on_posix() -> None:
    if ON_WINDOWS:
        pytest.skip("cmd is valid on Windows")
    with pytest.raises(RuntimeError, match="cmd is only available on Windows"):
        _resolve_shell_argv("cmd", "dir")


def test_resolve_shell_argv_rejects_sh_on_windows() -> None:
    if not ON_WINDOWS:
        pytest.skip("sh is valid on POSIX")
    with pytest.raises(RuntimeError, match="sh is not available on Windows"):
        _resolve_shell_argv("sh", "ls")


def test_coerce_env_vars_accepts_json_object_string() -> None:
    assert _coerce_env_vars('{"A": "1", "B": "2"}') == {"A": "1", "B": "2"}


def test_coerce_env_vars_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="JSON must be an object"):
        _coerce_env_vars("[1, 2, 3]")


def test_coerce_env_vars_rejects_malformed_json() -> None:
    with pytest.raises(ValueError, match="must be valid JSON"):
        _coerce_env_vars("{not json}")


@pytest.mark.asyncio
async def test_execute_command_echo_returns_stdout() -> None:
    cmd = "Write-Output hello" if ON_WINDOWS else "echo hello"
    shell = "powershell" if ON_WINDOWS else "auto"
    result = await execute_command(command=cmd, shell=shell)
    assert result["returncode"] == 0
    assert "hello" in result["stdout"]
    assert result["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_execute_command_nonzero_raises_when_fail_on_nonzero_true() -> None:
    cmd = "exit 7"
    shell = "powershell" if ON_WINDOWS else "bash"
    with pytest.raises(RuntimeError, match="exited with code 7"):
        await execute_command(command=cmd, shell=shell, fail_on_nonzero=True)


@pytest.mark.asyncio
async def test_execute_command_nonzero_returns_when_fail_on_nonzero_false() -> None:
    cmd = "exit 3"
    shell = "powershell" if ON_WINDOWS else "bash"
    result = await execute_command(
        command=cmd, shell=shell, fail_on_nonzero=False
    )
    assert result["returncode"] == 3


@pytest.mark.asyncio
async def test_execute_command_blank_command_rejected() -> None:
    with pytest.raises(ValueError, match="command is required"):
        await execute_command(command="")


@pytest.mark.asyncio
async def test_execute_command_timeout_kills_process() -> None:
    if ON_WINDOWS:
        cmd = "Start-Sleep -Seconds 30"
        shell = "powershell"
    else:
        cmd = "sleep 30"
        shell = "bash"
    with pytest.raises(RuntimeError, match="timed out"):
        await asyncio.wait_for(
            execute_command(command=cmd, shell=shell, timeout_seconds=1),
            timeout=10,  # outer guard so the test itself can't hang
        )
