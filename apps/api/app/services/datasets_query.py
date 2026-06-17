"""Read-only DuckDB SQL execution against dataset artifacts.

Powers the "Open in SQL" explorer in the NDV. The backing Parquet file is
exposed to the query as the views ``dataset`` and ``input``. Only a single
read-only ``SELECT``/``WITH`` statement is accepted and the result set is
hard-capped so a heavy workflow can be inspected without materialising the
whole table.
"""

from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path
from typing import Any

from app.models import Artifact
from app.services.artifact_backends import get_backend

# Statements that must never run through the explorer. The query is also
# required to start with SELECT/WITH and contain no statement separator, so
# this list is defence-in-depth rather than the only gate.
_FORBIDDEN = re.compile(
    r"\b("
    r"insert|update|delete|drop|create|alter|attach|detach|copy|export|import|"
    r"install|load|pragma|call|set|reset|vacuum|checkpoint|truncate|replace|"
    r"read_csv|read_csv_auto|read_json|read_json_auto|read_parquet|read_ndjson|"
    r"read_text|read_blob|parquet_scan|csv_scan|glob"
    r")\b",
    re.IGNORECASE,
)

MAX_ROWS = 1000
DEFAULT_ROWS = 200


class DatasetQueryError(ValueError):
    """Raised for invalid queries or unsupported artifacts."""


def _validate_sql(sql: str) -> str:
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise DatasetQueryError("Query is empty")
    if ";" in cleaned:
        raise DatasetQueryError("Only a single statement is allowed")
    lowered = cleaned.lstrip("(").lstrip().lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise DatasetQueryError("Only SELECT / WITH queries are allowed")
    if _FORBIDDEN.search(cleaned):
        raise DatasetQueryError("Query contains a disallowed keyword")
    return cleaned


def _local_path(artifact: Artifact) -> tuple[Path, Path | None]:
    """Return ``(path, temp_to_cleanup)`` for the artifact's bytes.

    Local artifacts resolve to their on-disk path directly. Object-store
    backends are streamed into a temp file so DuckDB can read them.
    """
    backend = get_backend(artifact.storage_backend)
    path_for = getattr(backend, "path_for_artifact", None)
    if path_for is not None:
        path = path_for(artifact)
        if not Path(path).exists():
            raise DatasetQueryError("Artifact file not found")
        return Path(path), None
    download = backend.open_download(artifact)
    if download.path is not None:
        return Path(download.path), None
    if download.stream is None:
        raise DatasetQueryError("Artifact backend produced no readable bytes")
    tmp = Path(tempfile.mkstemp(suffix=".parquet")[1])
    with tmp.open("wb") as fh:
        for chunk in download.stream:
            fh.write(chunk)
    return tmp, tmp


def run_dataset_query(artifact: Artifact, sql: str, limit: int) -> dict[str, Any]:
    """Execute a read-only query against the artifact's Parquet file."""
    try:
        import duckdb  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - duckdb ships in the image
        raise DatasetQueryError("DuckDB is not available on the server") from exc

    cleaned = _validate_sql(sql)
    capped = max(1, min(int(limit or DEFAULT_ROWS), MAX_ROWS))
    path, tmp = _local_path(artifact)

    started = time.perf_counter()
    conn = duckdb.connect(":memory:")
    try:
        escaped = str(path).replace("'", "''")
        # Eagerly materialize into an in-memory TABLE (not a lazy view) so the
        # only external file access happens here, under our control. Then latch
        # off ALL external access before the user's query runs: this is the real
        # guard against server-side file reads via DuckDB table functions
        # (read_csv_auto/parquet_scan/read_ndjson/… — the _FORBIDDEN regex can't
        # enumerate every alias). enable_external_access is one-way in DuckDB, so
        # once false the user query cannot touch the filesystem or network.
        # Trade-off: the dataset is loaded into memory; acceptable for the
        # inspect-a-dataset use case and bounded by the artifact's own size.
        conn.execute(
            f"CREATE TABLE dataset AS SELECT * FROM read_parquet('{escaped}')"
        )
        conn.execute("CREATE VIEW input AS SELECT * FROM dataset")
        conn.execute("SET enable_external_access=false")
        # Wrap to enforce the row cap without trusting a user LIMIT.
        wrapped = f"SELECT * FROM (\n{cleaned}\n) AS _q LIMIT {capped + 1}"
        rel = conn.execute(wrapped)
        columns = [
            {"name": c, "type": str(t)}
            for c, t in zip(
                [d[0] for d in rel.description or []],
                [d[1] for d in rel.description or []],
                strict=True,
            )
        ]
        fetched = rel.fetchall()
        truncated = len(fetched) > capped
        rows_data = fetched[:capped]
        col_names = [c["name"] for c in columns]
        rows = [
            {name: _jsonify(val) for name, val in zip(col_names, row, strict=True)}
            for row in rows_data
        ]
    except DatasetQueryError:
        raise
    except Exception as exc:  # surface DuckDB parse/runtime errors verbatim
        raise DatasetQueryError(str(exc)) from exc
    finally:
        conn.close()
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _jsonify(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    return str(value)
