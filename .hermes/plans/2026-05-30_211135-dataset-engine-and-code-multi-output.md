# Dataset Engine + Code Multi-Output Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make Nodyra handle large table/file workloads predictably by introducing first-class `DatasetRef`s, DuckDB/Parquet-backed dataset nodes, artifact-backed files/bytes, and a Code node that can intentionally produce multiple named outputs without forcing large data through inline JSON.

**Architecture:** Split Nodyra values into a small inline control plane and an artifact-backed data plane. Built-in table/file nodes use explicit node contracts and always return `DatasetRef`/`ArtifactRef` where appropriate; the runtime only guesses for ambiguous custom/code outputs via conservative auto-promotion. Because the product has no users, this plan intentionally chooses clean breaking semantics over compatibility with old `csv_parse -> list[dict]` / `csv_write -> str` behavior.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, SQLAlchemy, existing Nodyra SDK/runtime, DuckDB, Parquet, existing artifact APIs, React/Vite editor.

---

## Executive decisions

1. **Yes, add dataset processing nodes.** Join, sort, group-by, filter, select, limit, sample, dedupe, union, append, SQL, preview, and import/export should all exist as dataset-aware nodes.
2. **Do not make Nodyra guess every “heavy” node.** Heavy behavior is primarily known from node contracts: dataset nodes declare dataset inputs/outputs and always use `DatasetRef`s.
3. **Parquet is the internal table format.** CSV/JSONL/Excel/API records are import/export shapes; internal table processing should normalize to Parquet unless a node is explicitly exporting.
4. **Inline values are still valid.** Booleans, numbers, short strings, small config objects, control-flow payloads, and status objects stay inline.
5. **Table-shaped data from dataset nodes is never inline by default.** Even tiny CSV reads return `DatasetRef`; users use explicit extraction nodes to produce inline rows.
6. **Code node should support multiple outputs.** Nodyra already has engine support for multi-output nodes via `outputs_override`; Code should expose it cleanly in the UI/runtime.
7. **Code output should be auto-promoted.** DataFrames become Parquet-backed `DatasetRef`s; large row-shaped arrays become `DatasetRef`s; large bytes/text become `ArtifactRef`s; small control values remain inline.
8. **No backward compatibility.** Replace old CSV/table node semantics now rather than carrying parallel “legacy” and “dataset” node families.

---

## Current codebase facts observed

- `packages/core/nodyra/sdk.py`
  - `@node(outputs=[...])` already supports declared multiple outputs.
  - Multi-output nodes must return a dict keyed by output names.
  - `GraphNode.outputs_override` already exists and can dynamically define output port names.
  - Uploaded user module functions currently always preview/register as one `main` output.
- `packages/core/nodyra/models.py`
  - `NodeManifest` has `inputs`, `params`, `outputs`, but ports do not currently carry data-kind metadata.
  - `GraphNode.outputs_override` already exists.
- `packages/core/nodyra/engine.py`
  - `_normalize_outputs()` already supports multi-output nodes and `outputs_override`.
  - Code nodes are process-isolated by node type `code`.
  - Node output size capping currently JSON-dumps outputs and errors if above cap; this must run after auto-promotion, not before.
- `packages/nodes/nodyra_nodes/builtin.py`
  - Code node currently accepts `input` and `code`, executes Python, and returns `namespace.get("output")` only.
  - Code node docs tell users to assign `output`.
  - It passes `artifacts` to user code.
- `packages/nodes/nodyra_nodes/transform_extra.py`
  - `csv_parse` currently materializes all CSV rows as a Python list.
  - `csv_write` currently materializes a full CSV string.
  - These are exactly the semantics to replace.
- `packages/core/nodyra/artifacts.py`
  - Existing `LocalArtifactStore` writes bytes and returns JSON-compatible artifact refs.
  - Existing `write_dataframe()` only supports CSV/JSON and materializes the whole payload; it should move to Parquet/DatasetRef for table data.
- `packages/core/nodyra/serialization.py`
  - DataFrames currently serialize as typed preview envelopes with a default 100-row preview.
  - Truncated DataFrames are marked non-restorable.
  - This is useful for UI preview, but not sufficient for data-plane processing.
- `apps/web/src/editor/NodeCard.tsx` and `PortDataViewer.tsx`
  - UI already renders multiple output handles via `outputsOverride`.
  - Port data viewer already understands artifact refs and typed DataFrame envelopes, but not `DatasetRef` yet.
- Dirty working tree exists.
  - Implementation must preserve unrelated edits and avoid broad formatting churn.

---

## Desired value model

### Inline control values

Use inline JSON/Python values for:

- booleans
- numbers
- short strings
- small dicts/lists used as configuration or branch/control payloads
- trigger metadata
- API response metadata
- counters/status objects
- error envelopes
- small arrays that are intentionally control data, not a table

### `ArtifactRef`

Use `ArtifactRef` for:

- bytes
- files
- images/audio/video/PDFs
- large text blobs
- exported CSV/JSON/Excel files
- arbitrary user-created artifacts

