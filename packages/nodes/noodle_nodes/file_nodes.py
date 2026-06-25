"""File reading nodes — local path and browser-upload (artifact) sources."""

from __future__ import annotations

import pathlib
from typing import Any

from noodle.sdk import node
from noodle_nodes._creds import cred_multi
from noodle_nodes.datasets import csv_parse as csv_parse  # re-exported for patching


def _read_upload_bytes(artifact_id: str) -> tuple[bytes, str]:
    """Read bytes for a browser-uploaded artifact given its artifact_id.

    Uploaded artifacts are stored at:  uploads/{artifact_id}/{filename}
    """
    from noodle.context import artifact_store

    store = artifact_store.get()
    upload_dir = store.base_dir / "uploads" / artifact_id
    if upload_dir.exists():
        for entry in upload_dir.iterdir():
            if entry.is_file():
                return entry.read_bytes(), entry.name
    raise FileNotFoundError(
        f"No uploaded artifact found for id {artifact_id!r}. "
        "Make sure the artifact_id refers to a file uploaded via the browser."
    )


@node(
    name="Read Text File",
    category="Files",
    description="Read a text file from a server path or browser-uploaded artifact.",
    icon="file-text",
    params={
        "file": {
            "widget": "file_upload",
            "display_name": "Upload file",
            "description": "Upload from your browser. The file is saved and reused across runs — no need to re-upload.",
        },
        "path": {
            "advanced": True,
            "display_name": "Server path",
            "description": (
                "Absolute path on the server filesystem. "
                "When the data volume is mounted, use /app/data/yourfile.ext. "
                "See deploy/docker-compose.yml for setup."
            ),
        },
    },
)
def read_text_file(
    input: Any,
    path: str = "",
    file: str = "",
    encoding: str = "utf-8",
) -> dict[str, Any]:
    if file:
        content_bytes, filename = _read_upload_bytes(file)
        text = content_bytes.decode(encoding or "utf-8")
        return {"text": text, "filename": filename, "size_bytes": len(content_bytes)}
    if path:
        p = pathlib.Path(path)
        text = p.read_text(encoding=encoding or "utf-8")
        return {"text": text, "filename": p.name, "size_bytes": p.stat().st_size}
    raise ValueError("read_text_file: either path or file (artifact_id) must be provided")


_DATASET_TOGGLE = {"param": "output_as_dataset", "true": "dataset", "false": "any"}


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
            return csv_parse(
                input=None, text=text, delimiter=delimiter or ",", has_header=has_header
            )
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

        from noodle.datasets import reserve_artifact_path
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


@node(
    name="Read CSV File",
    category="Files",
    description=(
        "Read a CSV file from a server path or uploaded artifact. "
        "Toggle output_as_dataset to get a DatasetRef (Parquet)."
    ),
    icon="table",
    param_output_kinds={"main": _DATASET_TOGGLE},
    params={
        "file": {
            "widget": "file_upload",
            "display_name": "Upload file",
            "description": "Upload from your browser. The file is saved and reused across runs — no need to re-upload.",
        },
        "path": {
            "advanced": True,
            "display_name": "Server path",
            "description": (
                "Absolute path on the server filesystem. "
                "When the data volume is mounted, use /app/data/yourfile.ext. "
                "See deploy/docker-compose.yml for setup."
            ),
        },
    },
)
def read_csv_file(
    input: Any,
    path: str = "",
    file: str = "",
    delimiter: str = ",",
    has_header: bool = True,
    output_as_dataset: bool = True,
) -> Any:
    import csv
    import io as _io

    if file:
        content_bytes, filename = _read_upload_bytes(file)
        text = content_bytes.decode("utf-8")
    elif path:
        p = pathlib.Path(path)
        text = p.read_text(encoding="utf-8")
        filename = p.name
    else:
        raise ValueError("read_csv_file: either path or file (artifact_id) must be provided")

    if output_as_dataset:
        return csv_parse(input=None, text=text, delimiter=delimiter or ",", has_header=has_header)

    reader = csv.reader(_io.StringIO(text), delimiter=delimiter or ",")
    all_rows = list(reader)
    if not all_rows:
        return {"rows": [], "filename": filename, "row_count": 0}

    if has_header:
        headers = all_rows[0]
        data_rows = all_rows[1:]
        dicts = [dict(zip(headers, row, strict=False)) for row in data_rows]
    else:
        data_rows = all_rows
        dicts = [dict(enumerate(row)) for row in data_rows]

    return {"rows": dicts, "filename": filename, "row_count": len(dicts)}


