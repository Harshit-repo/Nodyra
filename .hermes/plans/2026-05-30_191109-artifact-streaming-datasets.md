# Artifact-First Streaming Datasets Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make Nodyra genuinely handle large datasets by passing durable dataset references through node outputs while processing large table/file data through artifacts, DuckDB scans, and streaming/chunked transforms instead of giant in-memory Python lists.

**Architecture:** Keep existing inline JSON/Python values for small values. Add a first-class `DatasetRef` envelope for table-shaped large data that points at an artifact/local/S3 URI plus schema/preview metadata. New dataset nodes read/write CSV, JSONL, and Parquet through artifact-backed files and DuckDB SQL so workflows pass small control-plane refs while the data plane stays outside the DB.

**Tech Stack:** Python 3.12, Nodyra core serialization/artifacts, built-in `nodyra_nodes`, DuckDB Python package, existing local/S3-compatible artifact backends, FastAPI schemas/UI rendering, pytest/uv.

---

## Current Context / Assumptions

- Repo: `/mnt/d/nodyra`
- Current branch observed: `feat/orchestration-upgrades`
- Current working tree is dirty. Before implementing, preserve unrelated edits and avoid broad formatting churn.
  - Modified: `apps/api/app/models.py`, `apps/api/app/routers/nodes.py`, `apps/api/app/routers/runs.py`, `apps/api/app/routers/webhooks.py`, `apps/api/app/routers/workflows.py`, `apps/api/app/schemas.py`, `apps/api/app/services/runner.py`, `packages/core/nodyra/engine.py`, `packages/core/nodyra/models.py`
  - Untracked: `.hermes/plans/2026-05-30_n8n-vs-nodyra-architecture.md`, `apps/api/alembic/versions/0023_workflow_allow_concurrent.py`, `test_out.txt`
- Existing safety mechanisms:
  - Per-output persisted cap defaults to 256 KiB in `apps/api/app/config.py`.
  - Artifact caps default to 50 MiB / 100 artifacts per run.
  - DataFrame serialization previews only 100 rows and marks truncated DataFrames as non-restorable.
- Existing built-in CSV nodes in `packages/nodes/nodyra_nodes/transform_extra.py` materialize entire CSVs as strings/lists. Keep these for small values/backward compatibility; add new dataset nodes instead of silently changing semantics.

---

## Should Small Values Use Dataset/Artifact Handling Too?

**No, not by default. Use a hybrid policy.**

Small values should stay inline because inline JSON/Python values are faster and simpler for normal workflow automation:

- No disk/object-store round trip.
- No artifact lifecycle or cleanup concern.
- Easier debugging and UI rendering.
- Lower latency for common cases like API payloads, booleans, short strings, small lists, and config dicts.

Dataset/artifact handling should be used when one of these is true:

1. The value is table-shaped and may be larger than a small preview.
2. The value is file-like bytes/text above a threshold.
3. The user intentionally selected a dataset node.
4. The output exceeds an auto-promotion threshold.
5. The value needs durable retry/cache restoration beyond the output cap.

Recommended default behavior:

- **Inline small scalar/list/dict:** keep current behavior.
- **Inline small table results:** allow small rows to return normally from existing nodes.
- **Dataset nodes:** always return `DatasetRef`, even for small datasets, because their contract is table/file semantics.
- **Auto-promotion:** optional later feature: if a Code node returns a list of dicts or DataFrame above a threshold, Nodyra can suggest or convert to artifact/dataset ref.

Initial thresholds:

- `max_inline_dataset_rows`: default `10_000`
- `max_inline_dataset_bytes`: default `1 MiB`
- `dataset_preview_rows`: default `100`
- Existing `max_output_bytes` still controls persisted UI/DB output caps.

This keeps small workflows efficient while making large data safe.

---

## Design Principles

1. **Control plane stays small:** node outputs carry metadata refs, not full datasets.
2. **Data plane is durable:** large bytes/tables live in artifacts/local/S3 and are cleaned up by existing retention.
3. **Backwards compatibility:** existing `csv_parse` / `csv_write` behavior remains unchanged.
4. **Explicit dataset nodes first:** introduce new nodes like `Read CSV Dataset`, `DuckDB SQL Dataset`, `Write Dataset`.
5. **No hidden partial restoration:** retry/cache restores dataset refs, not DataFrame previews.
6. **DuckDB as v1 query engine:** use DuckDB for CSV/JSONL/Parquet scan, schema inference, SQL transforms, and Parquet output.
7. **Small values remain inline:** do not impose artifact overhead on normal automation payloads.

