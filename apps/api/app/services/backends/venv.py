"""VenvBackend: uv-managed virtual environments."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from app.config import settings
from app.services.backends.base import _run, local_noodle_packages, venv_dir


def venv_python(env_id: str) -> Path:
    base = venv_dir(env_id)
    if sys.platform == "win32":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


class VenvBackend:
    async def build(self, env) -> tuple[str, str]:
        index_urls = list((env.backend_config or {}).get("index_urls", []))
        return await _do_build(env.id, env.python_version, list(env.packages), index_urls)

    def python_path(self, env_id: str) -> Path:
        return venv_python(env_id)

    async def destroy(self, env_id: str) -> None:
        target = venv_dir(env_id)
        if target.exists():
            shutil.rmtree(target)




async def _do_build(
    env_id: str,
    python_version: str,
    packages: list[str],
    index_urls: list[str] | None = None,
) -> tuple[str, str]:
    target = venv_dir(env_id)
    try:
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        code, log = await _run("uv", "venv", "--python", python_version, str(target))
        if code != 0:
            return "error", log.strip()[-4000:]

        to_install = [*local_noodle_packages(), *packages]
        if to_install:
            extra_index_args: list[str] = []
            for url in (index_urls or []):
                extra_index_args += ["--extra-index-url", url]
            code, install_log = await _run(
                "uv", "pip", "install",
                "--python", str(venv_python(env_id)),
                *extra_index_args,
                *to_install,
            )
            log = f"{log}\n{install_log}"

        status = "ready" if code == 0 else "error"
        return status, log.strip()[-4000:]
    except Exception as exc:  # noqa: BLE001
        return "error", f"{type(exc).__name__}: {exc}"


async def _measure_worker_rss(env_id: str) -> int | None:
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return None

    python = venv_python(env_id)
    if not python.exists():
        return None

    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            str(python), "-u", "-m", "noodle_runtime",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None:
            return None
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
        except TimeoutError:
            return None
        if not line:
            return None
        try:
            ready = json.loads(line)
        except json.JSONDecodeError:
            return None
        if ready.get("type") != "ready":
            return None
        try:
            rss = int(psutil.Process(process.pid).memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            return None
        return rss if rss > 0 else None
    except (OSError, RuntimeError):
        return None
    finally:
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            except ProcessLookupError:
                pass
