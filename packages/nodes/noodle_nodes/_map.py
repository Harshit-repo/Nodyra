"""Shared child-workflow call helper for map nodes."""

from __future__ import annotations

import asyncio
from typing import Any


async def _map_call_child(
    *,
    caller: Any,
    workflow_id: str,
    payload: dict,
    index: int,
    sem: asyncio.Semaphore,
) -> dict:
    """Call a child workflow under a semaphore; always returns a result dict."""
    async with sem:
        try:
            result = await caller(workflow_id, payload)
            return {"index": index, "ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"index": index, "ok": False, "error": str(exc), "input": payload}