### `DatasetRef`

Use `DatasetRef` for:

- tabular data
- DataFrames
- CSV/JSONL/Parquet/Excel imported as tables
- large list-of-dicts / row-shaped arrays when auto-promoted
- SQL/query results
- join/sort/filter/group outputs

### Core phrase to preserve in docs/UI

> Inline values are for workflow logic. DatasetRefs are for table data. Artifacts are for files/bytes.

---

## `DatasetRef` contract

Create a stable JSON-compatible envelope in core, not app-specific API code.

Suggested shape:

```json
{
  "__nodyra_dataset__": true,
  "version": 1,
  "dataset_id": "uuid-or-artifact-id",
  "artifact": {
    "__nodyra_artifact__": true,
    "version": 1,
    "artifact_id": "...",
    "run_id": "...",
    "node_id": "...",
    "name": "customers.parquet",
    "kind": "dataset",
    "content_type": "application/vnd.apache.parquet",
    "size_bytes": 123456,
    "storage_backend": "local",
    "storage_key": "runs/.../customers.parquet"
  },
  "format": "parquet",
  "logical_type": "table",
  "schema": [
    {"name": "customer_id", "type": "BIGINT", "nullable": true},
    {"name": "country", "type": "VARCHAR", "nullable": true}
  ],
  "row_count": 1000000,
  "column_count": 12,
  "preview": {
    "columns": ["customer_id", "country"],
    "rows": [{"customer_id": 1, "country": "US"}],
    "truncated": true,
    "preview_row_count": 100
  },
  "stats": {
    "created_by_node_id": "read_csv_1",
    "source_format": "csv",
    "source_name": "customers.csv"
  }
}
```

Implementation notes:

- Keep the top-level `DatasetRef` small and JSON-serializable.
- Store actual bytes in the existing artifact store.
- Prefer embedding the artifact ref rather than duplicating all storage fields at the top level.
- Add `is_dataset_ref(value)` helper equivalent to `is_artifact_ref()`.
- Add `dataset_path_for_ref(ref)` helper that uses `artifact_store.path_for_ref(ref["artifact"])`.
- Dataset refs should serialize/deserialze as-is. Never turn them into typed generic dicts.
- Dataset refs should be immutable from user perspective: transforms produce a new dataset artifact/ref.

---

## Node contract model: how Nodyra knows what is heavy

Add data-kind metadata to port specs and manifests.

Suggested additions:

```python
class PortDataKind(StrEnum):
    any = "any"
    control = "control"
    dataset = "dataset"
    artifact = "artifact"
    file = "file"

class PortSpec(BaseModel):
    name: str
    description: str = ""
    data_kind: PortDataKind = PortDataKind.any
    required: bool = True

class NodeManifest(BaseModel):
    ...
    data_plane: bool = False
```

SDK decorator should allow:

```python
@node(
    name="Join Datasets",
    id="dataset_join",
    category="Dataset",
    inputs={"left": "dataset", "right": "dataset"},
    outputs={"main": "dataset"},
)
```

If that is too big a decorator change, keep `inputs=[...]`/`outputs=[...]` and add optional `input_kinds={...}`, `output_kinds={...}`.

Rules:

- Dataset nodes declare dataset output kind and always return `DatasetRef`.
- File nodes declare artifact/file output kind and return `ArtifactRef`.
- Control/transform nodes default to `any` or `control` and may remain inline.
- Engine validates required kind at runtime and gives clear errors:
  - “Dataset Join expected `left` to be a DatasetRef, got inline array. Add Records To Dataset before Join.”
  - “Dataset To Records requires a `max_rows` cap.”
- UI uses port kind to color handles and suggest compatible nodes.

---

## Dataset node inventory

### V1 import/read nodes

1. **CSV Parse / Read CSV**
   - Replace current `csv_parse` semantics.
   - Input can be CSV text, artifact ref, file path if allowed, or URL if later supported.
   - Output: `DatasetRef` in Parquet.
   - Params: delimiter, header, quote char, escape char, encoding, null values, sample size for inference, malformed row mode.

2. **Read JSONL**
   - Input: text/artifact/path.
   - Output: `DatasetRef` in Parquet.
   - Edge: nested objects should be stringified or flattened depending param.

3. **Read Parquet**
   - Input: artifact/path.
   - Output: `DatasetRef` pointing at existing parquet or normalized copy.

4. **Records To Dataset**
   - Input: list[dict] or object containing records/rows/items/data/results.
   - Output: `DatasetRef` in Parquet.
   - Required for explicit conversion from API/control payloads to table data.

### V1 transform nodes

1. **Dataset Select Columns**
2. **Dataset Rename Columns**
3. **Dataset Filter Rows**
4. **Dataset Limit**
5. **Dataset Sample**
6. **Dataset Sort**
7. **Dataset Join**
8. **Dataset Group By / Aggregate**
9. **Dataset Deduplicate**
10. **Dataset Union / Append**
11. **DuckDB SQL**
12. **Dataset Profile**

