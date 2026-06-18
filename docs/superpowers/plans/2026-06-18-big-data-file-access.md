# Big-Data File Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four complementary big-data access mechanisms — volume-mounted data directory, S3-compatible read node, HTTP URL read node, and streaming large-file node — without breaking existing file-reading nodes.

**Architecture:** All three new nodes live in `packages/nodes/noodle_nodes/file_nodes.py` alongside the existing four. A new private helper `_parse_file_bytes` is extracted to share CSV/JSON/Parquet parsing logic between `read_s3_file` and `read_url_file`. The `stream_large_file` node uses DuckDB directly for true memory-efficient streaming. The S3 credential type is registered in the API's credential registry.

**Tech Stack:** Python, boto3 (S3), requests (HTTP), DuckDB (streaming + Parquet), pyarrow, `noodle_nodes.datasets._finalize_parquet` + `noodle.artifacts.reserve_artifact_path` (DatasetRef construction).

## Global Constraints

- All nodes go in `packages/nodes/noodle_nodes/file_nodes.py` — do not create new files for nodes
- Tests go in `packages/nodes/tests/test_file_nodes.py` — extend the existing file
- Tests that produce real DatasetRef output need the `store_ctx` fixture (defined in `test_datasets.py` — copy it into `test_file_nodes.py`)
- `output_as_dataset=True` always produces a DatasetRef; `False` produces a raw dict
- For Parquet format, `output_as_dataset` is ignored — always produces DatasetRef
- `stream_large_file` never has an `output_as_dataset` param — always DatasetRef
- Run tests: `uv run --directory packages/nodes python -m pytest tests/test_file_nodes.py -q`
- `reserve_artifact_path` is imported from `noodle.artifacts` (noodle-core package)
- `_finalize_parquet` is imported from `noodle_nodes.datasets` (same package)
- `emit_chunk` is imported from `noodle.context`
- Credential type `s3_compatible` must be added to `_TYPES` in `apps/api/app/services/credential_types.py`

---

### Task 1: Volume-mounted data directory

**Files:**
- Modify: `deploy/docker-compose.yml`
- Create: `deploy/data/.gitkeep`
- Modify: `packages/nodes/noodle_nodes/file_nodes.py` (path descriptions only)

**Interfaces:**
- Produces: nothing new — infrastructure + documentation change only

- [ ] **Step 1: Create the host-side data directory placeholder**

```bash
# Run from repo root
mkdir -p deploy/data
echo "" > deploy/data/.gitkeep
```

- [ ] **Step 2: Add commented data volume to `api` service in docker-compose.yml**

In `deploy/docker-compose.yml`, find the `api` service `volumes:` block (currently ends at `artifactdata:/app/artifacts`) and replace it:

```yaml
    volumes:
      - envdata:/app/envs
      - artifactdata:/app/artifacts
      # User data directory: uncomment both lines below to expose a host folder to nodes.
      # Files placed in ./data/ on the host are then available at /app/data/ in the container.
      # - ./data:/app/data
```

Also add the commented env var to the `api` service `environment:` block, after `ARTIFACTS_DIR`:

```yaml
      ARTIFACTS_DIR: /app/artifacts
      # DATA_DIR: /app/data
```

- [ ] **Step 3: Add same commented volume + env var to `worker` service**

In the `worker` service `volumes:` block (currently ends before `# Required for EXECUTION_SANDBOX`), replace:

```yaml
    volumes:
      - envdata:/app/envs
      - artifactdata:/app/artifacts
      # User data directory: uncomment to expose a host folder to nodes.
      # - ./data:/app/data
      # Required for EXECUTION_SANDBOX: lets the worker spawn sibling run
```

Also add in the `worker` `environment:` block after `ARTIFACTS_DIR`:

```yaml
      ARTIFACTS_DIR: /app/artifacts
      # DATA_DIR: /app/data
```

- [ ] **Step 4: Update `path` param descriptions on all four existing nodes**

In `packages/nodes/noodle_nodes/file_nodes.py`, update the `"path"` param description in all four `@node` decorators (`read_text_file`, `read_csv_file`, `read_json_file`, `read_xml_file`) from:

```python
"description": "Absolute path on the server filesystem (power users / CI only).",
```

to:

```python
"description": (
    "Absolute path on the server filesystem. "
    "When the data volume is mounted, use /app/data/yourfile.ext. "
    "See deploy/docker-compose.yml for setup."
),
```

- [ ] **Step 5: Verify docker-compose is still valid YAML**

```bash
docker compose -f deploy/docker-compose.yml config --quiet
```

Expected: exits 0 (no output means valid).

