"""File-change polling trigger — local filesystem, Amazon S3, Google Cloud Storage."""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from typing import Any

from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)

_MAX_CURSOR_ENTRIES = 5000

_LOCAL = {"param": "source_type", "value": "local"}
_S3 = {"param": "source_type", "value": "s3"}
_GCS = {"param": "source_type", "value": "gcs"}
_CLOUD = {"param": "source_type", "values": ["s3", "gcs"]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _trim_cursor(files: dict[str, Any]) -> dict[str, Any]:
    """Keep only the most-recently-seen _MAX_CURSOR_ENTRIES entries."""
    if len(files) <= _MAX_CURSOR_ENTRIES:
        return files
    # Keep last N by insertion order (Python 3.7+ dicts are ordered).
    items = list(files.items())
    return dict(items[-_MAX_CURSOR_ENTRIES:])


def _make_event(
    source_type: str,
    event_type: str,
    key: str,
    size: int,
    modified_at: str,
    **extra: Any,
) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "provider": "filesystem",
        "source_type": source_type,
        "event_type": event_type,
        "filename": pathlib.Path(key).name or key,
        "path" if source_type == "local" else "key": key,
        "size_bytes": size,
        "modified_at": modified_at,
    }
    ev.update(extra)
    return ev


# ---------------------------------------------------------------------------
# Local filesystem
# ---------------------------------------------------------------------------


