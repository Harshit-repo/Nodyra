"""Dataset nodes — DuckDB/Parquet-backed table operations.

These nodes are the data plane of Noodle. They produce and consume
``DatasetRef`` envelopes (small JSON wrappers around a Parquet artifact)
rather than materializing rows as Python lists.

Use this module's public helpers (``dataframe_to_dataset``,
``records_to_dataset``, ``read_dataset``) from the engine's auto-promote
hook and from Code nodes that want to write datasets directly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from noodle.artifacts import is_artifact_ref
from noodle.datasets import (
    dataset_path_for_ref,
    finalize_artifact_ref,
    is_dataset_ref,
    make_dataset_ref,
    register_materializer,
    remember_dataset,
    reserve_artifact_path,
)
from noodle.sdk import node

_DUCKDB_ERROR = (
    "DuckDB is required for dataset nodes. Install with `uv pip install duckdb`."
)


def _duckdb():
    try:
        import duckdb  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(_DUCKDB_ERROR) from exc
    return duckdb


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_ident(name: str) -> str:
    """Quote a column/identifier for safe interpolation into DuckDB SQL."""
    if not isinstance(name, str) or not name:
        raise ValueError("column name must be a non-empty string")
    return '"' + name.replace('"', '""') + '"'


def _ensure_dataset(value: Any, *, label: str = "input") -> dict[str, Any]:
    if is_dataset_ref(value):
        return value
    raise ValueError(
        f"{label} must be a DatasetRef. Use Records To Dataset, CSV Parse, "
        f"or Read Parquet to produce one."
    )


def _schema_from_duckdb(conn) -> list[dict[str, Any]]:
    desc = conn.description or []
    return [{"name": col[0], "type": str(col[1])} for col in desc]


def _preview_from_table(path: Path, *, limit: int = 50) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], int, bool,
]:
    duckdb = _duckdb()
    conn = duckdb.connect(":memory:")
    try:
        rel = conn.from_parquet(str(path))
        row_count = int(rel.count("*").fetchone()[0])
        preview_rel = rel.limit(limit)
        columns = preview_rel.columns
        rows = preview_rel.fetchall()
        preview = [
            {col: _jsonify(val) for col, val in zip(columns, row, strict=True)}
            for row in rows
        ]
        types = preview_rel.dtypes
        schema = [{"name": c, "type": str(t)} for c, t in zip(columns, types, strict=True)]
        return preview, schema, row_count, row_count > limit
    finally:
        conn.close()


def _jsonify(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    return str(value)


def _finalize_parquet(
    path: Path,
    partial: dict[str, Any],
    *,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preview, schema, row_count, truncated = _preview_from_table(path, limit=50)
    artifact = finalize_artifact_ref(
        path,
        partial,
        metadata={"format": "parquet", **(extra_metadata or {})},
        preview={
            "row_count": row_count,
            "preview_rows": len(preview),
        },
    )
    ref = make_dataset_ref(
        artifact,
        schema=schema,
        row_count=row_count,
        preview=preview,
        preview_truncated=truncated,
        format="parquet",
        metadata=extra_metadata or {},
    )
    remember_dataset(ref)
    return ref


# ---------------------------------------------------------------------------
# Public helpers (used by engine auto-promote + Code helpers)
# ---------------------------------------------------------------------------


def dataframe_to_dataset(df: Any, *, name: str = "dataset.parquet") -> dict[str, Any]:
    """Write a pandas/arrow DataFrame to Parquet and return a DatasetRef."""
    duckdb = _duckdb()
    path, partial = reserve_artifact_path(
        name, content_type="application/vnd.apache.parquet", kind="dataset"
    )
    conn = duckdb.connect(":memory:")
    try:
        conn.register("df", df)
        conn.execute(
            f"COPY (SELECT * FROM df) TO '{str(path).replace(chr(39), chr(39) * 2)}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        conn.close()
    return _finalize_parquet(path, partial)


def records_to_dataset(
    records: list[dict[str, Any]], *, name: str = "dataset.parquet"
) -> dict[str, Any]:
    """Write a list of dicts to Parquet and return a DatasetRef."""
    if not isinstance(records, list):
        raise ValueError("records_to_dataset expects a list of dicts")
    duckdb = _duckdb()

    if not records:
        # DuckDB cannot infer schema from zero rows — write an empty Parquet
        # with a single placeholder column so downstream nodes still see a
        # valid dataset.
        import pyarrow as pa  # type: ignore[import-not-found]
        import pyarrow.parquet as pq  # type: ignore[import-not-found]

        path, partial = reserve_artifact_path(
            name, content_type="application/vnd.apache.parquet", kind="dataset"
        )
        pq.write_table(pa.table({"_empty": []}), str(path))
        return _finalize_parquet(path, partial)

    path, partial = reserve_artifact_path(
        name, content_type="application/vnd.apache.parquet", kind="dataset"
    )
    conn = duckdb.connect(":memory:")
    try:
        # Use Arrow as the bridge to preserve types/nulls.
        import pyarrow as pa  # type: ignore[import-not-found]

        keys: list[str] = []
        seen: set[str] = set()
        for row in records:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    keys.append(str(key))
        table = pa.Table.from_pylist(
            [{k: row.get(k) for k in keys} for row in records]
        )
        conn.register("t", table)
        conn.execute(
            f"COPY (SELECT * FROM t) TO '{str(path).replace(chr(39), chr(39) * 2)}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        conn.close()
    return _finalize_parquet(path, partial)


def read_dataset(ref: dict[str, Any]):
    """Return a DuckDB relation over a DatasetRef's Parquet file."""
    duckdb = _duckdb()
    _ensure_dataset(ref)
    path = dataset_path_for_ref(ref)
    conn = duckdb.connect(":memory:")
    return conn, conn.from_parquet(str(path))


