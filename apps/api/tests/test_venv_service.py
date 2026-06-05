"""Unit tests for venv.py — no actual uv subprocess calls."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.venv import _do_build


@pytest.mark.asyncio
async def test_do_build_passes_extra_index_urls(tmp_path) -> None:
    calls: list[tuple] = []

    async def fake_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    with patch("app.services.backends.venv._run", side_effect=fake_run):
        with patch("app.services.backends.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"):
            with patch("app.services.backends.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.backends.venv._local_noodle_packages", return_value=[]):
                    status, _ = await _do_build(
                        "test-env",
                        "3.12",
                        ["pandas"],
                        index_urls=["https://download.pytorch.org/whl/cu121"],
                    )

    assert status == "ready"
    install_call = next(c for c in calls if "pip" in c)
    assert "--extra-index-url" in install_call
    idx = install_call.index("--extra-index-url")
    assert install_call[idx + 1] == "https://download.pytorch.org/whl/cu121"


@pytest.mark.asyncio
async def test_do_build_no_extra_index_urls_when_empty(tmp_path) -> None:
    calls: list[tuple] = []

    async def fake_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    with patch("app.services.backends.venv._run", side_effect=fake_run):
        with patch("app.services.backends.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"):
            with patch("app.services.backends.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.backends.venv._local_noodle_packages", return_value=[]):
                    status, _ = await _do_build("test-env", "3.12", ["pandas"])

    assert status == "ready"
    install_call = next(c for c in calls if "pip" in c)
    assert "--extra-index-url" not in install_call