All transform nodes:

- Input: one or more `DatasetRef`s.
- Output: new `DatasetRef` unless explicitly a preview/profile node.
- Use DuckDB to scan Parquet and write Parquet.
- Never materialize full table as Python list.
- Include preview/schema/row_count in output ref.

### V1 preview/extraction nodes

1. **Dataset Preview**
   - Input: `DatasetRef`.
   - Output: small inline preview object.
   - Params: limit default 100, max hard cap 1000, columns.

2. **Dataset To Records**
   - Input: `DatasetRef`.
   - Output: inline list of dicts.
   - Requires explicit `max_rows` with hard cap, default maybe 100, max 10,000.
   - Fails if actual row count exceeds cap unless `allow_truncate=true`.

3. **Dataset Schema**
   - Input: `DatasetRef`.
   - Output: inline schema/stat object.

### V1 export/write nodes

1. **Write Parquet**
   - Input: `DatasetRef`.
   - Output: `ArtifactRef` or `DatasetRef` depending whether user wants file artifact or continued dataset.
   - Recommended: output both `dataset` and `artifact` ports.

2. **CSV Write / Export CSV**
   - Replace current `csv_write` semantics.
   - Input: `DatasetRef` or records.
   - Output: `ArtifactRef` for CSV file, not giant string.
   - If user wants CSV string, add explicit **Artifact To Text** or **Dataset To CSV Text** with cap.

3. **Export JSONL**
4. **Export Excel** later.

---

## Code node multi-output design

### Recommendation

Yes, implement multiple outputs for Code. It is already compatible with core concepts and will be useful for:

- `success` / `error` / `skipped` branches
- splitting small metadata from large datasets
- emitting multiple datasets from one custom transform
- separating `records`, `summary`, `artifact`, `debug`, etc.

### User-facing API

Support both old/simple and new styles:

#### Single output remains easy

```python
output = {"ok": True}
```

#### Multiple outputs via `outputs`

```python
outputs = {
    "clean": clean_df,
    "rejected": rejected_df,
    "summary": {"clean_rows": len(clean_df), "rejected_rows": len(rejected_df)},
}
```

#### Multiple outputs via assignment helper, optional later

```python
emit("clean", clean_df)
emit("summary", {"rows": row_count})
```

For v1, plain `outputs = {...}` is enough.

### Output port configuration

Add Code node params:

- `output_ports`: string or list, default `main`
  - UI can expose as comma-separated names initially.
  - Backend stores as `GraphNode.outputs_override`.
- `output_mode`: `auto | inline | dataset | artifact`, default `auto`.
- `large_row_mode`: `dataset | error | inline`, default `dataset`.
- `max_inline_rows`, default 1000 or from settings.
- `max_inline_bytes`, default 256 KiB or from settings.

Behavior:

- If Code node has only `main`, `output` is used.
- If `outputs` exists and configured output ports are more than one, normalize from `outputs`.
- If both `output` and `outputs` exist:
  - Prefer `outputs` when multi-output mode is configured.
  - Otherwise use `output`.
  - Add a debug warning if both are present.
- If configured ports include `clean,rejected,summary`, missing keys are treated as untaken branches, matching current `_normalize_outputs()` behavior.
- If Code returns keys not declared in configured ports, ignore them and add debug warning, or fail in strict mode.

### Engine/runtime impact

Existing engine behavior:

- `outputs_override` already controls `output_names`.
- `_normalize_outputs()` expects dict for multiple outputs.

Needed changes:

- Make Code node return a dict when `outputs` is defined.
- Add Code output auto-promotion before `_normalize_outputs()` output-size cap.
- If auto-promotion needs artifact store, be careful: Code nodes run in process pool and contextvars/artifact store must be available in that process. Current artifacts API may not work in process-isolated Code if context is not propagated. Validate with tests.
- If process-pool context is unreliable, either:
  1. move auto-promotion to parent engine after raw output returns, or
  2. pass a serializable artifact-store config into the Code process, or
  3. stop running artifact-writing Code in process pool and use a supervised subprocess protocol.

Recommended for v1:

- Do auto-promotion in the parent engine after Code returns raw Python values and before JSON output size cap.
- But DataFrames returned from process pool must be pickled back to parent. This is okay for medium frames but bad for huge frames.
- For truly large Code-generated datasets, provide `datasets.write_dataframe(df)` / `artifacts.write_dataset(...)` helpers inside Code so user code can write inside the worker and return a `DatasetRef`. This may require explicit artifact-store config propagation to the Code process.
- Therefore implement in two stages:
  - Stage A: parent-side auto-promotion for DataFrames/large row arrays that fit process return.
  - Stage B: in-process Code helper for large DataFrames without pickling full data back.

---

## Auto-promotion decision tree

Apply after node function returns and before persistence/output cap.

For every output port value:

