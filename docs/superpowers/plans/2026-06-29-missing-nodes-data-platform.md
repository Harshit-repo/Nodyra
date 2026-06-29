# Missing Nodes & Data Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add standalone Parquet/Excel file nodes, Snowflake/BigQuery/dbt Cloud/MLflow data-platform nodes, a GitLab pipeline-trigger operation, Docker security hardening, and AI-builder allow-list additions — all production-grade with tests.

**Architecture:** New file nodes follow the existing `read_csv_file`/`write_csv_file` pattern in `file_nodes.py` (file-upload widget + server path, DatasetRef output, duckdb/pyarrow under the hood). New data-platform nodes live in a new `data_platform_nodes.py` file using lazy SDK imports. GitLab pipeline trigger is a new `OperationSpec` in the existing `gitlab/operations.py`. AI builder allow-list is updated in `ai_builder.py`.

**Tech Stack:** Python 3.12+, duckdb ≥ 1.0, pyarrow ≥ 15.0, openpyxl (lazy), snowflake-connector-python (lazy), google-cloud-bigquery (lazy), mlflow (lazy), requests (for dbt Cloud API). Tests use pytest + unittest.mock.

## Global Constraints

- All new SDK imports must be lazy (inside function body) with a helpful `RuntimeError` if missing
- All nodes follow `@node(id=..., name=..., category=..., description=..., icon=...)` decorator pattern
- DatasetRef is the canonical output type for tabular data — use `_finalize_parquet` + `reserve_artifact_path`
- Credentials must use the `cred_multi(...)` helper or `CredentialSpec` — never plain string fields for secrets
- No secrets in log output; use `safe_request` from `noodle_nodes.http_security` for HTTP calls
- Tests run with: `cd packages/nodes && python -m pytest tests/<file>.py -v`
- Every node added to the palette must also be added to `_ALLOWED_NODE_TYPES` and `_NODE_REGISTRY` in `ai_builder.py`
- Each new file or module import must be added to `noodle_nodes/__init__.py`

---

## Audit: Existing Nodes That Need Enhancement

Before adding new nodes, fix gaps in existing ones:

| Node | Issue | Fix |
|------|-------|-----|
| `docker_run_container` | No security warning; no tests for run/stop; no multi-tenancy guard | Add description warning; add tests |
| `gitlab` | Missing pipeline trigger operation (assessment lists CI trigger as needed) | Add `gitlab_trigger_pipeline_v2` operation |

---

## File Map

**Create:**
- `packages/nodes/noodle_nodes/data_platform_nodes.py` — Snowflake, BigQuery, dbt Cloud, MLflow nodes
- `packages/nodes/tests/test_data_platform_nodes.py` — tests for all data platform nodes
- `packages/nodes/tests/test_gitlab_v2.py` — GitLab registration + executor tests

**Modify:**
- `packages/nodes/noodle_nodes/file_nodes.py` — add `read_parquet_file`, `write_parquet_file`, `read_excel_file`, `write_excel_file`
- `packages/nodes/noodle_nodes/__init__.py` — import `data_platform_nodes`
- `packages/nodes/noodle_nodes/integrations_v2/providers/gitlab/operations.py` — add `gitlab_trigger_pipeline_v2`
- `packages/nodes/noodle_nodes/docker_nodes.py` — security description hardening
- `packages/nodes/tests/test_file_nodes.py` — add parquet + excel tests
- `packages/nodes/tests/test_cloud_devops.py` — add docker run/stop tests
- `apps/api/app/services/ai_builder.py` — add new nodes to `_ALLOWED_NODE_TYPES` + `_NODE_REGISTRY`

---

## Task 1: Standalone Read/Write Parquet Nodes

**Files:**
- Modify: `packages/nodes/noodle_nodes/file_nodes.py`
- Test: `packages/nodes/tests/test_file_nodes.py`

**Interfaces:**
- Consumes: `_read_upload_bytes(artifact_id)`, `_finalize_parquet(path, partial)`, `reserve_artifact_path(name, content_type, kind)`, `_DATASET_TOGGLE` constant — all already in file_nodes.py or importable from noodle.datasets
- Produces: `read_parquet_file(input, path, file, columns, limit)` → DatasetRef dict; `write_parquet_file(input, path, compression)` → DatasetRef dict

- [ ] **Step 1: Write failing tests for read_parquet_file and write_parquet_file**

Add to `packages/nodes/tests/test_file_nodes.py`:

```python
import io
import pathlib
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from unittest.mock import patch

from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, current_node_id
from noodle_nodes.file_nodes import read_parquet_file, write_parquet_file


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="test-run")
    tok_a = artifact_store.set(store)
    tok_n = current_node_id.set("test-node")
    yield store
    current_node_id.reset(tok_n)
    artifact_store.reset(tok_a)


def _write_test_parquet(path: pathlib.Path) -> None:
    table = pa.table({"name": ["Alice", "Bob"], "score": [95, 87]})
    pq.write_table(table, str(path))


def test_read_parquet_file_from_path(tmp_path, store_ctx):
    p = tmp_path / "data.parquet"
    _write_test_parquet(p)
    result = read_parquet_file(input=None, path=str(p))
    assert result["row_count"] == 2
    assert result["format"] == "parquet"
    assert "artifact" in result


def test_read_parquet_file_column_filter(tmp_path, store_ctx):
    p = tmp_path / "data.parquet"
    _write_test_parquet(p)
    result = read_parquet_file(input=None, path=str(p), columns="name")
    # Schema should only have "name" column
    assert all(col["name"] == "name" for col in result["schema"])


def test_read_parquet_file_row_limit(tmp_path, store_ctx):
    p = tmp_path / "data.parquet"
    _write_test_parquet(p)
    result = read_parquet_file(input=None, path=str(p), limit=1)
    assert result["row_count"] == 1


def test_read_parquet_file_no_source():
    with pytest.raises(ValueError, match="either path or file"):
        read_parquet_file(input=None, path="", file="")


def test_read_parquet_file_from_upload(store_ctx, tmp_path):
    p = tmp_path / "up.parquet"
    _write_test_parquet(p)
    raw = p.read_bytes()
    with patch("noodle_nodes.file_nodes._read_upload_bytes", return_value=(raw, "up.parquet")):
        result = read_parquet_file(input=None, file="abc123")
    assert result["row_count"] == 2


def test_write_parquet_from_list(store_ctx):
    data = [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]
    result = write_parquet_file(input=data)
    assert result["row_count"] == 2
    assert result["format"] == "parquet"


def test_write_parquet_from_dataset_ref(tmp_path, store_ctx):
    p = tmp_path / "src.parquet"
    _write_test_parquet(p)
    # Read as DatasetRef first
    ref = read_parquet_file(input=None, path=str(p))
    result = write_parquet_file(input=ref)
    assert result["row_count"] == 2


def test_write_parquet_rejects_empty_input(store_ctx):
    with pytest.raises(ValueError, match="write_parquet_file"):
        write_parquet_file(input=None)
```

- [ ] **Step 2: Run to verify tests fail**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py::test_read_parquet_file_from_path -v
```
Expected: `ImportError` or `AttributeError` — `read_parquet_file` not yet defined.

- [ ] **Step 3: Implement read_parquet_file and write_parquet_file in file_nodes.py**

Append to the end of `packages/nodes/noodle_nodes/file_nodes.py` (before any existing `__all__`):

```python
# ---------------------------------------------------------------------------
# Read Parquet
# ---------------------------------------------------------------------------