@node(
    name="Read JSON File",
    category="Files",
    description=(
        "Read a JSON file from a server path or uploaded artifact. "
        "Enable output_as_dataset to convert an array of objects to a DatasetRef."
    ),
    icon="braces",
    param_output_kinds={"main": _DATASET_TOGGLE},
    params={
        "file": {
            "widget": "file_upload",
            "display_name": "Upload file",
            "description": "Upload from your browser. The file is saved and reused across runs — no need to re-upload.",
        },
        "path": {
            "advanced": True,
            "display_name": "Server path",
            "description": (
                "Absolute path on the server filesystem. "
                "When the data volume is mounted, use /app/data/yourfile.ext. "
                "See deploy/docker-compose.yml for setup."
            ),
        },
    },
)
def read_json_file(
    input: Any,
    path: str = "",
    file: str = "",
    output_as_dataset: bool = False,
) -> Any:
    import json as _json

    if file:
        content_bytes, filename = _read_upload_bytes(file)
        text = content_bytes.decode("utf-8")
    elif path:
        p = pathlib.Path(path)
        text = p.read_text(encoding="utf-8")
        filename = p.name
    else:
        raise ValueError("read_json_file: either path or file (artifact_id) must be provided")

    data = _json.loads(text)

    if output_as_dataset:
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise ValueError(
                "read_json_file: output_as_dataset requires the JSON root to be an array of objects"
            )
        import csv as _csv
        import io as _io

        if not data:
            csv_text = ""
        else:
            headers = list(data[0].keys())
            buf = _io.StringIO()
            writer = _csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(data)
            csv_text = buf.getvalue()
        return csv_parse(input=None, text=csv_text, delimiter=",", has_header=True)

    return {"data": data, "filename": filename}


@node(
    name="Read XML File",
    category="Files",
    description=(
        "Read an XML file from a server path or uploaded artifact. "
        "Provide row_xpath and enable output_as_dataset to extract "
        "repeated elements as a DatasetRef."
    ),
    icon="code",
    requirements=("xmltodict",),
    param_output_kinds={"main": _DATASET_TOGGLE},
    params={
        "file": {
            "widget": "file_upload",
            "display_name": "Upload file",
            "description": "Upload from your browser. The file is saved and reused across runs — no need to re-upload.",
        },
        "path": {
            "advanced": True,
            "display_name": "Server path",
            "description": (
                "Absolute path on the server filesystem. "
                "When the data volume is mounted, use /app/data/yourfile.ext. "
                "See deploy/docker-compose.yml for setup."
            ),
        },
    },
)
def read_xml_file(
    input: Any,
    path: str = "",
    file: str = "",
    row_xpath: str = "",
    output_as_dataset: bool = False,
) -> Any:
    from defusedxml import DefusedXmlException
    from defusedxml import ElementTree as ET

    if file:
        content_bytes, filename = _read_upload_bytes(file)
        text = content_bytes.decode("utf-8")
    elif path:
        p = pathlib.Path(path)
        text = p.read_text(encoding="utf-8")
        filename = p.name
    else:
        raise ValueError("read_xml_file: either path or file (artifact_id) must be provided")

    if output_as_dataset:
        if not row_xpath:
            raise ValueError("read_xml_file: row_xpath is required when output_as_dataset is true")
        try:
            root = ET.fromstring(text)
        except (ET.ParseError, DefusedXmlException) as exc:
            raise ValueError(f"read_xml_file: invalid or unsafe XML: {exc}") from exc
        rows_els = root.findall(f".//{row_xpath}")
        rows: list[dict] = []
        for el in rows_els:
            row: dict = {}
            for child in el:
                row[child.tag] = (child.text or "").strip()
            for attr_name, attr_val in el.attrib.items():
                row[f"@{attr_name}"] = attr_val
            rows.append(row)
        import csv as _csv
        import io as _io

        if rows:
            headers = list(rows[0].keys())
            buf = _io.StringIO()
            writer = _csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            csv_text = buf.getvalue()
        else:
            csv_text = ""
        return csv_parse(input=None, text=csv_text, delimiter=",", has_header=True)

    import xmltodict

    data = xmltodict.parse(text, disable_entities=True)
    return {"data": dict(data), "filename": filename}


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


_REDIRECT_AUTH_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-key",
        "x-auth-token",
        "x-access-token",
    }
)