---

## Proposed Data Model

### DatasetRef Envelope

Create a JSON-compatible envelope similar to artifact refs:

```json
{
  "__nodyra_dataset__": true,
  "version": 1,
  "dataset_id": "...",
  "artifact_id": "...",
  "run_id": "...",
  "node_id": "...",
  "name": "customers.parquet",
  "format": "parquet",
  "uri": "artifact://...",
  "storage_backend": "local",
  "storage_key": "runs/<run>/<node>/<artifact>-customers.parquet",
  "size_bytes": 123456,
  "row_count": 25000000,
  "column_count": 18,
  "schema": [
    {"name": "id", "type": "BIGINT"},
    {"name": "email", "type": "VARCHAR"}
  ],
  "preview": [
    {"id": 1, "email": "a@example.com"}
  ],
  "stats": {
    "source_format": "csv",
    "created_by": "read_csv_dataset"
  }
}
```

Important: a `DatasetRef` is also backed by an artifact row/file so existing retention and download paths continue to work.

---

## Files Likely To Change

Core:

- Create: `packages/core/nodyra/datasets.py`
- Modify: `packages/core/nodyra/serialization.py`
- Modify: `packages/core/nodyra/artifacts.py`
- Modify: `packages/core/nodyra/__init__.py`
- Tests: `packages/core/tests/test_datasets.py`
- Tests: `packages/core/tests/test_serialization.py`

Built-in nodes:

- Create: `packages/nodes/nodyra_nodes/datasets.py`
- Modify: `packages/nodes/nodyra_nodes/__init__.py`
- Modify: `packages/nodes/pyproject.toml`
- Tests: `packages/nodes/tests/test_datasets.py`

API / persistence / UI support:

- Modify: `apps/api/app/services/artifacts.py`
- Modify: `apps/api/app/services/runner.py` only if needed for artifact collection / preview shaping
- Modify: `apps/api/app/schemas.py` for settings if adding thresholds to system settings
- Modify: `apps/api/app/models.py` / Alembic only if adding persisted dataset metadata table; avoid in v1 unless necessary
- Tests: `apps/api/tests/test_artifacts.py`
- Tests: `apps/api/tests/test_retention.py`

Frontend:

- Modify: `apps/web/src/types.ts`
- Modify likely output rendering component(s); identify exact files with `search_files("__nodyra_typed__|output|artifact", path="/mnt/d/nodyra/apps/web/src", file_glob="*.tsx")` before implementation
- Add dataset card UI with preview/schema/download actions

Docs:

- Modify: `README.md`
- Create: `docs/datasets.md` or `docs/artifact-first-datasets.md`

---

## Phase 1: Core DatasetRef Type And Serialization

### Task 1: Add DatasetRef helpers

**Objective:** Add the core `DatasetRef` marker and helper functions without changing runtime behavior.

**Files:**

- Create: `packages/core/nodyra/datasets.py`
- Test: `packages/core/tests/test_datasets.py`

**Implementation sketch:**

```python
from __future__ import annotations

from typing import Any

DATASET_MARKER = "__nodyra_dataset__"
DATASET_VERSION = 1


def is_dataset_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get(DATASET_MARKER) is True
        and value.get("version") == DATASET_VERSION
        and isinstance(value.get("dataset_id"), str)
    )


def make_dataset_ref(
    *,
    dataset_id: str,
    artifact_id: str,
    run_id: str,
    node_id: str,
    name: str,
    format: str,
    storage_backend: str,
    storage_key: str,
    uri: str | None = None,
    size_bytes: int | None = None,
    row_count: int | None = None,
    column_count: int | None = None,
    schema: list[dict[str, Any]] | None = None,
    preview: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        DATASET_MARKER: True,
        "version": DATASET_VERSION,
        "dataset_id": dataset_id,
        "artifact_id": artifact_id,
        "run_id": run_id,
        "node_id": node_id,
        "name": name,
        "format": format,
        "uri": uri or f"artifact://{artifact_id}",
        "storage_backend": storage_backend,
        "storage_key": storage_key,
        "size_bytes": size_bytes,
        "row_count": row_count,
        "column_count": column_count,
        "schema": schema or [],
        "preview": preview or [],
        "stats": stats or {},
    }
```