@node(
    name="Read Parquet File",
    id="read_parquet_file",
    category="Files",
    description=(
        "Read a Parquet file from a server path or browser-uploaded artifact "
        "and return a DatasetRef (Parquet-backed) for use in downstream data nodes. "
        "Optionally filter columns or limit the number of rows."
    ),
    icon="table",
    requirements=("duckdb", "pyarrow"),
    params={
        "file": {
            "widget": "file_upload",
            "display_name": "Upload file",
            "description": "Upload a .parquet file from your browser. Saved and reused across runs.",
        },
        "path": {
            "advanced": True,
            "display_name": "Server path",
            "description": (
                "Absolute path on the server filesystem, e.g. /app/data/dataset.parquet. "
                "See deploy/docker-compose.yml for volume mount setup."
            ),
        },
        "columns": {
            "group": "Options",
            "display_name": "Column filter",
            "description": "Comma-separated list of column names to keep. Leave blank to keep all columns.",
            "placeholder": "id, name, score",
        },
        "limit": {
            "group": "Options",
            "type": "number",
            "display_name": "Row limit",
            "description": "Maximum number of rows to read. Leave blank or 0 for all rows.",
            "placeholder": "0",
        },
    },
)
def read_parquet_file(
    input: Any = None,
    path: str = "",
    file: str = "",
    columns: str = "",
    limit: int = 0,
) -> Any:
    import os
    import tempfile

    import duckdb as _duckdb
    from noodle.datasets import reserve_artifact_path
    from noodle_nodes.datasets import _finalize_parquet

    if file:
        content_bytes, filename = _read_upload_bytes(file)
        tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
        try:
            tmp.write(content_bytes)
            tmp.close()
            src_path = tmp.name
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
    elif path:
        src_path = path
        tmp = None
    else:
        raise ValueError(
            "read_parquet_file: either path or file (artifact_id) must be provided"
        )

    try:
        src_sql = src_path.replace("'", "''")
        col_list = [c.strip() for c in columns.split(",") if c.strip()] if columns else []
        select = ", ".join(f'"{c}"' for c in col_list) if col_list else "*"
        row_limit = max(0, int(limit or 0))
        limit_clause = f" LIMIT {row_limit}" if row_limit > 0 else ""

        out_path, out_partial = reserve_artifact_path(
            "dataset.parquet",
            content_type="application/vnd.apache.parquet",
            kind="dataset",
        )
        out_sql = str(out_path).replace("'", "''")
        con = _duckdb.connect(":memory:")
        con.execute(
            f"COPY (SELECT {select} FROM read_parquet('{src_sql}'){limit_clause}) "
            f"TO '{out_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        return _finalize_parquet(out_path, out_partial)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Write Parquet
# ---------------------------------------------------------------------------


def _input_to_parquet_source(
    data: Any,
    *,
    con: Any,
    src_path: "pathlib.Path | None" = None,
) -> str:
    """Register *data* in *con* and return a SQL FROM clause string."""
    import pyarrow as _pa

    if data is None:
        raise ValueError(
            "write_parquet_file: input is required — wire a list, DatasetRef, or dict with 'rows'"
        )
    if isinstance(data, dict):
        # DatasetRef
        artifact = data.get("artifact") or {}
        pq_path = artifact.get("path") or data.get("path", "")
        if pq_path:
            safe = str(pq_path).replace("'", "''")
            return f"read_parquet('{safe}')"
        # dict with rows/records
        rows = data.get("rows") or data.get("records") or data.get("items")
        if isinstance(rows, list):
            data = rows
        else:
            raise ValueError(
                "write_parquet_file: dict input must be a DatasetRef or contain 'rows'/'records' key"
            )
    if isinstance(data, list):
        if not data:
            raise ValueError("write_parquet_file: input list is empty")
        table = _pa.Table.from_pylist(data)
        con.register("_write_src", table)
        return "_write_src"
    raise ValueError(
        f"write_parquet_file: unsupported input type {type(data).__name__}. "
        "Expected list of dicts or DatasetRef."
    )


@node(
    name="Write Parquet File",
    id="write_parquet_file",
    category="Files",
    description=(
        "Write a DatasetRef, list of dicts, or tabular data to a Parquet file artifact. "
        "Returns a DatasetRef pointing to the written file for use in downstream nodes."
    ),
    icon="table",
    requirements=("duckdb", "pyarrow"),
    params={
        "compression": {
            "group": "Options",
            "choices": ["zstd", "snappy", "gzip", "none"],
            "description": "Parquet compression codec. ZSTD (default) gives the best size/speed ratio.",
            "default": "zstd",
        },
        "filename": {
            "group": "Options",
            "display_name": "Output filename",
            "description": "Name for the output artifact file.",
            "placeholder": "output.parquet",
        },
    },
)
def write_parquet_file(
    input: Any = None,
    compression: str = "zstd",
    filename: str = "",
) -> Any:
    import duckdb as _duckdb
    from noodle.datasets import reserve_artifact_path
    from noodle_nodes.datasets import _finalize_parquet

    codec = (compression or "zstd").upper()
    if codec not in {"ZSTD", "SNAPPY", "GZIP", "NONE"}:
        codec = "ZSTD"
    out_name = filename.strip() or "output.parquet"

    con = _duckdb.connect(":memory:")
    src_expr = _input_to_parquet_source(input, con=con)

    out_path, out_partial = reserve_artifact_path(
        out_name,
        content_type="application/vnd.apache.parquet",
        kind="dataset",
    )
    out_sql = str(out_path).replace("'", "''")
    con.execute(
        f"COPY (SELECT * FROM {src_expr}) TO '{out_sql}' "
        f"(FORMAT PARQUET, COMPRESSION {codec})"
    )
    return _finalize_parquet(out_path, out_partial)
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py -k "parquet" -v
```
Expected: all 8 parquet tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/file_nodes.py packages/nodes/tests/test_file_nodes.py
git commit -m "feat(nodes): add standalone read_parquet_file and write_parquet_file nodes"
```

---

## Task 2: Standalone Read/Write Excel Nodes

**Files:**
- Modify: `packages/nodes/noodle_nodes/file_nodes.py`
- Test: `packages/nodes/tests/test_file_nodes.py`

**Interfaces:**
- Consumes: `_read_upload_bytes`, `records_to_dataset` from `noodle_nodes.datasets`, `reserve_artifact_path`
- Produces: `read_excel_file(input, path, file, sheet, has_header, limit)` → DatasetRef or dict; `write_excel_file(input, sheet, filename)` → dict with artifact metadata

- [ ] **Step 1: Write failing tests**

Add to `packages/nodes/tests/test_file_nodes.py`:

```python
import openpyxl
from noodle_nodes.file_nodes import read_excel_file, write_excel_file


def _write_test_xlsx(path: pathlib.Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["name", "score"])
    ws.append(["Alice", 95])
    ws.append(["Bob", 87])
    wb.save(str(path))


def test_read_excel_file_from_path(tmp_path, store_ctx):
    p = tmp_path / "data.xlsx"
    _write_test_xlsx(p)
    result = read_excel_file(input=None, path=str(p))
    assert result["row_count"] == 2
    assert result["format"] == "parquet"


def test_read_excel_file_limit(tmp_path, store_ctx):
    p = tmp_path / "data.xlsx"
    _write_test_xlsx(p)
    result = read_excel_file(input=None, path=str(p), limit=1)
    assert result["row_count"] == 1


def test_read_excel_file_no_header(tmp_path, store_ctx):
    p = tmp_path / "nohead.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Alice", 95])
    ws.append(["Bob", 87])
    wb.save(str(p))
    result = read_excel_file(input=None, path=str(p), has_header=False)
    assert result["row_count"] == 2


def test_read_excel_file_invalid_sheet(tmp_path, store_ctx):
    p = tmp_path / "data.xlsx"
    _write_test_xlsx(p)
    with pytest.raises(ValueError, match="sheet"):
        read_excel_file(input=None, path=str(p), sheet="NoSuchSheet")


def test_read_excel_file_no_source():
    with pytest.raises(ValueError, match="either path or file"):
        read_excel_file(input=None, path="", file="")


def test_read_excel_file_from_upload(store_ctx, tmp_path):
    p = tmp_path / "up.xlsx"
    _write_test_xlsx(p)
    raw = p.read_bytes()
    with patch("noodle_nodes.file_nodes._read_upload_bytes", return_value=(raw, "up.xlsx")):
        result = read_excel_file(input=None, file="abc123")
    assert result["row_count"] == 2


def test_write_excel_from_list(store_ctx, tmp_path):
    data = [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]
    result = write_excel_file(input=data)
    assert result["filename"].endswith(".xlsx")
    assert result["row_count"] == 2


def test_write_excel_from_dataset_ref(tmp_path, store_ctx):
    p = tmp_path / "src.parquet"
    _write_test_parquet(p)
    ref = read_parquet_file(input=None, path=str(p))
    result = write_excel_file(input=ref)
    assert result["row_count"] == 2


def test_write_excel_rejects_none_input(store_ctx):
    with pytest.raises(ValueError, match="write_excel_file"):
        write_excel_file(input=None)
```

- [ ] **Step 2: Run to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py -k "excel" -v
```
Expected: `ImportError` or `AttributeError` — functions not yet defined.

- [ ] **Step 3: Implement read_excel_file and write_excel_file**

Append after the Write Parquet node in `file_nodes.py`:

```python
# ---------------------------------------------------------------------------
# Read Excel
# ---------------------------------------------------------------------------


@node(
    name="Read Excel File",
    id="read_excel_file",
    category="Files",
    description=(
        "Read an Excel (.xlsx / .xls) file from a server path or browser-uploaded artifact "
        "and return a DatasetRef. Supports sheet selection, header row control, and row limits."
    ),
    icon="table",
    requirements=("openpyxl",),
    params={
        "file": {
            "widget": "file_upload",
            "display_name": "Upload file",
            "description": "Upload an .xlsx or .xls file from your browser.",
        },
        "path": {
            "advanced": True,
            "display_name": "Server path",
            "description": "Absolute path on the server filesystem, e.g. /app/data/report.xlsx.",
        },
        "sheet": {
            "group": "Options",
            "display_name": "Sheet name",
            "description": "Name of the sheet to read. Defaults to the first sheet.",
            "placeholder": "Sheet1",
        },
        "has_header": {
            "group": "Options",
            "type": "boolean",
            "description": "Treat the first row as column headers. Disable for headerless data.",
            "default": True,
        },
        "limit": {
            "group": "Options",
            "type": "number",
            "display_name": "Row limit",
            "description": "Maximum number of data rows to read. 0 means all rows.",
            "placeholder": "0",
        },
    },
)
def read_excel_file(
    input: Any = None,
    path: str = "",
    file: str = "",
    sheet: str = "",
    has_header: bool = True,
    limit: int = 0,
) -> Any:
    import io as _io

    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "read_excel_file requires the 'openpyxl' package. "
            "Install with: uv pip install openpyxl"
        ) from exc
    from noodle_nodes.datasets import records_to_dataset

    if file:
        content_bytes, _filename = _read_upload_bytes(file)
        raw = _io.BytesIO(content_bytes)
    elif path:
        raw = pathlib.Path(path).open("rb")
    else:
        raise ValueError(
            "read_excel_file: either path or file (artifact_id) must be provided"
        )

    try:
        wb = openpyxl.load_workbook(raw, read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(f"read_excel_file: could not open file — {exc}") from exc

    sheet_names = wb.sheetnames
    if sheet:
        if sheet not in sheet_names:
            raise ValueError(
                f"read_excel_file: sheet {sheet!r} not found. "
                f"Available sheets: {sheet_names}"
            )
        ws = wb[sheet]
    else:
        ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    all_rows = list(rows_iter)

    if has_header:
        if not all_rows:
            return records_to_dataset([])
        headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(all_rows[0])]
        data_rows = all_rows[1:]
    else:
        data_rows = all_rows
        headers = [f"col_{i}" for i in range(len(data_rows[0]) if data_rows else 0)]

    row_limit = max(0, int(limit or 0))
    if row_limit > 0:
        data_rows = data_rows[:row_limit]

    records = [
        {headers[i]: (cell if cell is not None else "") for i, cell in enumerate(row)}
        for row in data_rows
    ]
    return records_to_dataset(records)