def _ssrf_safe_fetch(url: str, headers: dict, *, timeout: int = 60) -> tuple[bytes, str]:
    """Fetch ``url`` and return ``(body_bytes, content_type)``.

    SSRF protections applied:
    1. DNS-rebinding prevention: the hostname is resolved once via
       ``socket.getaddrinfo``, every returned address is checked to be globally
       routable, and the TCP connection is made directly to the validated IP via
       urllib3.  ``assert_hostname`` / ``server_hostname`` preserve the original
       hostname for TLS certificate verification and SNI so HTTPS keeps working.
    2. Cross-host redirect credential stripping: auth-class headers
       (Authorization, Cookie, …) are removed before following a redirect to a
       different hostname, preventing user credentials from being forwarded to an
       attacker-controlled domain.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    import urllib3

    from noodle_nodes.http_security import private_egress_allowed

    allow_private = private_egress_allowed()
    current_url = url
    current_headers = dict(headers)
    max_redirects = 10

    while True:
        parsed = urlparse(current_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                f"read_url_file: unsupported URL scheme {parsed.scheme!r}; "
                "only http and https are allowed"
            )
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("read_url_file: URL must include a hostname")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        # Resolve once and validate every returned address before connecting.
        try:
            infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"read_url_file: cannot resolve hostname {hostname!r}: {exc}") from exc
        if not infos:
            raise ValueError(f"read_url_file: no addresses for {hostname!r}")
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if not allow_private and not ip.is_global:
                raise ValueError(
                    f"read_url_file: {hostname!r} resolves to {addr}, which is not a "
                    "globally routable address; requests to private/loopback/link-local "
                    "addresses are not allowed"
                )

        # Connect directly to the validated IP.  For HTTPS, assert_hostname and
        # server_hostname keep TLS verification and SNI tied to the original
        # hostname rather than the raw IP.
        validated_ip = infos[0][4][0]
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        req_headers = {"Host": hostname, **current_headers}

        if parsed.scheme == "https":
            pool: urllib3.HTTPConnectionPool = urllib3.HTTPSConnectionPool(
                validated_ip,
                port=port,
                assert_hostname=hostname,
                server_hostname=hostname,
            )
        else:
            pool = urllib3.HTTPConnectionPool(validated_ip, port=port)

        resp = pool.request(
            "GET",
            path,
            headers=req_headers,
            redirect=False,
            preload_content=True,
            timeout=timeout,
        )

        if resp.status in (301, 302, 303, 307, 308) and max_redirects > 0:
            location = resp.headers.get("Location", "")
            if not location:
                break
            # Resolve relative Location headers (e.g. "/new-path") against the
            # current URL so same-origin redirects continue to work correctly.
            from urllib.parse import urljoin as _urljoin

            resolved_location = _urljoin(current_url, location)
            redirect_hostname = urlparse(resolved_location).hostname
            if redirect_hostname and redirect_hostname != hostname:
                # Cross-host redirect: strip auth-class headers so credentials
                # are never forwarded to an attacker-controlled domain.
                current_headers = {
                    k: v
                    for k, v in current_headers.items()
                    if k.lower() not in _REDIRECT_AUTH_HEADERS
                }
            current_url = resolved_location
            max_redirects -= 1
        else:
            break

    if resp.status >= 400:
        raise ValueError(f"read_url_file: HTTP {resp.status} from {current_url}")

    content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
    return resp.data, content_type


_URL_FORMAT_CHOICES = ["auto", "csv", "json", "parquet", "text"]
_EXT_TO_FMT = {
    ".csv": "csv",
    ".json": "json",
    ".jsonl": "json",
    ".ndjson": "json",
    ".parquet": "parquet",
    ".txt": "text",
    ".md": "text",
}
_CT_TO_FMT = {
    "text/csv": "csv",
    "application/csv": "csv",
    "application/json": "json",
    "application/x-ndjson": "json",
    "application/vnd.apache.parquet": "parquet",
    "text/plain": "text",
}


@node(
    name="Read URL File",
    category="Files",
    description=(
        "Download a file from an HTTP/HTTPS URL and parse it as CSV, JSON, Parquet, or text. "
        "Set format to 'auto' to detect from URL extension or Content-Type."
    ),
    icon="link",
    param_output_kinds={"main": _DATASET_TOGGLE},
    params={
        "url": {
            "description": "Full HTTP/HTTPS URL of the file to download.",
            "placeholder": "https://example.com/data.csv",
        },
        "format": {
            "choices": _URL_FORMAT_CHOICES,
            "description": (
                "File format. 'auto' detects from URL extension then Content-Type header."
            ),
        },
        "request_headers": {
            "display_name": "Request headers (JSON)",
            "advanced": True,
            "description": (
                "Optional extra HTTP headers as a JSON object, "
                'e.g. {"Authorization": "Bearer token"}.'
            ),
            "placeholder": '{"Authorization": "Bearer token"}',
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

    if not url:
        raise ValueError("read_url_file: url must be provided")

    # Parse extra headers
    headers: dict = {}
    if request_headers:
        try:
            parsed_headers = _json.loads(request_headers)
        except _json.JSONDecodeError as exc:
            raise ValueError(f"read_url_file: request_headers must be valid JSON: {exc}") from exc
        if not isinstance(parsed_headers, dict):
            raise ValueError("read_url_file: request_headers must be a JSON object")
        headers = parsed_headers

    content_bytes, content_type = _ssrf_safe_fetch(url, headers)

    # Derive filename from the original URL path (the final URL may be a CDN path)
    url_path = urlparse(url).path
    filename = url_path.rstrip("/").split("/")[-1] or "download"

    # Resolve format
    fmt = format
    if fmt == "auto":
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        fmt = _EXT_TO_FMT.get(ext) or _CT_TO_FMT.get(content_type) or "text"

    return _parse_file_bytes(
        content_bytes,
        filename=filename,
        fmt=fmt,
        delimiter=delimiter,
        has_header=has_header,
        output_as_dataset=output_as_dataset,
    )


_STREAM_FORMAT_CHOICES = ["auto", "csv", "json"]
_STREAM_EXT_TO_FMT = {
    ".csv": "csv",
    ".tsv": "csv",
    ".json": "json",
    ".jsonl": "json",
    ".ndjson": "json",
}


@node(
    name="Stream Large File",
    category="Files",
    description=(
        "Stream a large CSV or NDJSON file into a DatasetRef using DuckDB. "
        "Handles files too large to load into memory. Emits progress updates while processing."
    ),
    icon="database",
    output_kinds={"main": "dataset"},
    params={
        "file": {
            "widget": "file_upload",
            "display_name": "Upload file",
            "description": "Upload from your browser. The file is saved and reused across runs.",
        },
        "path": {
            "advanced": True,
            "display_name": "Server path",
            "description": (
                "Absolute path on the server filesystem. "
                "When the data volume is mounted, use /app/data/yourfile.ext."
            ),
        },
        "format": {
            "choices": _STREAM_FORMAT_CHOICES,
            "description": "'auto' detects from filename extension (csv or json/jsonl/ndjson).",
        },
        "delimiter": {
            "display_when": {"format": "csv"},
            "description": "CSV delimiter character. Leave blank to auto-detect.",
        },
    },
)
def stream_large_file(
    input: Any,
    path: str = "",
    file: str = "",
    format: str = "auto",
    delimiter: str = "",
) -> Any:
    import os
    import tempfile

    import duckdb as _duckdb

    from noodle.context import emit_chunk
    from noodle.datasets import reserve_artifact_path
    from noodle_nodes.datasets import _finalize_parquet

    # Resolve source: upload artifact or server path
    if file:
        content_bytes, filename = _read_upload_bytes(file)
        src_bytes = content_bytes
    elif path:
        p = pathlib.Path(path)
        filename = p.name
        src_bytes = p.read_bytes()
    else:
        raise ValueError("stream_large_file: either path or file (artifact_id) must be provided")

    # Resolve format
    fmt = format
    if fmt == "auto":
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        fmt = _STREAM_EXT_TO_FMT.get(ext, "csv")

    emit_chunk(f"Reading {filename}…")

    # Write bytes to a temp file so DuckDB can read it
    suffix = ".csv" if fmt == "csv" else ".jsonl"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(src_bytes)
        tmp.close()
        src_sql = tmp.name.replace("'", "''")

        emit_chunk("Indexing…")

        out_path, out_partial = reserve_artifact_path(
            filename.rsplit(".", 1)[0] + ".parquet",
            content_type="application/vnd.apache.parquet",
            kind="dataset",
        )
        out_sql = str(out_path).replace("'", "''")

        con = _duckdb.connect(":memory:")
        if fmt == "csv":
            if delimiter:
                if len(delimiter) != 1 or delimiter in ("'", "\\"):
                    raise ValueError(
                        "stream_large_file: delimiter must be a single character other than ' or \\"
                    )
                delim_clause = f", DELIM '{delimiter}'"
            else:
                delim_clause = ""
            con.execute(
                f"COPY (SELECT * FROM read_csv_auto('{src_sql}'{delim_clause})) "
                f"TO '{out_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        else:
            con.execute(
                f"COPY (SELECT * FROM read_ndjson('{src_sql}')) "
                f"TO '{out_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        con.close()

        result = _finalize_parquet(out_path, out_partial)
        row_count = result.get("row_count", 0)
        emit_chunk(f"Done — {row_count:,} rows")
        return result
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