**Tests:**

- `test_is_dataset_ref_accepts_valid_ref`
- `test_is_dataset_ref_rejects_artifact_ref_or_plain_dict`
- `test_make_dataset_ref_is_json_compatible`

**Run:**

```bash
uv run pytest packages/core/tests/test_datasets.py -v
```

Expected: new tests pass.

---

### Task 2: Export DatasetRef helpers from core package

**Objective:** Make dataset helpers importable by built-in nodes and user code.

**Files:**

- Modify: `packages/core/nodyra/__init__.py`
- Test: `packages/core/tests/test_datasets.py`

**Steps:**

1. Import `DATASET_MARKER`, `DATASET_VERSION`, `is_dataset_ref`, `make_dataset_ref`.
2. Add them to `__all__`.
3. Add test that `from nodyra import is_dataset_ref` works.

**Run:**

```bash
uv run pytest packages/core/tests/test_datasets.py -v
```

---

### Task 3: Preserve DatasetRef through serialization

**Objective:** Ensure dataset refs are treated as already-safe JSON values and are restorable.

**Files:**

- Modify: `packages/core/nodyra/serialization.py`
- Test: `packages/core/tests/test_serialization.py`

**Steps:**

1. Import `is_dataset_ref`.
2. In `serialize_value`, before generic dict/list handling, return dataset refs unchanged.
3. In `deserialize_value`, ensure dataset refs remain dicts unchanged.
4. In `truncate_serialized_value`, keep dataset refs compact by preserving metadata and preview; if still too large, trim preview to fit.

**Test cases:**

- Dataset refs survive serialize/deserialize unchanged.
- Huge dataset preview is trimmed under a cap without losing `dataset_id`, `artifact_id`, `format`, `row_count`, `schema`.

**Run:**

```bash
uv run pytest packages/core/tests/test_serialization.py packages/core/tests/test_datasets.py -v
```

---

## Phase 2: Artifact Store Support For Dataset Files

### Task 4: Add artifact helper for writing files without loading all bytes twice

**Objective:** Enable dataset nodes to create large artifact files via paths/streams instead of only `write_bytes(bytes)`.

**Files:**

- Modify: `packages/core/nodyra/artifacts.py`
- Test: `packages/core/tests/test_datasets.py` or create `packages/core/tests/test_artifacts.py` if absent

**Current issue:** `LocalArtifactStore.write_bytes` converts data to bytes and writes it, which is okay for small files but not ideal for a 5 GB dataset.

**Implementation options:**

- Add `reserve_path(name, content_type, kind, metadata=None, preview=None) -> tuple[Path, finalize_fn]`
- Or add `write_file(path, name, content_type, kind, metadata=None, preview=None) -> ref` that moves/copies an existing file into artifact storage.

**Recommended v1:** `write_file` using `shutil.copyfileobj` with chunking.

Pseudo-code:

```python
def write_file(
    self,
    source_path: str | Path,
    *,
    name: str,
    content_type: str = "application/octet-stream",
    kind: str = "dataset",
    metadata: dict[str, Any] | None = None,
    preview: Any = None,
) -> dict[str, Any]:
    source = Path(source_path)
    size = source.stat().st_size
    if self.max_bytes and size > self.max_bytes:
        raise ValueError(...)
    if self.max_count and self._written >= self.max_count:
        raise ValueError(...)
    # allocate artifact_id/path/ref as write_bytes does
    # copy in 8 MiB chunks
```

**Tests:**

- Writes file artifact and preserves size.
- Enforces `max_bytes` based on file size before copy.
- Enforces `max_count`.

**Run:**

```bash
uv run pytest packages/core/tests/test_datasets.py apps/api/tests/test_artifacts.py -v
```

---

### Task 5: Add top-level runtime helper `artifacts.write_file`

