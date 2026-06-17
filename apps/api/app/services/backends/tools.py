"""Auto-download static tool binaries (micromamba, pixi) on first use."""
from __future__ import annotations

import io
import logging
import sys
import tarfile
from pathlib import Path
from typing import Literal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TOOLS_DIR = Path(settings.envs_dir).resolve().parent / "tools"

# micromamba API returns a tar.bz2 archive containing bin/micromamba
_MICROMAMBA_URLS: dict[str, str] = {
    "linux":  "https://micro.mamba.pm/api/micromamba/linux-64/latest",
    "darwin": "https://micro.mamba.pm/api/micromamba/osx-arm64/latest",
    "win32":  "https://micro.mamba.pm/api/micromamba/win-64/latest",
}
# pixi releases are direct static binaries
_PIXI_URLS: dict[str, str] = {
    "linux":  "https://github.com/prefix-dev/pixi/releases/latest/download/pixi-x86_64-unknown-linux-musl",
    "darwin": "https://github.com/prefix-dev/pixi/releases/latest/download/pixi-aarch64-apple-darwin",
    "win32":  "https://github.com/prefix-dev/pixi/releases/latest/download/pixi-x86_64-pc-windows-msvc.exe",
}


def _extract_micromamba(data: bytes) -> bytes:
    """Extract the micromamba binary from the tar.bz2 payload."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tf:
        member = tf.getmember("bin/micromamba")
        f = tf.extractfile(member)
        if f is None:
            raise RuntimeError("bin/micromamba not found inside micromamba archive")
        return f.read()


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
        binary = _extract_micromamba(r.content) if name == "micromamba" else r.content
        dest.write_bytes(binary)
    if sys.platform != "win32":
        dest.chmod(0o755)
    logger.info("Downloaded %s to %s", name, dest)
    return dest
