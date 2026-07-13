"""Unit tests for the backend dispatcher and all backend classes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_env(backend: str):
    env = MagicMock()
    env.backend = backend
    return env


# ---------------------------------------------------------------------------
# Dispatcher tests
# ---------------------------------------------------------------------------


def test_get_backend_returns_venv_backend() -> None:
    from app.services.backends import get_backend
    from app.services.backends.venv import VenvBackend

    assert isinstance(get_backend(_make_env("venv")), VenvBackend)


def test_get_backend_returns_conda_backend() -> None:
    from app.services.backends import get_backend
    from app.services.backends.conda import CondaBackend

    assert isinstance(get_backend(_make_env("conda")), CondaBackend)


def test_get_backend_returns_pixi_backend() -> None:
    from app.services.backends import get_backend
    from app.services.backends.pixi import PixiBackend

    assert isinstance(get_backend(_make_env("pixi")), PixiBackend)


def test_get_backend_raises_for_unknown() -> None:
    from app.services.backends import get_backend

    with pytest.raises(ValueError, match="unknown_backend"):
        get_backend(_make_env("unknown_backend"))


# ---------------------------------------------------------------------------
# VenvBackend tests
# ---------------------------------------------------------------------------


def test_venv_backend_python_path_posix() -> None:
    from app.services.backends.venv import VenvBackend

    b = VenvBackend()
    with patch("sys.platform", "linux"):
        with patch("app.services.backends.venv.venv_dir", return_value=Path("/fake")):
            path = b.python_path("env123")
    assert "bin" in str(path)
    assert "python" in str(path)


def test_venv_backend_python_path_win32() -> None:
    from app.services.backends.venv import VenvBackend

    b = VenvBackend()
    with patch("sys.platform", "win32"):
        with patch("app.services.backends.venv.venv_dir", return_value=Path("/fake")):
            path = b.python_path("env123")
    assert "Scripts" in str(path)
    assert "python.exe" in str(path)


@pytest.mark.asyncio
async def test_venv_backend_build_passes_index_urls() -> None:
    from app.services.backends.venv import VenvBackend

    env = MagicMock()
    env.id = "test-venv"
    env.python_version = "3.12"
    env.packages = ["numpy"]
    env.interpreter = "cpython"
    env.backend_config = {"index_urls": ["https://download.pytorch.org/whl/cu121"]}

    captured: list[tuple] = []

    async def mock_do_build(
        env_id,
        python_version,
        packages,
        index_urls=None,
        *,
        interpreter="cpython",
        backend_config=None,
    ):
        captured.append(
            (
                env_id,
                python_version,
                packages,
                index_urls,
                interpreter,
                backend_config,
            )
        )
        return "ready", "ok"

    with patch("app.services.backends.venv._do_build", side_effect=mock_do_build):
        b = VenvBackend()
        status, log = await b.build(env)

    assert status == "ready"
    assert captured[0][3] == ["https://download.pytorch.org/whl/cu121"]
    assert captured[0][4] == "cpython"
    assert captured[0][5] == env.backend_config


# ---------------------------------------------------------------------------
# CondaBackend tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conda_build_calls_micromamba_with_channels(tmp_path) -> None:
    from app.services.backends.conda import CondaBackend

    env = MagicMock()
    env.id = "conda-test"
    env.python_version = "3.11"
    env.packages = ["numpy", "pandas"]
    env.backend_config = {"channels": ["conda-forge", "nvidia"]}

    calls: list[tuple] = []

    async def mock_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    fake_solver = tmp_path / "micromamba"
    fake_solver.write_bytes(b"fake")

    with patch("app.services.backends.conda._run", side_effect=mock_run):
        with patch("app.services.backends.conda.ensure_tool", return_value=fake_solver):
            with patch("app.services.backends.conda.venv_dir", return_value=tmp_path / "env"):
                with patch("app.services.backends.conda.local_nodyra_packages", return_value=[]):
                    b = CondaBackend()
                    status, _ = await b.build(env)

    assert status == "ready"
    assert len(calls) == 1
    cmd = calls[0]
    assert str(fake_solver) == cmd[0]
    assert "create" in cmd
    assert "--yes" in cmd
    assert "-c" in cmd
    chan_idx = list(cmd).index("-c")
    assert cmd[chan_idx + 1] == "conda-forge"
    assert "python=3.11" in cmd
    assert "numpy" in cmd
    assert "pandas" in cmd


@pytest.mark.asyncio
async def test_conda_build_returns_error_on_nonzero_exit(tmp_path) -> None:
    from app.services.backends.conda import CondaBackend

    env = MagicMock()
    env.id = "conda-fail"
    env.python_version = "3.12"
    env.packages = []
    env.backend_config = {}

    async def mock_run(*args: str) -> tuple[int, str]:
        return 1, "solver error: package not found"

    with patch("app.services.backends.conda._run", side_effect=mock_run):
        with patch("app.services.backends.conda.ensure_tool", return_value=tmp_path / "micromamba"):
            with patch("app.services.backends.conda.venv_dir", return_value=tmp_path / "env"):
                b = CondaBackend()
                status, log = await b.build(env)

    assert status == "error"
    assert "solver error" in log


def test_conda_python_path_posix(tmp_path) -> None:
    from app.services.backends.conda import CondaBackend

    b = CondaBackend()
    with patch("sys.platform", "linux"):
        with patch("app.services.backends.conda.venv_dir", return_value=tmp_path):
            p = b.python_path("env-id")
    assert p.parts[-2:] == ("bin", "python")


def test_conda_python_path_win32(tmp_path) -> None:
    from app.services.backends.conda import CondaBackend

    b = CondaBackend()
    with patch("sys.platform", "win32"):
        with patch("app.services.backends.conda.venv_dir", return_value=tmp_path):
            p = b.python_path("env-id")
    assert "Scripts" in str(p) and "python.exe" in str(p)


# ---------------------------------------------------------------------------
# PixiBackend tests
# ---------------------------------------------------------------------------


def test_pixi_split_packages_routes_pypi_suffix() -> None:
    from app.services.backends.pixi import _split_packages

    conda, pypi = _split_packages(["numpy", "httpx @ pypi", "pandas", "mylib@pypi"])
    assert set(conda) == {"numpy", "pandas"}
    assert set(pypi) == {"httpx", "mylib"}


def test_pixi_split_packages_no_pypi() -> None:
    from app.services.backends.pixi import _split_packages

    conda, pypi = _split_packages(["numpy", "scipy"])
    assert set(conda) == {"numpy", "scipy"}
    assert pypi == []


def test_pixi_write_toml_puts_conda_in_dependencies(tmp_path) -> None:
    from app.services.backends.pixi import _write_pixi_toml

    env = MagicMock()
    env.id = "pixi-test"
    env.python_version = "3.12"
    env.packages = ["numpy", "httpx @ pypi"]
    env.backend_config = {"channels": ["conda-forge"]}

    toml_path = tmp_path / "pixi.toml"
    _write_pixi_toml(toml_path, env)
    content = toml_path.read_text()

    assert 'name = "nodyra-env-pixi-test"' in content
    assert '"conda-forge"' in content
    assert "3.12" in content
    assert "[dependencies]" in content
    assert "numpy" in content
    assert "[pypi-dependencies]" in content
    assert "httpx" in content


def test_pixi_write_toml_no_pypi_packages(tmp_path) -> None:
    from app.services.backends.pixi import _write_pixi_toml

    env = MagicMock()
    env.id = "pixi-test2"
    env.python_version = "3.11"
    env.packages = ["pandas"]
    env.backend_config = {}

    toml_path = tmp_path / "pixi.toml"
    _write_pixi_toml(toml_path, env)
    content = toml_path.read_text()

    assert "pandas" in content
    conda_section = (
        content.split("[pypi-dependencies]")[0] if "[pypi-dependencies]" in content else content
    )
    assert "pandas" in conda_section


def test_pixi_python_path_posix(tmp_path) -> None:
    from app.services.backends.pixi import PixiBackend

    b = PixiBackend()
    with patch("sys.platform", "linux"):
        with patch("app.services.backends.pixi.venv_dir", return_value=tmp_path):
            p = b.python_path("env-id")
    assert ".pixi" in str(p)
    assert p.parts[-2:] == ("bin", "python")


def test_pixi_python_path_win32(tmp_path) -> None:
    from app.services.backends.pixi import PixiBackend

    b = PixiBackend()
    with patch("sys.platform", "win32"):
        with patch("app.services.backends.pixi.venv_dir", return_value=tmp_path):
            p = b.python_path("env-id")
    assert ".pixi" in str(p)
    assert "python.exe" in str(p)


@pytest.mark.asyncio
async def test_pixi_build_calls_pixi_install(tmp_path) -> None:
    from app.services.backends.pixi import PixiBackend

    env = MagicMock()
    env.id = "pixi-build"
    env.python_version = "3.12"
    env.packages = ["numpy"]
    env.backend_config = {"channels": ["conda-forge"]}

    calls: list[tuple] = []

    async def mock_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    fake_pixi = tmp_path / "pixi"

    with patch("app.services.backends.pixi._run", side_effect=mock_run):
        with patch("app.services.backends.pixi.ensure_tool", return_value=fake_pixi):
            with patch("app.services.backends.pixi.venv_dir", return_value=tmp_path / "env"):
                with patch("app.services.backends.pixi.local_nodyra_packages", return_value=[]):
                    b = PixiBackend()
                    status, _ = await b.build(env)

    assert status == "ready"
    assert len(calls) == 1
    cmd = calls[0]
    assert str(fake_pixi) == cmd[0]
    assert "install" in cmd
    assert "--manifest-path" in cmd


@pytest.mark.asyncio
async def test_pixi_build_incremental_add_when_env_exists(tmp_path) -> None:
    """When env already built and only new packages added, uses pixi add not install."""
    from app.services.backends.pixi import PixiBackend

    env_dir = tmp_path / "env"
    env_dir.mkdir()

    # Existing pixi.toml with numpy already installed
    toml_content = (
        '[project]\nname = "nodyra-env-test"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n[dependencies]\npython = "3.12.*"\nnumpy = "*"\n'
    )
    (env_dir / "pixi.toml").write_text(toml_content)

    # Fake python binary so python_path().exists() returns True
    python_bin = env_dir / ".pixi" / "envs" / "default" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_bytes(b"fake")

    env = MagicMock()
    env.id = "test"
    env.python_version = "3.12"
    env.packages = ["numpy", "pandas"]  # pandas is new
    env.backend_config = {"channels": ["conda-forge"]}

    calls: list[tuple] = []

    async def mock_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    fake_pixi = tmp_path / "pixi"

    with patch("app.services.backends.pixi._run", side_effect=mock_run):
        with patch("app.services.backends.pixi.ensure_tool", return_value=fake_pixi):
            with patch("app.services.backends.pixi.venv_dir", return_value=env_dir):
                with patch("sys.platform", "linux"):
                    b = PixiBackend()
                    status, _ = await b.build(env)

    assert status == "ready"
    assert len(calls) == 1
    cmd = calls[0]
    assert "add" in cmd
    assert "pandas" in cmd
    assert "install" not in cmd


@pytest.mark.asyncio
async def test_pixi_build_full_rebuild_on_removal(tmp_path) -> None:
    """When a package is removed, falls back to full pixi install."""
    from app.services.backends.pixi import PixiBackend

    env_dir = tmp_path / "env"
    env_dir.mkdir()

    toml_content = (
        '[project]\nname = "nodyra-env-test"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n[dependencies]\npython = "3.12.*"\n'
        'numpy = "*"\npandas = "*"\n'
    )
    (env_dir / "pixi.toml").write_text(toml_content)

    python_bin = env_dir / ".pixi" / "envs" / "default" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_bytes(b"fake")

    env = MagicMock()
    env.id = "test"
    env.python_version = "3.12"
    env.packages = ["numpy"]  # pandas removed
    env.backend_config = {"channels": ["conda-forge"]}

    calls: list[tuple] = []

    async def mock_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    fake_pixi = tmp_path / "pixi"

    with patch("app.services.backends.pixi._run", side_effect=mock_run):
        with patch("app.services.backends.pixi.ensure_tool", return_value=fake_pixi):
            with patch("app.services.backends.pixi.venv_dir", return_value=env_dir):
                with patch("app.services.backends.pixi.local_nodyra_packages", return_value=[]):
                    with patch("sys.platform", "linux"):
                        b = PixiBackend()
                        status, _ = await b.build(env)

    assert status == "ready"
    assert any("install" in c for c in calls)
