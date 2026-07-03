"""Auto-promotion of heavy values to Dataset/Artifact refs.

Called by the engine for nodes in ``AUTO_PROMOTE_NODE_TYPES`` (currently
just Code). DataFrames and large row-shaped lists become DatasetRefs;
large bytes/strings become ArtifactRefs; small inline values pass through.

Promotion runs in the *parent* process after the node returns its raw
value, so DataFrames must already have been pickled back from the
process pool. For truly large in-worker datasets, Code should use the
``datasets`` / ``artifacts`` helpers exposed inside the worker namespace
to write a ref directly (TODO: stage B in the plan).
"""

from __future__ import annotations

import json
from typing import Any

from nodyra.artifacts import write_bytes as artifact_write_bytes
from nodyra.artifacts import write_text as artifact_write_text


def _is_dataframe(value: Any) -> bool:
    return type(value).__name__ == "DataFrame" and all(
        hasattr(value, attr) for attr in ("columns", "head", "to_dict", "shape")
    )


def _row_shaped(value: Any, *, min_rows: int) -> bool:
    if not isinstance(value, list) or len(value) < min_rows:
        return False
    sample = value[: min(10, len(value))]
    return all(isinstance(item, dict) for item in sample)


def _estimate_bytes(value: Any, *, cap: int) -> int:
    """Approximate JSON size; bail out fast if obviously over cap."""
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return cap + 1
    return len(text)


def _promote_dataframe(value: Any, port_name: str) -> Any:
    """Try to write a DataFrame to Parquet and return a DatasetRef."""
    try:
        from nodyra_nodes.datasets import dataframe_to_dataset
    except ImportError:
        return value
    try:
        return dataframe_to_dataset(value, name=f"{port_name}.parquet")
    except Exception:  # noqa: BLE001 - fall back to inline preview on failure
        return value


def _promote_records(value: list[dict[str, Any]], port_name: str) -> Any:
    try:
        from nodyra_nodes.datasets import records_to_dataset
    except ImportError:
        return value
    try:
        return records_to_dataset(value, name=f"{port_name}.parquet")
    except Exception:  # noqa: BLE001
        return value


def promote_value(
    value: Any,
    *,
    port_name: str,
    max_inline_rows: int,
    max_inline_bytes: int,
) -> Any:
    if _is_dataframe(value):
        return _promote_dataframe(value, port_name)

    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if len(raw) > max_inline_bytes:
            try:
                return artifact_write_bytes(raw, name=f"{port_name}.bin")
            except Exception:  # noqa: BLE001
                return value
        return value

    if isinstance(value, str) and len(value.encode("utf-8", errors="ignore")) > max_inline_bytes:
        try:
            return artifact_write_text(value, name=f"{port_name}.txt")
        except Exception:  # noqa: BLE001
            return value

    if _row_shaped(value, min_rows=max(1, max_inline_rows + 1)):
        return _promote_records(value, port_name)
    if isinstance(value, list) and _row_shaped(value, min_rows=1):
        size = _estimate_bytes(value, cap=max_inline_bytes)
        if size > max_inline_bytes:
            return _promote_records(value, port_name)

    return value
