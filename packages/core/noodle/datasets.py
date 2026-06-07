"""DatasetRef — artifact-backed envelope for tabular/dataset values.

A ``DatasetRef`` is a small JSON-compatible dict that points at an existing
:class:`noodle.artifacts.LocalArtifactStore` artifact (typically a Parquet
file) and carries lightweight metadata (schema, row count, optional preview).

The envelope is the *only* shape used to pass table data between nodes — the
bytes never travel inline. Nodes that intentionally need rows in Python
extract them via the ``Dataset To Records`` node which enforces a cap.

This module owns the envelope + generic helpers. DuckDB-backed read/write
implementations live in ``noodle_nodes.datasets`` to keep the heavy
dependency out of core.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from noodle.artifacts import (
    ARTIFACT_MARKER,
    is_artifact_ref,
    sanitize_name,
)
from noodle.context import artifact_store, current_node_id, node_debug

DATASET_MARKER = "__noodle_dataset__"
DATASET_VERSION = 1


def is_dataset_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get(DATASET_MARKER) is True
        and value.get("version") == DATASET_VERSION
        and isinstance(value.get("dataset_id"), str)
        and is_artifact_ref(value.get("artifact"))
    )


def _store():
    store = artifact_store.get()
    if store is None:
        raise RuntimeError("datasets are not available in this execution context")
    return store


def _remember(ref: dict[str, Any]) -> None:
    debug = node_debug.get()
    if debug is None:
        return
    datasets = debug.setdefault("datasets", [])
    if isinstance(datasets, list):
        datasets.append(
            {
                "dataset_id": ref.get("dataset_id"),
                "row_count": ref.get("row_count"),
                "columns": [c.get("name") for c in ref.get("schema", [])],
            }
        )


def make_dataset_ref(
    artifact_ref: dict[str, Any],
    *,
    schema: list[dict[str, Any]] | None = None,
    row_count: int | None = None,
    preview: list[dict[str, Any]] | None = None,
    preview_truncated: bool = False,
    format: str = "parquet",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap an existing artifact ref in a DatasetRef envelope."""
    if not is_artifact_ref(artifact_ref):
        raise ValueError("make_dataset_ref requires an artifact ref")
    return {
        DATASET_MARKER: True,
        "version": DATASET_VERSION,
        "dataset_id": uuid.uuid4().hex,
        "format": format,
        "row_count": int(row_count) if row_count is not None else None,
        "column_count": len(schema) if schema else None,
        "schema": schema or [],
        "preview": preview or [],
        "preview_truncated": preview_truncated,
        "artifact": artifact_ref,
        "metadata": metadata or {},
    }


def dataset_path_for_ref(ref: dict[str, Any]) -> Path:
    """Resolve the on-disk path of the backing artifact for a DatasetRef."""
    if not is_dataset_ref(ref):
        raise ValueError("expected a DatasetRef")
    return _store().path_for_ref(ref["artifact"])


def reserve_artifact_path(
    name: str,
    *,
    content_type: str = "application/octet-stream",
    kind: str = "dataset",
) -> tuple[Path, dict[str, Any]]:
    """Reserve a filesystem path inside the artifact store without writing yet.

    Returns ``(path, partial_ref)``. The caller must write bytes to ``path``
    and then finalize via :func:`finalize_artifact_ref` to obtain a complete
    artifact ref (with size, optional metadata, preview).
    """
    store = _store()
    artifact_id = uuid.uuid4().hex
    node_id = sanitize_name(current_node_id.get() or "unknown")
    safe_name = sanitize_name(name)
    storage_key = f"runs/{store.run_id}/{node_id}/{artifact_id}-{safe_name}"
    path = store._path_for_key(storage_key)  # noqa: SLF001 - internal helper
    path.parent.mkdir(parents=True, exist_ok=True)
    partial: dict[str, Any] = {
        ARTIFACT_MARKER: True,
        "version": 1,
        "artifact_id": artifact_id,
        "run_id": store.run_id,
        "node_id": node_id,
        "name": safe_name,
        "kind": kind,
        "content_type": content_type,
        "size_bytes": 0,
        "storage_backend": "local",
        "storage_key": storage_key,
    }
    return path, partial


def finalize_artifact_ref(
    path: Path,
    partial: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
    preview: Any = None,
) -> dict[str, Any]:
    """Complete an artifact ref reserved via :func:`reserve_artifact_path`."""
    store = _store()
    size = path.stat().st_size
    if store.max_bytes and size > store.max_bytes:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError(
            f"artifact {partial.get('name')!r} is {size} bytes; "
            f"limit is {store.max_bytes} bytes"
        )
    if store.max_count and store._written >= store.max_count:  # noqa: SLF001
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError(f"artifact limit reached for run {store.run_id}")
    store._written += 1  # noqa: SLF001
    ref = dict(partial)
    ref["size_bytes"] = size
    if metadata:
        ref["metadata"] = metadata
    if preview is not None:
        ref["preview"] = preview
    return ref


def remember_dataset(ref: dict[str, Any]) -> None:
    _remember(ref)


# ---------------------------------------------------------------------------
# Materialization hook
#
# Reading rows out of a DatasetRef requires DuckDB, which lives in the
# ``noodle_nodes`` package to keep the heavy dependency out of core. The nodes
# package registers its implementation on import; the engine calls
# :func:`materialize_dataset_rows` to expand a DatasetRef into plain rows when
# a generic (non-dataset) node receives one as input.
# ---------------------------------------------------------------------------

_Materializer = Callable[..., list[dict[str, Any]]]
_materializer: _Materializer | None = None


def register_materializer(fn: _Materializer) -> None:
    """Register the DuckDB-backed row materializer (called by noodle_nodes)."""
    global _materializer
    _materializer = fn


_DatasetWriter = Callable[..., dict[str, Any]]
_dataset_writer: _DatasetWriter | None = None


def register_dataset_writer(fn: _DatasetWriter) -> None:
    """Register the DuckDB-backed records->DatasetRef writer (called by noodle_nodes)."""
    global _dataset_writer
    _dataset_writer = fn


def dataset_from_records(
    records: list[dict[str, Any]], *, name: str = "loop_output.parquet"
) -> dict[str, Any]:
    """Write a list of dicts to a DatasetRef using the registered writer.

    Raises if no writer has been registered (i.e. the nodes package was never
    imported).
    """
    if _dataset_writer is None:
        raise RuntimeError(
            "no dataset writer registered; import noodle_nodes.datasets"
        )
    return _dataset_writer(records, name=name)


def materialize_dataset_rows(
    ref: dict[str, Any],
    *,
    cap: int,
    allow_truncate: bool = False,
) -> list[dict[str, Any]]:
    """Expand a DatasetRef into a list of row dicts (bounded by ``cap``).

    Raises if no materializer has been registered (i.e. the nodes package was
    never imported) or if the dataset exceeds ``cap`` and ``allow_truncate``
    is false.
    """
    if not is_dataset_ref(ref):
        raise ValueError("materialize_dataset_rows requires a DatasetRef")
    if _materializer is None:
        raise RuntimeError(
            "no dataset materializer registered; import noodle_nodes.datasets"
        )
    return _materializer(ref, cap=cap, allow_truncate=allow_truncate)