def materialize_dataset(
    ref: Any,
    *,
    cap: int = 10000,
    allow_truncate: bool = False,
) -> list[dict[str, Any]]:
    """Read rows from a DatasetRef into a list of dicts (bounded by ``cap``).

    Raises if the dataset has more than ``cap`` rows unless ``allow_truncate``
    is set, mirroring the ``Dataset To Records`` node's safety contract. Used
    by the engine to auto-expand a DatasetRef passed into a generic per-item
    node (Loop Over Items, Filter, Edit Fields, …).
    """
    dataset = _ensure_dataset(ref, label="input")
    limit = max(1, int(cap or 1))
    duckdb = _duckdb()
    path = str(dataset_path_for_ref(dataset)).replace("'", "''")
    conn = duckdb.connect(":memory:")
    try:
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM read_parquet('{path}')"
            ).fetchone()[0]
        )
        if total > limit and not allow_truncate:
            raise ValueError(
                f"dataset has {total} rows but only {limit} can be expanded "
                f"inline; reduce rows upstream with Dataset Filter/Limit, or "
                f"use Dataset To Records with allow_truncate to opt in."
            )
        rel = conn.execute(f"SELECT * FROM read_parquet('{path}') LIMIT {limit}")
        cols = [d[0] for d in rel.description]
        rows = rel.fetchall()
    finally:
        conn.close()
    return [
        {col: _jsonify(val) for col, val in zip(cols, row, strict=True)}
        for row in rows
    ]


# Register the DuckDB-backed materializer so the engine (core) can expand
# DatasetRefs into rows without importing DuckDB directly.
register_materializer(materialize_dataset)


# ---------------------------------------------------------------------------
# Built-in dataset nodes
# ---------------------------------------------------------------------------