# ---------------------------------------------------------------------------
# Write Excel
# ---------------------------------------------------------------------------


@node(
    name="Write Excel File",
    id="write_excel_file",
    category="Files",
    description=(
        "Write a DatasetRef or list of dicts to an Excel (.xlsx) file artifact. "
        "Returns artifact metadata including the downloadable file reference."
    ),
    icon="table",
    requirements=("openpyxl", "duckdb"),
    params={
        "sheet": {
            "group": "Options",
            "display_name": "Sheet name",
            "description": "Name of the worksheet in the output file.",
            "placeholder": "Sheet1",
            "default": "Sheet1",
        },
        "filename": {
            "group": "Options",
            "display_name": "Output filename",
            "description": "Name for the output .xlsx artifact.",
            "placeholder": "output.xlsx",
        },
    },
)
def write_excel_file(
    input: Any = None,
    sheet: str = "Sheet1",
    filename: str = "",
) -> dict[str, Any]:
    import io as _io

    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "write_excel_file requires the 'openpyxl' package. "
            "Install with: uv pip install openpyxl"
        ) from exc

    if input is None:
        raise ValueError(
            "write_excel_file: input is required — wire a list of dicts or a DatasetRef"
        )

    # Resolve input to a list of dicts
    if isinstance(input, dict):
        artifact = input.get("artifact") or {}
        pq_path = artifact.get("path") or input.get("path", "")
        if pq_path:
            import duckdb as _duckdb  # noqa: PLC0415
            con = _duckdb.connect(":memory:")
            safe = str(pq_path).replace("'", "''")
            rel = con.execute(f"SELECT * FROM read_parquet('{safe}')")
            cols = [desc[0] for desc in rel.description]
            records = [dict(zip(cols, row, strict=False)) for row in rel.fetchall()]
        else:
            rows_val = input.get("rows") or input.get("records") or input.get("items")
            if not isinstance(rows_val, list):
                raise ValueError(
                    "write_excel_file: dict input must be a DatasetRef or contain 'rows'/'records' key"
                )
            records = rows_val
    elif isinstance(input, list):
        records = input
    else:
        raise ValueError(
            f"write_excel_file: unsupported input type {type(input).__name__}. "
            "Expected list of dicts or DatasetRef."
        )

    if not records:
        raise ValueError("write_excel_file: input is empty — nothing to write")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet or "Sheet1"

    headers = list(records[0].keys())
    ws.append(headers)
    for row in records:
        ws.append([row.get(h) for h in headers])

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    xlsx_bytes = buf.read()

    out_name = filename.strip() or "output.xlsx"

    from noodle.datasets import reserve_artifact_path
    from noodle.artifacts import finalize_artifact_ref

    out_path, out_partial = reserve_artifact_path(
        out_name,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        kind="file",
    )
    out_path.write_bytes(xlsx_bytes)
    artifact = finalize_artifact_ref(out_path, out_partial, metadata={"format": "xlsx"})

    return {
        "filename": out_name,
        "row_count": len(records),
        "column_count": len(headers),
        "columns": headers,
        "artifact": artifact,
        "size_bytes": len(xlsx_bytes),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd packages/nodes && python -m pytest tests/test_file_nodes.py -k "excel" -v
```
Expected: all 8 excel tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/file_nodes.py packages/nodes/tests/test_file_nodes.py
git commit -m "feat(nodes): add standalone read_excel_file and write_excel_file nodes"
```

---

## Task 3: GitLab Pipeline Trigger + Docker Security Hardening

**Files:**
- Modify: `packages/nodes/noodle_nodes/integrations_v2/providers/gitlab/operations.py`
- Modify: `packages/nodes/noodle_nodes/docker_nodes.py`
- Create: `packages/nodes/tests/test_gitlab_v2.py`
- Modify: `packages/nodes/tests/test_cloud_devops.py`

**Interfaces:**
- Produces: `gitlab_trigger_pipeline_v2` node — calls `POST /api/v4/projects/{id}/pipeline` with `ref` + `variables`; returns `{"id", "status", "web_url", "ref"}`
- Produces: `docker_run_container` description hardened with security note

- [ ] **Step 1: Write failing tests for GitLab pipeline trigger**

Create `packages/nodes/tests/test_gitlab_v2.py`:

```python
"""Tests for GitLab v2 integration nodes."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import noodle_nodes  # noqa: F401 — registers provider nodes
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.gitlab import operations


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def test_gitlab_v2_pipeline_trigger_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "gitlab_trigger_pipeline_v2" in manifests
    m = manifests["gitlab_trigger_pipeline_v2"]
    assert m.name == "GitLab Trigger Pipeline"
    assert m.icon == "brand:gitlab"
    assert m.tool_side_effecting is True


def test_gitlab_trigger_pipeline_executor() -> None:
    mock_t = _mock_transport(
        {"id": 42, "status": "pending", "web_url": "https://gitlab.com/owner/repo/-/pipelines/42", "ref": "main"}
    )
    with patch.object(operations, "_transport", return_value=mock_t):
        result = operations.trigger_pipeline(
            credentials={"access_token": "tok", "server_url": "https://gitlab.com"},
            project_id="owner/repo",
            ref="main",
            variables={"DEPLOY_ENV": "staging"},
        )
    assert result["id"] == 42
    assert result["status"] == "pending"
    mock_t.request.assert_called_once()
    call_kwargs = mock_t.request.call_args
    assert call_kwargs[0][0] == "POST"
    assert "pipeline" in call_kwargs[0][1]


def test_gitlab_trigger_pipeline_requires_project_id() -> None:
    with pytest.raises(ValueError, match="project_id"):
        operations.trigger_pipeline(
            credentials={"access_token": "tok"},
            project_id="",
            ref="main",
        )


def test_gitlab_trigger_pipeline_requires_ref() -> None:
    with pytest.raises(ValueError, match="ref"):
        operations.trigger_pipeline(
            credentials={"access_token": "tok"},
            project_id="owner/repo",
            ref="",
        )


def test_gitlab_v2_list_projects_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "gitlab_list_projects_v2" in manifests
```

- [ ] **Step 2: Write failing Docker run/stop tests**

Append to `packages/nodes/tests/test_cloud_devops.py`:

```python
from types import SimpleNamespace


class _FakeContainer:
    def __init__(self, exit_code=0):
        self.id = "abc123def456"
        self.name = "noodle-test"
        self.status = "running"
        self._exit_code = exit_code

    def wait(self, timeout=60):
        return {"StatusCode": self._exit_code}

    def logs(self, stdout=True, stderr=True):
        return b"hello from container\n"

    def remove(self):
        self.status = "removed"

    def reload(self):
        self.status = "exited"


def _fake_docker(container: _FakeContainer):
    containers = MagicMock()
    containers.run.return_value = container
    containers.get.return_value = container
    client = MagicMock()
    client.containers = containers
    module = MagicMock()
    module.from_env.return_value = client
    return module


def test_docker_run_container_success() -> None:
    container = _FakeContainer(exit_code=0)
    fake_docker = _fake_docker(container)

    with patch("noodle_nodes.docker_nodes._docker", return_value=fake_docker):
        result = cloud_devops.docker_list_containers.__module__  # noqa — ensure import
        from noodle_nodes import docker_nodes
        result = docker_nodes.docker_run_container(image="python:3.12-slim", command="echo hi")

    assert result["exit_code"] == 0
    assert "hello from container" in result["logs"]
    assert result["container_id"] == "abc123def456"


def test_docker_run_container_requires_image() -> None:
    from noodle_nodes import docker_nodes
    with pytest.raises(ValueError, match="image"):
        docker_nodes.docker_run_container(image="")


def test_docker_run_container_detached_mode() -> None:
    container = _FakeContainer()
    fake_docker = _fake_docker(container)
    from noodle_nodes import docker_nodes
    with patch("noodle_nodes.docker_nodes._docker", return_value=fake_docker):
        result = docker_nodes.docker_run_container(
            image="python:3.12-slim", command="sleep 10", detach=True
        )
    assert result["container_id"] == "abc123def456"
    assert "exit_code" not in result


def test_docker_stop_container_requires_id() -> None:
    from noodle_nodes import docker_nodes
    with pytest.raises(ValueError, match="container_id"):
        docker_nodes.docker_stop_container(container_id="")
```

- [ ] **Step 3: Run to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_gitlab_v2.py::test_gitlab_v2_pipeline_trigger_registered tests/test_cloud_devops.py::test_docker_run_container_success -v
```
Expected: `KeyError` for pipeline trigger (not registered), tests may error for docker (import issues).

- [ ] **Step 4: Add gitlab_trigger_pipeline_v2 to gitlab/operations.py**

Open `packages/nodes/noodle_nodes/integrations_v2/providers/gitlab/operations.py`.

After the `GITLAB_GET_FILE_SPEC` block and before the executor functions section, add:

```python
GITLAB_TRIGGER_PIPELINE_SPEC = OperationSpec(
    node_id="gitlab_trigger_pipeline_v2",
    name="GitLab Trigger Pipeline",
    provider="gitlab",
    resource="pipeline",
    operation="trigger",
    description=(
        "Trigger a new GitLab CI/CD pipeline on a given branch or tag. "
        "Returns the pipeline ID, status, and a link to view it in GitLab."
    ),
    icon="brand:gitlab",
    tool_side_effecting=True,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="project_id",
            required=True,
            display_name="Project ID or path",
            placeholder="owner/repo or 12345",
            description=(
                "Numeric project ID or URL-encoded path (e.g. 'mygroup/myproject'). "
                "Find this in your project's GitLab settings page."
            ),
        ),
        OperationParamSpec(
            name="ref",
            required=True,
            placeholder="main",
            description="Branch name, tag, or commit SHA to run the pipeline on.",
        ),
        OperationParamSpec(
            name="variables",
            type="object",
            group="Options",
            description=(
                "Pipeline variables as a JSON object, e.g. {\"DEPLOY_ENV\": \"staging\"}. "
                "These are available as CI/CD variables inside the pipeline."
            ),
        ),
    ),
)
```

Then add the executor function before the `register_operation(...)` calls:

```python
def trigger_pipeline(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    project_id: str = "",
    ref: str = "",
    variables: dict[str, Any] | None = None,
) -> Any:
    if not project_id:
        raise ValueError("gitlab_trigger_pipeline_v2: project_id is required")
    if not ref:
        raise ValueError("gitlab_trigger_pipeline_v2: ref is required")

    from urllib.parse import quote as _quote  # noqa: PLC0415 — already imported at top

    encoded_id = _quote(str(project_id), safe="")
    payload: dict[str, Any] = {"ref": ref}
    if variables and isinstance(variables, dict):
        payload["variables"] = [
            {"key": str(k), "value": str(v), "variable_type": "env_var"}
            for k, v in variables.items()
        ]
    return _transport(credentials).request(
        "POST",
        f"/api/v4/projects/{encoded_id}/pipeline",
        operation="trigger_pipeline",
        json_body=payload,
    )