**Objective:** Expose file artifact writing to Code/user-module nodes and dataset nodes.

**Files:**

- Modify: `packages/core/nodyra/artifacts.py`
- Test: `packages/core/tests/test_datasets.py`

**Steps:**

1. Add function:

```python
def write_file(path: str | Path, name: str | None = None, content_type: str = "application/octet-stream", *, kind: str = "binary", metadata: dict[str, Any] | None = None, preview: Any = None) -> dict[str, Any]:
    source = Path(path)
    ref = _store().write_file(...)
    _remember(ref)
    return ref
```

2. Keep existing `write_bytes`, `write_text`, `read_bytes`, `read_text` behavior unchanged.

**Run:**

```bash
uv run pytest packages/core/tests/test_datasets.py apps/api/tests/test_artifacts.py -v
```

---

## Phase 3: DuckDB-Backed Dataset Utilities

### Task 6: Add DuckDB dependency to built-in nodes

**Objective:** Make DuckDB available wherever `nodyra-nodes` is installed.

**Files:**

- Modify: `packages/nodes/pyproject.toml`

**Change:**

```toml
"duckdb>=1.0",
```

**Run:**

```bash
uv lock
uv run python -c "import duckdb; print(duckdb.__version__)"
```

Expected: DuckDB imports successfully.

**Risk:** Lockfile changes may be large. Keep them isolated in this task/commit.

---

### Task 7: Create shared dataset node utilities

**Objective:** Centralize path resolution, DuckDB schema extraction, preview, row count, and DatasetRef creation.

**Files:**

- Create: `packages/nodes/nodyra_nodes/datasets.py`
- Test: `packages/nodes/tests/test_datasets.py`

**Utilities to create:**

- `_connect() -> duckdb.DuckDBPyConnection`
- `_artifact_path(ref: dict) -> Path` for local artifact refs/dataset refs
- `_schema_for_relation(con, relation_sql) -> list[dict]`
- `_preview_for_relation(con, relation_sql, rows=100) -> list[dict]`
- `_count_for_relation(con, relation_sql) -> int | None`
- `_dataset_from_artifact_ref(ref, *, format, schema, row_count, preview, stats) -> dict`
- `_quote_identifier(name: str) -> str`
- `_safe_output_name(name: str, suffix: str) -> str`

**Important:** v1 may only support local artifact paths inside the runtime process. S3-backed artifacts are rehomed by the API after the run. For remote/S3 direct scanning, add a later phase.

**Run:**

```bash
uv run pytest packages/nodes/tests/test_datasets.py -v
```

---

## Phase 4: New Dataset Nodes

### Task 8: Add `Read CSV Dataset` node

**Objective:** Convert CSV text/file/artifact input into a Parquet-backed `DatasetRef` without returning all rows.

**Files:**

- Modify: `packages/nodes/nodyra_nodes/datasets.py`
- Test: `packages/nodes/tests/test_datasets.py`

**Node contract:**

- ID: `read_csv_dataset`
- Inputs/params:
  - `input`: artifact ref, dataset ref, local path, or text
  - `path`: optional local path
  - `text`: optional CSV text for small uploads/tests
  - `delimiter`: default `,`
  - `has_header`: default `true`
  - `sample_size`: default maybe `20480`
  - `output_format`: v1 default `parquet`
- Output: `DatasetRef`

**Implementation approach:**

1. If `path` given, use DuckDB `read_csv_auto(path, delim=...)`.
2. If input is artifact/dataset ref, resolve local artifact path and scan it.
3. If `text` given, write text to a temp file first, then scan.
4. `COPY (SELECT * FROM read_csv_auto(...)) TO '<tmp>.parquet' (FORMAT PARQUET)`.
5. Use `artifacts.write_file` to store Parquet output.
6. Return `make_dataset_ref(...)` with schema/count/preview.

**Tests:**

- Small CSV returns dataset ref, not list.
- Dataset ref points to artifact file.
- Preview contains first rows.
- Schema includes columns.
- Handles no-header CSV.

**Run:**

```bash
uv run pytest packages/nodes/tests/test_datasets.py -v
```

---

### Task 9: Add `Read JSONL Dataset` node

**Objective:** Convert JSONL file/text/artifact to Parquet-backed DatasetRef.