@node(
    name="CSV Parse",
    requirements=["duckdb"],
    id="csv_parse",
    category="Data",
    icon="table",
    output_kinds={"main": "dataset"},
    params={
        "text": {
            "description": "CSV text to parse. Falls back to the wired input if blank.",
            "multiline": True,
        },
        "delimiter": {
            "group": "Options",
            "placeholder": ",",
            "description": "Field delimiter (default ,).",
        },
        "has_header": {
            "group": "Options",
            "description": "First row is the header row.",
        },
    },
)
def csv_parse(
    input: Any = None,
    text: str = "",
    delimiter: str = ",",
    has_header: bool = True,
) -> dict[str, Any]:
    """Parse CSV text/bytes into a DatasetRef (Parquet-backed).

    Returns a small reference, not the rows themselves. Use ``Dataset
    Preview`` to see a sample or ``Dataset To Records`` to materialize
    rows when you must process them inline.
    """
    duckdb = _duckdb()

    payload = text
    if not payload and is_artifact_ref(input):
        from noodle.artifacts import read_text as artifact_read_text

        payload = artifact_read_text(input)
    if not payload and input is not None:
        payload = input if isinstance(input, str) else str(input)
    if payload is None:
        payload = ""

    src_path, src_partial = reserve_artifact_path(
        "input.csv", content_type="text/csv; charset=utf-8", kind="csv_source"
    )
    src_path.write_text(payload, encoding="utf-8")
    # Don't register the source artifact — we only need a temp file to
    # feed DuckDB and then immediately throw away the row.

    out_path, out_partial = reserve_artifact_path(
        "dataset.parquet",
        content_type="application/vnd.apache.parquet",
        kind="dataset",
    )
    conn = duckdb.connect(":memory:")
    try:
        delim = (delimiter or ",")[:4]
        delim_sql = delim.replace("'", "''")
        header_sql = "TRUE" if has_header else "FALSE"
        src_sql = str(src_path).replace("'", "''")
        out_sql = str(out_path).replace("'", "''")
        conn.execute(
            f"COPY (SELECT * FROM read_csv_auto('{src_sql}', "
            f"delim='{delim_sql}', header={header_sql})) "
            f"TO '{out_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        conn.close()
        try:
            src_path.unlink(missing_ok=True)
        except OSError:
            pass
    return _finalize_parquet(out_path, out_partial)


@node(
    name="CSV Write",
    requirements=["duckdb"],
    id="csv_write",
    category="Data",
    icon="table",
    input_kinds={"input": "dataset"},
    output_kinds={"main": "artifact"},
    params={
        "filename": {
            "placeholder": "export.csv",
            "description": "Filename for the exported CSV artifact.",
        },
        "delimiter": {
            "group": "Options",
            "placeholder": ",",
            "description": "Field delimiter (default ,).",
        },
        "include_header": {
            "group": "Options",
            "description": "Write a header row.",
        },
    },
)
def csv_write(
    input: Any = None,
    filename: str = "export.csv",
    delimiter: str = ",",
    include_header: bool = True,
) -> dict[str, Any]:
    """Export a DatasetRef as a CSV ArtifactRef (the file, not a string)."""
    ref = _ensure_dataset(input, label="input")
    duckdb = _duckdb()
    src = str(dataset_path_for_ref(ref)).replace("'", "''")
    out_path, out_partial = reserve_artifact_path(
        filename or "export.csv",
        content_type="text/csv; charset=utf-8",
        kind="csv_export",
    )
    delim_sql = (delimiter or ",")[:4].replace("'", "''")
    header_sql = "TRUE" if include_header else "FALSE"
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            f"COPY (SELECT * FROM read_parquet('{src}')) "
            f"TO '{str(out_path).replace(chr(39), chr(39) * 2)}' "
            f"(FORMAT CSV, HEADER {header_sql}, DELIMITER '{delim_sql}')"
        )
    finally:
        conn.close()
    return finalize_artifact_ref(
        out_path,
        out_partial,
        metadata={"format": "csv", "rows": ref.get("row_count")},
    )


