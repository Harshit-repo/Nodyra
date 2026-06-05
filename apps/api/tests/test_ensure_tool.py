"""Unit tests for ensure_tool() auto-download."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.backends.tools as tools_mod


@pytest.mark.asyncio
async def test_ensure_tool_returns_existing_binary(tmp_path: Path) -> None:
    suffix = ".exe" if sys.platform == "win32" else ""
    dest = tmp_path / f"micromamba{suffix}"
    dest.write_bytes(b"fake binary content")

    with patch.object(tools_mod, "TOOLS_DIR", tmp_path):
        result = await tools_mod.ensure_tool("micromamba")

    assert result == dest
    assert result.read_bytes() == b"fake binary content"


@pytest.mark.asyncio
async def test_ensure_tool_does_not_download_when_binary_exists(tmp_path: Path) -> None:
    """No HTTP call when binary already present."""
    suffix = ".exe" if sys.platform == "win32" else ""
    dest = tmp_path / f"pixi{suffix}"
    dest.write_bytes(b"existing pixi")

    with patch.object(tools_mod, "TOOLS_DIR", tmp_path):
        with patch("httpx.AsyncClient") as mock_cls:
            result = await tools_mod.ensure_tool("pixi")

    assert result == dest
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_tool_downloads_when_absent(tmp_path: Path) -> None:
    mock_response = MagicMock()
    mock_response.content = b"downloaded binary"
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch.object(tools_mod, "TOOLS_DIR", tmp_path):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tools_mod.ensure_tool("micromamba")

    suffix = ".exe" if sys.platform == "win32" else ""
    assert result == tmp_path / f"micromamba{suffix}"
    assert result.exists()
    assert result.read_bytes() == b"downloaded binary"


@pytest.mark.asyncio
async def test_ensure_tool_raises_on_http_error(tmp_path: Path) -> None:
    import httpx

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch.object(tools_mod, "TOOLS_DIR", tmp_path):
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                await tools_mod.ensure_tool("pixi")
