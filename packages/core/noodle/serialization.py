"""Typed JSON serialization for values crossing process/UI boundaries.

The execution engine can pass real Python objects between nodes during a run.
When values leave that process for WebSockets, persistence, pinned data, or a
retry cache, they must become JSON-compatible. This module preserves useful
type information for known safe Python types without using pickle.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

TYPED_MARKER = "__noodle_typed__"
TYPED_VERSION = 1
DEFAULT_DATAFRAME_ROWS = 100


def is_typed_envelope(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get(TYPED_MARKER) is True
        and value.get("version") == TYPED_VERSION
        and isinstance(value.get("type"), str)
    )


def _envelope(
    kind: str,
    value: Any,
    *,
    restorable: bool = True,
    **meta: Any,
) -> dict[str, Any]:
    return {
        TYPED_MARKER: True,
        "version": TYPED_VERSION,
        "type": kind,
        "value": value,
        "restorable": restorable,
        **meta,
    }


def _safe_repr(value: Any, limit: int = 500) -> str:
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - serialization must never fail a run
        text = f"<{type(value).__name__}>"
    return text if len(text) <= limit else f"{text[:limit - 1]}..."


def _object_preview(value: Any, *, reason: str | None = None) -> dict[str, Any]:
    meta = {
        "python_type": type(value).__name__,
        "module": getattr(type(value), "__module__", ""),
        "repr": _safe_repr(value),
    }
    if reason:
        meta["reason"] = reason
    return _envelope("object", None, restorable=False, **meta)


def _is_dataframe(value: Any) -> bool:
    if type(value).__name__ != "DataFrame":
        return False
    return all(
        hasattr(value, attr)
        for attr in ("columns", "dtypes", "head", "shape", "to_dict")
    )


def _serialize_dataframe(
    value: Any,
    *,
    dataframe_max_rows: int,
    seen: set[int],
) -> dict[str, Any]:
    try:
        rows, cols = value.shape
        row_count = int(rows)
        column_count = int(cols)
        preview_rows = max(0, dataframe_max_rows)
        preview = value.head(preview_rows).to_dict("records")
        columns = [str(col) for col in value.columns]
        dtypes = {str(col): str(dtype) for col, dtype in value.dtypes.items()}
    except Exception:  # noqa: BLE001 - fall back to a safe object preview
        return _object_preview(value, reason="dataframe_serialization_failed")

    serialized_records = serialize_value(
        preview,
        dataframe_max_rows=dataframe_max_rows,
        _seen=seen,
    )
    truncated = row_count > preview_rows
    return _envelope(
        "dataframe",
        {
            "columns": columns,
            "records": serialized_records,
            "row_count": row_count,
            "column_count": column_count,
            "preview_row_count": min(row_count, preview_rows),
            "dtypes": dtypes,
            "truncated": truncated,
        },
        # Rehydrating a truncated DataFrame would silently feed incomplete data
        # into a retry, so only full previews are marked restorable.
        restorable=not truncated,
        python_type="DataFrame",
        module=getattr(type(value), "__module__", ""),
    )


def _serialize_bytes(value: bytes | bytearray, kind: str) -> dict[str, Any]:
    raw = bytes(value)
    preview: str | None = None
    try:
        text = raw.decode("utf-8")
        preview = text if len(text) <= 200 else f"{text[:199]}..."
    except UnicodeDecodeError:
        preview = None
    return _envelope(
        kind,
        {
            "base64": base64.b64encode(raw).decode("ascii"),
            "byte_length": len(raw),
            "utf8_preview": preview,
        },
    )


def serialize_value(
    value: Any,
    *,
    dataframe_max_rows: int = DEFAULT_DATAFRAME_ROWS,
    _seen: set[int] | None = None,
) -> Any:
    """Return a JSON-compatible value with safe type envelopes where useful."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_typed_envelope(value):
        return value

    seen = _seen if _seen is not None else set()
    value_id = id(value)
    if value_id in seen:
        return _object_preview(value, reason="cycle")

    if isinstance(value, datetime):
        return _envelope("datetime", value.isoformat())
    if isinstance(value, date):
        return _envelope("date", value.isoformat())
    if isinstance(value, time):
        return _envelope("time", value.isoformat())
    if isinstance(value, Decimal):
        return _envelope("decimal", str(value))
    if isinstance(value, bytes):
        return _serialize_bytes(value, "bytes")
    if isinstance(value, bytearray):
        return _serialize_bytes(value, "bytearray")

    seen.add(value_id)
    try:
        if _is_dataframe(value):
            return _serialize_dataframe(
                value,
                dataframe_max_rows=dataframe_max_rows,
                seen=seen,
            )
        if isinstance(value, tuple):
            return _envelope(
                "tuple",
                {
                    "items": [
                        serialize_value(
                            item,
                            dataframe_max_rows=dataframe_max_rows,
                            _seen=seen,
                        )
                        for item in value
                    ],
                    "length": len(value),
                },
            )
        if isinstance(value, (set, frozenset)):
            items = sorted(value, key=lambda item: repr(item))
            kind = "frozenset" if isinstance(value, frozenset) else "set"
            return _envelope(
                kind,
                {
                    "items": [
                        serialize_value(
                            item,
                            dataframe_max_rows=dataframe_max_rows,
                            _seen=seen,
                        )
                        for item in items
                    ],
                    "length": len(value),
                },
            )
        if isinstance(value, list):
            return [
                serialize_value(
                    item,
                    dataframe_max_rows=dataframe_max_rows,
                    _seen=seen,
                )
                for item in value
            ]
        if isinstance(value, dict):
            return {
                str(key): serialize_value(
                    item,
                    dataframe_max_rows=dataframe_max_rows,
                    _seen=seen,
                )
                for key, item in value.items()
            }
        return _object_preview(value)
    finally:
        seen.discard(value_id)