@node(
    name="Records To Dataset",
    requirements=["duckdb"],
    id="records_to_dataset",
    category="Data",
    icon="table",
    output_kinds={"main": "dataset"},
)
def records_to_dataset_node(input: Any = None) -> dict[str, Any]:
    """Convert a list of dicts (or object with `records`/`rows`/`items`) into a DatasetRef."""
    rows: list[dict[str, Any]]
    if isinstance(input, list):
        rows = [r for r in input if isinstance(r, dict)]
    elif isinstance(input, dict):
        for key in ("records", "rows", "items", "data", "results"):
            candidate = input.get(key)
            if isinstance(candidate, list):
                rows = [r for r in candidate if isinstance(r, dict)]
                break
        else:
            rows = []
    else:
        rows = []
    return records_to_dataset(rows)


@node(
    name="Dataset Preview",
    requirements=["duckdb"],
    id="dataset_preview",
    category="Data",
    icon="eye",
    input_kinds={"input": "dataset"},
    params={
        "limit": {"description": "Number of preview rows (max 1000)."},
    },
)
def dataset_preview(input: Any = None, limit: int = 100) -> dict[str, Any]:
    """Return a small inline preview (rows + schema + row count) of a DatasetRef."""
    ref = _ensure_dataset(input, label="input")
    cap = max(1, min(1000, int(limit or 100)))
    preview, schema, row_count, truncated = _preview_from_table(
        dataset_path_for_ref(ref), limit=cap
    )
    return {
        "row_count": row_count,
        "preview_row_count": len(preview),
        "truncated": truncated,
        "columns": [s["name"] for s in schema],
        "schema": schema,
        "rows": preview,
    }


@node(
    name="Dataset To Records",
    requirements=["duckdb"],
    id="dataset_to_records",
    category="Data",
    icon="list",
    input_kinds={"input": "dataset"},
    params={
        "max_rows": {
            "description": (
                "Maximum rows to materialize inline (required, hard max 10000)."
            ),
        },
        "allow_truncate": {
            "description": (
                "If true, silently truncate when the dataset has more rows than max_rows. "
                "If false (default), the node errors so you don't accidentally drop rows."
            ),
        },
    },
)
def dataset_to_records(
    input: Any = None,
    max_rows: int = 1000,
    allow_truncate: bool = False,
) -> list[dict[str, Any]]:
    """Materialize rows from a DatasetRef as a Python list of dicts."""
    cap = max(1, min(10000, int(max_rows or 1000)))
    return materialize_dataset(input, cap=cap, allow_truncate=allow_truncate)


@node(
    name="Dataset Select Columns",
    requirements=["duckdb"],
    id="dataset_select",
    category="Data",
    icon="columns",
    input_kinds={"input": "dataset"},
    output_kinds={"main": "dataset"},
    params={
        "columns": {
            "description": "Comma-separated list of columns to keep.",
            "placeholder": "id, name, email",
        },
    },
)
def dataset_select(input: Any = None, columns: str = "") -> dict[str, Any]:
    """Keep only the listed columns from a DatasetRef."""
    ref = _ensure_dataset(input, label="input")
    names = [c.strip() for c in (columns or "").split(",") if c.strip()]
    if not names:
        return ref
    duckdb = _duckdb()
    src = str(dataset_path_for_ref(ref)).replace("'", "''")
    select_list = ", ".join(quote_ident(n) for n in names)
    out_path, out_partial = reserve_artifact_path(
        "dataset.parquet",
        content_type="application/vnd.apache.parquet",
        kind="dataset",
    )
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            f"COPY (SELECT {select_list} FROM read_parquet('{src}')) "
            f"TO '{str(out_path).replace(chr(39), chr(39) * 2)}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        conn.close()
    return _finalize_parquet(out_path, out_partial)