**Files:**

- Modify: `packages/nodes/nodyra_nodes/datasets.py`
- Test: `packages/nodes/tests/test_datasets.py`

**Node contract:**

- ID: `read_jsonl_dataset`
- Params similar to CSV.
- Use DuckDB `read_json_auto` or `read_json` depending available DuckDB version.

**Tests:**

- JSONL text -> DatasetRef.
- Preview/schema/count correct.

---

### Task 10: Add `Read Parquet Dataset` node

**Objective:** Wrap an existing Parquet file/artifact/path as DatasetRef without converting.

**Files:**

- Modify: `packages/nodes/nodyra_nodes/datasets.py`
- Test: `packages/nodes/tests/test_datasets.py`

**Node contract:**

- ID: `read_parquet_dataset`
- Input: artifact ref, dataset ref, or path.
- Output: DatasetRef.

**Tests:**

- Reads a tiny Parquet file created by DuckDB.
- Preserves/refers to artifact metadata.

---

### Task 11: Add `DuckDB SQL Dataset` transform node

**Objective:** Run SQL against one or more DatasetRefs and return either small inline results or a new DatasetRef.

**Files:**

- Modify: `packages/nodes/nodyra_nodes/datasets.py`
- Test: `packages/nodes/tests/test_datasets.py`

**Node contract:**

- ID: `duckdb_sql_dataset`
- Params:
  - `sql`: SQL text. Expose input as table `input` for single DatasetRef.
  - `output_mode`: `dataset`, `records`, or `auto`; default `dataset`.
  - `max_inline_rows`: default `1000` for `auto`/`records` guard.
  - `name`: output artifact name.
- Output:
  - `DatasetRef` for large/table output.
  - list of records only when explicitly `records` or `auto` result is below thresholds.

**Implementation approach:**

1. Resolve input DatasetRef path.
2. Register a DuckDB view:

```sql
CREATE VIEW input AS SELECT * FROM read_parquet('<path>')
```

3. For `dataset` mode:

```sql
COPY (<user_sql>) TO '<tmp>.parquet' (FORMAT PARQUET)
```

4. For `records` mode, enforce `LIMIT max_inline_rows + 1` and error if too large.
5. Return DatasetRef with preview/count/schema.

**Tests:**

- Filter returns DatasetRef.
- Aggregate small result can return records in `records` mode.
- `records` mode errors if result exceeds `max_inline_rows`.
- SQL injection through file path is prevented by using safe path quoting / parameters where possible.

---

### Task 12: Add `Dataset Preview` and `Dataset Profile` nodes

**Objective:** Provide small inline inspection outputs for UI/workflow use.

**Files:**

- Modify: `packages/nodes/nodyra_nodes/datasets.py`
- Test: `packages/nodes/tests/test_datasets.py`

**Nodes:**

- `dataset_preview`
  - Params: `rows=100`
  - Returns: list of records, capped by `rows` max 1000.
- `dataset_profile`
  - Returns: schema, row count, null counts where practical, size bytes.

**Rationale:** Users need ways to inspect data without forcing the main dataset transform nodes to output full rows.

---

### Task 13: Add common transforms as convenience nodes

**Objective:** Wrap common SQL operations in user-friendly nodes.

**Files:**

- Modify: `packages/nodes/nodyra_nodes/datasets.py`
- Test: `packages/nodes/tests/test_datasets.py`

**Nodes:**

- `dataset_select_columns`
- `dataset_filter_rows`
- `dataset_limit`
- `dataset_sort`
- `dataset_group_by`

**Implementation:** Build safe SQL and call shared transform helper.

**YAGNI guard:** If this phase gets large, only implement `select`, `filter`, and `limit` first. `join`, `sort`, `group_by` can follow.

---

### Task 14: Register dataset nodes

**Objective:** Import dataset nodes when `nodyra_nodes` is imported.

**Files:**

- Modify: `packages/nodes/nodyra_nodes/__init__.py`
- Test: `apps/api/tests/test_nodes.py` or `packages/nodes/tests/test_datasets.py`

**Steps:**

1. Add `from nodyra_nodes import datasets as datasets`.
2. Add `"datasets"` to `__all__`.
3. Test that registry includes `read_csv_dataset`, `duckdb_sql_dataset`, etc.

