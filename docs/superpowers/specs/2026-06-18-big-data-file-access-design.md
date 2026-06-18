# Big-Data File Access — Design Spec

**Date:** 2026-06-18  
**Status:** Approved

## Overview

Four complementary mechanisms that give Noodle users access to files of any size, from any location, without being limited to the 50 MB browser-upload cap or requiring a filesystem explorer UI.

| Feature | Best for | Size limit |
|---|---|---|
| Volume-mounted data dir | Self-hosted, local big files | None |
| S3-compatible read node | Cloud/remote object storage | Practical: object download fits in memory |
| HTTP URL read node | Public datasets, pre-signed URLs | Practical: response fits in memory |
| Stream Large File node | Very large local files with progress | None (DuckDB streams) |

---

## Feature 1 — Volume-mounted data directory

### Goal

Self-hosters drop files on the host machine; nodes reference them via the existing `path` param at a known in-container path. No code changes to node logic.

### Changes

**`deploy/docker-compose.yml`**
- Add a commented-out `./data:/app/data` bind-mount to **both** `api` and `worker` services.
- Add a commented-out `DATA_DIR: /app/data` env var to both services.
- Both services get the mount: `api` handles artifact uploads, `worker` executes nodes.

**`deploy/data/.gitkeep`**
- Empty placeholder so the host-side directory is git-tracked and exists before first `docker compose up`.

**`packages/nodes/noodle_nodes/file_nodes.py`**
- Update `path` param `description` on all four existing file-reading nodes to include: *"When the data volume is mounted, files placed in `./data/` on the host are available at `/app/data/yourfile.csv`."*

### What stays the same

The `path` param (already in the Advanced group) handles everything. No new nodes, no new API surface.

---

## Feature 2 — S3-compatible read node (`read_s3_file`)

### Goal

Read CSV, JSON, Parquet, or text files directly from S3-compatible object storage (AWS S3, MinIO, Cloudflare R2, Backblaze B2). Credentials stored encrypted in the credential manager.

### New credential type

Added to `apps/api/app/services/credential_types.py` in the `_TYPES` tuple:

| Field | Key | Secret | Required | Notes |
|---|---|---|---|---|
| Access Key ID | `access_key_id` | No | Yes | |
| Secret Access Key | `secret_access_key` | Yes | Yes | |
| Endpoint URL | `endpoint_url` | No | No | Blank = AWS S3; `http://minio:9000` for local MinIO |
| Region | `region` | No | No | Defaults to `us-east-1` |

ID: `s3_compatible` · Provider: `Storage` · Auth method: `api_key`

### New node

**ID:** `read_s3_file` · **Category:** Files · **Icon:** `cloud`

**Params:**

| Param | Type | Notes |
|---|---|---|
| `credentials` | credential | `cred_multi("s3_compatible", ...)` — picker resolves to `{field: value}` dict |
| `bucket` | str | S3 bucket name |
| `key` | str | Object key, e.g. `data/sales.csv` |
| `format` | str | Choices: `csv`, `json`, `parquet`, `text` |
| `delimiter` | str | CSV only; shown via `display_when: {format: csv}` |
| `has_header` | bool | CSV only; shown via `display_when: {format: csv}` |
| `output_as_dataset` | bool | Toggle; drives `param_output_kinds` dataset/any |

**Logic:**
1. Build `boto3.client("s3", ...)` from the credential dict. Empty `endpoint_url` → omit for AWS. Signature version always `s3v4`.
2. `s3.get_object(Bucket=bucket, Key=key)["Body"].read()` — downloads full object bytes.
3. Parse by format:
   - `csv` → `csv_parse(text, delimiter, has_header)` (dataset) or raw dict (raw)
   - `json` → `json.loads(bytes)` → `records_to_dataset(data)` (dataset) or `{"data": data}` (raw)
   - `parquet` → bytes written to `tempfile.NamedTemporaryFile`, DuckDB `read_parquet` → `reserve_artifact_path` → DatasetRef
   - `text` → `{"text": bytes.decode("utf-8"), "filename": filename}`

**Dependencies:**
- `requirements=("boto3",)` on `@node` decorator
- Add `[project.optional-dependencies] storage = ["boto3>=1.34"]` to `packages/nodes/pyproject.toml`

---

## Feature 3 — HTTP URL read node (`read_url_file`)

### Goal

Download and parse a file from any URL. Covers public datasets, pre-signed S3 URLs, GitHub raw, government portals. Custom headers handle bearer-token auth without a full credential entry.

### New node

**ID:** `read_url_file` · **Category:** Files · **Icon:** `link`