def _poll_local(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    watch_path = str(ctx.params.get("watch_path") or "").strip()
    glob_pattern = str(ctx.params.get("glob_pattern") or "*").strip() or "*"
    recursive = bool(ctx.params.get("recursive", False))

    if not watch_path:
        raise ValueError("file_change_trigger: watch_path is required for local source")

    base = pathlib.Path(watch_path)
    if not base.exists():
        raise FileNotFoundError(f"file_change_trigger: watch_path does not exist: {watch_path!r}")

    iterator = base.rglob(glob_pattern) if recursive else base.glob(glob_pattern)
    current: dict[str, tuple[int, float]] = {}  # key → (size, mtime)
    for p in iterator:
        if p.is_file():
            try:
                st = p.stat()
                current[str(p)] = (int(st.st_size), st.st_mtime)
            except OSError:
                pass

    prev: dict[str, list] = ctx.cursor.get("files", {})
    first_run = not prev and "files" not in ctx.cursor

    events: list[dict[str, Any]] = []
    updated: dict[str, list] = {}

    for key, (size, mtime) in current.items():
        mtime_iso = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
        if first_run:
            updated[key] = [size, mtime]
        elif key not in prev:
            events.append(_make_event("local", "created", key, size, mtime_iso))
            updated[key] = [size, mtime]
        else:
            prev_size, prev_mtime = prev[key]
            if mtime != prev_mtime or size != prev_size:
                events.append(_make_event("local", "modified", key, size, mtime_iso))
            updated[key] = [size, mtime]

    # Merge with entries we still know about (files not in current scan stay in cursor
    # so we don't re-fire "created" if they reappear).
    merged = {**prev, **updated}
    return ProviderTriggerPollResult(
        events=events,
        cursor={"files": _trim_cursor(merged)},
    )


# ---------------------------------------------------------------------------
# Amazon S3
# ---------------------------------------------------------------------------


def _poll_s3(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    try:
        import boto3  # type: ignore[import-untyped]
    except ImportError:
        raise ImportError(
            "boto3 is required to poll S3 for file changes. "
            "Install it with: pip install boto3"
        )

    params = ctx.params
    bucket = str(params.get("bucket") or "").strip()
    prefix = str(params.get("prefix") or "").strip()
    creds = params.get("s3_credentials") or {}

    if not bucket:
        raise ValueError("file_change_trigger: bucket is required for S3 source")

    session = boto3.Session(
        aws_access_key_id=creds.get("access_key_id") or None,
        aws_secret_access_key=creds.get("secret_access_key") or None,
        region_name=creds.get("region") or None,
    )
    s3 = session.client("s3")

    current: dict[str, tuple[int, str]] = {}  # key → (size, etag)
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = str(obj["Key"])
            current[key] = (int(obj["Size"]), str(obj["ETag"]).strip('"'))

    prev: dict[str, list] = ctx.cursor.get("files", {})
    first_run = not prev and "files" not in ctx.cursor

    events: list[dict[str, Any]] = []
    updated: dict[str, list] = {}
    for key, (size, etag) in current.items():
        if first_run:
            updated[key] = [size, etag]
        elif key not in prev:
            events.append(
                _make_event("s3", "created", key, size, _now_iso(), bucket=bucket, etag=etag)
            )
            updated[key] = [size, etag]
        else:
            prev_size, prev_etag = prev[key]
            if etag != prev_etag or size != prev_size:
                events.append(
                    _make_event("s3", "modified", key, size, _now_iso(), bucket=bucket, etag=etag)
                )
            updated[key] = [size, etag]

    merged = {**prev, **updated}
    return ProviderTriggerPollResult(
        events=events,
        cursor={"files": _trim_cursor(merged)},
    )


# ---------------------------------------------------------------------------
# Google Cloud Storage
# ---------------------------------------------------------------------------


def _poll_gcs(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    from google.cloud import storage as gcs_storage  # type: ignore[import-not-found]
    from google.oauth2 import service_account  # type: ignore[import-not-found]

    params = ctx.params
    bucket_name = str(params.get("bucket") or "").strip()
    prefix = str(params.get("prefix") or "").strip()
    creds_info = params.get("gcs_credentials") or {}

    if not bucket_name:
        raise ValueError("file_change_trigger: bucket is required for GCS source")

    if creds_info and isinstance(creds_info, dict):
        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        client = gcs_storage.Client(credentials=credentials)
    else:
        client = gcs_storage.Client()

    blobs = client.list_blobs(bucket_name, prefix=prefix or None)
    current: dict[str, tuple[int, str]] = {}
    for blob in blobs:
        md5 = blob.md5_hash or ""
        current[blob.name] = (int(blob.size or 0), md5)

    prev: dict[str, list] = ctx.cursor.get("files", {})
    first_run = not prev and "files" not in ctx.cursor

    events: list[dict[str, Any]] = []
    updated: dict[str, list] = {}
    for key, (size, md5) in current.items():
        if first_run:
            updated[key] = [size, md5]
        elif key not in prev:
            events.append(
                _make_event("gcs", "created", key, size, _now_iso(), bucket=bucket_name, md5=md5)
            )
            updated[key] = [size, md5]
        else:
            prev_size, prev_md5 = prev[key]
            if md5 != prev_md5 or size != prev_size:
                events.append(
                    _make_event(
                        "gcs", "modified", key, size, _now_iso(), bucket=bucket_name, md5=md5
                    )
                )
            updated[key] = [size, md5]

    merged = {**prev, **updated}
    return ProviderTriggerPollResult(
        events=events,
        cursor={"files": _trim_cursor(merged)},
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def poll_file_changes(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    source_type = str(ctx.params.get("source_type") or "local").strip()
    if source_type == "s3":
        return _poll_s3(ctx)
    if source_type == "gcs":
        return _poll_gcs(ctx)
    return _poll_local(ctx)


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------

FILE_CHANGE_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="file_change_trigger",
    name="File Change",
    provider="filesystem",
    resource="file",
    event="change",
    description=(
        "Start a workflow when a file is created or modified. "
        "Supports local server paths, Amazon S3, and Google Cloud Storage."
    ),
    icon="file",
    params=(
        OperationParamSpec(
            name="source_type",
            type="string",
            default="local",
            choices=["local", "s3", "gcs"],
            display_name="Source",
            description="Where to watch for file changes.",
        ),
        # ---- Local ----
        OperationParamSpec(
            name="watch_path",
            display_name="Watch path",
            placeholder="/data/uploads",
            description="Absolute directory path to watch on the server.",
            display_when=_LOCAL,
        ),
        OperationParamSpec(
            name="glob_pattern",
            display_name="File pattern",
            default="*",
            placeholder="*.csv",
            description="Glob pattern to filter files (e.g. *.csv, reports/*.json).",
            display_when=_LOCAL,
            advanced=True,
        ),
        OperationParamSpec(
            name="recursive",
            type="boolean",
            display_name="Recursive",
            default=False,
            description="Watch all subdirectories, not just the top-level path.",
            display_when=_LOCAL,
            advanced=True,
        ),
        # ---- S3 / GCS shared ----
        OperationParamSpec(
            name="bucket",
            display_name="Bucket",
            placeholder="my-bucket",
            description="Name of the S3 or GCS bucket to watch.",
            display_when=_CLOUD,
        ),
        OperationParamSpec(
            name="prefix",
            display_name="Prefix / folder",
            placeholder="uploads/",
            description="Optional key prefix to narrow the watch scope.",
            display_when=_CLOUD,
            advanced=True,
        ),
        # ---- S3 credentials ----
        OperationParamSpec(
            name="s3_credentials",
            display_name="AWS credentials",
            type="credential",
            description="AWS access key and secret for S3 access.",
            display_when=_S3,
        ),
        # ---- GCS credentials ----
        OperationParamSpec(
            name="gcs_credentials",
            display_name="GCS service account",
            type="credential",
            description="Google Cloud service-account JSON for GCS access.",
            display_when=_GCS,
        ),
        # ---- Shared advanced ----
        OperationParamSpec(
            name="poll_interval_override",
            type="number",
            display_name="Poll interval (seconds)",
            default=60,
            description="How often to check for changes. Minimum 30 s.",
            advanced=True,
        ),
    ),
    requirements=(),
    poll=poll_file_changes,
    poll_interval_seconds=60,
)

register_provider_trigger(FILE_CHANGE_TRIGGER_SPEC)
