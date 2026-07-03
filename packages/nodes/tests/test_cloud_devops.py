"""Tests for Cloud / DevOps node hardening."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nodyra_nodes import cloud_devops


def test_ssh_execute_rejects_unknown_host_keys() -> None:
    client = MagicMock()
    stdin = MagicMock()
    stdout = MagicMock()
    stderr = MagicMock()
    stdout.read.return_value = b"ok"
    stdout.channel.recv_exit_status.return_value = 0
    stderr.read.return_value = b""
    client.exec_command.return_value = (stdin, stdout, stderr)
    reject_policy = object()
    fake_paramiko = SimpleNamespace(
        SSHClient=lambda: client,
        RejectPolicy=lambda: reject_policy,
        SSHException=Exception,
    )

    with patch.dict(sys.modules, {"paramiko": fake_paramiko}):
        result = cloud_devops.ssh_execute(
            host="server.example.com",
            credentials={"username": "operator", "password": "secret"},
            command="uptime",
        )

    client.load_system_host_keys.assert_called_once_with()
    client.set_missing_host_key_policy.assert_called_once_with(reject_policy)
    assert result["stdout"] == "ok"


class _HangingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if not self.killed:
            await asyncio.sleep(30)
        return b"", b"killed"

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


@pytest.mark.asyncio
async def test_git_clone_timeout_kills_process(monkeypatch) -> None:
    process = _HangingProcess()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(cloud_devops.shutil, "which", lambda _name: "git")
    monkeypatch.setattr(
        cloud_devops.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    with pytest.raises(RuntimeError, match="timed out"):
        await cloud_devops.git_clone(
            url="https://example.com/repo.git",
            directory="/tmp/repo",
            timeout_seconds=1,
        )

    assert process.killed is True


@pytest.mark.asyncio
async def test_git_pull_timeout_kills_process(monkeypatch) -> None:
    process = _HangingProcess()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(cloud_devops.shutil, "which", lambda _name: "git")
    monkeypatch.setattr(
        cloud_devops.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    with pytest.raises(RuntimeError, match="timed out"):
        await cloud_devops.git_pull(directory="/tmp/repo", timeout_seconds=1)

    assert process.killed is True