def _deserialize_dataframe(envelope: dict[str, Any]) -> Any:
    if envelope.get("restorable") is False:
        return envelope
    value = envelope.get("value")
    if not isinstance(value, dict):
        return envelope
    records = deserialize_value(value.get("records"))
    if not isinstance(records, list):
        return envelope
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - pandas is optional in the host process
        return envelope
    try:
        columns = value.get("columns")
        if isinstance(columns, list):
            return pd.DataFrame.from_records(records, columns=columns)
        return pd.DataFrame.from_records(records)
    except Exception:  # noqa: BLE001 - keep the envelope if rehydration fails
        return envelope


def _deserialize_envelope(envelope: dict[str, Any]) -> Any:
    if envelope.get("restorable") is False:
        return envelope

    kind = envelope.get("type")
    value = envelope.get("value")
    try:
        if kind == "datetime" and isinstance(value, str):
            return datetime.fromisoformat(value)
        if kind == "date" and isinstance(value, str):
            return date.fromisoformat(value)
        if kind == "time" and isinstance(value, str):
            return time.fromisoformat(value)
        if kind == "decimal" and isinstance(value, str):
            return Decimal(value)
        if kind == "tuple" and isinstance(value, dict):
            items = value.get("items", [])
            return tuple(deserialize_value(items if isinstance(items, list) else []))
        if kind == "set" and isinstance(value, dict):
            items = value.get("items", [])
            return set(deserialize_value(items if isinstance(items, list) else []))
        if kind == "frozenset" and isinstance(value, dict):
            items = value.get("items", [])
            return frozenset(
                deserialize_value(items if isinstance(items, list) else [])
            )
        if kind in {"bytes", "bytearray"} and isinstance(value, dict):
            encoded = value.get("base64")
            if not isinstance(encoded, str):
                return envelope
            raw = base64.b64decode(encoded.encode("ascii"))
            return bytearray(raw) if kind == "bytearray" else raw
        if kind == "dataframe":
            return _deserialize_dataframe(envelope)
    except Exception:  # noqa: BLE001 - bad stored data should not crash loading
        return envelope
    return envelope


def deserialize_value(value: Any) -> Any:
    """Rehydrate safe known typed envelopes inside a JSON-compatible value."""
    if is_typed_envelope(value):
        return _deserialize_envelope(value)
    if isinstance(value, list):
        return [deserialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: deserialize_value(item) for key, item in value.items()}
    return value


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}..."


def _cap_typed_envelope(value: dict[str, Any], cap: int) -> dict[str, Any]:
    kind = value.get("type")
    capped = dict(value)
    capped["restorable"] = False
    capped["truncated"] = True

    body = capped.get("value")
    if kind == "dataframe" and isinstance(body, dict):
        next_body = dict(body)
        records = next_body.get("records")
        if isinstance(records, list):
            next_body["records"] = records[: min(len(records), 10)]
            next_body["preview_row_count"] = len(next_body["records"])
            capped["value"] = next_body
            while next_body["records"] and _encoded_size(capped) > cap:
                next_body["records"] = next_body["records"][:-1]
                next_body["preview_row_count"] = len(next_body["records"])
        next_body["truncated"] = True
        capped["value"] = next_body
    elif kind in {"bytes", "bytearray"} and isinstance(body, dict):
        next_body = dict(body)
        next_body.pop("base64", None)
        preview = next_body.get("utf8_preview")
        if isinstance(preview, str):
            next_body["utf8_preview"] = _truncate_text(preview, 200)
        capped["value"] = next_body
    elif kind in {"tuple", "set", "frozenset"} and isinstance(body, dict):
        next_body = dict(body)
        items = next_body.get("items")
        if isinstance(items, list):
            next_body["items"] = items[:10]
            capped["value"] = next_body
            while next_body["items"] and _encoded_size(capped) > cap:
                next_body["items"] = next_body["items"][:-1]
        capped["value"] = next_body
    elif kind == "object":
        text = capped.get("repr")
        if isinstance(text, str):
            capped["repr"] = _truncate_text(text, 200)

    return capped


def truncate_serialized_value(value: Any, cap: int) -> Any:
    """Bound a JSON-compatible value while keeping typed envelopes readable."""
    if cap <= 0 or value is None:
        return value
    try:
        encoded = json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return value
    if len(encoded) <= cap:
        return value

    if is_typed_envelope(value):
        capped = _cap_typed_envelope(value, cap)
        if _encoded_size(capped) <= max(cap, 512):
            return capped

    return {
        "_truncated": True,
        "size_bytes": len(encoded),
        "preview": encoded[:1024],
    }