@node(
    name="Dataset Filter",
    requirements=["duckdb"],
    id="dataset_filter",
    category="Data",
    icon="filter",
    input_kinds={"input": "dataset"},
    output_kinds={"main": "dataset"},
    params={
        "where": {
            "description": (
                "SQL WHERE expression (DuckDB syntax). e.g. age > 18 AND status = 'active'."
            ),
            "placeholder": "age > 18",
            "multiline": True,
        },
    },
)
def dataset_filter(input: Any = None, where: str = "") -> dict[str, Any]:
    """Filter rows by a DuckDB SQL boolean expression."""
    ref = _ensure_dataset(input, label="input")
    if not where.strip():
        return ref
    if ";" in where:
        raise ValueError("filter expression must not contain ';'")
    duckdb = _duckdb()
    src = str(dataset_path_for_ref(ref)).replace("'", "''")
    out_path, out_partial = reserve_artifact_path(
        "dataset.parquet",
        content_type="application/vnd.apache.parquet",
        kind="dataset",
    )
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            f"COPY (SELECT * FROM read_parquet('{src}') WHERE {where}) "
            f"TO '{str(out_path).replace(chr(39), chr(39) * 2)}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        conn.close()
    return _finalize_parquet(out_path, out_partial)


@node(
    name="Dataset Limit",
    requirements=["duckdb"],
    id="dataset_limit",
    category="Data",
    icon="hash",
    input_kinds={"input": "dataset"},
    output_kinds={"main": "dataset"},
    params={
        "limit": {"description": "Max rows to keep."},
        "offset": {"description": "Rows to skip from the start (default 0)."},
    },
)
def dataset_limit(input: Any = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Keep at most ``limit`` rows starting at ``offset``."""
    ref = _ensure_dataset(input, label="input")
    n = max(0, int(limit or 0))
    off = max(0, int(offset or 0))
    duckdb = _duckdb()
    src = str(dataset_path_for_ref(ref)).replace("'", "''")
    out_path, out_partial = reserve_artifact_path(
        "dataset.parquet",
        content_type="application/vnd.apache.parquet",
        kind="dataset",
    )
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            f"COPY (SELECT * FROM read_parquet('{src}') LIMIT {n} OFFSET {off}) "
            f"TO '{str(out_path).replace(chr(39), chr(39) * 2)}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        conn.close()
    return _finalize_parquet(out_path, out_partial)


@node(
    name="DuckDB SQL",
    requirements=["duckdb"],
    id="duckdb_sql",
    category="Data",
    icon="terminal",
    input_kinds={"input": "dataset"},
    output_kinds={"main": "dataset"},
    params={
        "sql": {
            "multiline": True,
            "description": (
                "DuckDB SELECT against the wired dataset, referenced as `input`. "
                "Example: SELECT name, COUNT(*) AS n FROM input GROUP BY name"
            ),
            "placeholder": "SELECT * FROM input",
        },
    },
)
def duckdb_sql(input: Any = None, sql: str = "SELECT * FROM input") -> dict[str, Any]:
    """Run a single DuckDB SELECT over the wired DatasetRef."""
    ref = _ensure_dataset(input, label="input")
    query = (sql or "SELECT * FROM input").strip().rstrip(";")
    if ";" in query:
        raise ValueError("duckdb_sql expects a single statement (no `;`)")
    head = query.lstrip().split(None, 1)[0].lower() if query else ""
    if head not in ("select", "with"):
        raise ValueError("duckdb_sql only allows SELECT / WITH queries")

    duckdb = _duckdb()
    src = str(dataset_path_for_ref(ref)).replace("'", "''")
    out_path, out_partial = reserve_artifact_path(
        "dataset.parquet",
        content_type="application/vnd.apache.parquet",
        kind="dataset",
    )
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(f"CREATE VIEW input AS SELECT * FROM read_parquet('{src}')")
        conn.execute(
            f"COPY ({query}) TO '{str(out_path).replace(chr(39), chr(39) * 2)}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        conn.close()
    return _finalize_parquet(out_path, out_partial)