```

Then register it — add to the existing `register_operation(...)` block:

```python
register_operation(GITLAB_TRIGGER_PIPELINE_SPEC, trigger_pipeline, node_registry=None)
```

And add it to the `GITLAB_INTEGRATION` ResourceSpec list by adding a new `ResourceSpec`:

```python
ResourceSpec(
    id="pipeline",
    name="Pipeline",
    operations=(GITLAB_TRIGGER_PIPELINE_SPEC,),
),
```

- [ ] **Step 5: Harden docker_run_container description**

In `packages/nodes/noodle_nodes/docker_nodes.py`, update the `docker_run_container` `@node` decorator description:

```python
@node(
    name="Docker Run Container",
    id="docker_run_container",
    category="DevOps",
    icon="brand:docker",
    description=(
        "Run a Docker container and capture its output. "
        "WARNING: This node requires Docker access on the host. "
        "It should be disabled or restricted in multi-tenant deployments. "
        "Never use privileged mode or bind-mount sensitive host paths."
    ),
    params={
        "image": {
            "placeholder": "python:3.12-slim",
            "description": "Container image to run. Must be pre-pulled or accessible from the registry.",
        },
        "command": {
            "placeholder": "python -c 'print(\"hello\")'",
            "description": "Command to run inside the container.",
        },
        "detach": {
            "description": "Run in background (detached mode). Returns container ID immediately without waiting.",
        },
        "env": {
            "key_value": True,
            "description": "Environment variables to inject into the container.",
        },
        "timeout": {
            "placeholder": "60",
            "description": "Maximum seconds to wait for the container to finish (attached mode only).",
        },
    },
)
```

- [ ] **Step 6: Run all GitLab and Docker tests**

```
cd packages/nodes && python -m pytest tests/test_gitlab_v2.py tests/test_cloud_devops.py -v
```
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/nodes/noodle_nodes/integrations_v2/providers/gitlab/operations.py \
        packages/nodes/noodle_nodes/docker_nodes.py \
        packages/nodes/tests/test_gitlab_v2.py \
        packages/nodes/tests/test_cloud_devops.py
git commit -m "feat(nodes): add GitLab pipeline trigger, harden docker_run_container description, add tests"
```

