"""System-level nodes (shell/OS commands)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from typing import Any

from nodyra.sdk import node

_SHELL_CHOICES = ["auto", "bash", "sh", "powershell", "cmd"]


def _resolve_shell_argv(shell: str, command: str) -> tuple[list[str], str]:
    """Pick the actual ``argv`` for ``command`` and the human-readable shell name.

    Raises ``RuntimeError`` if the requested shell isn't available on the host
    (e.g. ``cmd`` on POSIX, ``bash`` on Windows without Git Bash on PATH).
    """
    shell = (shell or "auto").lower()
    on_windows = sys.platform == "win32"

    if shell == "auto":
        if on_windows:
            shell = "powershell"
        else:
            shell = "bash" if shutil.which("bash") else "sh"

    if shell == "bash":
        if not shutil.which("bash"):
            raise RuntimeError(
                "bash is not available on PATH (install Git Bash on Windows or bash on POSIX)"
            )
        return ["bash", "-c", command], "bash"
    if shell == "sh":
        if on_windows:
            raise RuntimeError("sh is not available on Windows; use powershell or bash")
        if not shutil.which("sh"):
            raise RuntimeError("sh is not available on PATH")
        return ["sh", "-c", command], "sh"
    if shell == "powershell":
        if not on_windows and not shutil.which("powershell") and not shutil.which("pwsh"):
            raise RuntimeError("PowerShell is not available on PATH")
        exe = "powershell" if on_windows or shutil.which("powershell") else "pwsh"
        return [exe, "-NoProfile", "-Command", command], "powershell"
    if shell == "cmd":
        if not on_windows:
            raise RuntimeError("cmd is only available on Windows")
        return ["cmd.exe", "/C", command], "cmd"
    raise RuntimeError(f"unknown shell '{shell}'")


def _coerce_env_vars(value: Any) -> dict[str, str] | None:
    """Accept a dict or a JSON object string for the ``env_vars`` param."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"env_vars must be valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("env_vars JSON must be an object")
        return {str(k): str(v) for k, v in parsed.items()}
    raise ValueError("env_vars must be a JSON object or dict")


@node(
    name="Execute Command",
    id="execute_command",
    category="System",
    icon="terminal",
    params={
        "command": {
            "placeholder": "echo hello",
            "description": (
                "Shell command to run. Use expressions for dynamic "
                "arguments. Secrets should come from credentials, not be "
                "pasted here."
            ),
            "multiline": True,
        },
        "shell": {
            "group": "Options",
            "choices": _SHELL_CHOICES,
            "description": (
                "Which shell to invoke. 'auto' picks bash on POSIX and "
                "PowerShell on Windows."
            ),
        },
        "cwd": {
            "group": "Options",
            "placeholder": "/tmp",
            "description": "Working directory (defaults to the runtime's cwd).",
        },
        "env_vars": {
            "group": "Options",
            "placeholder": '{"FOO": "bar"}',
            "description": (
                "Extra environment variables, JSON object or dict. Merged "
                "on top of the parent process env."
            ),
            "multiline": True,
        },
        "timeout_seconds": {
            "group": "Options",
            "description": (
                "Kill the process if it runs longer than this many seconds."
            ),
        },
        "fail_on_nonzero": {
            "group": "Options",
            "description": (
                "If true, a non-zero exit raises a node error so retry/"
                "error-workflow logic engages."
            ),
        },
    },
)
async def execute_command(
    input: Any = None,
    command: str = "",
    shell: str = "auto",
    cwd: str = "",
    env_vars: Any = "",
    timeout_seconds: int = 60,
    fail_on_nonzero: bool = True,
) -> dict:
    """Run a shell command and return stdout/stderr/returncode/duration."""
    _ = input  # only here so expressions can read upstream context
    command = (command or "").strip()
    if not command:
        raise ValueError("execute_command: command is required")

    argv, shell_used = _resolve_shell_argv(shell, command)
    extra_env = _coerce_env_vars(env_vars)
    process_env = None
    if extra_env:
        process_env = {**os.environ, **extra_env}

    cwd_value = cwd.strip() or None

    start = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd_value,
            env=process_env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"execute_command: shell '{shell_used}' not found: {exc}"
        ) from exc

    timeout_value = max(0, int(timeout_seconds or 0)) or None
    try:
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout_value
            )
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            raise RuntimeError(
                f"execute_command: timed out after {timeout_seconds}s"
            ) from None
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)

    returncode = process.returncode or 0
    stdout = stdout_bytes.decode("utf-8", "replace")
    stderr = stderr_bytes.decode("utf-8", "replace")

    if fail_on_nonzero and returncode != 0:
        snippet = stderr.strip() or stdout.strip()
        if len(snippet) > 500:
            snippet = snippet[:500] + "…"
        raise RuntimeError(
            f"execute_command exited with code {returncode}: {snippet}"
        )

    return {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": returncode,
        "duration_ms": duration_ms,
    }