```text
Is value already DatasetRef or ArtifactRef?
  yes -> keep as-is

Is output port declared dataset?
  yes -> coerce to DatasetRef or fail with helpful error

Is output port declared artifact/file?
  yes -> coerce bytes/text/path-like to ArtifactRef or fail

Is value a DataFrame-like object?
  yes -> write Parquet artifact, return DatasetRef

Is value a DuckDB relation / Arrow table / Polars DataFrame?
  yes -> write Parquet artifact, return DatasetRef

Is value a list[dict] with consistent row shape and beyond threshold?
  yes -> write Parquet artifact, return DatasetRef

Is value large bytes/bytearray/memoryview?
  yes -> write ArtifactRef

Is value large string?
  yes -> write text ArtifactRef unless node output mode says inline/error

Else keep inline
```

Suggested default thresholds:

- `dataset_preview_rows = 100`
- `dataset_to_records_max_rows = 1000`, hard max 10000
- `max_inline_dataset_rows = 1000` for Code auto mode
- `max_inline_dataset_bytes = min(settings.max_output_bytes, 1 MiB)`
- `max_inline_text_bytes = settings.max_output_bytes`
- `max_dataset_columns_preview = 50`
- `max_dataset_profile_columns = 200`

Important: thresholds are for ambiguous Code/custom outputs only. Dataset nodes always produce `DatasetRef` regardless of size.

---

## DuckDB/Parquet implementation notes

Add dependencies to `packages/nodes/pyproject.toml` or core if helpers live there:

- `duckdb`
- Consider `pyarrow` only if needed for richer schema/records conversions; DuckDB can write Parquet by itself.

Core helper module options:

- `packages/core/nodyra/datasets.py` for `DatasetRef`, detection, writing, schema, preview helpers.
- `packages/nodes/nodyra_nodes/datasets.py` for node definitions.

Recommended split:

- Core owns the envelope + generic helpers that need artifact store.
- Nodes own DuckDB transform SQL.

DuckDB write pattern:

```sql
COPY (
  SELECT ...
) TO '/safe/output/path.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
```

DuckDB scan pattern:

```sql
SELECT * FROM read_parquet('/safe/input/path.parquet')
```

Path safety:

- Never interpolate unsanitized user paths directly into SQL.
- Use DuckDB parameters where possible.
- For identifiers/column names, quote identifiers with a dedicated helper.
- Use artifact-store-resolved paths for dataset refs.
- If direct local path reads are allowed, restrict/validate path policy separately.

---

## Edge cases to handle

### CSV/import edge cases

- Empty file.
- Header-only file.
- No header.
- Duplicate column names.
- Invalid UTF-8 / configurable encoding.
- BOM (`utf-8-sig`).
- Different delimiters, quote chars, escape chars.
- Newlines inside quoted fields.
- Extremely wide rows.
- Ragged rows / malformed rows.
- Type inference surprises: leading zeros, IDs interpreted as numbers, dates, decimals.
- Null markers (`NA`, `N/A`, empty string, `null`).
- Very large source CSV bigger than current default artifact cap.
- Compression: gzip/zstd later.

### Parquet/schema edge cases

- Empty dataset schema.
- Nested/list/map columns.
- Decimal precision.
- Timezone timestamps.
- Date vs datetime.
- Binary columns.
- Column names with spaces, punctuation, SQL keywords, unicode.
- Duplicate column names after joins.
- Schema mismatch in union/append.

### Join edge cases

- Missing join keys.
- Different key names left/right.
- Type mismatch on join keys.
- Null join keys.
- Duplicate column names.
- Many-to-many explosion.
- Join result too large for artifact cap or disk.
- Full outer join support.
- Anti/semi joins later.
- Case sensitivity.

### Sort edge cases

- Null ordering.
- Multi-column sort.
- Asc/desc per column.
- Locale/collation not guaranteed.
- Large sort may spill to disk; configure DuckDB temp directory.
- Stable sorting not guaranteed unless explicitly requested and supported.

### Group-by edge cases

- Empty group list means global aggregate.
- Multiple aggregations per column.
- Aggregating non-numeric columns.
- Distinct count.
- Null handling.
- Output column naming collisions.
- Very high-cardinality groups.

### Filter/SQL edge cases

- SQL injection from UI params.
- Column names requiring quoting.
- Invalid expressions.
- Filter producing zero rows.
- SQL query returning multiple statements: disallow.
- SQL query referencing unauthorized file paths/functions.
- Query output not tabular.

### Dataset preview/extraction edge cases

- Preview should not scan full table if avoidable.
- Row count may be expensive for some formats; for Parquet it is usually available but verify.
- Preview of binary/nested values must be JSON-safe.
- `Dataset To Records` must require row cap.
- Refuse extraction if rows > cap unless explicit truncate.

### Artifact/storage edge cases

- Artifact byte cap reached.
- Artifact count cap reached.
- Local vs remote backend rehoming.
- Retention deletes dataset artifacts with runs.
- Pinned outputs/retry caches should keep refs, not copy files.
- Remote runner must upload dataset artifacts back to API store.
- Multiple dataset outputs from one node count as multiple artifacts.
- Temporary files must be cleaned on failure.

