"""Unit tests for venv.py — no actual uv subprocess calls."""

from unittest.mock import patch

import pytest

from app.services.backends.venv import uv_python_request
from app.services.venv import _do_build


@pytest.mark.asyncio
async def test_do_build_passes_extra_index_urls(tmp_path) -> None:
    calls: list[tuple] = []

    async def fake_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    async def fake_smoke_check(env_id: str) -> None:
        return None

    with patch("app.services.backends.venv._run", side_effect=fake_run):
        with patch(
            "app.services.backends.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"
        ):
            with patch("app.services.backends.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.backends.venv.local_nodyra_packages", return_value=[]):
                    with patch(
                        "app.services.backends.venv._smoke_check_runtime",
                        side_effect=fake_smoke_check,
                    ):
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

    async def fake_smoke_check(env_id: str) -> None:
        return None

    with patch("app.services.backends.venv._run", side_effect=fake_run):
        with patch(
            "app.services.backends.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"
        ):
            with patch("app.services.backends.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.backends.venv.local_nodyra_packages", return_value=[]):
                    with patch(
                        "app.services.backends.venv._smoke_check_runtime",
                        side_effect=fake_smoke_check,
                    ):
                        status, _ = await _do_build("test-env", "3.12", ["pandas"])

    assert status == "ready"
    install_call = next(c for c in calls if "pip" in c)
    assert "--extra-index-url" not in install_call


# ---------------------------------------------------------------------------
# Phase 3: uv_python_request — pure function, unit-testable
# ---------------------------------------------------------------------------


def test_uv_python_request_cpython() -> None:
    assert uv_python_request("cpython", "3.14") == "3.14"


def test_uv_python_request_cpython_ft() -> None:
    assert uv_python_request("cpython-ft", "3.14") == "3.14t"


def test_uv_python_request_pypy() -> None:
    assert uv_python_request("pypy", "3.11") == "pypy@3.11"


def test_uv_python_request_unknown_interpreter_raises() -> None:
    with pytest.raises(ValueError):
        uv_python_request("graalpy", "3.12")


# ---------------------------------------------------------------------------
# Phase 3: _do_build interpreter request + smoke check wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_do_build_cpython_ft_uses_t_suffix(tmp_path) -> None:
    calls: list[tuple] = []

    async def fake_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    async def fake_smoke_check(env_id: str) -> None:
        return None

    with patch("app.services.backends.venv._run", side_effect=fake_run):
        with patch(
            "app.services.backends.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"
        ):
            with patch("app.services.backends.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.backends.venv.local_nodyra_packages", return_value=[]):
                    with patch(
                        "app.services.backends.venv._smoke_check_runtime",
                        side_effect=fake_smoke_check,
                    ):
                        status, _ = await _do_build(
                            "test-env", "3.14", [], interpreter="cpython-ft"
                        )

    assert status == "ready"
    venv_call = next(c for c in calls if "venv" in c)
    assert "3.14t" in venv_call


