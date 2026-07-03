"""Runtime artifact helpers exposed to Code and user-module nodes.

Artifacts keep large bytes out of ``NodeRun.output``. A node writes bytes to
the configured store and receives a small JSON-compatible reference that can
flow through normal node outputs, pins, and retry caches.
"""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any

from nodyra.context import artifact_store, current_node_id, node_debug
from nodyra.serialization import serialize_value

ARTIFACT_MARKER = "__nodyra_artifact__"
ARTIFACT_VERSION = 1

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def is_artifact_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get(ARTIFACT_MARKER) is True
        and value.get("version") == ARTIFACT_VERSION
        and isinstance(value.get("artifact_id"), str)
    )


def sanitize_name(name: str | None) -> str:
    raw = str(name or "artifact").replace("\\", "/").split("/")[-1]
    cleaned = _SAFE_NAME_RE.sub("_", raw.strip())
    cleaned = cleaned.replace("\\", "_").replace("/", "_").strip(" .")
    return cleaned[:180] or "artifact"


def _store() -> LocalArtifactStore:
    store = artifact_store.get()
    if store is None:
        raise RuntimeError("artifacts are not available in this execution context")
    return store


def _remember(ref: dict[str, Any]) -> None:
    debug = node_debug.get()
    if debug is None:
        return
    artifacts = debug.setdefault("artifacts", [])
    if isinstance(artifacts, list):
        artifacts.append(ref)


