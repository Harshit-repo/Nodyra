"""CLI output helpers: rich tables for humans, JSON for machines."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table

_console = Console()


def _plain(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, list):
        return [_plain(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _plain(value) for key, value in obj.items()}
    return obj


def _cell(item: Any, attr: str) -> str:
    value = (
        getattr(item, attr, None)
        if hasattr(item, attr)
        else item.get(attr)
        if isinstance(item, dict)
        else None
    )
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def emit(
    data: Any,
    *,
    json_mode: bool,
    columns: Sequence[tuple[str, str]] | None = None,
) -> None:
    if json_mode:
        print(json.dumps(_plain(data), indent=2, default=str))
        return
    if data is None:
        print("OK")
        return
    if columns is not None and isinstance(data, list):
        table = Table(header_style="bold")
        for header, _ in columns:
            table.add_column(header)
        for item in data:
            table.add_row(*[_cell(item, attr) for _, attr in columns])
        _console.print(table)
        return
    print(json.dumps(_plain(data), indent=2, default=str))


def emit_json_line(data: Any) -> None:
    """Emit one compact JSON object for streaming/NDJSON use cases."""
    print(json.dumps(_plain(data), separators=(",", ":"), default=str), flush=True)
