"""ETag-based response caching helpers.

Provides a lightweight ``etag`` dependency that can be injected into FastAPI
route handlers to return 304 Not Modified when the client's cached copy
matches the current resource hash, reducing serialization and bandwidth on
read-heavy endpoints.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse


def compute_etag(data: Any) -> str:
    """Return a quoted hex ETag for *data* (must be JSON-serializable)."""
    raw = str(sorted(_flatten(data))).encode()
    return f'"{hashlib.md5(raw, usedforsecurity=False).hexdigest()}"'


def _flatten(obj: Any) -> Any:
    """Recursively normalise dicts so the same logical data always has the
    same ETag regardless of key ordering."""
    if isinstance(obj, dict):
        return {k: _flatten(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return sorted(_flatten(v) for v in obj)
    return obj


def etag(data: Any, request: Request) -> Response:
    """Build a JSON response for *data* with an ``ETag`` header.

    If the request carries ``If-None-Match`` matching the computed ETag, a
    304 Not Modified (empty body) is returned instead.
    """
    tag = compute_etag(data)
    if_none = request.headers.get("If-None-Match")
    if if_none and if_none.strip('" ') == tag.strip('"'):
        return Response(status_code=304, headers={"ETag": tag})
    return JSONResponse(data, headers={"ETag": tag})