### Runtime/concurrency edge cases

- Current workflow timeout default is short for large work; dataset nodes need per-node timeout overrides and/or larger defaults.
- Parallel dataset nodes may saturate disk/CPU; consider concurrency class/weight later.
- DuckDB temp directory should be per-run or per-node.
- Memory limit should be configurable (`PRAGMA memory_limit`).
- Threads should be configurable (`PRAGMA threads`).
- If node retries, ensure retry writes a new artifact and does not leave stale partially-written refs.

### Serialization/UI edge cases

- Dataset refs must not be truncated by output cap; they are small.
- Dataset previews must be capped.
- UI should not render raw JSON for DatasetRef; render dataset card.
- Dataset card should show row count, columns, file size, format, preview, download/export actions.
- If dataset artifact missing/deleted, UI should show missing artifact state.
- Pinned dataset refs should remain refs and not embed data.

### Code node edge cases

- User assigns `output`, not `outputs`.
- User assigns `outputs` but forgot to configure output ports.
- User returns dict intended as one payload but it is mistaken as multi-output.
  - Avoid this by only treating `outputs` variable specially when multi-output mode or `outputs_override` is active.
- Output port name invalid/empty/duplicate.
- Output key contains spaces/punctuation.
- Missing output branch is untaken.
- `outputs` not dict in multi-output mode.
- Both `output` and `outputs` set.
- DataFrame output cannot write Parquet because optional deps missing.
- Large DataFrame cannot pickle back from process pool.
- User code writes its own artifacts/datasets and returns refs.
- Code helper cannot access artifact store in process pool.
- Logs/debug variable preview should not serialize whole huge outputs.

### Security edge cases

- Code node is arbitrary Python; current trust model likely accepts this, but artifact/dataset helpers should not worsen it.
- DuckDB SQL should not allow arbitrary filesystem reads from SQL unless intended.
- If reading local paths is supported, define workspace sandbox policy.
- Never expose full filesystem paths in user-facing refs if not needed.
- Redact secrets in metadata/previews/logs where existing redaction applies.

---

## Implementation plan

### Phase 0: Safety and baseline

**Task 0.1: Record dirty tree and guardrails**

- Files: no modification.
- Run: `git status --short --branch`.
- Note dirty files before implementation.
- Do not reformat unrelated files.

**Task 0.2: Add baseline tests for current multi-output engine behavior**

- Modify: `packages/core/tests/test_engine.py`.
- Tests:
  - multi-output node with missing branch skips downstream.
  - `outputs_override` defines dynamic ports.
  - single-output dict remains one value, not multi-output.
- Run: `uv run pytest packages/core/tests/test_engine.py -q`.

### Phase 1: Core dataset reference model

**Task 1.1: Add core dataset envelope helpers**

- Create: `packages/core/nodyra/datasets.py`.
- Implement:
  - `DATASET_MARKER = "__nodyra_dataset__"`
  - `DATASET_VERSION = 1`
  - `is_dataset_ref(value)`
  - `make_dataset_ref(...)`
  - `dataset_artifact_ref(value)`
  - `dataset_preview(...)` caps helper
  - schema normalization helper
- Tests: `packages/core/tests/test_datasets.py`.

**Task 1.2: Teach serialization to preserve DatasetRefs**

- Modify: `packages/core/nodyra/serialization.py`.
- Behavior:
  - `serialize_value(dataset_ref)` returns it as-is after serializing preview/schema safely.
  - `deserialize_value(dataset_ref)` returns it as-is.
  - `truncate_serialized_value(dataset_ref, cap)` should keep marker/id/schema summary and only truncate preview if necessary.
- Tests: `packages/core/tests/test_serialization.py` or new test file.

**Task 1.3: Add port data kinds**

- Modify: `packages/core/nodyra/models.py`.
- Add `PortDataKind` enum and optional `data_kind` field on `PortSpec`.
- Keep default `any` so existing manifests validate.
- Tests: `packages/core/tests/test_sdk.py` or existing SDK tests.

**Task 1.4: Extend SDK decorator for port kinds**

- Modify: `packages/core/nodyra/sdk.py`.
- Add optional `input_kinds: dict[str, str] | None` and `output_kinds: dict[str, str] | None` to `node()`.
- `_build_manifest()` sets `PortSpec(data_kind=...)`.
- Static user function discovery remains `any` for now.
- Tests: create node with dataset input/output kind and assert manifest.

**Task 1.5: Runtime validation for declared dataset/artifact ports**

- Modify: `packages/core/nodyra/engine.py`.
- Before function call, validate wired inputs for ports with `data_kind=dataset/artifact`.
- After output normalization/promotion, validate outputs for declared kind.
- Error messages must tell user which conversion node to add.
- Tests: `packages/core/tests/test_engine.py`.