**Run:**

```bash
uv run pytest packages/nodes/tests/test_datasets.py apps/api/tests/test_nodes.py -v
```

---

## Phase 5: API Persistence, Retention, And Settings

### Task 15: Ensure dataset refs are collected as artifact refs

**Objective:** Persist artifact metadata for files referenced by DatasetRefs.

**Files:**

- Modify: `apps/api/app/services/artifacts.py`
- Test: `apps/api/tests/test_artifacts.py`

**Current:** `collect_artifact_refs` looks for `__nodyra_artifact__` refs recursively.

**Change:** When it sees a `DatasetRef`, also collect the backing `artifact_id`/storage fields as an artifact ref, or make DatasetRef include a nested artifact ref.

**Preferred shape:** Include enough artifact fields at top-level and teach `collect_artifact_refs` to treat dataset refs as artifact-backed values.

**Tests:**

- A code node returning DatasetRef persists an artifact row.
- Retention deletes dataset artifact bytes with the run.

---

### Task 16: Add dataset threshold settings

**Objective:** Add operator-configurable thresholds for inline-vs-dataset behavior and previews.

**Files:**

- Modify: `apps/api/app/config.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/services/live_settings.py`
- Modify: `apps/api/alembic/versions/<new>_dataset_settings.py` only if settings are DB-backed in `SystemSettings`
- Tests: `apps/api/tests/test_settings_endpoints.py`

**Settings:**

- `dataset_preview_rows: int = 100`
- `max_inline_dataset_rows: int = 10_000`
- `max_inline_dataset_bytes: int = 1 * 1024 * 1024`

**Note:** Dataset nodes can use params first; global settings can come later if DB migration conflicts with dirty tree.

---

### Task 17: Add optional Code-node auto-promotion warning, not conversion

**Objective:** Help users avoid giant inline outputs without silently changing behavior.

**Files:**

- Modify likely: `apps/api/app/services/runner.py` or output event shaping path
- Tests: `apps/api/tests/test_retention.py` or new API test

**Behavior:**

- If a node output is a large `list[dict]` or DataFrame and gets truncated, add a warning/debug note:
  - “This output looks like a large dataset. Use Read CSV Dataset / DuckDB SQL Dataset / artifacts.write_file for scalable handling.”

**Do not auto-convert in v1.** Silent conversion may break workflows expecting a list.

---

## Phase 6: Frontend Dataset Card

### Task 18: Add DatasetRef TypeScript type

**Objective:** Let UI identify dataset refs separately from typed envelopes/artifacts.

**Files:**

- Modify: `apps/web/src/types.ts`

**Type sketch:**

```ts
export interface DatasetRef {
  __nodyra_dataset__: true;
  version: number;
  dataset_id: string;
  artifact_id: string;
  name: string;
  format: string;
  row_count: number | null;
  column_count: number | null;
  size_bytes: number | null;
  schema: Array<{ name: string; type: string }>;
  preview: Array<Record<string, unknown>>;
}
```

---

### Task 19: Add Dataset output renderer

**Objective:** Render dataset refs as compact cards instead of raw JSON.

**Files:**

- Identify with: `search_files("__nodyra_typed__|artifact|output", path="apps/web/src", file_glob="*.tsx")`
- Modify likely output/detail panel component.

**UI card contents:**

- Dataset name + format
- Rows / columns / size
- Schema preview
- First N preview rows
- Download artifact link
- “Copy ref JSON” action for debugging

**Tests / verification:**

- Run web build:

```bash
uv run pytest packages/core/tests/test_datasets.py packages/nodes/tests/test_datasets.py -v
npm --prefix apps/web run build
```

Use the project’s actual frontend package manager command if different.

---

## Phase 7: Documentation And Examples

### Task 20: Document artifact-first dataset patterns

**Objective:** Explain when to use inline values vs DatasetRef/artifacts.

**Files:**

- Create: `docs/artifact-first-datasets.md`
- Modify: `README.md`

**Include:**

- Small values stay inline.
- Large datasets should use dataset nodes.
- CSV -> Parquet recommendation.
- DuckDB SQL examples.
- Output/artifact caps.
- Anti-patterns: returning millions of rows from Code/CSV Parse.