- [ ] **Step 6: Commit**

```bash
git add deploy/docker-compose.yml deploy/data/.gitkeep packages/nodes/noodle_nodes/file_nodes.py
git commit -m "feat(deploy): add opt-in data volume mount for large local files"
```

---

### Task 2: S3-compatible credential type + boto3 dependency

**Files:**
- Modify: `apps/api/app/services/credential_types.py`
- Modify: `packages/nodes/pyproject.toml`

**Interfaces:**
- Produces: credential type `"s3_compatible"` available via `GET /credentials/types` and resolvable by nodes that declare `cred_multi("s3_compatible", ...)`

- [ ] **Step 1: Write failing test for credential type existence**

In `apps/api/tests/` (find an appropriate existing test file or use `test_credentials.py`):

```bash
# Find existing credential type tests
grep -rn "s3_compatible\|list_credential_types" apps/api/tests/ | head -5
```

Add to the appropriate test file (or create `apps/api/tests/test_credential_types.py`):

```python
from app.services.credential_types import get_credential_type, list_credential_types


def test_s3_compatible_credential_type_exists() -> None:
    spec = get_credential_type("s3_compatible")
    assert spec is not None
    assert spec.provider == "Storage"
    field_keys = {f.key for f in spec.fields}
    assert field_keys == {"access_key_id", "secret_access_key", "endpoint_url", "region"}


def test_s3_compatible_endpoint_url_not_required() -> None:
    spec = get_credential_type("s3_compatible")
    endpoint_field = next(f for f in spec.fields if f.key == "endpoint_url")
    assert not endpoint_field.required


def test_s3_compatible_secret_key_is_secret() -> None:
    spec = get_credential_type("s3_compatible")
    secret_field = next(f for f in spec.fields if f.key == "secret_access_key")
    assert secret_field.secret is True


def test_s3_compatible_access_key_not_secret() -> None:
    spec = get_credential_type("s3_compatible")
    field = next(f for f in spec.fields if f.key == "access_key_id")
    assert field.secret is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/api && uv run python -m pytest tests/test_credential_types.py -q 2>&1 | head -20
```