---

## Task 4: Data Platform Nodes (Snowflake, BigQuery, dbt Cloud, MLflow)

**Files:**
- Create: `packages/nodes/noodle_nodes/data_platform_nodes.py`
- Create: `packages/nodes/tests/test_data_platform_nodes.py`
- Modify: `packages/nodes/noodle_nodes/__init__.py`

**Interfaces:**
- Produces:
  - `snowflake_query(input, credentials, query, database, schema, warehouse, limit)` → DatasetRef or list
  - `bigquery_query(input, credentials, query, project, limit)` → DatasetRef or list
  - `dbt_cloud_trigger_job(input, credentials, account_id, job_id, cause, git_sha, steps_override)` → dict with run_id, status, href
  - `mlflow_log_metric(input, credentials, metric_name, metric_value, run_id, step)` → dict
  - `mlflow_log_artifact(input, credentials, local_path, artifact_path, run_id)` → dict

- [ ] **Step 1: Write failing tests**

Create `packages/nodes/tests/test_data_platform_nodes.py`:

```python
"""Tests for data platform nodes: Snowflake, BigQuery, dbt Cloud, MLflow."""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import noodle_nodes  # noqa: F401


# ---------------------------------------------------------------------------
# Snowflake
# ---------------------------------------------------------------------------

def _fake_snowflake_module(rows, col_names):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.description = [SimpleNamespace(name=c) for c in col_names]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)

    module = MagicMock()
    module.connector.connect.return_value = conn
    return module


def test_snowflake_query_returns_dataset(tmp_path):
    from noodle.artifacts import LocalArtifactStore
    from noodle.context import artifact_store, current_node_id
    store = LocalArtifactStore(tmp_path, run_id="r1")
    tok_a = artifact_store.set(store)
    tok_n = current_node_id.set("n1")
    try:
        fake_sf = _fake_snowflake_module(
            rows=[("Alice", 30), ("Bob", 25)],
            col_names=["name", "age"],
        )
        with patch.dict(sys.modules, {"snowflake": fake_sf, "snowflake.connector": fake_sf.connector}):
            from noodle_nodes import data_platform_nodes
            result = data_platform_nodes.snowflake_query(
                credentials={"account": "myaccount", "user": "myuser", "password": "secret"},
                query="SELECT name, age FROM users LIMIT 2",
            )
        assert result["row_count"] == 2
    finally:
        artifact_store.reset(tok_a)
        current_node_id.reset(tok_n)


def test_snowflake_query_requires_credentials():
    from noodle_nodes import data_platform_nodes
    with pytest.raises(ValueError, match="account"):
        data_platform_nodes.snowflake_query(credentials={}, query="SELECT 1")


def test_snowflake_query_requires_query():
    from noodle_nodes import data_platform_nodes
    with pytest.raises(ValueError, match="query"):
        data_platform_nodes.snowflake_query(
            credentials={"account": "x", "user": "u", "password": "p"},
            query="",
        )


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------

def _fake_bigquery_module(rows, field_names):
    bq_row = [SimpleNamespace(**{f: r[i] for i, f in enumerate(field_names)}) for r in rows]
    job = MagicMock()
    job.result.return_value = bq_row
    job.schema = [SimpleNamespace(name=f) for f in field_names]

    client = MagicMock()
    client.query.return_value = job

    module = MagicMock()
    module.Client.return_value = client
    return module


def test_bigquery_query_returns_dataset(tmp_path):
    from noodle.artifacts import LocalArtifactStore
    from noodle.context import artifact_store, current_node_id
    store = LocalArtifactStore(tmp_path, run_id="r2")
    tok_a = artifact_store.set(store)
    tok_n = current_node_id.set("n2")
    try:
        fake_bq = _fake_bigquery_module(
            rows=[("Alice", 30), ("Bob", 25)],
            field_names=["name", "age"],
        )
        fake_credentials = MagicMock()
        fake_service_account = MagicMock()
        fake_service_account.Credentials.from_service_account_info.return_value = fake_credentials

        with patch.dict(sys.modules, {
            "google.cloud.bigquery": fake_bq,
            "google.oauth2.service_account": fake_service_account,
        }):
            from noodle_nodes import data_platform_nodes
            result = data_platform_nodes.bigquery_query(
                credentials={"service_account_json": json.dumps({"type": "service_account"})},
                query="SELECT name, age FROM dataset.users LIMIT 2",
                project="my-project",
            )
        assert result["row_count"] == 2
    finally:
        artifact_store.reset(tok_a)
        current_node_id.reset(tok_n)


def test_bigquery_query_requires_query():
    from noodle_nodes import data_platform_nodes
    with pytest.raises(ValueError, match="query"):
        data_platform_nodes.bigquery_query(credentials={}, query="", project="proj")


# ---------------------------------------------------------------------------
# dbt Cloud
# ---------------------------------------------------------------------------

def test_dbt_cloud_trigger_job_success():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "data": {
            "id": 999,
            "status": 1,
            "status_humanized": "Queued",
            "href": "https://cloud.getdbt.com/runs/999",
        }
    }

    with patch("noodle_nodes.data_platform_nodes._dbt_request", return_value=fake_resp):
        from noodle_nodes import data_platform_nodes
        result = data_platform_nodes.dbt_cloud_trigger_job(
            credentials={"api_key": "token123", "account_id": "12345"},
            job_id="678",
            cause="Noodle workflow triggered",
        )
    assert result["run_id"] == 999
    assert result["status"] == "Queued"


def test_dbt_cloud_requires_job_id():
    from noodle_nodes import data_platform_nodes
    with pytest.raises(ValueError, match="job_id"):
        data_platform_nodes.dbt_cloud_trigger_job(
            credentials={"api_key": "tok", "account_id": "12"},
            job_id="",
        )


def test_dbt_cloud_requires_api_key():
    from noodle_nodes import data_platform_nodes
    with pytest.raises(ValueError, match="api_key"):
        data_platform_nodes.dbt_cloud_trigger_job(
            credentials={},
            job_id="678",
        )


# ---------------------------------------------------------------------------
# MLflow
# ---------------------------------------------------------------------------

def test_mlflow_log_metric_success():
    fake_client = MagicMock()
    fake_mlflow = MagicMock()
    fake_mlflow.MlflowClient.return_value = fake_client
    fake_mlflow.set_tracking_uri = MagicMock()

    with patch.dict(sys.modules, {"mlflow": fake_mlflow}):
        from noodle_nodes import data_platform_nodes
        result = data_platform_nodes.mlflow_log_metric(
            credentials={"tracking_uri": "http://mlflow.example.com", "token": "tok"},
            run_id="abc123",
            metric_name="accuracy",
            metric_value=0.95,
        )
    assert result["metric_name"] == "accuracy"
    assert result["metric_value"] == 0.95


def test_mlflow_log_metric_requires_run_id():
    from noodle_nodes import data_platform_nodes
    with pytest.raises(ValueError, match="run_id"):
        data_platform_nodes.mlflow_log_metric(
            credentials={"tracking_uri": "http://x"},
            run_id="",
            metric_name="acc",
            metric_value=1.0,
        )


def test_mlflow_log_metric_requires_metric_name():
    from noodle_nodes import data_platform_nodes
    with pytest.raises(ValueError, match="metric_name"):
        data_platform_nodes.mlflow_log_metric(
            credentials={"tracking_uri": "http://x"},
            run_id="r1",
            metric_name="",
            metric_value=1.0,
        )


def test_mlflow_log_artifact_success():
    fake_client = MagicMock()
    fake_mlflow = MagicMock()
    fake_mlflow.MlflowClient.return_value = fake_client
    fake_mlflow.set_tracking_uri = MagicMock()

    with patch.dict(sys.modules, {"mlflow": fake_mlflow}):
        from noodle_nodes import data_platform_nodes
        result = data_platform_nodes.mlflow_log_artifact(
            credentials={"tracking_uri": "http://mlflow.example.com"},
            run_id="abc123",
            local_path="/tmp/model.pkl",
        )
    assert result["run_id"] == "abc123"
    fake_client.log_artifact.assert_called_once()
```