class LocalArtifactStore:
    """Filesystem-backed artifact store used by local API and env runners."""

    def __init__(
        self,
        base_dir: str | Path,
        run_id: str,
        *,
        max_bytes: int = 0,
        max_count: int = 0,
        key_prefix: str = "",
    ) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.run_id = str(run_id)
        self.max_bytes = max(0, int(max_bytes or 0))
        self.max_count = max(0, int(max_count or 0))
        # Tenancy namespace prepended to every storage key (e.g. an org id).
        # Normalised to "segment/" or "" so key construction stays additive.
        cleaned = str(key_prefix or "").strip("/")
        self.key_prefix = f"{cleaned}/" if cleaned else ""
        self._written = 0

    def _storage_key(self, artifact_id: str, node_id: str, name: str) -> str:
        return (
            f"{self.key_prefix}runs/{self.run_id}/"
            f"{node_id}/{artifact_id}-{sanitize_name(name)}"
        )

    def _path_for_key(self, storage_key: str) -> Path:
        path = (self.base_dir / storage_key).resolve()
        try:
            path.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError("artifact storage key escapes the artifact directory") from exc
        return path

    def path_for_ref(self, ref: dict[str, Any]) -> Path:
        storage_key = ref.get("storage_key")
        if not isinstance(storage_key, str) or not storage_key:
            run_id = str(ref.get("run_id") or self.run_id)
            node_id = sanitize_name(str(ref.get("node_id") or "unknown"))
            artifact_id = str(ref.get("artifact_id") or "")
            name = sanitize_name(str(ref.get("name") or "artifact"))
            if not artifact_id:
                raise ValueError("artifact ref is missing artifact_id")
            storage_key = (
                f"{self.key_prefix}runs/{run_id}/{node_id}/{artifact_id}-{name}"
            )
        return self._path_for_key(storage_key)

    def write_bytes(
        self,
        data: bytes | bytearray | memoryview,
        *,
        name: str,
        content_type: str = "application/octet-stream",
        kind: str = "binary",
        metadata: dict[str, Any] | None = None,
        preview: Any = None,
    ) -> dict[str, Any]:
        payload = bytes(data)
        if self.max_bytes and len(payload) > self.max_bytes:
            raise ValueError(
                f"artifact {name!r} is {len(payload)} bytes; "
                f"limit is {self.max_bytes} bytes"
            )
        if self.max_count and self._written >= self.max_count:
            raise ValueError(f"artifact limit reached for run {self.run_id}")

        artifact_id = uuid.uuid4().hex
        node_id = sanitize_name(current_node_id.get() or "unknown")
        safe_name = sanitize_name(name)
        storage_key = self._storage_key(artifact_id, node_id, safe_name)
        path = self._path_for_key(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        self._written += 1

        ref: dict[str, Any] = {
            ARTIFACT_MARKER: True,
            "version": ARTIFACT_VERSION,
            "artifact_id": artifact_id,
            "run_id": self.run_id,
            "node_id": node_id,
            "name": safe_name,
            "kind": kind,
            "content_type": content_type,
            "size_bytes": len(payload),
            "checksum_sha256": hashlib.sha256(payload).hexdigest(),
            "storage_backend": "local",
            "storage_key": storage_key,
        }
        if metadata:
            ref["metadata"] = serialize_value(metadata)
        if preview is not None:
            ref["preview"] = serialize_value(preview)
        return ref

    def read_bytes(self, ref: dict[str, Any]) -> bytes:
        if not is_artifact_ref(ref):
            raise ValueError("expected a Nodyra artifact ref")
        return self.path_for_ref(ref).read_bytes()

    def open(self, ref: dict[str, Any], mode: str = "rb"):
        if not is_artifact_ref(ref):
            raise ValueError("expected a Nodyra artifact ref")
        path = self.path_for_ref(ref)
        if any(flag in mode for flag in ("w", "a", "+")):
            raise ValueError("artifact refs are read-only")
        return builtins.open(path, mode)


def write_bytes(
    data: bytes | bytearray | memoryview,
    name: str = "artifact.bin",
    content_type: str = "application/octet-stream",
    *,
    kind: str = "binary",
    metadata: dict[str, Any] | None = None,
    preview: Any = None,
) -> dict[str, Any]:
    ref = _store().write_bytes(
        data,
        name=name,
        content_type=content_type,
        kind=kind,
        metadata=metadata,
        preview=preview,
    )
    _remember(ref)
    return ref


def write_text(
    text: str,
    name: str = "artifact.txt",
    content_type: str = "text/plain; charset=utf-8",
    *,
    metadata: dict[str, Any] | None = None,
    preview_chars: int = 2000,
) -> dict[str, Any]:
    preview = text[:preview_chars]
    return write_bytes(
        text.encode("utf-8"),
        name=name,
        content_type=content_type,
        kind="text",
        metadata=metadata,
        preview=preview,
    )


def write_json(
    value: Any,
    name: str = "artifact.json",
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = serialize_value(value)
    return write_bytes(
        json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
        name=name,
        content_type="application/json",
        kind="json",
        metadata=metadata,
        preview=payload,
    )


def write_dataframe(
    df: Any,
    name: str = "dataframe.csv",
    *,
    format: str = "csv",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fmt = format.lower()
    preview = serialize_value(df)
    meta = {"format": fmt, **(metadata or {})}
    if fmt == "json":
        if not hasattr(df, "to_json"):
            raise ValueError("write_dataframe(format='json') requires a DataFrame")
        payload = str(df.to_json(orient="records")).encode("utf-8")
        content_type = "application/json"
        if name == "dataframe.csv":
            name = "dataframe.json"
    elif fmt == "csv":
        if not hasattr(df, "to_csv"):
            raise ValueError("write_dataframe(format='csv') requires a DataFrame")
        payload = str(df.to_csv(index=False)).encode("utf-8")
        content_type = "text/csv; charset=utf-8"
        if not name.lower().endswith(".csv"):
            name = f"{name}.csv"
    else:
        raise ValueError("write_dataframe supports csv or json in this build")

    return write_bytes(
        payload,
        name=name,
        content_type=content_type,
        kind="dataframe",
        metadata=meta,
        preview=preview,
    )


def read_bytes(ref: dict[str, Any]) -> bytes:
    return _store().read_bytes(ref)


def read_text(ref: dict[str, Any], encoding: str = "utf-8") -> str:
    return read_bytes(ref).decode(encoding)


def read_json(ref: dict[str, Any]) -> Any:
    return json.loads(read_text(ref))


def read_dataframe(ref: dict[str, Any]) -> Any:
    import pandas as pd  # type: ignore[import-not-found]

    content_type = str(ref.get("content_type") or "")
    payload = read_bytes(ref)
    if "json" in content_type or str(ref.get("name") or "").lower().endswith(".json"):
        return pd.read_json(io.BytesIO(payload))
    return pd.read_csv(io.BytesIO(payload))


def open(ref: dict[str, Any], mode: str = "rb"):
    return _store().open(ref, mode)