### Phase 2: Dataset artifact writing helpers

**Task 2.1: Add safe artifact write-path support**

- Modify: `packages/core/nodyra/artifacts.py`.
- Existing store only exposes `write_bytes`, which materializes data.
- Add a helper such as `reserve_path(name, content_type, kind, metadata)` or `write_file_from_path(path, ...)`.
- Need to create artifact ref after DuckDB writes to path without reading whole file into memory.
- Ensure max artifact bytes is checked after file write via `path.stat().st_size`.
- Ensure temp files are cleaned on failed writes.

**Task 2.2: Add dataset write/read helpers**

- Modify/Create: `packages/core/nodyra/datasets.py`.
- Helpers:
  - `write_parquet_from_duckdb_query(conn, sql, params, name, metadata)`
  - `dataset_path(ref)`
  - `load_dataset_schema(ref)`
  - `load_dataset_preview(ref, limit)`
  - `load_dataset_row_count(ref)`
- Tests should use a temp `LocalArtifactStore` and contextvar.

**Task 2.3: Add DuckDB dependency**

- Modify: `packages/nodes/pyproject.toml` if only dataset nodes need it.
- If core helpers import DuckDB directly, modify `packages/core/pyproject.toml` too.
- Prefer lazy import with clear error if dependency missing.
- Run lock/update command according to current uv workspace practice.

### Phase 3: Replace CSV semantics and add dataset nodes

**Task 3.1: Create dataset nodes module**

- Create: `packages/nodes/nodyra_nodes/datasets.py`.
- Register it from `packages/nodes/nodyra_nodes/__init__.py`.
- Add basic helpers for quoting identifiers and resolving dataset refs.

**Task 3.2: Replace `csv_parse` with DatasetRef-producing implementation**

- Modify: `packages/nodes/nodyra_nodes/transform_extra.py` or move to dataset module while preserving node id `csv_parse`.
- Output: DatasetRef.
- Use DuckDB `read_csv_auto` / `read_csv` and write Parquet.
- Tests: update `packages/nodes/tests/test_transform_extra.py`.
- No legacy list output.

**Task 3.3: Replace `csv_write` with ArtifactRef-producing implementation**

- Output: CSV `ArtifactRef`, not string.
- Accept `DatasetRef`; optionally accept records by converting to DatasetRef first.
- Tests: verify artifact ref marker, content type, file contents.

**Task 3.4: Add Records To Dataset**

- Input: inline list[dict] / nested record list.
- Output: DatasetRef.
- Edge tests: empty list, inconsistent keys, nested values, non-record input error.

**Task 3.5: Add Dataset Preview and Dataset To Records**

- Preview outputs small inline object.
- To Records requires cap and fails/truncates predictably.
- Tests: cap enforcement and zero-row behavior.

**Task 3.6: Add Select/Rename/Filter/Limit/Sample**

- Implement with DuckDB SQL writing new Parquet.
- Validate column names.
- Tests: missing columns, SQL keyword columns, empty output.

**Task 3.7: Add Sort**

- Dataset sort node should replace inline list sort for table workflows.
- Existing built-in `sort` may currently sort lists; decide whether to repurpose or create `dataset_sort`.
- Clean-slate recommendation: make `sort` dataset-aware if input is DatasetRef; otherwise keep small inline sort only as control transform or rename to `list_sort` later.
- Tests: null ordering, multi-column sort.

**Task 3.8: Add Join**

- Inputs: `left`, `right` DatasetRefs.
- Params: left_key, right_key, join_type, suffixes/conflict handling.
- Output: DatasetRef.
- Tests: inner/left/full if supported, duplicate column handling, missing key error.

**Task 3.9: Add Group By / Aggregate**

- Params: group_columns, aggregations.
- Represent aggregations as key-value UI param or JSON param initially.
- Output: DatasetRef.
- Tests: global aggregate, count, sum, avg, count distinct, invalid column.

**Task 3.10: Add Union/Append/Deduplicate**

- Union must handle schema mismatch mode:
  - `strict`: require same columns/types.
  - `by_name`: align missing columns with null.
- Deduplicate params: subset columns, keep first/last maybe later.
- Tests: mismatched schema, empty datasets.

**Task 3.11: Add DuckDB SQL node**

- Inputs: either `input` DatasetRef and optional named datasets via params, or multiple input ports later.
- V1: support one primary dataset as table `input`.
- Params: SQL string.
- Disallow multiple statements.
- Output: DatasetRef.
- Tests: select/filter, invalid SQL, attempts at filesystem reads if restricted.

### Phase 4: Code node multi-output

**Task 4.1: Add Code output mode params**

- Modify: `packages/nodes/nodyra_nodes/builtin.py`.
- Params:
  - `output_ports` text default `main`.
  - `output_mode` choices `auto`, `inline`, `dataset`, `artifact`.
- Return logic:
  - If `outputs` variable exists and output_ports != `main`, return `outputs`.
  - Else return `output`.
  - Add debug warning for both `output` and `outputs`.