- [ ] **Step 2: Run to verify they fail**

```
cd packages/nodes && python -m pytest tests/test_data_platform_nodes.py -v
```
Expected: `ImportError` — `data_platform_nodes` module doesn't exist yet.

- [ ] **Step 3: Create data_platform_nodes.py**

Create `packages/nodes/noodle_nodes/data_platform_nodes.py`:

```python
"""Data platform nodes — Snowflake, BigQuery, dbt Cloud, MLflow.

All heavy SDK imports are lazy (inside function bodies) so the base node
library still registers without these SDKs installed. Users must add the
appropriate package to their workflow environment.
"""

from __future__ import annotations

import json as json_mod
from typing import Any

from noodle.sdk import node
from noodle_nodes._creds import cred_multi

_DATA_CATEGORY = "Data"
_DEVOPS_CATEGORY = "DevOps"


# ===========================================================================
# Snowflake Query
# ===========================================================================

@node(
    id="snowflake_query",
    name="Snowflake Query",
    category=_DATA_CATEGORY,
    description=(
        "Run a SQL query against a Snowflake data warehouse and return results "
        "as a DatasetRef. Requires the 'snowflake-connector-python' package."
    ),
    icon="brand:snowflake",
    requirements=["snowflake-connector-python"],
    params={
        "credentials": {
            **cred_multi(
                "snowflake",
                "Snowflake credentials",
                ["account", "user", "password", "warehouse", "database", "schema"],
            ),
            "description": "Snowflake account identifier, user, password, and optional warehouse/database/schema.",
        },
        "query": {
            "multiline": True,
            "required": True,
            "placeholder": "SELECT * FROM my_table LIMIT 100",
            "description": "SQL query to execute. Use LIMIT to avoid returning huge result sets.",
        },
        "database": {
            "group": "Options",
            "description": "Override the default database from credentials.",
            "placeholder": "MY_DATABASE",
        },
        "schema": {
            "group": "Options",
            "description": "Override the default schema from credentials.",
            "placeholder": "PUBLIC",
        },
        "warehouse": {
            "group": "Options",
            "description": "Override the default warehouse from credentials.",
            "placeholder": "COMPUTE_WH",
        },
        "limit": {
            "group": "Options",
            "type": "number",
            "description": "Cap returned rows. 0 means no additional limit beyond your SQL.",
            "placeholder": "0",
        },
    },
)
def snowflake_query(
    input: Any = None,
    credentials: Any = None,
    query: str = "",
    database: str = "",
    schema: str = "",
    warehouse: str = "",
    limit: int = 0,
) -> Any:
    try:
        import snowflake.connector as sf_connector  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "snowflake_query requires 'snowflake-connector-python'. "
            "Install with: uv pip install snowflake-connector-python"
        ) from exc
    from noodle_nodes.datasets import records_to_dataset  # noqa: PLC0415

    creds = credentials if isinstance(credentials, dict) else {}
    account = str(creds.get("account") or "")
    user = str(creds.get("user") or "")
    password = str(creds.get("password") or "")

    if not account:
        raise ValueError("snowflake_query: 'account' is required in credentials")
    if not user:
        raise ValueError("snowflake_query: 'user' is required in credentials")
    if not query.strip():
        raise ValueError("snowflake_query: query is required")

    connect_kwargs: dict[str, Any] = {
        "account": account,
        "user": user,
        "password": password,
    }
    for field, override in [
        ("warehouse", warehouse or creds.get("warehouse", "")),
        ("database", database or creds.get("database", "")),
        ("schema", schema or creds.get("schema", "")),
    ]:
        if override:
            connect_kwargs[field] = override

    with sf_connector.connect(**connect_kwargs) as conn:
        cur = conn.cursor()
        cur.execute(query)
        col_names = [desc.name for desc in cur.description]
        rows = cur.fetchall()

    row_limit = max(0, int(limit or 0))
    if row_limit > 0:
        rows = rows[:row_limit]

    records = [dict(zip(col_names, row, strict=False)) for row in rows]
    return records_to_dataset(records)


# ===========================================================================
# BigQuery Query
# ===========================================================================

@node(
    id="bigquery_query",
    name="BigQuery Query",
    category=_DATA_CATEGORY,
    description=(
        "Run a SQL query against Google BigQuery and return results as a DatasetRef. "
        "Authenticate with a service account JSON key credential or Application Default Credentials. "
        "Requires the 'google-cloud-bigquery' package."
    ),
    icon="brand:googlecloud",
    requirements=["google-cloud-bigquery"],
    params={
        "credentials": {
            **cred_multi(
                "google_service_account",
                "Google Cloud service account",
                ["service_account_json", "project"],
            ),
            "description": (
                "Google Cloud service account JSON key (paste the full JSON string). "
                "Leave blank to use Application Default Credentials (ADC)."
            ),
        },
        "query": {
            "multiline": True,
            "required": True,
            "placeholder": "SELECT * FROM `project.dataset.table` LIMIT 100",
            "description": "BigQuery standard SQL query.",
        },
        "project": {
            "description": "Google Cloud project ID for billing. Required if not in credentials.",
            "placeholder": "my-gcp-project",
        },
        "limit": {
            "group": "Options",
            "type": "number",
            "description": "Cap returned rows after fetching. 0 means all rows returned by the query.",
            "placeholder": "0",
        },
    },
)
def bigquery_query(
    input: Any = None,
    credentials: Any = None,
    query: str = "",
    project: str = "",
    limit: int = 0,
) -> Any:
    try:
        from google.cloud import bigquery as bq  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "bigquery_query requires 'google-cloud-bigquery'. "
            "Install with: uv pip install google-cloud-bigquery"
        ) from exc
    from noodle_nodes.datasets import records_to_dataset  # noqa: PLC0415

    if not query.strip():
        raise ValueError("bigquery_query: query is required")

    creds = credentials if isinstance(credentials, dict) else {}
    sa_json_str = str(creds.get("service_account_json") or "")
    project_id = project or str(creds.get("project") or "")

    bq_credentials = None
    if sa_json_str:
        try:
            from google.oauth2 import service_account as sa_module  # noqa: PLC0415
            sa_info = json_mod.loads(sa_json_str)
            bq_credentials = sa_module.Credentials.from_service_account_info(
                sa_info,
                scopes=["https://www.googleapis.com/auth/bigquery.readonly"],
            )
        except Exception as exc:
            raise ValueError(
                f"bigquery_query: invalid service_account_json — {exc}"
            ) from exc

    client = bq.Client(project=project_id or None, credentials=bq_credentials)
    job = client.query(query)
    result = job.result()

    schema_fields = [field.name for field in job.schema] if job.schema else []
    rows_raw = list(result)

    row_limit = max(0, int(limit or 0))
    if row_limit > 0:
        rows_raw = rows_raw[:row_limit]

    records = [
        {field: getattr(row, field, None) for field in schema_fields}
        for row in rows_raw
    ]
    return records_to_dataset(records)


# ===========================================================================
# dbt Cloud Trigger Job — internal helper
# ===========================================================================

def _dbt_request(method: str, url: str, *, api_key: str, **kwargs: Any) -> Any:
    """Thin wrapper for dbt Cloud API calls using safe_request."""
    from noodle_nodes.http_security import safe_request  # noqa: PLC0415

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }
    return safe_request(method, url, headers=headers, timeout=30, **kwargs)


@node(
    id="dbt_cloud_trigger_job",
    name="dbt Cloud Trigger Job",
    category=_DEVOPS_CATEGORY,
    description=(
        "Trigger a dbt Cloud job run via the dbt Cloud API. "
        "Returns the job run ID, status, and a link to the run in dbt Cloud."
    ),
    icon="brand:dbt",
    params={
        "credentials": {
            **cred_multi(
                "dbt_cloud",
                "dbt Cloud credentials",
                ["api_key", "account_id"],
            ),
            "description": "dbt Cloud API key and Account ID. Find these in dbt Cloud → Account Settings.",
        },
        "job_id": {
            "required": True,
            "placeholder": "12345",
            "description": "The numeric ID of the dbt Cloud job to trigger.",
        },
        "cause": {
            "placeholder": "Triggered by Noodle workflow",
            "description": "Human-readable reason for this job run, visible in the dbt Cloud UI.",
        },
        "git_sha": {
            "group": "Options",
            "description": "Specific Git commit SHA to run. Overrides the job's configured branch.",
            "placeholder": "abc1234",
        },
        "steps_override": {
            "group": "Options",
            "type": "array",
            "description": (
                "Override the job's configured steps with this list of dbt commands, "
                "e.g. [\"dbt run\", \"dbt test\"]."
            ),
        },
        "base_url": {
            "group": "Options",
            "placeholder": "https://cloud.getdbt.com",
            "description": "Override the dbt Cloud base URL (for single-tenant deployments).",
        },
    },
)
def dbt_cloud_trigger_job(
    input: Any = None,
    credentials: Any = None,
    job_id: str = "",
    cause: str = "",
    git_sha: str = "",
    steps_override: list[str] | None = None,
    base_url: str = "",
) -> dict[str, Any]:
    creds = credentials if isinstance(credentials, dict) else {}
    api_key = str(creds.get("api_key") or "")
    account_id = str(creds.get("account_id") or "")

    if not api_key:
        raise ValueError("dbt_cloud_trigger_job: 'api_key' is required in credentials")
    if not account_id:
        raise ValueError("dbt_cloud_trigger_job: 'account_id' is required in credentials")
    if not job_id:
        raise ValueError("dbt_cloud_trigger_job: job_id is required")

    host = (base_url or "https://cloud.getdbt.com").rstrip("/")
    url = f"{host}/api/v2/accounts/{account_id}/jobs/{job_id}/run/"

    payload: dict[str, Any] = {"cause": cause or "Triggered by Noodle"}
    if git_sha:
        payload["git_sha"] = git_sha
    if steps_override:
        payload["steps_override"] = list(steps_override)

    resp = _dbt_request("POST", url, api_key=api_key, json=payload)

    if resp.status_code >= 400:
        raise RuntimeError(
            f"dbt_cloud_trigger_job: API error {resp.status_code} — {resp.text[:500]}"
        )

    data = resp.json().get("data", {})
    return {
        "run_id": data.get("id"),
        "status": data.get("status_humanized", str(data.get("status", ""))),
        "href": data.get("href", ""),
        "job_id": job_id,
        "account_id": account_id,
    }


# ===========================================================================
# MLflow Log Metric
# ===========================================================================

def _setup_mlflow(credentials: Any) -> Any:
    """Configure MLflow tracking URI and return an MlflowClient."""
    try:
        import mlflow  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "mlflow_log_metric / mlflow_log_artifact require the 'mlflow' package. "
            "Install with: uv pip install mlflow"
        ) from exc

    creds = credentials if isinstance(credentials, dict) else {}
    tracking_uri = str(creds.get("tracking_uri") or "")
    token = str(creds.get("token") or "")

    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    if token:
        import os  # noqa: PLC0415
        os.environ.setdefault("MLFLOW_TRACKING_TOKEN", token)

    return mlflow.MlflowClient()


@node(
    id="mlflow_log_metric",
    name="MLflow Log Metric",
    category=_DATA_CATEGORY,
    description=(
        "Log a numeric metric to an MLflow experiment run. "
        "Requires an active MLflow tracking server and the 'mlflow' package."
    ),
    icon="brand:mlflow",
    requirements=["mlflow"],
    params={
        "credentials": {
            **cred_multi(
                "mlflow",
                "MLflow tracking credentials",
                ["tracking_uri", "token"],
            ),
            "description": "MLflow tracking server URI and optional Bearer token.",
        },
        "run_id": {
            "required": True,
            "placeholder": "abc123def456",
            "description": "The MLflow run ID to log the metric against.",
        },
        "metric_name": {
            "required": True,
            "placeholder": "accuracy",
            "description": "Name of the metric to log (e.g. 'loss', 'accuracy', 'f1_score').",
        },
        "metric_value": {
            "required": True,
            "type": "number",
            "placeholder": "0.95",
            "description": "Numeric value of the metric.",
        },
        "step": {
            "group": "Options",
            "type": "number",
            "placeholder": "0",
            "description": "Training step or epoch number associated with this metric.",
        },
    },
)
def mlflow_log_metric(
    input: Any = None,
    credentials: Any = None,
    run_id: str = "",
    metric_name: str = "",
    metric_value: float = 0.0,
    step: int = 0,
) -> dict[str, Any]:
    if not run_id:
        raise ValueError("mlflow_log_metric: run_id is required")
    if not metric_name:
        raise ValueError("mlflow_log_metric: metric_name is required")

    client = _setup_mlflow(credentials)
    client.log_metric(
        run_id=run_id,
        key=metric_name,
        value=float(metric_value),
        step=max(0, int(step or 0)),
    )
    return {
        "run_id": run_id,
        "metric_name": metric_name,
        "metric_value": float(metric_value),
        "step": int(step or 0),
    }


# ===========================================================================
# MLflow Log Artifact
# ===========================================================================

@node(
    id="mlflow_log_artifact",
    name="MLflow Log Artifact",
    category=_DATA_CATEGORY,
    description=(
        "Upload a local file as an artifact to an MLflow experiment run. "
        "Requires the 'mlflow' package and a reachable MLflow tracking server."
    ),
    icon="brand:mlflow",
    requirements=["mlflow"],
    params={
        "credentials": {
            **cred_multi(
                "mlflow",
                "MLflow tracking credentials",
                ["tracking_uri", "token"],
            ),
            "description": "MLflow tracking server URI and optional Bearer token.",
        },
        "run_id": {
            "required": True,
            "placeholder": "abc123def456",
            "description": "The MLflow run ID to attach the artifact to.",
        },
        "local_path": {
            "required": True,
            "placeholder": "/app/data/model.pkl",
            "description": "Absolute path to the local file to upload as an artifact.",
        },
        "artifact_path": {
            "group": "Options",
            "placeholder": "models",
            "description": "Optional subdirectory within the run's artifact store to upload to.",
        },
    },
)
def mlflow_log_artifact(
    input: Any = None,
    credentials: Any = None,
    run_id: str = "",
    local_path: str = "",
    artifact_path: str = "",
) -> dict[str, Any]:
    if not run_id:
        raise ValueError("mlflow_log_artifact: run_id is required")
    if not local_path:
        raise ValueError("mlflow_log_artifact: local_path is required")

    client = _setup_mlflow(credentials)
    client.log_artifact(
        run_id=run_id,
        local_path=local_path,
        artifact_path=artifact_path or None,
    )
    import pathlib as _pathlib  # noqa: PLC0415
    return {
        "run_id": run_id,
        "local_path": local_path,
        "artifact_path": artifact_path or "",
        "filename": _pathlib.Path(local_path).name,
    }
```

