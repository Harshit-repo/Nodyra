"""Tests for Cloud / DevOps node hardening."""

from __future__ import annotations

import asyncio

import pytest

from noodle_nodes import cloud_devops


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