@pytest.mark.asyncio
async def test_do_build_pypy_uses_pypy_request(tmp_path) -> None:
    calls: list[tuple] = []

    async def fake_run(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 0, "ok"

    async def fake_smoke_check(env_id: str) -> None:
        return None

    with patch("app.services.backends.venv._run", side_effect=fake_run):
        with patch(
            "app.services.backends.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"
        ):
            with patch("app.services.backends.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.backends.venv.local_nodyra_packages", return_value=[]):
                    with patch(
                        "app.services.backends.venv._smoke_check_runtime",
                        side_effect=fake_smoke_check,
                    ):
                        status, _ = await _do_build(
                            "test-env", "3.11", [], interpreter="pypy"
                        )

    assert status == "ready"
    venv_call = next(c for c in calls if "venv" in c)
    assert "pypy@3.11" in venv_call


@pytest.mark.asyncio
async def test_do_build_failing_smoke_check_yields_error(tmp_path) -> None:
    async def fake_run(*args: str) -> tuple[int, str]:
        return 0, "ok"

    async def fake_smoke_check(env_id: str) -> str:
        return "runtime did not emit a ready event"

    with patch("app.services.backends.venv._run", side_effect=fake_run):
        with patch(
            "app.services.backends.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"
        ):
            with patch("app.services.backends.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.backends.venv.local_nodyra_packages", return_value=[]):
                    with patch(
                        "app.services.backends.venv._smoke_check_runtime",
                        side_effect=fake_smoke_check,
                    ):
                        status, detail = await _do_build("test-env", "3.12", [])

    assert status == "error"
    assert "runtime did not emit a ready event" in detail


@pytest.mark.asyncio
async def test_do_build_passing_smoke_check_yields_ready(tmp_path) -> None:
    async def fake_run(*args: str) -> tuple[int, str]:
        return 0, "ok"

    async def fake_smoke_check(env_id: str) -> None:
        return None

    with patch("app.services.backends.venv._run", side_effect=fake_run):
        with patch(
            "app.services.backends.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"
        ):
            with patch("app.services.backends.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.backends.venv.local_nodyra_packages", return_value=[]):
                    with patch(
                        "app.services.backends.venv._smoke_check_runtime",
                        side_effect=fake_smoke_check,
                    ):
                        status, _ = await _do_build("test-env", "3.12", [])

    assert status == "ready"


# ---------------------------------------------------------------------------
# Phase 5: mypyc compile wiring in _do_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_do_build_mypyc_compile_failure_still_ready_with_warning(tmp_path) -> None:
    async def fake_run(*args: str) -> tuple[int, str]:
        return 0, "ok"

    async def fake_smoke_check(env_id: str) -> None:
        return None

    async def fake_mypyc_compile(python, modules):
        return False, "mypyc: compilation of 'my_pkg.transforms' failed"

    with patch("app.services.backends.venv._run", side_effect=fake_run):
        with patch(
            "app.services.backends.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"
        ):
            with patch("app.services.backends.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.backends.venv.local_nodyra_packages", return_value=[]):
                    with patch(
                        "app.services.backends.venv._smoke_check_runtime",
                        side_effect=fake_smoke_check,
                    ):
                        with patch(
                            "app.services.backends.accelerate.mypyc_compile",
                            side_effect=fake_mypyc_compile,
                        ):
                            status, detail = await _do_build(
                                "test-env",
                                "3.12",
                                [],
                                backend_config={
                                    "accelerate": {"mypyc_modules": ["my_pkg.transforms"]}
                                },
                            )

    assert status == "ready"
    assert "WARNING: mypyc acceleration incomplete" in detail


@pytest.mark.asyncio
async def test_do_build_mypyc_post_compile_smoke_failure_yields_error(tmp_path) -> None:
    calls = {"n": 0}

    async def fake_run(*args: str) -> tuple[int, str]:
        return 0, "ok"

    async def fake_smoke_check(env_id: str):
        # First call (post-install) passes; second call (post-mypyc) fails.
        calls["n"] += 1
        return None if calls["n"] == 1 else "import error after mypyc compile"

    async def fake_mypyc_compile(python, modules):
        return True, "mypyc: compiled my_pkg.transforms"

    with patch("app.services.backends.venv._run", side_effect=fake_run):
        with patch(
            "app.services.backends.venv.venv_dir", return_value=tmp_path / "envs" / "test-env"
        ):
            with patch("app.services.backends.venv.venv_python", return_value=tmp_path / "python"):
                with patch("app.services.backends.venv.local_nodyra_packages", return_value=[]):
                    with patch(
                        "app.services.backends.venv._smoke_check_runtime",
                        side_effect=fake_smoke_check,
                    ):
                        with patch(
                            "app.services.backends.accelerate.mypyc_compile",
                            side_effect=fake_mypyc_compile,
                        ):
                            status, detail = await _do_build(
                                "test-env",
                                "3.12",
                                [],
                                backend_config={
                                    "accelerate": {"mypyc_modules": ["my_pkg.transforms"]}
                                },
                            )

    assert status == "error"
    assert "import error after mypyc compile" in detail


# ---------------------------------------------------------------------------
# Phase 5: mypyc_compile internals (argv sequence, cwd placement, failure path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mypyc_compile_invokes_mypyc_from_site_packages_root(tmp_path) -> None:
    from app.services.backends.accelerate import mypyc_compile

    # Fake env layout: <site>/my_pkg/transforms.py for module "my_pkg.transforms".
    site = tmp_path / "site-packages"
    (site / "my_pkg").mkdir(parents=True)
    source = site / "my_pkg" / "transforms.py"
    source.write_text("def f(): return 1\n")

    recorded: list[tuple[tuple[str, ...], str | None]] = []

    async def fake_run(*args: str, cwd: str | None = None) -> tuple[int, str]:
        recorded.append((args, cwd))
        if args[0] == "uv":  # build-dep install
            return 0, "installed"
        if "-m" in args and "mypyc" in args:  # the compile itself
            return 0, "built"
        # find_spec resolve/verify calls: first resolve → .py source, then
        # verify → compiled extension so the success path is exercised.
        if any("find_spec" in a for a in args):
            resolved = len([r for r in recorded if any("find_spec" in a for a in r[0])])
            if resolved <= 1:
                return 0, str(source)
            return 0, str(source.with_suffix("")) + ".cpython-312-x86_64-linux-gnu.so"
        return 0, ""

    with patch("app.services.backends.accelerate._run", side_effect=fake_run):
        ok, log = await mypyc_compile(tmp_path / "python", ["my_pkg.transforms"])

    assert ok, log
    # Install must come first, then the compile, run FROM the site root with a
    # RELATIVE source path — mypyc emits the extension relative to its cwd, so
    # anything else would drop the .so outside the environment.
    assert recorded[0][0][:3] == ("uv", "pip", "install")
    compile_calls = [r for r in recorded if "mypyc" in r[0]]
    assert len(compile_calls) == 1
    compile_args, compile_cwd = compile_calls[0]
    assert compile_cwd == str(site)
    rel = str(source.relative_to(site))
    assert compile_args[-1] == rel
    assert "loads from compiled extension" in log


@pytest.mark.asyncio
async def test_mypyc_compile_nonzero_exit_returns_false_without_raising(tmp_path) -> None:
    from app.services.backends.accelerate import mypyc_compile

    site = tmp_path / "site-packages"
    (site / "my_pkg").mkdir(parents=True)
    source = site / "my_pkg" / "transforms.py"
    source.write_text("def f(): return 1\n")

    async def fake_run(*args: str, cwd: str | None = None) -> tuple[int, str]:
        if args[0] == "uv":
            return 0, "installed"
        if "mypyc" in args:
            return 1, "compile exploded"
        if any("find_spec" in a for a in args):
            return 0, str(source)
        return 0, ""

    with patch("app.services.backends.accelerate._run", side_effect=fake_run):
        ok, log = await mypyc_compile(tmp_path / "python", ["my_pkg.transforms"])

    assert ok is False
    assert "failed (exit=1)" in log