**Params:**

| Param | Type | Notes |
|---|---|---|
| `url` | str | Full URL including scheme |
| `format` | str | Choices: `auto`, `csv`, `json`, `parquet`, `text` |
| `request_headers` | str | Optional JSON object string; widget: `textarea`. e.g. `{"Authorization": "Bearer sk-…"}` |
| `delimiter` | str | CSV; `display_when: {format: [csv, auto]}` |
| `has_header` | bool | CSV; `display_when: {format: [csv, auto]}` |
| `output_as_dataset` | bool | Same dataset/any toggle |

**Format auto-detection (when `format == "auto"`):**
1. Parse URL path, extract extension (strip query strings): `.csv`, `.json`/`.jsonl`/`.ndjson`, `.parquet`, `.txt`
2. Fall back to response `Content-Type` header
3. Default to `text` if unresolved

**Logic:**
1. `requests.get(url, headers=parsed_headers, timeout=60)` — raise on HTTP error codes.
2. Extract `filename` from URL path (strip query string).
3. Detect format (see above).
4. Parse using same per-format logic as `read_s3_file`.

**Dependencies:** None new — `requests` is already a base dep.

---

## Feature 4 — Stream Large File node (`stream_large_file`)

### Goal

Read arbitrarily large CSV or JSONL files with live progress emitted to the editor canvas. The primary use case is server-path files that exceed the upload size cap. Always outputs DatasetRef.

### New node

**ID:** `stream_large_file` · **Category:** Files · **Icon:** `database`

**Params:**

| Param | Type | Notes |
|---|---|---|
| `file` | str | widget: `file_upload`; browser upload source |
| `path` | str | advanced: True; server path (main use case for very large files) |
| `format` | str | Choices: `csv`, `jsonl` |
| `delimiter` | str | CSV only; `display_when: {format: csv}` |
| `has_header` | bool | CSV only; `display_when: {format: csv}` |

No `output_as_dataset` toggle — always produces DatasetRef. Raw mode is not applicable to a streaming node.

**Logic:**
1. Source resolution:
   - `file` → `_read_upload_bytes(file)` → write to `tempfile.NamedTemporaryFile(suffix=".csv"/.jsonl)` so DuckDB gets a real path.
   - `path` → use directly.
2. `emit_chunk(f"Reading {filename}…")`
3. DuckDB executes one streaming query:
   - CSV: `COPY (SELECT * FROM read_csv_auto(path, delim=..., header=...)) TO parquet_path (FORMAT PARQUET, COMPRESSION ZSTD)`
   - JSONL: `COPY (SELECT * FROM read_ndjson(path)) TO parquet_path (FORMAT PARQUET, COMPRESSION ZSTD)`
   - `parquet_path` comes from `reserve_artifact_path` (same as `csv_parse`)
4. `emit_chunk("Indexing…")`
5. `SELECT COUNT(*) FROM read_parquet(parquet_path)` → `row_count`
6. `emit_chunk(f"Done — {row_count:,} rows")`
7. Temp file cleaned up in `finally`.
8. Return DatasetRef envelope (same schema as `csv_parse` output).

**Dependencies:** DuckDB (already a base dep). No pandas required.

### Key differentiator from `read_csv_file(output_as_dataset=True)`

- Handles files of any size via `path` (no upload size cap).
- JSONL format support.
- Live progress visible on the canvas during long reads.

---

## Files Changed

| File | Change |
|---|---|
| `deploy/docker-compose.yml` | Add commented data volume + env var to api + worker |
| `deploy/data/.gitkeep` | New — host-side data dir placeholder |
| `apps/api/app/services/credential_types.py` | Add `s3_compatible` credential type to `_TYPES` |
| `packages/nodes/noodle_nodes/file_nodes.py` | Update path descriptions; add `read_s3_file`, `read_url_file`, `stream_large_file` |
| `packages/nodes/pyproject.toml` | Add `[storage]` optional dep group with `boto3>=1.34` |

## Tests

- `packages/nodes/tests/test_file_nodes.py` — add tests for all three new nodes:
  - `read_s3_file`: mock boto3 `get_object`; test csv/json/parquet/text formats; test empty endpoint → AWS config; test bad bucket raises
  - `read_url_file`: mock `requests.get`; test auto-detection from URL extension and Content-Type; test custom headers forwarded; test HTTP error raises; test all formats
  - `stream_large_file`: use `tmp_path` fixture; test csv and jsonl; test upload path (bytes → temp file); verify emit_chunk calls; verify DatasetRef output; test missing source raises
