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
import shutil
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.metrics import output_store_events_total

logger = logging.getLogger(__name__)

# Outputs below this size (bytes) stay inline in the DB column.
# Default 1 KB — small enough to keep simple values inline but large
# enough to avoid the overhead of a file round-trip for trivial outputs.
_INLINE_THRESHOLD_BYTES: int = 1024

# Key prefix under the output root: data/outputs/<run_id>/<node_id>.json
_OUTPUT_DIR_NAME: str = "outputs"

_REF_MARKER: str = "__output_ref"

# Keys are constructed as "<run_id>/<node_id>":
#   - run_id is always a 32-char hex UUID (``uuid4().hex``, assigned by the API).
#   - node_id is a workflow-assigned identifier, e.g. "n_ab12_3" or "producer" —
#     NOT a UUID. The original check required BOTH segments to be hex UUIDs,
#     which no real node_id ever satisfies, so ``_write_output`` raised on every
#     offload and ``maybe_offload_output`` silently kept every output inline —
#     the whole offload feature was dead code (OS-1). We now validate run_id as
#     a UUID and node_id as a bounded identifier, still forbidding path
#     separators and "."/".." segments so a tampered marker can't escape the
#     output root on read.
_RUN_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _validate_key(key: str) -> bool:
    """Reject keys that are malformed or could escape the output root."""
    parts = key.split("/")
    if len(parts) != 2:
        return False
    run_id, node_id = parts
    if not _RUN_ID_RE.match(run_id):
        return False
    if node_id in (".", "..") or not _NODE_ID_RE.match(node_id):
        return False
    return True


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
        # Best-effort: the run still succeeds with the output inline, but a
        # sustained rate here means the output store is degraded (disk full,
        # permissions, bad backend config) and every large output is silently
        # bloating node_runs.output instead of spilling to disk (P1 §Phase 1.2).
        output_store_events_total.inc(event="write_failed")
        logger.warning("output_store: write failed key=%s — keeping inline", key)
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
        # The caller receives the raw marker back — degraded, but visible
        # (better than raising and failing the read entirely).
        output_store_events_total.inc(event="read_failed")
        logger.warning("output_store: read failed key=%s — returning marker", key)
        return outputs


def delete_outputs_for_run_ids(run_ids: list[str]) -> None:
    """Delete every offloaded output file for the given runs (Phase 3.1 GC).

    Offloaded outputs live under ``<output_root>/<run_id>/`` — one directory
    per run, independent of the ``NodeRun`` DB rows. Deleting ``NodeRun`` rows
    (retention pruning) does not touch these files, so without this call
    every offloaded run leaves an orphaned directory on disk forever. Only
    ``run_id``s that already passed :func:`_validate_key`'s hex-UUID check
    when writing can match a real directory; anything else is a no-op.
    """
    root = _output_root()
    for run_id in run_ids:
        if not _RUN_ID_RE.match(run_id):
            continue  # never a real offload directory — skip rather than risk a bad path
        run_dir = root / run_id
        try:
            shutil.rmtree(run_dir, ignore_errors=False)
        except FileNotFoundError:
            pass  # nothing was ever offloaded for this run
        except OSError:
            logger.warning("output_store: failed to remove run dir %s", run_dir)


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
