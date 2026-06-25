"""File system watcher trigger — fires on file create/modify/delete events."""

from __future__ import annotations

import fnmatch
import os
import time
from datetime import UTC, datetime
from typing import Any

from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)

_MAX_SEEN_PATHS = 2000
_WATCHFILES_EVENT_NAMES = {1: "add", 2: "modify", 3: "delete"}


def _matches_patterns(filename: str, patterns: list[str]) -> bool:
    if not patterns or patterns == ["*"]:
        return True
    return any(fnmatch.fnmatch(filename, p.strip()) for p in patterns)


def _scan_directory(
    directory: str,
    patterns: list[str],
    recursive: bool,
    events_wanted: set[str],
    max_size: int,
    include_content: bool,
    encoding: str,
) -> list[dict[str, Any]]:
    """Return a snapshot of all matching files with their mtime + size."""
    results: list[dict[str, Any]] = []
    if not os.path.isdir(directory):
        raise ValueError(f"file_watcher_trigger: directory not found: {directory!r}")
    try:
        walk = os.walk(directory) if recursive else ((directory, [], os.listdir(directory)),)
    except PermissionError:
        return results

    for dirpath, _dirnames, filenames in walk:
        for fname in filenames:
            if not _matches_patterns(fname, patterns):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                stat = os.stat(fpath)
            except (PermissionError, FileNotFoundError):
                continue
            results.append(
                {
                    "path": fpath,
                    "filename": fname,
                    "extension": os.path.splitext(fname)[1],
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        if not recursive:
            break
    return results


def _format_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=UTC).isoformat()


def poll_file_watcher(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    params = ctx.params
    directory = str(params.get("directory") or "").strip()
    if not directory:
        raise ValueError("file_watcher_trigger: directory is required")

    patterns_raw = str(params.get("patterns") or "*")
    patterns = [p.strip() for p in patterns_raw.split(",") if p.strip()]
    events_raw = str(params.get("events") or "create")
    events_wanted = {e.strip().lower() for e in events_raw.split(",") if e.strip()}
    recursive = str(params.get("recursive", "true")).lower() not in ("false", "0", "no")
    include_content = str(params.get("include_content", "false")).lower() in ("true", "1", "yes")
    encoding = str(params.get("encoding") or "utf-8")
    try:
        max_file_size = int(params.get("max_file_size") or 52_428_800)
    except (ValueError, TypeError):
        max_file_size = 52_428_800

    cursor = ctx.cursor
    prev_snapshot: dict[str, float] | None = cursor.get("snapshot")

    current_files = _scan_directory(
        directory, patterns, recursive, events_wanted, max_file_size, include_content, encoding
    )
    current_snapshot = {f["path"]: f["mtime"] for f in current_files}

    # First run: seed snapshot without firing events
    if prev_snapshot is None:
        return ProviderTriggerPollResult(
            events=[],
            cursor={"snapshot": current_snapshot},
        )

    events: list[dict[str, Any]] = []

    # Detect creates and modifies
    for finfo in current_files:
        fpath = finfo["path"]
        prev_mtime = prev_snapshot.get(fpath)
        if prev_mtime is None and "create" in events_wanted:
            evt = {
                "event": "create",
                "path": fpath,
                "filename": finfo["filename"],
                "extension": finfo["extension"],
                "size": finfo["size"],
                "modified_at": _format_mtime(finfo["mtime"]),
            }
            if include_content and finfo["size"] <= max_file_size:
                try:
                    with open(fpath, encoding=encoding, errors="replace") as fh:
                        evt["content"] = fh.read()
                except (PermissionError, OSError):
                    pass
            events.append(evt)
        elif prev_mtime is not None and finfo["mtime"] > prev_mtime and "modify" in events_wanted:
            evt = {
                "event": "modify",
                "path": fpath,
                "filename": finfo["filename"],
                "extension": finfo["extension"],
                "size": finfo["size"],
                "modified_at": _format_mtime(finfo["mtime"]),
            }
            if include_content and finfo["size"] <= max_file_size:
                try:
                    with open(fpath, encoding=encoding, errors="replace") as fh:
                        evt["content"] = fh.read()
                except (PermissionError, OSError):
                    pass
            events.append(evt)

    # Detect deletes
    if "delete" in events_wanted:
        deleted_paths = set(prev_snapshot) - set(current_snapshot)
        for fpath in deleted_paths:
            fname = os.path.basename(fpath)
            if _matches_patterns(fname, patterns):
                events.append(
                    {
                        "event": "delete",
                        "path": fpath,
                        "filename": fname,
                        "extension": os.path.splitext(fname)[1],
                        "size": 0,
                        "modified_at": _format_mtime(time.time()),
                    }
                )

    return ProviderTriggerPollResult(
        events=events,
        cursor={"snapshot": current_snapshot},
    )


FILE_WATCHER_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="file_watcher_trigger",
    name="File Watcher",
    provider="filesystem",
    resource="file",
    event="file_change",
    description="Start a workflow when files in a directory are created, modified, or deleted.",
    icon="folder",
    params=(
        OperationParamSpec(
            name="directory",
            required=True,
            description="Absolute path to the directory to watch.",
        ),
        OperationParamSpec(
            name="patterns",
            default="*",
            description="Comma-separated glob patterns (e.g. '*.csv, *.json'). Default: all files.",
        ),
        OperationParamSpec(
            name="events",
            default="create",
            description="Comma-separated events to watch: create, modify, delete.",
        ),
        OperationParamSpec(
            name="recursive",
            type="boolean",
            default=True,
            description="Watch subdirectories recursively.",
        ),
        OperationParamSpec(
            name="include_content",
            type="boolean",
            default=False,
            description="Read and include file content in the payload.",
        ),
        OperationParamSpec(
            name="encoding",
            default="utf-8",
            group="Advanced",
            description="Text encoding for reading file content.",
            advanced=True,
        ),
        OperationParamSpec(
            name="max_file_size",
            type="number",
            default=52_428_800,
            group="Advanced",
            description="Skip files larger than this size in bytes (default 50 MB).",
            advanced=True,
        ),
    ),
    requirements=("watchfiles>=0.20",),
    poll=poll_file_watcher,
    poll_interval_seconds=10,
)

register_provider_trigger(FILE_WATCHER_TRIGGER_SPEC)