---

### Task 21: Add starter workflow templates

**Objective:** Help users discover dataset workflows.

**Files:**

- Modify: `apps/web/src/workflowTemplates.ts`

**Templates:**

1. CSV to Parquet + Preview
2. Parquet SQL Aggregate
3. Dataset Filter + Export

---

## Phase 8: Optional Later Enhancements

These are deliberately not v1 requirements:

1. **Direct S3 DuckDB scanning**
   - Configure DuckDB `httpfs` and credentials for S3/R2/MinIO paths.
   - Avoid local download for huge remote datasets.
2. **Dataset metadata table**
   - Add `datasets` table if we need searchable dataset catalog/history independent of artifact rows.
3. **Auto-promotion from Code outputs**
   - Convert large DataFrames/list-of-dicts to DatasetRef automatically when safe.
   - Must be opt-in or very carefully versioned.
4. **Polars/Dask/Spark backends**
   - Add alternative engines only when use cases require them.
5. **Streaming row iterator SDK**
   - User code can receive `DatasetRef` and iterate batches through a stable SDK.
6. **Partitioned datasets**
   - Support directory-style Parquet datasets and partition metadata.

---

## Testing Strategy

Core tests:

```bash
uv run pytest packages/core/tests/test_datasets.py packages/core/tests/test_serialization.py -v
```

Node tests:

```bash
uv run pytest packages/nodes/tests/test_datasets.py packages/nodes/tests/test_transform_extra.py -v
```

API artifact/retention/settings tests:

```bash
uv run pytest apps/api/tests/test_artifacts.py apps/api/tests/test_retention.py apps/api/tests/test_settings_endpoints.py -v
```

Full relevant regression:

```bash
uv run pytest packages/core packages/nodes apps/api/tests/test_artifacts.py apps/api/tests/test_runs.py apps/api/tests/test_retention.py -v
```

Frontend verification:

```bash
npm --prefix apps/web run build
```

If Docker stack verification is needed later:

```bash
"/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe" compose -f deploy/docker-compose.yml build api web worker
"/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe" compose -f deploy/docker-compose.yml up -d
```

---

## Acceptance Criteria

- Existing small-value nodes and workflows continue to work unchanged.
- New dataset nodes return compact `DatasetRef` objects, not full row lists.
- CSV/JSONL/Parquet can be read into Parquet-backed artifacts.
- DuckDB SQL can transform a dataset and return a new DatasetRef.
- Dataset artifacts are persisted, downloadable, and deleted by retention cleanup.
- Large dataset output does not inflate `NodeRun.output` beyond existing caps.
- UI recognizes DatasetRefs and displays metadata/preview instead of raw giant JSON.
- Docs clearly explain inline vs dataset handling.

---

## Risks / Tradeoffs

- **DuckDB dependency size:** Adds a heavier dependency to built-in nodes, but it avoids much larger distributed-system complexity.
- **Local-only v1:** Runtime can scan local artifact paths easily. Direct S3 scans require additional DuckDB credential handling.
- **Artifact caps:** Default 50 MiB artifact limit is too low for real big data. Users must raise it or configure S3. Plan should include docs/UI guidance.
- **Dirty working tree:** Existing modified files may conflict with settings/schema/runner changes. Implementation must isolate commits and avoid overwriting unrelated work.
- **SQL safety:** DuckDB SQL is arbitrary query text. Treat it like Code node risk level, and reuse unsafe-node policy if needed.
- **Preview cost:** Counting rows and generating previews on huge files can be expensive. Use DuckDB efficiently and allow row count to be nullable/estimated if needed.

---

## Recommended Implementation Order

1. Core `DatasetRef` helpers and serialization.
2. Artifact `write_file` support.
3. DuckDB dependency and dataset utilities.
4. `Read CSV Dataset` + tests.
5. `DuckDB SQL Dataset` + tests.
6. API artifact collection/retention tests.
7. UI dataset card.
8. Docs/templates.
9. Optional JSONL/Parquet/convenience transforms.

This order gives a thin vertical slice quickly: CSV -> DatasetRef -> SQL transform -> DatasetRef -> persisted artifact -> UI preview.
