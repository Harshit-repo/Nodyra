"""Tests for POST /artifacts/upload — browser file upload endpoint."""
from __future__ import annotations

import io

import pytest
from httpx import AsyncClient


async def test_upload_artifact_returns_artifact_info(client: AsyncClient) -> None:
    content = b"col1,col2\nval1,val2\n"
    resp = await client.post(
        "/artifacts/upload",
        files={"file": ("test.csv", io.BytesIO(content), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "test.csv"
    assert data["content_type"] == "text/csv"
    assert data["size_bytes"] == len(content)
    assert data["kind"] == "upload"
    assert "id" in data


async def test_upload_artifact_missing_file_returns_422(client: AsyncClient) -> None:
    resp = await client.post("/artifacts/upload")
    assert resp.status_code == 422


async def test_upload_artifact_can_be_downloaded(client: AsyncClient) -> None:
    content = b"hello upload"
    upload_resp = await client.post(
        "/artifacts/upload",
        files={"file": ("hello.txt", io.BytesIO(content), "text/plain")},
    )
    assert upload_resp.status_code == 200
    artifact_id = upload_resp.json()["id"]

    download_resp = await client.get(f"/artifacts/{artifact_id}/download")
    assert download_resp.status_code == 200
    assert download_resp.content == content