- [ ] **Step 4: Add data_platform_nodes to __init__.py**

In `packages/nodes/noodle_nodes/__init__.py`, add after the `from noodle_nodes import cloud_devops as cloud_devops` line:

```python
from noodle_nodes import data_platform_nodes as data_platform_nodes
```

And add `"data_platform_nodes"` to the `__all__` list.

- [ ] **Step 5: Run failing tests, then verify they pass**

```
cd packages/nodes && python -m pytest tests/test_data_platform_nodes.py -v
```
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/data_platform_nodes.py \
        packages/nodes/noodle_nodes/__init__.py \
        packages/nodes/tests/test_data_platform_nodes.py
git commit -m "feat(nodes): add Snowflake, BigQuery, dbt Cloud, and MLflow data-platform nodes"
```

---

## Task 5: AI Builder Allow-List and Registry Updates

**Files:**
- Modify: `apps/api/app/services/ai_builder.py`

**Interfaces:**
- Consumes: existing `_ALLOWED_NODE_TYPES` set and `_NODE_REGISTRY` dict starting at line 32
- Produces: new nodes discoverable by the AI workflow builder

- [ ] **Step 1: Write a failing test**

In `apps/api/tests/` check for existing ai_builder tests:

```
grep -n "def test_" apps/api/tests/test_ai_builder.py | head -10
```

Then add a test that verifies new nodes are in the registry. Append to `apps/api/tests/test_ai_builder.py`:

```python
def test_new_data_platform_nodes_in_allowed_list() -> None:
    from app.services.ai_builder import _ALLOWED_NODE_TYPES, _NODE_REGISTRY

    new_nodes = {
        "read_parquet_file",
        "write_parquet_file",
        "read_excel_file",
        "write_excel_file",
        "snowflake_query",
        "bigquery_query",
        "dbt_cloud_trigger_job",
        "mlflow_log_metric",
        "mlflow_log_artifact",
        "gitlab_trigger_pipeline_v2",
    }
    for node_id in new_nodes:
        assert node_id in _ALLOWED_NODE_TYPES, f"{node_id} missing from _ALLOWED_NODE_TYPES"
        assert node_id in _NODE_REGISTRY, f"{node_id} missing from _NODE_REGISTRY"
        assert _NODE_REGISTRY[node_id].get("name"), f"{node_id} has no name in _NODE_REGISTRY"
