"""Pluggable store for large node outputs — Prefect-inspired Result Store.

Node outputs above a configurable threshold are saved to disk (or S3) and
replaced with a lightweight ``{"__output_ref": "<key>"}`` marker in the DB.
On read, the marker is transparently resolved back to the full value.

This prevents JSON-bloating ``node_runs.output`` when a node returns MB-scale
payloads (DataFrames, API responses, file contents).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Outputs below this size (bytes) stay inline in the DB column.
# Default 1 KB — small enough to keep simple values inline but large
# enough to avoid the overhead of a file round-trip for trivial outputs.
_INLINE_THRESHOLD_BYTES: int = 1024

# Key prefix under the output root: data/outputs/<run_id>/<node_id>.json
_OUTPUT_DIR_NAME: str = "outputs"

_REF_MARKER: str = "__output_ref"

# Keys are constructed as "<run_id>/<node_id>" where both are hex UUIDs
# (32 chars, [0-9a-f]).  Anything else is a tampered ref marker.
_KEY_RE = re.compile(r"^[0-9a-fA-F]{32}/[0-9a-fA-F]{32}$")


def _validate_key(key: str) -> bool:
    """Reject keys that don't match the expected hex-UUID format."""
    return bool(_KEY_RE.match(key))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _output_root() -> Path:
    """The filesystem root for offloaded outputs.  Configurable for tests."""
    custom = getattr(settings, "output_store_path", None) or ""
    if custom:
        return Path(custom)
    return Path("data") / _OUTPUT_DIR_NAME


def maybe_offload_output(
    outputs: dict[str, Any] | None,
    *,
    run_id: str,
    node_id: str,
) -> dict[str, Any] | None:
    """If ``outputs`` is large, persist to disk and return a ref marker.

    Small outputs are returned unchanged (no-op).  ``None`` passes through.
    """
    if outputs is None:
        return None
    try:
        raw = json.dumps(outputs, default=str)
    except (TypeError, ValueError):
        return outputs  # unserializable — keep inline, it'll fail later anyway

    if len(raw) <= _INLINE_THRESHOLD_BYTES:
        return outputs

    key = f"{run_id}/{node_id}"
    try:
        _write_output(key, raw)
    except Exception:  # noqa: BLE001 — offloading is best-effort
        logger.debug("output_store: write failed key=%s — keeping inline", key)
        return outputs

    return {_REF_MARKER: key}


def resolved_output(output: Any) -> Any:
    """Resolve ``output`` if it's a ref marker, else return as-is.

    Use this in read paths that consume ``NodeRun.output`` directly
    (retry cache, debug snapshots, UI).  ``None`` passes through.
    """
    if isinstance(output, dict):
        return maybe_load_output(output)
    return output


def maybe_load_output(outputs: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve a ``__output_ref`` marker back to the full output dict.

    Non-reference values are returned unchanged.
    """
    if outputs is None:
        return None
    key = outputs.get(_REF_MARKER)
    if not isinstance(key, str) or not key:
        return outputs

    try:
        raw = _read_output(key)
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        logger.debug("output_store: read failed key=%s — returning marker", key)
        return outputs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_output(key: str, raw: str) -> None:
    if not _validate_key(key):
        raise ValueError(f"output_store: invalid key {key!r}")
    path = _output_root() / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")


def _read_output(key: str) -> str:
    if not _validate_key(key):
        raise ValueError(f"output_store: invalid key {key!r}")
    path = _output_root() / f"{key}.json"
    return path.read_text(encoding="utf-8")