- Tests: `packages/nodes/tests/test_builtin_nodes.py`.

**Task 4.2: Wire Code output ports into graph node `outputs_override` from UI**

- Modify likely:
  - `apps/web/src/editor/NDVPanels.tsx`
  - `apps/web/src/editor/NodeDetails.tsx`
  - `apps/web/src/editor/store.ts`
- UI should let user edit Code output ports as comma-separated names.
- Updating ports should update `node.data.outputsOverride` / graph `outputs_override`.
- Validate names: non-empty, unique, identifier-ish or safe slug.

**Task 4.3: Add backend graph normalization if needed**

- Modify API schemas/routers only if graph save/load strips `outputs_override`.
- Ensure saved workflows preserve Code outputs_override.
- Tests: API workflow create/update/load with Code outputs override.

**Task 4.4: Add auto-promotion hook in engine**

- Modify: `packages/core/nodyra/engine.py`.
- Add `auto_promote_outputs(outputs, manifest, graph_node, settings?)` before output size cap.
- Since engine is core and does not know app settings, pass thresholds to `execute()`:
  - `max_inline_dataset_rows`
  - `max_inline_dataset_bytes`
  - `max_inline_artifact_bytes`
  - maybe `auto_promote_outputs=True`
- Initial defaults can live in core.

**Task 4.5: Implement DataFrame -> DatasetRef auto-promotion**

- Modify: `packages/core/nodyra/datasets.py` and engine hook.
- Use Parquet.
- Need optional pandas/pyarrow or DuckDB registration.
- Tests with pandas if available; otherwise mark optional or use DuckDB relation.

**Task 4.6: Implement large row array -> DatasetRef auto-promotion**

- Detect list[dict] row-shaped arrays.
- Estimate JSON bytes safely without exploding memory more than already in list.
- Write to Parquet via DuckDB/PyArrow.
- Tests: below threshold stays inline; above threshold becomes DatasetRef.

**Task 4.7: Implement large text/bytes -> ArtifactRef auto-promotion**

- Use existing artifact helper.
- Tests: large text becomes artifact, small text stays inline.

**Task 4.8: Validate process-isolated Code + artifact context**

- Write integration test where Code returns DataFrame/list and engine auto-promotes.
- If artifact store not available in parent/child context, fix context propagation or move promotion into API runner layer.
- Test with actual `make_artifact_store` in API runner if needed.

### Phase 5: API persistence, artifacts, remote runners

**Task 5.1: Persist DatasetRefs like artifact refs**

- Modify: `apps/api/app/services/artifacts.py`.
- `collect_artifact_refs()` must find artifact refs nested inside DatasetRefs.
- `persist_artifact_refs()` must persist/rehome dataset artifacts too.
- Tests: API run with dataset output creates Artifact DB row.

**Task 5.2: Add artifact download/export awareness**

- Existing artifact download should work because DatasetRef embeds ArtifactRef.
- Add dataset-specific route only if needed for preview/schema download.

**Task 5.3: Remote runner artifact upload path**

- Inspect current remote dispatch runner artifact path before implementing.
- Ensure remote dataset artifacts are uploaded/re-homed just like normal artifacts.
- Tests may need mocked remote runner.

**Task 5.4: Runtime settings**

- Modify `apps/api/app/config.py` and schemas if needed.
- Add settings:
  - `max_inline_dataset_rows`
  - `max_inline_dataset_bytes`
  - `dataset_preview_rows`
  - `dataset_to_records_max_rows`
  - `duckdb_memory_limit`
  - `duckdb_threads`
  - `dataset_node_timeout_seconds`
- Expose in settings UI if appropriate.

### Phase 6: UI dataset experience

**Task 6.1: Add dataset value utilities**

- Create: `apps/web/src/editor/datasetValues.ts`.
- Detect DatasetRef marker.
- Format row/column/file size/schema.

**Task 6.2: Render DatasetRef cards in PortDataViewer**

- Modify: `apps/web/src/editor/PortDataViewer.tsx`.
- Dataset card shows:
  - Dataset badge
  - name/format
  - row count, column count, file size
  - schema preview
  - row preview
  - download artifact link
  - “Use Dataset To Records to extract rows” hint

**Task 6.3: Port kind visuals**

- Modify: `NodeCard.tsx` and styles.
- Dataset ports visually distinct from artifact/control ports.
- Tooltip shows port kind.

**Task 6.4: Node palette recommendations**

- Modify: `NodePalette.tsx` suggestions so DatasetRef-producing nodes suggest dataset transforms and exports.

**Task 6.5: Code output ports editor**

- Add a focused UI for Code output ports rather than asking users to edit raw graph JSON.
- Ensure handles update immediately when ports change.

### Phase 7: Documentation and examples

**Task 7.1: Update README/HANDOFF/docs**

- Explain new value model.
- Explain no inline table behavior by default.
- Explain Dataset To Records cap.

**Task 7.2: Add example workflows**

