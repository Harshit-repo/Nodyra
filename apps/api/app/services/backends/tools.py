"""Auto-download static tool binaries (micromamba, pixi) on first use."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Literal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TOOLS_DIR = Path(settings.envs_dir).resolve().parent / "tools"

_MICROMAMBA_URLS: dict[str, str] = {
    "linux":  "https://micro.mamba.pm/api/micromamba/linux-64/latest",
    "darwin": "https://micro.mamba.pm/api/micromamba/osx-arm64/latest",
    "win32":  "https://micro.mamba.pm/api/micromamba/win-64/latest",
}
_PIXI_URLS: dict[str, str] = {
    "linux":  "https://github.com/prefix-dev/pixi/releases/latest/download/pixi-x86_64-unknown-linux-musl",
    "darwin": "https://github.com/prefix-dev/pixi/releases/latest/download/pixi-aarch64-apple-darwin",
    "win32":  "https://github.com/prefix-dev/pixi/releases/latest/download/pixi-x86_64-pc-windows-msvc.exe",
}


async def ensure_tool(name: Literal["micromamba", "pixi"]) -> Path:
    """Return path to tool binary, downloading it first if absent."""
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    dest = TOOLS_DIR / f"{name}{suffix}"
    if dest.exists():
        return dest

    platform = sys.platform
    urls = _MICROMAMBA_URLS if name == "micromamba" else _PIXI_URLS
    url = urls.get(platform)
    if url is None:
        raise RuntimeError(
            f"No {name} download URL for platform {platform!r}. "
            "Install it manually and ensure it is on PATH."
        )
    logger.info("Downloading %s from %s", name, url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        r = await client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    if sys.platform != "win32":
        dest.chmod(0o755)
    logger.info("Downloaded %s to %s", name, dest)
    return dest