Expected: FAIL with `AssertionError` (spec is None — type doesn't exist yet).

- [ ] **Step 3: Add `s3_compatible` to `_TYPES` in credential_types.py**

In `apps/api/app/services/credential_types.py`, find the `_TYPES: tuple[CredentialTypeSpec, ...] = (` tuple and add this entry (place it near the end, before the closing parenthesis):

```python
    CredentialTypeSpec(
        id="s3_compatible",
        name="S3-Compatible Storage",
        provider="Storage",
        auth_method="api_key",
        fields=[
            CredentialFieldSpec(
                key="access_key_id",
                label="Access Key ID",
                secret=False,
                placeholder="AKIAIOSFODNN7EXAMPLE",
            ),
            CredentialFieldSpec(
                key="secret_access_key",
                label="Secret Access Key",
                placeholder="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            ),
            CredentialFieldSpec(
                key="endpoint_url",
                label="Endpoint URL",
                secret=False,
                required=False,
                placeholder="https://s3.amazonaws.com",
                help="Leave blank for AWS S3. Set to e.g. http://minio:9000 for MinIO.",
            ),
            CredentialFieldSpec(
                key="region",
                label="Region",
                secret=False,
                required=False,
                placeholder="us-east-1",
            ),
        ],
    ),
```

- [ ] **Step 4: Add boto3 to nodes pyproject.toml**

In `packages/nodes/pyproject.toml`, after the existing `[project.optional-dependencies]` section (the `ml` group), add:

```toml
storage = [
    "boto3>=1.34",
]
```

So the full optional-dependencies section becomes:

```toml
[project.optional-dependencies]
ml = [
    "scikit-learn>=1.4",
    "pandas>=2.0",
    "numpy>=1.26",
    "joblib>=1.3",
]
storage = [
    "boto3>=1.34",
]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd apps/api && uv run python -m pytest tests/test_credential_types.py -q 2>&1 | head -20
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/credential_types.py apps/api/tests/test_credential_types.py packages/nodes/pyproject.toml
git commit -m "feat(credentials): add s3_compatible credential type and boto3 optional dep"
```

---

### Task 3: `_parse_file_bytes` helper + `read_s3_file` node

**Files:**
- Modify: `packages/nodes/noodle_nodes/file_nodes.py`
- Modify: `packages/nodes/tests/test_file_nodes.py`

**Interfaces:**
- Produces:
  - `_parse_file_bytes(content_bytes, *, filename, fmt, delimiter, has_header, output_as_dataset) -> Any` — private helper used by `read_s3_file` and `read_url_file`
  - `read_s3_file(input, credentials, bucket, key, format, delimiter, has_header, output_as_dataset) -> Any` — registered node

- [ ] **Step 1: Add `store_ctx` fixture to test_file_nodes.py**

At the top of `packages/nodes/tests/test_file_nodes.py`, add these imports and fixture (needed for tests that produce real DatasetRef via reserve_artifact_path):

```python
import pytest
from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, current_node_id


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="test-run")
    tok_a = artifact_store.set(store)
    tok_n = current_node_id.set("test-node")
    yield store
    current_node_id.reset(tok_n)
    artifact_store.reset(tok_a)
```

Check if `pytest` and `LocalArtifactStore` are already imported — if so, skip duplicates.

- [ ] **Step 2: Write failing tests for `read_s3_file`**

Append to `packages/nodes/tests/test_file_nodes.py`:

```python
# ---------------------------------------------------------------------------
# read_s3_file
# ---------------------------------------------------------------------------

import io
import json as json_mod
from unittest.mock import MagicMock, patch


def _make_s3_creds(endpoint: str = "") -> dict:
    return {
        "access_key_id": "AKIATEST",
        "secret_access_key": "secrettest",
        "endpoint_url": endpoint,
        "region": "us-east-1",
    }


def _mock_boto3_client(content_bytes: bytes):
    """Return a mock boto3 client whose get_object yields content_bytes."""
    body = MagicMock()
    body.read.return_value = content_bytes
    client = MagicMock()
    client.get_object.return_value = {"Body": body}
    return client


def test_read_s3_csv_raw_mode() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    csv_bytes = b"name,age\nAlice,30\nBob,25\n"
    mock_client = _mock_boto3_client(csv_bytes)
    with patch("boto3.client", return_value=mock_client):
        result = read_s3_file(
            input=None,
            credentials=_make_s3_creds(),
            bucket="my-bucket",
            key="data/people.csv",
            format="csv",
            output_as_dataset=False,
        )
    assert result["row_count"] == 2
    assert result["rows"][0] == {"name": "Alice", "age": "30"}
    assert result["filename"] == "people.csv"


def test_read_s3_csv_as_dataset() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    fake_ref = {"__noodle_dataset__": True}
    csv_bytes = b"name,age\nAlice,30\n"
    mock_client = _mock_boto3_client(csv_bytes)
    with patch("boto3.client", return_value=mock_client):
        with patch("noodle_nodes.file_nodes.csv_parse", return_value=fake_ref) as mock_parse:
            result = read_s3_file(
                input=None,
                credentials=_make_s3_creds(),
                bucket="b",
                key="f.csv",
                format="csv",
                output_as_dataset=True,
            )
    assert result is fake_ref
    mock_parse.assert_called_once()


def test_read_s3_json_raw_mode() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    data = [{"x": 1}, {"x": 2}]
    json_bytes = json_mod.dumps(data).encode()
    mock_client = _mock_boto3_client(json_bytes)
    with patch("boto3.client", return_value=mock_client):
        result = read_s3_file(
            input=None,
            credentials=_make_s3_creds(),
            bucket="b",
            key="data.json",
            format="json",
            output_as_dataset=False,
        )
    assert result["data"] == data
    assert result["filename"] == "data.json"


def test_read_s3_json_as_dataset() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    fake_ref = {"__noodle_dataset__": True}
    data = [{"x": 1}, {"x": 2}]
    json_bytes = json_mod.dumps(data).encode()
    mock_client = _mock_boto3_client(json_bytes)
    with patch("boto3.client", return_value=mock_client):
        with patch("noodle_nodes.file_nodes._parse_file_bytes") as mock_parse:
            mock_parse.return_value = fake_ref
            result = read_s3_file(
                input=None,
                credentials=_make_s3_creds(),
                bucket="b",
                key="data.json",
                format="json",
                output_as_dataset=True,
            )
    assert result is fake_ref


def test_read_s3_text_format() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    mock_client = _mock_boto3_client(b"hello world")
    with patch("boto3.client", return_value=mock_client):
        result = read_s3_file(
            input=None,
            credentials=_make_s3_creds(),
            bucket="b",
            key="notes.txt",
            format="text",
            output_as_dataset=False,
        )
    assert result["text"] == "hello world"
    assert result["filename"] == "notes.txt"


def test_read_s3_empty_endpoint_passes_none_to_boto3() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    mock_client = _mock_boto3_client(b"a,b\n1,2\n")
    with patch("boto3.client", return_value=mock_client) as mock_boto3:
        with patch("noodle_nodes.file_nodes.csv_parse", return_value={}):
            read_s3_file(
                input=None,
                credentials=_make_s3_creds(endpoint=""),
                bucket="b",
                key="f.csv",
                format="csv",
                output_as_dataset=True,
            )
    call_kwargs = mock_boto3.call_args.kwargs
    assert call_kwargs["endpoint_url"] is None


def test_read_s3_parquet_as_dataset(store_ctx, tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from noodle.datasets import is_dataset_ref
    from noodle_nodes.file_nodes import read_s3_file

    # Write a small real Parquet file to get valid bytes
    parquet_file = tmp_path / "sample.parquet"
    table = pa.table({"col1": [1, 2, 3], "col2": ["a", "b", "c"]})
    pq.write_table(table, str(parquet_file))
    parquet_bytes = parquet_file.read_bytes()

    mock_client = _mock_boto3_client(parquet_bytes)
    with patch("boto3.client", return_value=mock_client):
        result = read_s3_file(
            input=None,
            credentials=_make_s3_creds(),
            bucket="b",
            key="data/sample.parquet",
            format="parquet",
            output_as_dataset=True,
        )
    assert is_dataset_ref(result)
    assert result["row_count"] == 3
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run --directory packages/nodes python -m pytest tests/test_file_nodes.py -k "read_s3" -q 2>&1 | head -20
```

Expected: FAIL with `ImportError` or `cannot import name 'read_s3_file'`.

- [ ] **Step 4: Add `_creds` import and `_parse_file_bytes` helper to file_nodes.py**

At the top of `packages/nodes/noodle_nodes/file_nodes.py`, add:

```python
from noodle_nodes._creds import cred_multi
```

After the existing `_DATASET_TOGGLE = ...` line, add the shared parsing helper:

```python
def _parse_file_bytes(
    content_bytes: bytes,
    *,
    filename: str,
    fmt: str,
    delimiter: str,
    has_header: bool,
    output_as_dataset: bool,
) -> Any:
    """Parse raw bytes into the appropriate output format.

    Shared between read_s3_file and read_url_file.
    fmt must be one of: csv, json, parquet, text.
    """
    import io as _io
    import json as _json

    if fmt == "csv":
        text = content_bytes.decode("utf-8")
        if output_as_dataset:
            return csv_parse(input=None, text=text, delimiter=delimiter or ",", has_header=has_header)
        import csv as _csv

        reader = _csv.reader(_io.StringIO(text), delimiter=delimiter or ",")
        all_rows = list(reader)
        if not all_rows:
            return {"rows": [], "filename": filename, "row_count": 0}
        if has_header:
            headers = all_rows[0]
            data_rows = all_rows[1:]
            dicts = [dict(zip(headers, row, strict=False)) for row in data_rows]
        else:
            dicts = [dict(enumerate(row)) for row in all_rows]
        return {"rows": dicts, "filename": filename, "row_count": len(dicts)}

    if fmt == "json":
        data = _json.loads(content_bytes.decode("utf-8"))
        if output_as_dataset:
            from noodle_nodes.datasets import records_to_dataset

            if not isinstance(data, list):
                raise ValueError(
                    "JSON output_as_dataset requires the root to be an array of objects; "
                    f"got {type(data).__name__}"
                )
            return records_to_dataset(data)
        return {"data": data, "filename": filename}

    if fmt == "parquet":
        import os
        import tempfile

        import duckdb as _duckdb_mod
        from noodle.artifacts import reserve_artifact_path
        from noodle_nodes.datasets import _finalize_parquet

        tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
        try:
            tmp.write(content_bytes)
            tmp.close()
            out_path, out_partial = reserve_artifact_path(
                filename or "dataset.parquet",
                content_type="application/vnd.apache.parquet",
                kind="dataset",
            )
            src_sql = tmp.name.replace("'", "''")
            out_sql = str(out_path).replace("'", "''")
            con = _duckdb_mod.connect(":memory:")
            con.execute(
                f"COPY (SELECT * FROM read_parquet('{src_sql}')) "
                f"TO '{out_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            return _finalize_parquet(out_path, out_partial)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    # text / fallback
    return {"text": content_bytes.decode("utf-8", errors="replace"), "filename": filename}
```

- [ ] **Step 5: Add `read_s3_file` node to file_nodes.py**

Append after the last existing node (`read_xml_file`):

```python
@node(
    name="Read S3 File",
    category="Files",
    description=(
        "Read a CSV, JSON, Parquet, or text file from S3-compatible storage "
        "(AWS S3, MinIO, Cloudflare R2, Backblaze B2)."
    ),
    icon="cloud",
    requirements=("boto3",),
    param_output_kinds={"main": _DATASET_TOGGLE},
    params={
        "credentials": cred_multi(
            "s3_compatible",
            "S3-compatible storage",
            ["access_key_id", "secret_access_key", "endpoint_url", "region"],
        ),
        "bucket": {"description": "S3 bucket name."},
        "key": {
            "description": "Object key (path in bucket), e.g. data/sales.csv.",
            "placeholder": "data/sales.csv",
        },
        "format": {
            "choices": ["csv", "json", "parquet", "text"],
            "description": "File format.",
        },
        "delimiter": {
            "display_when": {"format": "csv"},
            "description": "CSV delimiter character.",
        },
        "has_header": {
            "display_when": {"format": "csv"},
            "description": "Whether the first row is a header.",
        },
        "output_as_dataset": {
            "description": (
                "Return a DatasetRef (Parquet) for CSV and JSON formats. "
                "Parquet format always returns DatasetRef regardless of this setting."
            ),
        },
    },
)
def read_s3_file(
    input: Any,
    credentials: Any = None,
    bucket: str = "",
    key: str = "",
    format: str = "csv",
    delimiter: str = ",",
    has_header: bool = True,
    output_as_dataset: bool = True,
) -> Any:
    import boto3
    import boto3.session

    creds = credentials if isinstance(credentials, dict) else {}
    endpoint = creds.get("endpoint_url") or None
    region = creds.get("region") or "us-east-1"

    client = boto3.client(
        "s3",
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        endpoint_url=endpoint,
        region_name=region,
        config=boto3.session.Config(signature_version="s3v4"),
    )
    obj = client.get_object(Bucket=bucket, Key=key)
    content_bytes: bytes = obj["Body"].read()
    filename = (key or "").rstrip("/").split("/")[-1] or "download"

    return _parse_file_bytes(
        content_bytes,
        filename=filename,
        fmt=format,
        delimiter=delimiter,
        has_header=has_header,
        output_as_dataset=output_as_dataset,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run --directory packages/nodes python -m pytest tests/test_file_nodes.py -k "read_s3" -q 2>&1 | head -30
```

Expected: 7 passed.

- [ ] **Step 7: Run full file-nodes test suite to verify no regressions**

```bash
uv run --directory packages/nodes python -m pytest tests/test_file_nodes.py -q 2>&1 | tail -5
```

Expected: all previously passing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add packages/nodes/noodle_nodes/file_nodes.py packages/nodes/tests/test_file_nodes.py
git commit -m "feat(nodes): add read_s3_file node with S3-compatible storage support"
```

---

### Task 4: `read_url_file` node

**Files:**
- Modify: `packages/nodes/noodle_nodes/file_nodes.py`
- Modify: `packages/nodes/tests/test_file_nodes.py`

**Interfaces:**
- Consumes: `_parse_file_bytes` from Task 3
- Produces: `read_url_file(input, url, format, request_headers, delimiter, has_header, output_as_dataset) -> Any`

- [ ] **Step 1: Write failing tests for `read_url_file`**

Append to `packages/nodes/tests/test_file_nodes.py`:

```python
# ---------------------------------------------------------------------------
# read_url_file
# ---------------------------------------------------------------------------


def _mock_response(content: bytes, content_type: str = "application/octet-stream", status: int = 200):
    resp = MagicMock()
    resp.content = content
    resp.headers = {"Content-Type": content_type}
    resp.raise_for_status = MagicMock()
    if status >= 400:
        import requests as _req
        resp.raise_for_status.side_effect = _req.HTTPError(f"{status}")
    return resp


def test_read_url_csv_auto_detect_by_extension() -> None:
    from noodle_nodes.file_nodes import read_url_file

    fake_ref = {"__noodle_dataset__": True}
    with patch("requests.get", return_value=_mock_response(b"a,b\n1,2\n")):
        with patch("noodle_nodes.file_nodes.csv_parse", return_value=fake_ref) as mock_parse:
            result = read_url_file(
                input=None,
                url="https://example.com/data.csv",
                format="auto",
                output_as_dataset=True,
            )
    assert result is fake_ref
    mock_parse.assert_called_once()


def test_read_url_csv_auto_detect_by_content_type() -> None:
    from noodle_nodes.file_nodes import read_url_file

    fake_ref = {"__noodle_dataset__": True}
    resp = _mock_response(b"x,y\n1,2\n", content_type="text/csv")
    with patch("requests.get", return_value=resp):
        with patch("noodle_nodes.file_nodes.csv_parse", return_value=fake_ref):
            result = read_url_file(
                input=None,
                url="https://example.com/download?token=abc",
                format="auto",
                output_as_dataset=True,
            )
    assert result is fake_ref


def test_read_url_text_format_explicit() -> None:
    from noodle_nodes.file_nodes import read_url_file

    with patch("requests.get", return_value=_mock_response(b"hello")):
        result = read_url_file(
            input=None,
            url="https://example.com/readme.txt",
            format="text",
            output_as_dataset=False,
        )
    assert result["text"] == "hello"
    assert result["filename"] == "readme.txt"


def test_read_url_custom_headers_forwarded() -> None:
    from noodle_nodes.file_nodes import read_url_file

    import json as _json

    with patch("requests.get", return_value=_mock_response(b"ok")) as mock_get:
        read_url_file(
            input=None,
            url="https://example.com/data.txt",
            format="text",
            request_headers=_json.dumps({"Authorization": "Bearer tok"}),
            output_as_dataset=False,
        )
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer tok"


def test_read_url_http_error_raises() -> None:
    from noodle_nodes.file_nodes import read_url_file
    import requests as _req

    with patch("requests.get", return_value=_mock_response(b"", status=403)):
        with pytest.raises(_req.HTTPError):
            read_url_file(input=None, url="https://example.com/private.csv", format="auto")


def test_read_url_invalid_headers_json_raises() -> None:
    from noodle_nodes.file_nodes import read_url_file

    with patch("requests.get", return_value=_mock_response(b"ok")):
        with pytest.raises(ValueError, match="request_headers must be valid JSON"):
            read_url_file(
                input=None,
                url="https://example.com/f.txt",
                format="text",
                request_headers="not-json",
            )


def test_read_url_missing_url_raises() -> None:
    from noodle_nodes.file_nodes import read_url_file

    with pytest.raises(ValueError, match="url must be provided"):
        read_url_file(input=None, url="", format="auto")


def test_read_url_json_auto_detect_by_extension() -> None:
    from noodle_nodes.file_nodes import read_url_file

    data = [{"id": 1}, {"id": 2}]
    json_bytes = json_mod.dumps(data).encode()
    fake_ref = {"__noodle_dataset__": True}
    with patch("requests.get", return_value=_mock_response(json_bytes)):
        with patch("noodle_nodes.file_nodes._parse_file_bytes", return_value=fake_ref) as mock_parse:
            result = read_url_file(
                input=None,
                url="https://example.com/records.json",
                format="auto",
                output_as_dataset=True,
            )
    assert result is fake_ref
    call_kwargs = mock_parse.call_args.kwargs
    assert call_kwargs["fmt"] == "json"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --directory packages/nodes python -m pytest tests/test_file_nodes.py -k "read_url" -q 2>&1 | head -10
```

Expected: FAIL with `cannot import name 'read_url_file'`.

- [ ] **Step 3: Add `read_url_file` node to file_nodes.py**

Append to `packages/nodes/noodle_nodes/file_nodes.py` (after `read_s3_file`):

```python
@node(
    name="Read URL File",
    category="Files",
    description=(
        "Download and parse a file from a URL. "
        "Supports CSV, JSON, Parquet, and plain text. "
        "Use request_headers for bearer-token auth."
    ),
    icon="link",
    param_output_kinds={"main": _DATASET_TOGGLE},
    params={
        "url": {
            "placeholder": "https://example.com/data.csv",
            "description": "Full URL to the file. Pre-signed S3 URLs work too.",
        },
        "format": {
            "choices": ["auto", "csv", "json", "parquet", "text"],
            "description": "'auto' detects format from URL extension or Content-Type header.",
        },
        "request_headers": {
            "display_name": "Request headers",
            "widget": "textarea",
            "description": 'Optional JSON object of HTTP headers, e.g. {"Authorization": "Bearer token"}.',
        },
        "delimiter": {"description": "CSV delimiter character."},
        "has_header": {"description": "Whether the first CSV row is a header."},
        "output_as_dataset": {
            "description": "Return a DatasetRef (Parquet) for CSV and JSON formats.",
        },
    },
)
def read_url_file(
    input: Any,
    url: str = "",
    format: str = "auto",
    request_headers: str = "",
    delimiter: str = ",",
    has_header: bool = True,
    output_as_dataset: bool = True,
) -> Any:
    import json as _json
    from urllib.parse import urlparse

    import requests as _requests

    if not url:
        raise ValueError("read_url_file: url must be provided")

    headers: dict[str, str] = {}
    if request_headers.strip():
        try:
            headers = _json.loads(request_headers)
        except _json.JSONDecodeError as exc:
            raise ValueError(
                f"read_url_file: request_headers must be valid JSON: {exc}"
            ) from exc

    resp = _requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    content_bytes: bytes = resp.content

    parsed_url = urlparse(url)
    filename = parsed_url.path.rstrip("/").split("/")[-1] or "download"

    detected = format
    if format == "auto":
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        ct = resp.headers.get("Content-Type", "").lower()
        if ext == "csv" or "text/csv" in ct or "application/csv" in ct:
            detected = "csv"
        elif ext in ("json", "jsonl", "ndjson") or "application/json" in ct:
            detected = "json"
        elif ext == "parquet" or "parquet" in ct:
            detected = "parquet"
        else:
            detected = "text"

    return _parse_file_bytes(
        content_bytes,
        filename=filename,
        fmt=detected,
        delimiter=delimiter,
        has_header=has_header,
        output_as_dataset=output_as_dataset,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --directory packages/nodes python -m pytest tests/test_file_nodes.py -k "read_url" -q 2>&1 | head -20
```

Expected: 8 passed.

- [ ] **Step 5: Run full suite to check for regressions**

```bash
uv run --directory packages/nodes python -m pytest tests/test_file_nodes.py -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/file_nodes.py packages/nodes/tests/test_file_nodes.py
git commit -m "feat(nodes): add read_url_file node with auto format detection"
```

---

### Task 5: `stream_large_file` node

**Files:**
- Modify: `packages/nodes/noodle_nodes/file_nodes.py`
- Modify: `packages/nodes/tests/test_file_nodes.py`

**Interfaces:**
- Consumes: `reserve_artifact_path` (noodle.artifacts), `_finalize_parquet` (noodle_nodes.datasets), `emit_chunk` (noodle.context), `_read_upload_bytes` from file_nodes.py
- Produces: `stream_large_file(input, file, path, format, delimiter, has_header) -> dict` — always a DatasetRef

- [ ] **Step 1: Write failing tests for `stream_large_file`**

Append to `packages/nodes/tests/test_file_nodes.py`:

```python
# ---------------------------------------------------------------------------
# stream_large_file
# ---------------------------------------------------------------------------

import textwrap


def test_stream_large_file_csv_from_path(store_ctx, tmp_path: Path) -> None:
    from noodle.datasets import is_dataset_ref
    from noodle_nodes.file_nodes import stream_large_file

    f = tmp_path / "big.csv"
    f.write_text("id,val\n1,a\n2,b\n3,c\n", encoding="utf-8")
    with patch("noodle.context.emit_chunk", return_value=None):
        result = stream_large_file(input=None, path=str(f), format="csv")
    assert is_dataset_ref(result)
    assert result["row_count"] == 3


def test_stream_large_file_jsonl_from_path(store_ctx, tmp_path: Path) -> None:
    from noodle.datasets import is_dataset_ref
    from noodle_nodes.file_nodes import stream_large_file

    f = tmp_path / "records.jsonl"
    f.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    with patch("noodle.context.emit_chunk", return_value=None):
        result = stream_large_file(input=None, path=str(f), format="jsonl")
    assert is_dataset_ref(result)
    assert result["row_count"] == 2


def test_stream_large_file_from_upload(store_ctx) -> None:
    from noodle.datasets import is_dataset_ref
    from noodle_nodes.file_nodes import stream_large_file

    csv_bytes = b"x,y\n10,20\n30,40\n"
    with patch(
        "noodle_nodes.file_nodes._read_upload_bytes",
        return_value=(csv_bytes, "upload.csv"),
    ):
        with patch("noodle.context.emit_chunk", return_value=None):
            result = stream_large_file(input=None, file="abc123", format="csv")
    assert is_dataset_ref(result)
    assert result["row_count"] == 2


def test_stream_large_file_emits_progress(store_ctx, tmp_path: Path) -> None:
    from noodle_nodes.file_nodes import stream_large_file

    f = tmp_path / "data.csv"
    f.write_text("col\n1\n2\n", encoding="utf-8")
    emitted: list[str] = []
    with patch("noodle.context.emit_chunk", side_effect=lambda msg: emitted.append(msg)):
        stream_large_file(input=None, path=str(f), format="csv")
    assert len(emitted) == 3
    assert any("Reading" in m for m in emitted)
    assert any("Indexing" in m for m in emitted)
    assert any("Done" in m for m in emitted)


def test_stream_large_file_no_source_raises(store_ctx) -> None:
    from noodle_nodes.file_nodes import stream_large_file

    with pytest.raises(ValueError, match="either path or file"):
        stream_large_file(input=None, file="", path="", format="csv")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --directory packages/nodes python -m pytest tests/test_file_nodes.py -k "stream_large" -q 2>&1 | head -10
```

Expected: FAIL with `cannot import name 'stream_large_file'`.

- [ ] **Step 3: Add `stream_large_file` node to file_nodes.py**

Append to `packages/nodes/noodle_nodes/file_nodes.py` (after `read_url_file`):

```python
@node(
    name="Stream Large File",
    category="Files",
    description=(
        "Read a very large CSV or JSONL file with live progress. "
        "Uses DuckDB streaming — no Python-level memory accumulation. "
        "Always outputs a DatasetRef (Parquet). "
        "When the data volume is mounted, set path to /app/data/yourfile.csv."
    ),
    icon="database",
    output_kinds={"main": "dataset"},
    params={
        "file": {
            "widget": "file_upload",
            "display_name": "Upload file",
            "description": "Upload from your browser. Saved and reused across runs.",
        },
        "path": {
            "advanced": True,
            "display_name": "Server path",
            "description": (
                "Absolute path on the server filesystem. "
                "Primary source for files too large to upload. "
                "When the data volume is mounted, use /app/data/yourfile.csv."
            ),
        },
        "format": {
            "choices": ["csv", "jsonl"],
            "description": "csv for comma-separated files; jsonl for newline-delimited JSON.",
        },
        "delimiter": {
            "display_when": {"format": "csv"},
            "description": "CSV delimiter character.",
        },
        "has_header": {
            "display_when": {"format": "csv"},
            "description": "Whether the first row is a header.",
        },
    },
)
def stream_large_file(
    input: Any,
    file: str = "",
    path: str = "",
    format: str = "csv",
    delimiter: str = ",",
    has_header: bool = True,
) -> Any:
    import os
    import tempfile

    import duckdb as _duckdb_mod
    from noodle.artifacts import reserve_artifact_path
    from noodle.context import emit_chunk
    from noodle_nodes.datasets import _finalize_parquet

    tmp_path_str: str | None = None
    try:
        if file:
            content_bytes, filename = _read_upload_bytes(file)
            suffix = ".jsonl" if format == "jsonl" else ".csv"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp.write(content_bytes)
            tmp.close()
            tmp_path_str = tmp.name
            src_str = tmp.name
        elif path:
            filename = pathlib.Path(path).name
            src_str = path
        else:
            raise ValueError("stream_large_file: either path or file must be provided")

        emit_chunk(f"Reading {filename}…")

        out_path, out_partial = reserve_artifact_path(
            "dataset.parquet",
            content_type="application/vnd.apache.parquet",
            kind="dataset",
        )
        src_sql = src_str.replace("'", "''")
        out_sql = str(out_path).replace("'", "''")

        con = _duckdb_mod.connect(":memory:")
        if format == "csv":
            delim_sql = (delimiter or ",")[:4].replace("'", "''")
            header_sql = "TRUE" if has_header else "FALSE"
            con.execute(
                f"COPY (SELECT * FROM read_csv_auto('{src_sql}', "
                f"delim='{delim_sql}', header={header_sql})) "
                f"TO '{out_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        else:
            con.execute(
                f"COPY (SELECT * FROM read_ndjson('{src_sql}')) "
                f"TO '{out_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )

        emit_chunk("Indexing…")
        row_count = int(
            con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{out_sql}')"
            ).fetchone()[0]
        )
        result = _finalize_parquet(out_path, out_partial)
        emit_chunk(f"Done — {row_count:,} rows")
        return result

    finally:
        if tmp_path_str:
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --directory packages/nodes python -m pytest tests/test_file_nodes.py -k "stream_large" -q 2>&1 | head -20
```

Expected: 5 passed.

- [ ] **Step 5: Run full suite one final time**

```bash
uv run --directory packages/nodes python -m pytest tests/test_file_nodes.py -q 2>&1 | tail -5
```

Expected: all tests pass (19 original + ~20 new = ~39 total).

- [ ] **Step 6: Verify all three new nodes appear in the manifest**

```bash
uv run --directory packages/nodes python -c "
from noodle_nodes import file_nodes  # noqa
from noodle.sdk import registry
ids = {m.id for m in registry.manifests()}
for nid in ('read_s3_file', 'read_url_file', 'stream_large_file'):
    status = 'OK' if nid in ids else 'MISSING'
    print(f'{nid}: {status}')
"
```

Expected:
```
read_s3_file: OK
read_url_file: OK
stream_large_file: OK
```

- [ ] **Step 7: Commit**

```bash
git add packages/nodes/noodle_nodes/file_nodes.py packages/nodes/tests/test_file_nodes.py
git commit -m "feat(nodes): add stream_large_file node with DuckDB streaming and progress"
```