Examples:

1. CSV -> Filter -> Sort -> Export CSV
2. CSV -> Join with another CSV -> Group By -> Preview
3. Code outputs `clean`, `rejected`, `summary` with clean/rejected as datasets.
4. API response -> Records To Dataset -> DuckDB SQL.

**Task 7.3: Migration note, despite no users**

- Keep this short.
- State old CSV Parse/Write semantics intentionally changed before product launch.

### Phase 8: Validation suite

Run focused tests first:

```bash
uv run pytest packages/core/tests/test_datasets.py -q
uv run pytest packages/core/tests/test_serialization.py -q
uv run pytest packages/core/tests/test_engine.py -q
uv run pytest packages/nodes/tests/test_transform_extra.py -q
uv run pytest packages/nodes/tests/test_builtin_nodes.py -q
uv run pytest apps/api/tests/test_artifacts.py -q
```

Then broader tests:

```bash
uv run pytest packages/core packages/nodes apps/api/tests/test_artifacts.py apps/api/tests/test_runs.py -q
```

Frontend checks:

```bash
cd apps/web
npm test -- --run
npm run build
```

If package manager differs, inspect `apps/web/package.json` first.

---

## Acceptance criteria

- `csv_parse` returns a `DatasetRef`, not `list[dict]`.
- `csv_write` returns an `ArtifactRef`, not a giant CSV string.
- Dataset refs are JSON-compatible, restorable as refs, and not truncated into unusable blobs.
- Dataset transform nodes can filter, sort, join, group, dedupe, and union using DuckDB without materializing full Python row lists.
- Parquet is the default internal table artifact format.
- Dataset preview and dataset-to-records require caps and never accidentally emit millions of rows inline.
- Code node can produce multiple named output ports using `outputs = {...}`.
- Code node can emit DataFrame/list[dict]/bytes/text and auto-promote heavy data to `DatasetRef`/`ArtifactRef` before output caps are applied.
- UI renders dataset cards and code multiple output handles.
- Artifact persistence/retention sees artifacts nested inside DatasetRefs.
- Tests cover edge cases listed above, especially malformed CSV, empty datasets, join key errors, extraction caps, and Code multi-output.

---

## Risks and tradeoffs

1. **Code process isolation vs artifact writing**
   - Code currently runs in a process pool. Large DataFrames returned to parent may be expensive to pickle.
   - Mitigation: implement parent-side auto-promotion first, then add in-process dataset writer helper for truly large Code outputs.

2. **DuckDB dependency placement**
   - If core helpers import DuckDB, core depends on DuckDB.
   - Mitigation: keep DuckDB imports lazy or put SQL-specific helpers in nodes package.

3. **Artifact caps are currently file-oriented**
   - Large datasets may exceed default 50 MiB artifact cap.
   - Mitigation: expose settings and set development defaults high enough for realistic datasets.

4. **UI complexity for Code ports**
   - Dynamic handles can break existing edges when renamed.
   - Mitigation: warn before removing/renaming ports with connected edges.

5. **SQL/path safety**
   - DuckDB can read files if allowed.
   - Mitigation: restrict SQL node to registered dataset refs initially; block multi-statement SQL and direct filesystem reads unless explicitly enabled later.

6. **No backward compatibility**
   - Existing tests expecting list CSV outputs will fail and must be updated.
   - This is intended because there are no current product users.

---

## Open questions to decide before implementation

1. Should local file path reads be allowed in dataset nodes, or only artifacts/uploaded text initially?
   - Safer v1: only text/artifact refs. Add path reads later with sandbox policy.
2. Should `sort`/`filter` existing inline nodes be repurposed or should dataset nodes be explicitly named `dataset_sort`, `dataset_filter`?
   - Clean UX: one node can detect DatasetRef vs inline for simple cases, but contracts are clearer with dedicated Dataset category nodes.
3. Should Code `output_ports` be stored as a param, `outputs_override`, or both?
   - Recommended: UI edits a Code-only param but store canonical runtime ports in `outputs_override`.
4. Should `DatasetRef` embed full preview rows or only a preview artifact/id?
   - Recommended: embed small preview rows capped to 100 for UX.
5. Should `DatasetRef` be a typed Pydantic model or plain dict envelope?
   - Recommended: Pydantic helpers internally, plain dict at runtime boundaries.

---

## Suggested implementation order

1. Core `DatasetRef` helpers + serialization tests.
2. Artifact file-write helper for non-materialized Parquet writes.
3. DuckDB dataset nodes for CSV -> Parquet -> preview.
4. Replace `csv_parse`/`csv_write` semantics.
5. Add filter/sort/select/limit.
6. Add join/group/dedupe/union.
7. Add Code multi-output UI/runtime.
8. Add auto-promotion.
9. API artifact persistence for nested DatasetRefs.
10. UI dataset cards.
11. Docs/examples.

This order gets a vertical slice working early: **CSV in -> DatasetRef -> preview/export** before the more complex transform and Code behavior.