```

- [ ] **Step 2: Run to verify it fails**

```
cd apps/api && python -m pytest tests/test_ai_builder.py::test_new_data_platform_nodes_in_allowed_list -v
```
Expected: `AssertionError` — nodes not yet added.

- [ ] **Step 3: Add new entries to _ALLOWED_NODE_TYPES in ai_builder.py**

In `apps/api/app/services/ai_builder.py`, add to the `_ALLOWED_NODE_TYPES` set (at line ~32):

```python
    # File nodes
    "read_parquet_file",
    "write_parquet_file",
    "read_excel_file",
    "write_excel_file",
    # Data platform
    "snowflake_query",
    "bigquery_query",
    "dbt_cloud_trigger_job",
    "mlflow_log_metric",
    "mlflow_log_artifact",
    # DevOps
    "gitlab_trigger_pipeline_v2",
```

- [ ] **Step 4: Add new entries to _NODE_REGISTRY in ai_builder.py**

After the existing `"airtable_create_record_v2"` entry (around line 220), add:

```python
    "read_parquet_file": {
        "name": "Read Parquet File",
        "description": "Read a .parquet file from a server path or uploaded artifact and return a DatasetRef.",
        "params": ["path", "file", "columns", "limit"],
        "output_type": "dataset",
        "category": "Files",
    },
    "write_parquet_file": {
        "name": "Write Parquet File",
        "description": "Write a DatasetRef or list of dicts to a Parquet file artifact.",
        "params": ["compression", "filename"],
        "input_type": "dataset",
        "output_type": "dataset",
        "category": "Files",
    },
    "read_excel_file": {
        "name": "Read Excel File",
        "description": "Read an Excel (.xlsx/.xls) file and return a DatasetRef.",
        "params": ["path", "file", "sheet", "has_header", "limit"],
        "output_type": "dataset",
        "category": "Files",
    },
    "write_excel_file": {
        "name": "Write Excel File",
        "description": "Write a DatasetRef or list of dicts to an Excel (.xlsx) file artifact.",
        "params": ["sheet", "filename"],
        "input_type": "dataset",
        "category": "Files",
    },
    "snowflake_query": {
        "name": "Snowflake Query",
        "description": "Run a SQL query against a Snowflake data warehouse and return results as a DatasetRef.",
        "params": ["credentials", "query", "database", "schema", "warehouse", "limit"],
        "credential_specs": [{"type": "snowflake", "param": "credentials", "key": "*"}],
        "output_type": "dataset",
        "category": "Data",
    },
    "bigquery_query": {
        "name": "BigQuery Query",
        "description": "Run a SQL query against Google BigQuery and return results as a DatasetRef.",
        "params": ["credentials", "query", "project", "limit"],
        "credential_specs": [{"type": "google_service_account", "param": "credentials", "key": "*"}],
        "output_type": "dataset",
        "category": "Data",
    },
    "dbt_cloud_trigger_job": {
        "name": "dbt Cloud Trigger Job",
        "description": "Trigger a dbt Cloud job run and return the run ID and status.",
        "params": ["credentials", "job_id", "cause", "git_sha"],
        "credential_specs": [{"type": "dbt_cloud", "param": "credentials", "key": "*"}],
        "category": "DevOps",
    },
    "mlflow_log_metric": {
        "name": "MLflow Log Metric",
        "description": "Log a numeric metric to an MLflow experiment run.",
        "params": ["credentials", "run_id", "metric_name", "metric_value", "step"],
        "credential_specs": [{"type": "mlflow", "param": "credentials", "key": "*"}],
        "category": "Data",
    },
    "mlflow_log_artifact": {
        "name": "MLflow Log Artifact",
        "description": "Upload a local file as an artifact to an MLflow experiment run.",
        "params": ["credentials", "run_id", "local_path", "artifact_path"],
        "credential_specs": [{"type": "mlflow", "param": "credentials", "key": "*"}],
        "category": "Data",
    },
    "gitlab_trigger_pipeline_v2": {
        "name": "GitLab Trigger Pipeline",
        "description": "Trigger a GitLab CI/CD pipeline on a given branch or tag.",
        "params": ["credentials", "project_id", "ref", "variables"],
        "credential_specs": [{"type": "gitlab_pat", "param": "credentials", "key": "*"}],
        "category": "DevOps",
    },
```

- [ ] **Step 5: Run the test to verify it passes**

```
cd apps/api && python -m pytest tests/test_ai_builder.py::test_new_data_platform_nodes_in_allowed_list -v
```
Expected: PASS.

- [ ] **Step 6: Run the full ai_builder test suite to check for regressions**

```
cd apps/api && python -m pytest tests/test_ai_builder.py -v
```
Expected: all existing tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/ai_builder.py apps/api/tests/test_ai_builder.py
git commit -m "feat(ai-builder): add new data platform and file nodes to allow-list and registry"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Read/Write Parquet — Task 1
- ✅ Read/Write Excel — Task 2
- ✅ GitLab pipeline trigger — Task 3
- ✅ Docker security hardening — Task 3
- ✅ Snowflake Query — Task 4
- ✅ BigQuery Query — Task 4
- ✅ dbt Cloud Trigger Job — Task 4
- ✅ MLflow Log Metric + Log Artifact — Task 4
- ✅ AI builder allow-list — Task 5
- ✅ All nodes have tests

**Placeholder scan:** No TBDs, no "similar to Task N" shortcuts, all code blocks complete.

**Type consistency:**
- `read_parquet_file` → `write_parquet_file` both return DatasetRef dict from `_finalize_parquet`
- `read_excel_file` → `write_excel_file`: write consumes same DatasetRef shape as read produces
- `records_to_dataset` used consistently in Snowflake, BigQuery, Excel read
- `_dbt_request` defined before `dbt_cloud_trigger_job` uses it
- `_setup_mlflow` defined before both mlflow nodes use it

**Missing items fixed:**
- Added `openpyxl` as requirement on both excel nodes
- Added `store_ctx` fixture to excel tests (reuses the one added in Task 1 — both go in the same test file, so the fixture is already available)
- `_write_test_parquet` helper used by excel tests is defined in Task 1's test additions — both tasks add to the same file, so it's available.
